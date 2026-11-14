"""Mapping of a source file to its dotted import name."""

from __future__ import annotations

from pathlib import Path

from pytestgen.errors import AnalysisError

_SRC_DIR = "src"


def module_name_for(path: Path, project_root: Path) -> str:
    """Dotted import name of ``path`` relative to ``project_root``.

    A leading ``src`` directory (src layout) is dropped and ``__init__.py`` maps to its
    package. Raises :class:`AnalysisError` when ``path`` lies outside ``project_root``.
    """
    root = project_root.resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError as exc:
        raise AnalysisError(f"{path} is outside the project root {root}") from exc
    if not relative.parts:
        raise AnalysisError(f"{path} is the project root, not a module inside {root}")
    parts = list(relative.with_suffix("").parts)
    if len(parts) > 1 and parts[0] == _SRC_DIR:
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else root.name
