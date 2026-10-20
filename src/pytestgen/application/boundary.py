"""Boundary values for numeric comparisons, so off-by-one bugs reach the oracle.

A solver picks *some* value satisfying a branch (``order_total > 100`` gets 101), never
the edge itself, and a function without branches gets a single arbitrary input. Cheap
static analysis fills the gap: for every ``param <op> constant`` comparison in the
source (``<``, ``<=``, ``>``, ``>=``) the constant and its neighbours are tried.
"""

from __future__ import annotations

import ast
import math
import textwrap
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from pytestgen.domain.annotations import is_none, parse_annotation, union_members
from pytestgen.domain.models import FunctionInfo, Parameter

_ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
Number = int | float


@dataclass(frozen=True, slots=True)
class _Edge:
    """A parameter compared with a numeric constant."""

    param: Parameter
    constant: Number


def boundary_vectors(
    function: FunctionInfo, base: Mapping[str, object], limit: int
) -> list[dict[str, object]]:
    """Copies of ``base`` with one parameter set to a boundary value, at most ``limit``.

    Exact constants come first (they decide ``>`` against ``>=``), then neighbours
    (``c - 1`` and ``c + 1`` for integers, the adjacent floats for floats). The result
    never contains ``base`` itself and has no duplicates.
    """
    if limit <= 0:
        return []
    edges = list(_edges(function))
    exact = [(e, e.constant) for e in edges]
    around = [(e, v) for e in edges for v in _neighbours(e.constant)]
    vectors: list[dict[str, object]] = []
    seen = [dict(base)]
    for edge, raw in (*exact, *around):
        value = _fit(edge.param, raw)
        if value is None:
            continue
        vector = {**base, edge.param.name: value}
        if vector not in seen:
            seen.append(vector)
            vectors.append(vector)
        if len(vectors) >= limit:
            break
    return vectors


def _edges(function: FunctionInfo) -> Iterator[_Edge]:
    params = {p.name: p for p in function.call_parameters}
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return
    found: set[tuple[str, Number]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for left, op, right in zip(operands, node.ops, operands[1:], strict=False):
            if not isinstance(op, _ORDERING):
                continue
            pair = _name_and_number(left, right) or _name_and_number(right, left)
            if pair is not None and pair[0] in params and pair not in found:
                found.add(pair)
                yield _Edge(params[pair[0]], pair[1])


def _name_and_number(name: ast.expr, number: ast.expr) -> tuple[str, Number] | None:
    value = _number(number)
    if isinstance(name, ast.Name) and value is not None:
        return name.id, value
    return None


def _number(node: ast.expr) -> Number | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _number(node.operand)
        return None if inner is None else -inner
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        if isinstance(node.value, bool) or not math.isfinite(node.value):
            return None
        return node.value
    return None


def _neighbours(constant: Number) -> list[Number]:
    if isinstance(constant, int):
        return [constant - 1, constant + 1]
    return [math.nextafter(constant, -math.inf), math.nextafter(constant, math.inf)]


def _fit(param: Parameter, value: Number) -> Number | None:
    """``value`` in the numeric type ``param`` is annotated with; ``None`` if unsuitable."""
    kinds = _numeric_kinds(param.annotation)
    if kinds is None:
        return value
    if "float" in kinds:
        return float(value)
    if "int" in kinds:
        return int(value) if float(value).is_integer() else None
    return None


def _numeric_kinds(annotation: str | None) -> set[str] | None:
    """``{"int"}``/``{"float"}``/... for annotations made of numbers and ``None``, else ``set()``.

    ``None`` means "no annotation": the constant's own type is used.
    """
    node = parse_annotation(annotation)
    if node is None:
        return None
    kinds: set[str] = set()
    for member in union_members(node):
        if is_none(member):
            continue
        if isinstance(member, ast.Name) and member.id in {"int", "float"}:
            kinds.add(member.id)
        else:
            return set()
    return kinds
