"""Project layout conventions."""

from __future__ import annotations

from pathlib import Path

_PROJECT_MARKERS = ("pyproject.toml", "setup.cfg", "pytest.ini", ".git")


def find_project_root(target: Path) -> Path:
    """Nearest parent of ``target`` holding a project marker, else the target's directory."""
    start = target.resolve().parent
    for candidate in (start, *start.parents):
        if any((candidate / marker).exists() for marker in _PROJECT_MARKERS):
            return candidate
    return start
