"""Conversion of domain objects to JSON-ready plain data."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from enum import Enum
from pathlib import PurePath
from typing import Any

from pytestgen.domain.models import Arc, CoverageDelta, CoverageSummary

_DERIVED: dict[type, tuple[str, ...]] = {
    CoverageSummary: ("line_percent", "branch_percent"),
    CoverageDelta: ("line_gain", "branch_gain"),
}
"""Read-only derived properties emitted next to the stored fields."""


def to_plain(value: object) -> Any:
    """Recursively turn ``value`` into dicts, lists and JSON scalars.

    Dataclasses become dicts (plus the derived properties in ``_DERIVED``), enums
    their values, paths strings, mappings dicts, sets sorted lists and arcs
    ``"src->dst"`` strings. Non-finite floats become ``"nan"``/``"inf"``/``"-inf"``.
    Anything unknown falls back to ``repr``.
    """
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, PurePath | Arc):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _dataclass_to_plain(value)
    if isinstance(value, Mapping):
        return {_key(k): to_plain(v) for k, v in value.items()}
    return _collection_to_plain(value)


def _collection_to_plain(value: object) -> Any:
    if isinstance(value, frozenset | set):
        return _sorted_items(value)
    if isinstance(value, list | tuple):
        return [to_plain(v) for v in value]
    return repr(value)


def _key(key: object) -> str:
    plain = to_plain(key)
    return plain if isinstance(plain, str) else str(plain)


def _dataclass_to_plain(obj: Any) -> dict[str, Any]:
    result = {f.name: to_plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    for name in _DERIVED.get(type(obj), ()):
        result[name] = to_plain(getattr(obj, name))
    return result


def _sorted_items(items: frozenset[object] | set[object]) -> list[Any]:
    """Deterministic order: arcs numerically, everything else by value or ``repr``."""
    arcs = [i for i in items if isinstance(i, Arc)]
    if arcs and len(arcs) == len(items):
        return [str(a) for a in sorted(arcs, key=_arc_key)]
    plain = [to_plain(i) for i in items]
    try:
        return sorted(plain)
    except TypeError:
        return sorted(plain, key=repr)


def _arc_key(arc: Arc) -> tuple[int, int]:
    return arc.src, arc.dst
