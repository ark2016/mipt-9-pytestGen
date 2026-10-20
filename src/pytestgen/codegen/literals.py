"""Python source for values that survive ``ast.literal_eval`` unchanged."""

from __future__ import annotations

import ast
import math
from collections.abc import Iterable


def to_literal_source(value: object) -> str | None:
    """Return source text of ``value`` if ``ast.literal_eval(src) == value``, else ``None``.

    Only exact builtin types qualify (``bool`` is never confused with ``int``,
    subclasses are rejected). ``nan``/``inf``, empty sets and arbitrary objects give ``None``.
    """
    return literal_source(value, double_quotes=False)


def literal_source(value: object, *, double_quotes: bool) -> str | None:
    """Like :func:`to_literal_source`; ``double_quotes`` matches ruff-format string style."""
    try:
        source = _render(value, double_quotes)
        if source is None:
            return None
        return source if _round_trips(source, value) else None
    except (RecursionError, ValueError):  # ValueError: int too long to repr
        return None


def _round_trips(source: str, value: object) -> bool:
    try:
        return bool(ast.literal_eval(source) == value)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return False


def _render(value: object, double_quotes: bool) -> str | None:  # noqa: PLR0911
    if value is None or type(value) is bool or type(value) is int:
        return repr(value)
    if type(value) is float:
        return repr(value) if math.isfinite(value) else None
    if type(value) is complex:
        return repr(value) if math.isfinite(value.real) and math.isfinite(value.imag) else None
    if type(value) is str:
        return _quote(repr(value), "", double_quotes)
    if type(value) is bytes:
        return _quote(repr(value), "b", double_quotes)
    if type(value) is list:
        return _join(value, "[", "]", double_quotes)
    if type(value) is tuple:
        return _join(value, "(", ")", double_quotes, trailing_comma=True)
    if type(value) is dict:
        return _render_dict(value, double_quotes)
    if type(value) is set and value:
        items = _render_items(value, double_quotes)
        return None if items is None else "{" + ", ".join(sorted(items)) + "}"
    return None


def _quote(text: str, prefix: str, double_quotes: bool) -> str:
    """Switch ``repr`` output from single to double quotes when that needs no escaping."""
    body = text[len(prefix) :]
    if double_quotes and body.startswith("'") and '"' not in body:
        return f'{prefix}"{body[1:-1]}"'
    return text


def _render_items(items: Iterable[object], double_quotes: bool) -> list[str] | None:
    rendered: list[str] = []
    for item in items:
        source = _render(item, double_quotes)
        if source is None:
            return None
        rendered.append(source)
    return rendered


def _join(
    items: Iterable[object],
    opener: str,
    closer: str,
    double_quotes: bool,
    *,
    trailing_comma: bool = False,
) -> str | None:
    rendered = _render_items(items, double_quotes)
    if rendered is None:
        return None
    tail = "," if trailing_comma and len(rendered) == 1 else ""
    return opener + ", ".join(rendered) + tail + closer


def _render_dict(mapping: dict[object, object], double_quotes: bool) -> str | None:
    parts: list[str] = []
    for key, val in mapping.items():
        key_src, val_src = _render(key, double_quotes), _render(val, double_quotes)
        if key_src is None or val_src is None:
            return None
        parts.append(f"{key_src}: {val_src}")
    return "{" + ", ".join(parts) + "}"
