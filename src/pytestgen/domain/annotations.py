"""Reading type annotations (source text / ``ast`` nodes) without importing anything.

One interpretation of ``A | B``, ``Optional[A]`` and ``Union[A, B]`` shared by the
symbolic layer (``concolic.ptypes``) and the validation of model-proposed inputs.
Pure functions over the standard library only.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Final

_SCALARS: Final = frozenset({"int", "float", "str", "bool", "None"})
_TUPLE_LIKE: Final = frozenset({"tuple"})
_LIST_LIKE: Final = frozenset({"list", "sequence", "iterable", "collection", "mutablesequence"})
_DICT_LIKE: Final = frozenset({"dict", "mapping", "mutablemapping"})


def parse_annotation(annotation: str | None) -> ast.expr | None:
    """The expression node of annotation source text; ``None`` when empty or not Python."""
    if not annotation:
        return None
    try:
        return ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return None


def base_name(node: ast.expr) -> str | None:
    """Name of the generic behind ``node``: ``list`` for ``list[int]``, ``Optional`` for
    ``typing.Optional[int]``; ``None`` when the node is not a (subscripted) name."""
    if isinstance(node, ast.Subscript):
        return base_name(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def type_args(node: ast.expr) -> list[ast.expr]:
    """Subscript arguments of ``node`` (``[int, str]`` for ``dict[int, str]``), else ``[]``."""
    if not isinstance(node, ast.Subscript):
        return []
    return list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]


def union_members(
    node: ast.expr, unwrap: Callable[[ast.expr], ast.expr] = lambda n: n
) -> list[ast.expr]:
    """Flatten ``A | B``, ``Optional[A]`` and ``Union[A, B]``; any other node is its own member.

    ``None`` members are kept (as ``Constant(None)``, also for ``Optional``). ``unwrap`` is
    applied to every node first, so a caller can resolve forward references or ``Annotated``.
    """
    node = unwrap(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [*union_members(node.left, unwrap), *union_members(node.right, unwrap)]
    if isinstance(node, ast.Subscript) and base_name(node) in {"Optional", "Union"}:
        found: list[ast.expr] = []
        for part in type_args(node):
            found.extend(union_members(part, unwrap))
        if base_name(node) == "Optional":
            found.append(ast.Constant(value=None))
        return found
    return [node]


def is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


# --------------------------------------------------------------------------- JSON values


def coerce_json_value(value: object, annotation: str | None) -> object:
    """JSON has lists only: make them tuples where the annotation asks for tuples."""
    node = parse_annotation(annotation)
    return value if node is None else _convert(value, node)


def fit_scalar(value: object, annotation: str | None) -> tuple[bool, object]:
    """Loose check against a plain ``int | float | str | bool | None`` annotation.

    Any other annotation fits. An integral float is accepted for ``int`` and converted.
    Returns ``(fits, possibly converted value)``.
    """
    names = _scalar_names(annotation)
    if names is None:
        return True, value
    if isinstance(value, float) and value.is_integer() and "int" in names and "float" not in names:
        value = int(value)
    return any(_has_type(value, name) for name in names), value


def _lower_name(node: ast.expr) -> str:
    return (base_name(node) or "").lower()


def _convert(value: object, node: ast.expr) -> object:
    if isinstance(value, list):
        return _convert_list(value, node)
    if isinstance(value, dict):
        for alt in union_members(node):
            args = type_args(alt)
            if _lower_name(alt) in _DICT_LIKE and len(args) == 2:
                return {k: _convert(v, args[1]) for k, v in value.items()}
    return value


def _convert_list(items: list[object], node: ast.expr) -> object:
    for alt in union_members(node):
        base, args = _lower_name(alt), type_args(alt)
        if base in _TUPLE_LIKE:
            if not args:
                return tuple(items)
            if len(args) == 2 and isinstance(args[1], ast.Constant) and args[1].value is ...:
                return tuple(_convert(v, args[0]) for v in items)
            if len(args) == len(items):
                return tuple(_convert(v, a) for v, a in zip(items, args, strict=True))
            return tuple(_convert(v, args[0]) for v in items)
        if base in _LIST_LIKE:
            return [_convert(v, args[0]) for v in items] if args else items
    return items


def _scalar_names(annotation: str | None) -> set[str] | None:
    node = parse_annotation(annotation)
    if node is None:
        return None
    names: set[str] = set()
    for alt in union_members(node):
        if is_none(alt):
            names.add("None")
        elif isinstance(alt, ast.Name) and alt.id in _SCALARS:
            names.add(alt.id)
        else:
            return None
    return names


def _has_type(value: object, name: str) -> bool:
    if name == "None":
        return value is None
    if name == "bool":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if name == "int":
        return isinstance(value, int)
    if name == "float":
        return isinstance(value, int | float)
    return isinstance(value, str)
