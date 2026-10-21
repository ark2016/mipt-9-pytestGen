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
    """Test files under ``project_root`` that import the module.

    Matches ``test_*.py`` and ``*_test.py``; skips hidden and virtual-environment
    directories, the ``output`` file and files pytestgen generated earlier.
    """
    imports = import_pattern(module_name)
    skipped = output.resolve()
    found: list[Path] = []
    for directory, subdirs, files in os.walk(project_root):
        subdirs[:] = sorted(d for d in subdirs if not _is_skipped_dir(d))
        for name in sorted(files):
            path = Path(directory, name).resolve()
            if _is_test_file(name) and path != skipped and _imports(path, imports):
                found.append(path)
    return tuple(found)


def import_pattern(module_name: str) -> re.Pattern[str]:
    """Regex for statements importing ``module_name`` under any dotted suffix of its name.

    ``pkg.calc`` matches ``import pkg.calc``, ``from calc import f`` and
    ``from pkg import (x, calc)``; a mere mention of the word ``calc`` does not.
    """
    parts = module_name.split(".")
    suffixes = [".".join(parts[i:]) for i in range(len(parts))]
    parents = [".".join(parts[i:-1]) for i in range(len(parts) - 1)]
    alternatives = [rf"(?:from|import)\s+{re.escape(name)}\b" for name in suffixes]
    alternatives += [
        rf"from\s+{re.escape(parent)}\s+import\s+(?:\([^)]*|[^\n]*)\b{re.escape(parts[-1])}\b"
        for parent in parents
    ]
    return re.compile(rf"^[ \t]*(?:{'|'.join(alternatives)})", re.MULTILINE)


def _is_skipped_dir(name: str) -> bool:
    return name.startswith(".") or name in _SKIPPED_DIRS


def _is_test_file(name: str) -> bool:
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _imports(path: Path, pattern: re.Pattern[str]) -> bool:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return not _starts_generated(text) and bool(pattern.search(text))


def _starts_generated(text: str) -> bool:
    return text.lstrip(_BLANKS).startswith(GENERATED_HEADER)
