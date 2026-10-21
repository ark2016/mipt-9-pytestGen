"""The local file system as the store of test files."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from pytestgen.testfiles.discovery import discover_existing_tests, is_generated


class LocalTestFiles:
    """Implements ``application.collaborators.TestFileStore`` on disk."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_generated(self, path: Path) -> bool:
        return is_generated(path)

    def discover(
        self, project_root: Path, module_name: str, exclude: Path, names: Collection[str] = ()
    ) -> tuple[Path, ...]:
        return discover_existing_tests(project_root, module_name, exclude, names)

    def write(self, path: Path, text: str) -> Path:
        """Write ``text`` (UTF-8, ``\n`` line ends), creating missing parent directories."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path
