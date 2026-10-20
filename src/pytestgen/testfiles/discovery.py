"""Locating the project's existing tests and recognising files pytestgen generated."""

from __future__ import annotations

import os
import re
import string
from pathlib import Path

from pytestgen.codegen import GENERATED_HEADER

_SKIPPED_DIRS = frozenset({"__pycache__", "node_modules", "venv", "env", "site-packages"})
_MAX_FILE_BYTES = 1_000_000
_BLANKS = string.whitespace + chr(0xFEFF)  # a BOM may precede the header


def is_generated(path: Path) -> bool:
    """Whether ``path`` starts with the pytestgen header docstring."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            head = handle.read(len(GENERATED_HEADER) + 64)
    except OSError:
        return False
    return _starts_generated(head)


def discover_existing_tests(project_root: Path, module_name: str, output: Path) -> tuple[Path, ...]:
    """Test files under ``project_root`` that mention the module's last name component.

    Matches ``test_*.py`` and ``*_test.py``; skips hidden and virtual-environment
    directories, the ``output`` file and files pytestgen generated earlier.
    """
    word = re.compile(rf"\b{re.escape(module_name.rsplit('.', 1)[-1])}\b")
    skipped = output.resolve()
    found: list[Path] = []
    for directory, subdirs, files in os.walk(project_root):
        subdirs[:] = sorted(d for d in subdirs if not _is_skipped_dir(d))
        for name in sorted(files):
            path = Path(directory, name).resolve()
            if _is_test_file(name) and path != skipped and _mentions(path, word):
                found.append(path)
    return tuple(found)


def _is_skipped_dir(name: str) -> bool:
    return name.startswith(".") or name in _SKIPPED_DIRS


def _is_test_file(name: str) -> bool:
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _mentions(path: Path, word: re.Pattern[str]) -> bool:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return not _starts_generated(text) and bool(word.search(text))


def _starts_generated(text: str) -> bool:
    return text.lstrip(_BLANKS).startswith(GENERATED_HEADER)
