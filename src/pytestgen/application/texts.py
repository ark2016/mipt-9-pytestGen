"""Tiny text helpers shared by the use cases."""

from __future__ import annotations

_ELLIPSIS = "..."


def first_line(text: str, width: int = 200) -> str:
    """First non-empty line of ``text``, shortened to ``width`` characters."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return _shorten(stripped, width)
    return ""


def last_line(text: str, width: int = 200) -> str:
    """Last non-empty line of ``text`` (where tracebacks put the actual error)."""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return _shorten(stripped, width)
    return ""


def _shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - len(_ELLIPSIS)] + _ELLIPSIS
