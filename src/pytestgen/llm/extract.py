"""Pull code and JSON out of free-form model output."""

from __future__ import annotations

import ast
import json
import re
import textwrap
from dataclasses import dataclass
from typing import Any

from pytestgen.errors import ExtractionError

_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)")
_PYTHON_LABELS = frozenset({"python", "py", "python3", "py3"})


@dataclass(frozen=True, slots=True)
class _Block:
    label: str
    body: str


def _fenced_blocks(text: str) -> list[_Block]:
    """Markdown fenced blocks; an unterminated last fence runs to the end of the text."""
    blocks: list[_Block] = []
    fence: str | None = None
    label = ""
    body: list[str] = []
    for line in text.splitlines():
        if fence is None:
            opened = _FENCE_OPEN.match(line)
            if opened:
                fence, label, body = opened.group(1), opened.group(2).lower(), []
            continue
        stripped = line.strip()
        if len(stripped) >= len(fence) and set(stripped) == {fence[0]}:
            blocks.append(_Block(label, textwrap.dedent("\n".join(body))))
            fence = None
        else:
            body.append(line)
    if fence is not None:
        blocks.append(_Block(label, textwrap.dedent("\n".join(body))))
    return [b for b in blocks if b.body.strip()]


def extract_code(text: str) -> str:
    """Return the Python source from ``text``, validated with ``ast.parse``.

    Prefers the longest block labelled python/py, then the longest block of any
    label, then the whole text.
    """
    blocks = _fenced_blocks(text)
    python = [b for b in blocks if b.label in _PYTHON_LABELS]
    pool = python or blocks
    code = (max(pool, key=lambda b: len(b.body.strip())).body if pool else text).strip()
    if not code:
        msg = "model output contains no code"
        raise ExtractionError(msg)
    try:
        ast.parse(code)
    except SyntaxError as exc:
        msg = f"extracted code is not valid Python: {exc.msg} (line {exc.lineno})"
        raise ExtractionError(msg) from exc
    return code


def extract_json(text: str) -> dict[str, Any]:
    """Return the first JSON object found in ``text``.

    Handles fenced blocks, prose around the object, braces inside strings and
    trailing commas before ``}`` / ``]``.
    """
    blocks = _fenced_blocks(text)
    ordered = [b for b in blocks if b.label == "json"] + [b for b in blocks if b.label != "json"]
    for source in [b.body for b in ordered] + [text]:
        found = _first_object(source)
        if found is not None:
            return found
    msg = "model output contains no JSON object"
    raise ExtractionError(msg)


def _first_object(source: str) -> dict[str, Any] | None:
    stripped = source.strip()
    whole = _loads_object(stripped)
    if whole is not None:
        return whole
    pos = stripped.find("{")
    while pos != -1:
        end = _balanced_end(stripped, pos)
        parsed = None if end is None else _loads_object(stripped[pos : end + 1])
        if parsed is not None:
            return parsed
        pos = stripped.find("{", pos + 1 if end is None else end + 1)
    return None


def _loads_object(raw: str) -> dict[str, Any] | None:
    for candidate in (raw, _drop_trailing_commas(raw)):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _scan_strings(text: str) -> list[tuple[int, str, bool]]:
    """(index, char, inside_string) for every char; strings honour backslash escapes."""
    result: list[tuple[int, str, bool]] = []
    in_string = escaped = False
    for i, ch in enumerate(text):
        result.append((i, ch, in_string))
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
    return result


def _balanced_end(text: str, start: int) -> int | None:
    """Index of the ``}`` closing the object that opens at ``start``, or None if cut off."""
    depth = 0
    for i, ch, in_string in _scan_strings(text[start:]):
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start + i
    return None


def _drop_trailing_commas(text: str) -> str:
    """Remove commas that directly precede ``}`` or ``]`` (outside strings)."""
    scanned = _scan_strings(text)
    drop: set[int] = set()
    for i, ch, in_string in scanned:
        if in_string or ch != ",":
            continue
        if text[i + 1 :].lstrip()[:1] in {"}", "]"}:
            drop.add(i)
    return "".join(ch for i, ch, _ in scanned if i not in drop)
