"""Types of the function parameters: annotations first, usage-based guesses as a fallback."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import replace

from pytestgen.concolic.ptypes import Kind, ParamType, parse_annotation, unknown_type
from pytestgen.domain.models import Parameter

_ARITHMETIC = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod, ast.Div)
_STR_METHODS = frozenset({"startswith", "endswith", "lower", "upper", "strip", "split"})
_NUMERIC = frozenset({Kind.INT, Kind.REAL})


def infer_types(
    parameters: Sequence[Parameter], conditions: Iterable[ast.expr]
) -> dict[str, ParamType]:
    """Map every parameter name to a ``ParamType``.

    Annotated parameters use their annotation. An unannotated parameter is ``INT`` or
    ``REAL`` when the conditions compare it with numbers or do arithmetic on it, ``STR``
    when it is compared with a string, used with ``len()``/``startswith``/``in`` on
    strings; otherwise it stays ``OTHER`` (unsupported).
    """
    types = {
        p.name: parse_annotation(p.annotation) if p.annotation else unknown_type()
        for p in parameters
    }
    scanner = _UsageScanner(frozenset(types))
    for condition in conditions:
        scanner.visit(condition)
    hints = scanner.resolved_hints({n: t.kind for n, t in types.items() if t.is_scalar})
    for param in parameters:
        if not param.annotation:
            types[param.name] = ParamType(hints.get(param.name, Kind.OTHER))
    for param in parameters:
        if _may_be_none(param, types[param.name], scanner.none_checked):
            types[param.name] = replace(types[param.name], optional=True)
    return types


def _may_be_none(param: Parameter, ptype: ParamType, none_checked: frozenset[str]) -> bool:
    """Whether ``None`` is a possible value of the parameter.

    Known for an explicit ``None`` default, and assumed (so that ``x is None`` can be
    solved instead of being declared impossible) when the condition tests an unannotated
    parameter or one whose annotation (``Any``, ``object``, a union...) is not understood.
    """
    if param.default == "None":
        return True
    return param.name in none_checked and (not param.annotation or ptype.kind is Kind.OTHER)


class _UsageScanner(ast.NodeVisitor):
    """Collects, per parameter name, which kinds the surrounding expressions suggest."""

    def __init__(self, names: frozenset[str]) -> None:
        self._names = names
        self._hints: dict[str, set[Kind]] = {n: set() for n in names}
        self._links: list[tuple[str, str]] = []
        self._none_checked: set[str] = set()

    @property
    def none_checked(self) -> frozenset[str]:
        """Parameters compared with ``is None`` / ``is not None``."""
        return frozenset(self._none_checked)

    def resolved_hints(self, known: dict[str, Kind]) -> dict[str, Kind]:
        """One kind per hinted name; ``known`` (annotated scalars) seeds the propagation."""
        for name, kind in known.items():
            self._hints[name] = {kind}
        for _ in self._names:
            changed = False
            for left, right in self._links:
                for src, dst in ((left, right), (right, left)):
                    if self._hints[src] and not self._hints[dst]:
                        self._hints[dst] = set(self._hints[src])
                        changed = True
            if not changed:
                break
        return {n: merged for n, h in self._hints.items() if (merged := _merge(h)) is not None}

    # ------------------------------------------------------------------ visitors

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for left, op, right in zip(operands[:-1], node.ops, operands[1:], strict=True):
            if isinstance(op, ast.In | ast.NotIn):
                self._membership(left, right)
            elif isinstance(op, ast.Is | ast.IsNot):
                self._none_check(left, right)
            else:
                self._pair(left, right)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, _ARITHMETIC):
            self._pair(node.left, node.right, numeric_only=True)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "len" and len(node.args) == 1:
            self._hint(node.args[0], Kind.STR)
        elif isinstance(func, ast.Attribute) and func.attr in _STR_METHODS:
            self._hint(func.value, Kind.STR)
        self.generic_visit(node)

    # ------------------------------------------------------------------ helpers

    def _none_check(self, left: ast.expr, right: ast.expr) -> None:
        for subject, other in ((left, right), (right, left)):
            name = self._param_name(subject)
            if name is not None and isinstance(other, ast.Constant) and other.value is None:
                self._none_checked.add(name)

    def _membership(self, left: ast.expr, right: ast.expr) -> None:
        if isinstance(right, ast.Tuple | ast.List | ast.Set):
            kinds = {_kind_of(e) for e in right.elts}
            only = kinds.pop() if len(kinds) == 1 else None
            if only is not None:
                self._hint(left, only)
        else:
            self._hint(left, Kind.STR)
            self._hint(right, Kind.STR)

    def _pair(self, left: ast.expr, right: ast.expr, *, numeric_only: bool = False) -> None:
        left_kind, right_kind = _kind_of(left), _kind_of(right)
        if right_kind and (not numeric_only or right_kind in _NUMERIC):
            self._hint(left, right_kind)
        if left_kind and (not numeric_only or left_kind in _NUMERIC):
            self._hint(right, left_kind)
        left_name, right_name = self._param_name(left), self._param_name(right)
        if left_name and right_name:
            self._links.append((left_name, right_name))

    def _hint(self, node: ast.expr, kind: Kind) -> None:
        """Record ``kind`` for the parameters ``node`` is built from by + - * // % and abs."""
        name = self._param_name(node)
        if name is not None:
            self._hints[name].add(kind)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            self._hint(node.operand, kind)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _ARITHMETIC):
            if kind is Kind.INT:
                self._hint(node.left, kind)
                self._hint(node.right, kind)
        elif isinstance(node, ast.Call) and _is_numeric_call(node) and kind in _NUMERIC:
            for arg in node.args:
                self._hint(arg, kind)

    def _param_name(self, node: ast.expr) -> str | None:
        return node.id if isinstance(node, ast.Name) and node.id in self._names else None


def _is_numeric_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id in ("abs", "min", "max")


def _kind_of(node: ast.expr) -> Kind | None:
    """Kind suggested by a constant or by arithmetic over constants; ``None`` when unknown."""
    if isinstance(node, ast.Constant):
        return _constant_kind(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return _kind_of(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ARITHMETIC):
        if isinstance(node.op, ast.Div):
            return Kind.REAL
        kinds = {_kind_of(node.left), _kind_of(node.right)} - {None}
        return Kind.REAL if Kind.REAL in kinds else (Kind.INT if Kind.INT in kinds else None)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
        return Kind.INT
    return None


def _constant_kind(value: object) -> Kind | None:
    if isinstance(value, bool):
        return Kind.BOOL
    if isinstance(value, int):
        return Kind.INT
    if isinstance(value, float):
        return Kind.REAL
    if isinstance(value, str):
        return Kind.STR
    return None


def _merge(kinds: set[Kind]) -> Kind | None:
    """Combine hints; contradictory ones (a str that is also a number) give up."""
    if len(kinds) > 1:
        kinds = kinds - {Kind.BOOL}
    if kinds == {Kind.INT, Kind.REAL}:
        return Kind.REAL
    return next(iter(kinds)) if len(kinds) == 1 else None
