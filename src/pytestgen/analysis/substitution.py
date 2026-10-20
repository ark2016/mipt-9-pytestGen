# ruff: noqa: N815  (ast.NodeVisitor dispatches on visit_<NodeClass> names)
"""Substitution of straight-line local assignments into path-condition expressions.

``y = x * 2`` followed by ``if y > 10`` yields the clause ``(x * 2) > 10``. Only names
that are bound exactly once in the function, outside loops and ``try`` blocks, may be
substituted; everything else keeps its name and is left to the solver to reject.
"""

from __future__ import annotations

import ast
import copy
from collections import Counter
from collections.abc import Mapping
from typing import cast

from pytestgen.analysis.astutil import FunctionNode

_ATOMS = (ast.Name, ast.Constant, ast.Attribute, ast.Call, ast.Subscript)
_MUTABLE_DISPLAYS = (
    ast.List,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.DictComp,
    ast.SetComp,
    ast.GeneratorExp,
)
_MUTABLE_FACTORIES = frozenset({"list", "dict", "set", "bytearray"})
_Comprehension = ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
_EFFECTFUL = (ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom)


class _BindingCounter(ast.NodeVisitor):
    """Counts how often each local name is bound; loop/try bindings count as "many"."""

    def __init__(self, parameters: list[str]) -> None:
        self.counts: Counter[str] = Counter(parameters)
        self._nesting = 0

    def _bind(self, name: str) -> None:
        self.counts[name] += 2 if self._nesting else 1

    def _nested(self, node: ast.AST) -> None:
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    visit_For = visit_AsyncFor = visit_While = visit_Try = visit_TryStar = _nested

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            self._bind(node.id)

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self._bind(node.name)  # the nested body is a separate scope

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _definition

    def _separate_scope(self, node: ast.AST) -> None:
        return None

    visit_Lambda = visit_ListComp = visit_SetComp = _separate_scope
    visit_DictComp = visit_GeneratorExp = _separate_scope

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind((alias.asname or alias.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def _declaration(self, node: ast.Global | ast.Nonlocal) -> None:
        self.counts.update(dict.fromkeys(node.names, 2))

    visit_Global = visit_Nonlocal = _declaration

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._bind(node.rest)
        self.generic_visit(node)


def substitutable_names(function: FunctionNode) -> frozenset[str]:
    """Local names bound exactly once in ``function`` (parameters count as a binding)."""
    args = function.args
    params = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    params += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
    counter = _BindingCounter(params)
    for stmt in function.body:
        counter.visit(stmt)
    return frozenset(name for name, count in counter.counts.items() if count == 1)


def _bound_by_comprehension(node: _Comprehension) -> set[str]:
    return {
        name.id
        for generator in node.generators
        for name in ast.walk(generator.target)
        if isinstance(name, ast.Name)
    }


class _Substituter(ast.NodeTransformer):
    def __init__(self, bindings: Mapping[str, ast.expr]) -> None:
        self._bindings = bindings

    def _without(self, names: set[str]) -> _Substituter:
        return _Substituter({k: v for k, v in self._bindings.items() if k not in names})

    def visit_Lambda(self, node: ast.Lambda) -> ast.expr:
        """Lambda parameters shadow assigned locals inside the body."""
        args = node.args
        params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        params |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
        args.defaults = [self.visit(d) for d in args.defaults]
        args.kw_defaults = [None if d is None else self.visit(d) for d in args.kw_defaults]
        node.body = self._without(params).visit(node.body)
        return node

    def _comprehension(self, node: _Comprehension) -> ast.expr:
        """Comprehension targets shadow assigned locals; the first iterable is outer."""
        first = node.generators[0]
        outer_iter = self.visit(first.iter)
        first.iter = ast.Constant(value=None)  # keep it away from the shadowing visitor
        self._without(_bound_by_comprehension(node)).generic_visit(node)
        first.iter = outer_iter
        return node

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _comprehension

    def visit_Name(self, node: ast.Name) -> ast.expr:
        value = self._bindings.get(node.id)
        if value is None or not isinstance(node.ctx, ast.Load):
            return node
        if isinstance(value, _ATOMS):
            return copy.deepcopy(value)
        # Smuggle the parenthesized text in as a Name so ast.unparse keeps it verbatim.
        return ast.Name(id=f"({ast.unparse(value)})", ctx=ast.Load())


class SubstitutionEnv:
    """Immutable mapping ``local name -> already-resolved assigned expression``."""

    def __init__(
        self, safe: frozenset[str], bindings: Mapping[str, ast.expr] | None = None
    ) -> None:
        self._safe = safe
        self._bindings: Mapping[str, ast.expr] = bindings or {}

    def resolve(self, expr: ast.expr) -> ast.expr:
        """Copy of ``expr`` with every bound name replaced by its assigned expression."""
        if not self._bindings:
            return expr
        return cast("ast.expr", _Substituter(self._bindings).visit(copy.deepcopy(expr)))

    def text(self, expr: ast.expr) -> str:
        """Source text of ``expr`` after substitution."""
        return ast.unparse(self.resolve(expr))

    def bind(self, stmt: ast.stmt) -> SubstitutionEnv:
        """Environment after ``stmt``; unchanged unless it is a substitutable assignment."""
        name, value = _simple_assignment(stmt)
        if name is None or value is None or name not in self._safe or not _is_immutable(value):
            return self
        return SubstitutionEnv(self._safe, {**self._bindings, name: self.resolve(value)})


def _simple_assignment(stmt: ast.stmt) -> tuple[str | None, ast.expr | None]:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target: ast.expr = stmt.targets[0]
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        target = stmt.target
    else:
        return None, None
    if isinstance(target, ast.Name):
        return target.id, stmt.value
    return None, None


def _is_immutable(value: ast.expr) -> bool:
    """False for values whose later mutation would make the substitution wrong or unsafe."""
    for node in ast.walk(value):
        if isinstance(node, _EFFECTFUL):
            return False
    if isinstance(value, _MUTABLE_DISPLAYS):
        return False
    return not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _MUTABLE_FACTORIES
    )
