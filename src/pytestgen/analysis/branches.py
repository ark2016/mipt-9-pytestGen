# ruff: noqa: N815  (ast.NodeVisitor dispatches on visit_<NodeClass> names)
"""Branch targets: both outcomes of every branch point, with path conditions and arcs.

The walk is sequential over each block. For every statement it knows

* the *prefix*: clauses that hold on entry (enclosing branch polarities and early exits
  of preceding siblings), already expressed over the function parameters;
* the *environment*: single straight-line assignments to substitute into clauses;
* the *continuation*: the line control reaches when a block falls off its end, which
  yields coverage.py arcs (``None`` when it cannot be determined statically).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from pytestgen.analysis.astutil import (
    FunctionNode,
    dotted_name,
    first_line,
    landing_line,
    raised_name,
    terminates,
)
from pytestgen.analysis.substitution import SubstitutionEnv, substitutable_names
from pytestgen.domain.models import Arc, BranchKind, BranchTarget, Clause, PathCondition

_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True, slots=True)
class _Scope:
    """What is known on entry to a statement."""

    prefix: tuple[Clause, ...]
    env: SubstitutionEnv

    def under(self, *clauses: Clause) -> _Scope:
        return _Scope((*self.prefix, *clauses), self.env)


@dataclass(frozen=True, slots=True)
class _Point:
    """A branch point: where it is, what it is and how its condition reads."""

    line: int
    kind: BranchKind
    condition: str


def _clause(env: SubstitutionEnv, expr: ast.expr, expected: bool) -> Clause:
    return Clause(env.text(expr), expected)


class _IfExpScanner(ast.NodeVisitor):
    """Finds ternaries in an expression together with the guards of enclosing ternaries."""

    def __init__(self, env: SubstitutionEnv) -> None:
        self._env = env
        self._guards: list[Clause] = []
        self.found: list[tuple[ast.IfExp, tuple[Clause, ...]]] = []

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.found.append((node, tuple(self._guards)))
        self.visit(node.test)
        for expected, arm in ((True, node.body), (False, node.orelse)):
            self._guards.append(_clause(self._env, node.test, expected))
            self.visit(arm)
            self._guards.pop()

    def _separate_scope(self, node: ast.AST) -> None:
        """Lambdas and comprehensions bind their own variables: not analysable here."""

    visit_Lambda = visit_ListComp = visit_SetComp = _separate_scope
    visit_DictComp = visit_GeneratorExp = _separate_scope


def _owned_expressions(stmt: ast.stmt) -> Iterator[ast.expr]:
    """Expressions evaluated by ``stmt`` itself (not by its nested statements)."""
    for _, value in ast.iter_fields(stmt):
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, ast.expr):
                yield item
            elif isinstance(item, ast.withitem):
                yield item.context_expr


class _FunctionWalk:
    """One pass over one function; collects its targets."""

    def __init__(self, function: FunctionNode, qualname: str) -> None:
        self._function = function
        self._qualname = qualname
        self._targets: list[BranchTarget] = []

    def run(self) -> tuple[BranchTarget, ...]:
        env = SubstitutionEnv(substitutable_names(self._function))
        exit_line = -first_line(self._function)
        self._block(self._function.body, _Scope((), env), exit_line)
        return tuple(sorted(self._targets, key=lambda t: t.line))

    # ------------------------------------------------------------------ blocks

    def _block(self, stmts: Sequence[ast.stmt], scope: _Scope, after: int | None) -> None:
        prefix = scope.prefix
        env = scope.env
        for index, stmt in enumerate(stmts):
            successor = landing_line(stmts[index + 1 :], after)
            self._statement(stmt, _Scope(prefix, env), successor)
            env = env.bind(stmt)
            prefix = (*prefix, *self._guards(stmt, env))

    @classmethod
    def _guards(cls, stmt: ast.stmt, env: SubstitutionEnv) -> tuple[Clause, ...]:
        """Clauses that hold after ``stmt`` because it exits early or asserts."""
        if isinstance(stmt, ast.Assert):
            return (_clause(env, stmt.test, True),)
        if not isinstance(stmt, ast.If):
            return ()
        body_exits = terminates(stmt.body)
        else_exits = terminates(stmt.orelse)
        if else_exits and not body_exits:
            return (_clause(env, stmt.test, True),)
        if body_exits and not else_exits:
            # an ``elif`` chain: falling through means every earlier test failed too
            chained = cls._guards(stmt.orelse[0], env) if len(stmt.orelse) == 1 else ()
            return (_clause(env, stmt.test, False), *chained)
        return ()

    def _statement(self, stmt: ast.stmt, scope: _Scope, successor: int | None) -> None:
        if isinstance(stmt, _NESTED_SCOPES):
            return
        if isinstance(stmt, ast.If):
            self._if(stmt, scope, successor)
        elif isinstance(stmt, ast.While):
            self._while(stmt, scope, successor)
        elif isinstance(stmt, ast.For | ast.AsyncFor):
            self._for(stmt, scope, successor)
        elif isinstance(stmt, ast.Assert):
            self._assert(stmt, scope)
        elif isinstance(stmt, ast.Try | ast.TryStar):
            self._try(stmt, scope, successor)
        elif isinstance(stmt, ast.With | ast.AsyncWith):
            self._block(stmt.body, scope, stmt.lineno)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                self._block(case.body, scope, successor)
        self._ternaries(stmt, scope)

    # ------------------------------------------------------------------ branch points

    def _if(self, stmt: ast.If, scope: _Scope, successor: int | None) -> None:
        test = stmt.test
        constant = isinstance(test, ast.Constant)
        arms = ((True, stmt.body), (False, stmt.orelse))
        for outcome, block in arms:
            dst = landing_line(block, successor)
            self._emit(
                _Point(stmt.lineno, BranchKind.IF, ast.unparse(test)),
                outcome,
                arc=None if constant else _arc(stmt.lineno, dst),
                scope=scope.under(_clause(scope.env, test, outcome)),
                raises=raised_name(block),
            )
        self._block(stmt.body, scope.under(_clause(scope.env, test, True)), successor)
        self._block(stmt.orelse, scope.under(_clause(scope.env, test, False)), successor)

    def _while(self, stmt: ast.While, scope: _Scope, successor: int | None) -> None:
        test = stmt.test
        constant = isinstance(test, ast.Constant)
        arms = ((True, stmt.body), (False, stmt.orelse))
        for outcome, block in arms:
            dst = landing_line(block, successor)
            self._emit(
                _Point(stmt.lineno, BranchKind.WHILE, ast.unparse(test)),
                outcome,
                arc=None if constant and not outcome else _arc(stmt.lineno, dst),
                scope=scope.under(_clause(scope.env, test, outcome)),
                raises=raised_name(block),
            )
        self._block(stmt.body, scope.under(_clause(scope.env, test, True)), stmt.lineno)
        self._block(stmt.orelse, scope.under(_clause(scope.env, test, False)), successor)

    def _for(self, stmt: ast.For | ast.AsyncFor, scope: _Scope, successor: int | None) -> None:
        plain = dotted_name(stmt.iter) is not None
        if plain:
            source = f"len({ast.unparse(stmt.iter)}) > 0"
            nonempty = f"len({scope.env.text(stmt.iter)}) > 0"
        else:
            source = ast.unparse(stmt.iter)
        arms = ((True, stmt.body), (False, stmt.orelse))
        for outcome, block in arms:
            dst = landing_line(block, successor)
            self._emit(
                _Point(stmt.lineno, BranchKind.FOR, source),
                outcome,
                arc=_arc(stmt.lineno, dst),
                scope=scope.under(Clause(nonempty, outcome)) if plain else scope,
                raises=raised_name(block),
            )
        entered = scope.under(Clause(nonempty, True)) if plain else scope
        self._block(stmt.body, entered, stmt.lineno)
        self._block(stmt.orelse, scope, successor)

    def _assert(self, stmt: ast.Assert, scope: _Scope) -> None:
        for outcome in (True, False):
            self._emit(
                _Point(stmt.lineno, BranchKind.ASSERT, ast.unparse(stmt.test)),
                outcome,
                arc=None,
                scope=scope.under(_clause(scope.env, stmt.test, outcome)),
                raises=None if outcome else "AssertionError",
            )

    def _try(self, stmt: ast.Try | ast.TryStar, scope: _Scope, successor: int | None) -> None:
        # Normal completion of every part flows into ``finally`` when there is one.
        after_parts = landing_line(stmt.finalbody, successor)
        body_after = landing_line(stmt.orelse, after_parts)
        self._block(stmt.body, scope, body_after)
        for handler in stmt.handlers:
            self._block(handler.body, scope, after_parts)
        self._block(stmt.orelse, scope, after_parts)
        self._block(stmt.finalbody, scope, successor)

    def _ternaries(self, stmt: ast.stmt, scope: _Scope) -> None:
        """Targets for conditional expressions evaluated directly by ``stmt``."""
        scanner = _IfExpScanner(scope.env)
        for expr in _owned_expressions(stmt):
            scanner.visit(expr)
        for node, guards in scanner.found:
            for outcome in (True, False):
                self._emit(
                    _Point(stmt.lineno, BranchKind.IF_EXP, ast.unparse(node.test)),
                    outcome,
                    arc=None,
                    scope=scope.under(*guards, _clause(scope.env, node.test, outcome)),
                    raises=None,
                )

    # ------------------------------------------------------------------ output

    def _emit(
        self,
        point: _Point,
        outcome: bool,
        *,
        arc: Arc | None,
        scope: _Scope,
        raises: str | None,
    ) -> None:
        self._targets.append(
            BranchTarget(
                function=self._qualname,
                line=point.line,
                kind=point.kind,
                condition_source=point.condition,
                outcome=outcome,
                arc=arc,
                path=PathCondition(scope.prefix),
                raises=raises,
            )
        )


def _arc(src: int, dst: int | None) -> Arc | None:
    return None if dst is None else Arc(src, dst)


class BranchExtractor:
    """Computes the branch targets of one function from its AST."""

    def extract(self, function: FunctionNode, qualname: str) -> tuple[BranchTarget, ...]:
        """Both outcomes of every if/elif, while, for, ternary and assert in ``function``.

        Nested functions and classes are not entered. Targets are ordered by line.
        """
        return _FunctionWalk(function, qualname).run()
