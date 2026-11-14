"""Parameter types as the symbolic layer understands them.

A parameter annotation is reduced to a ``ParamType``: a coarse ``Kind`` plus an
``optional`` flag (``X | None``). Annotations outside the table become ``Kind.OTHER``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum

from pytestgen.domain.annotations import base_name, is_none, union_members


class Kind(StrEnum):
    INT = "int"
    REAL = "real"
    BOOL = "bool"
    STR = "str"
    LIST = "list"
    TUPLE = "tuple"
    BYTES = "bytes"
    DICT = "dict"
    OTHER = "other"


SCALAR_KINDS = frozenset({Kind.INT, Kind.REAL, Kind.BOOL, Kind.STR})
LENGTH_KINDS = frozenset({Kind.LIST, Kind.TUPLE, Kind.BYTES})
"""Kinds that are not modelled by value; only ``len(x)`` and truthiness are understood."""

_KIND_BY_NAME: dict[str, Kind] = {
    "int": Kind.INT,
    "float": Kind.REAL,
    "bool": Kind.BOOL,
    "str": Kind.STR,
    "bytes": Kind.BYTES,
    "list": Kind.LIST,
    "List": Kind.LIST,
    "Sequence": Kind.LIST,
    "MutableSequence": Kind.LIST,
    "Collection": Kind.LIST,
    "tuple": Kind.TUPLE,
    "Tuple": Kind.TUPLE,
    "dict": Kind.DICT,
    "Dict": Kind.DICT,
    "Mapping": Kind.DICT,
    "MutableMapping": Kind.DICT,
}

_RUNTIME_CLASSES: dict[Kind, frozenset[str]] = {
    Kind.INT: frozenset({"int", "object"}),
    Kind.BOOL: frozenset({"bool", "int", "object"}),
    Kind.REAL: frozenset({"float", "object"}),
    Kind.STR: frozenset({"str", "object"}),
    Kind.LIST: frozenset({"list", "object"}),
    Kind.TUPLE: frozenset({"tuple", "object"}),
    Kind.BYTES: frozenset({"bytes", "object"}),
    Kind.DICT: frozenset({"dict", "object"}),
}

_NEUTRAL_SCALARS: dict[Kind, object] = {
    Kind.INT: 0,
    Kind.REAL: 0.0,
    Kind.BOOL: False,
    Kind.STR: "",
}


@dataclass(frozen=True, slots=True)
class ParamType:
    """Coarse type of one parameter."""

    kind: Kind
    optional: bool = False
    element: Kind = Kind.OTHER
    """Element kind of ``LIST``/``TUPLE`` (``OTHER`` when unknown)."""
    text: str = ""
    """Annotation source text; empty for unannotated parameters."""

    @property
    def is_scalar(self) -> bool:
        return self.kind in SCALAR_KINDS

    @property
    def is_length_modelled(self) -> bool:
        return self.kind in LENGTH_KINDS

    @property
    def runtime_classes(self) -> frozenset[str] | None:
        """Names of the classes a synthesized value belongs to, ``None`` when unknown."""
        return _RUNTIME_CLASSES.get(self.kind)

    @property
    def has_neutral(self) -> bool:
        return self.optional or self.kind in _NEUTRAL_SCALARS or self.kind in _CONTAINER_NEUTRAL

    def neutral(self) -> object:
        """A harmless value for a parameter the condition does not mention."""
        if self.optional:
            return None
        if self.kind in _NEUTRAL_SCALARS:
            return _NEUTRAL_SCALARS[self.kind]
        return _CONTAINER_NEUTRAL[self.kind]()

    def sized(self, length: int) -> object:
        """A ``list``/``tuple``/``bytes`` of ``length`` neutral elements."""
        if self.kind is Kind.BYTES:
            return bytes(length)
        items = [_NEUTRAL_SCALARS.get(self.element) for _ in range(length)]
        return tuple(items) if self.kind is Kind.TUPLE else items


_CONTAINER_NEUTRAL: dict[Kind, type] = {
    Kind.LIST: list,
    Kind.TUPLE: tuple,
    Kind.BYTES: bytes,
    Kind.DICT: dict,
}


def unknown_type(text: str = "") -> ParamType:
    return ParamType(Kind.OTHER, text=text)


def parse_annotation(text: str) -> ParamType:
    """Reduce annotation source text to a ``ParamType`` (``OTHER`` when not understood)."""
    try:
        node = ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        return unknown_type(text)
    return _from_node(node, text)


def _from_node(node: ast.expr, text: str) -> ParamType:
    optional, members = _split_union(_unwrap(node))
    if len(members) != 1:
        return ParamType(Kind.OTHER, optional=optional, text=text)
    member = members[0]
    kind = _KIND_BY_NAME.get(base_name(member) or "", Kind.OTHER)
    element = Kind.OTHER
    if isinstance(member, ast.Subscript) and kind in (Kind.LIST, Kind.TUPLE):
        element = _element_kind(member.slice)
    return ParamType(kind, optional=optional, element=element, text=text)


def _unwrap(node: ast.expr) -> ast.expr:
    """Resolve quoted forward references and ``Annotated[X, ...]``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _unwrap(ast.parse(node.value.strip(), mode="eval").body)
        except SyntaxError:
            return node
    if (
        isinstance(node, ast.Subscript)
        and base_name(node.value) == "Annotated"
        and isinstance(node.slice, ast.Tuple)
        and node.slice.elts
    ):
        return _unwrap(node.slice.elts[0])
    return node


def _split_union(node: ast.expr) -> tuple[bool, list[ast.expr]]:
    """Flatten ``A | B``, ``Optional[A]`` and ``Union[A, B]`` into ``(has_none, members)``."""
    members = union_members(node, _unwrap)
    return any(is_none(m) for m in members), [m for m in members if not is_none(m)]


def _element_kind(slice_node: ast.expr) -> Kind:
    first = (
        slice_node.elts[0] if isinstance(slice_node, ast.Tuple) and slice_node.elts else slice_node
    )
    kind = _KIND_BY_NAME.get(base_name(_unwrap(first)) or "", Kind.OTHER)
    return kind if kind in SCALAR_KINDS else Kind.OTHER
