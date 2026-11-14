"""Temporary copy of a project tree: the only place where generated code is allowed to run."""

from __future__ import annotations

import fnmatch
import shutil
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Self

from pytestgen.errors import SandboxError
from pytestgen.log import get_logger

DEFAULT_IGNORES: tuple[str, ...] = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytestgen",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
    "*.egg-info",
    "build",
    "dist",
    "out",
    "models",
)
MAX_FILE_BYTES = 5 * 1024 * 1024

_log = get_logger(__name__)


class Workspace:
    """Context manager that copies ``project_root`` into a fresh temporary directory.

    Layout of the temporary base directory::

        <base>/project   the copy of the project (``root``)
        <base>/scratch   driver input/output, junit and coverage data (``scratch``)
        <base>/home      fake home directory for the sandboxed process (``home``)

    Directories matching an ignore pattern are skipped unless they are Python
    packages (contain ``__init__.py``), so a package called ``models`` or ``build``
    still gets copied. Files larger than ``max_file_bytes`` are skipped.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        ignore: tuple[str, ...] = DEFAULT_IGNORES,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        self._source = project_root.resolve()
        self._ignore = ignore
        self._max_file_bytes = max_file_bytes
        self._base: Path | None = None

    def __enter__(self) -> Self:
        if not self._source.is_dir():
            raise SandboxError(f"project root is not a directory: {self._source}")
        base = Path(tempfile.mkdtemp(prefix="pytestgen_")).resolve()
        try:
            self._copy_project(base / "project")
            (base / "scratch").mkdir()
            (base / "home").mkdir()
        except OSError as exc:
            shutil.rmtree(base, ignore_errors=True)
            raise SandboxError(f"cannot create workspace for {self._source}: {exc}") from exc
        self._base = base
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        base, self._base = self._base, None
        if base is not None:
            shutil.rmtree(base, ignore_errors=True)

    # ------------------------------------------------------------------ layout

    @property
    def root(self) -> Path:
        """Copy of the project root."""
        return self._require() / "project"

    @property
    def scratch(self) -> Path:
        """Private directory for sandbox bookkeeping files (outside the project copy)."""
        return self._require() / "scratch"

    @property
    def home(self) -> Path:
        return self._require() / "home"

    @property
    def pythonpath(self) -> list[Path]:
        """Import roots: the workspace root and ``src`` when the project uses that layout."""
        paths = [self.root]
        if (self.root / "src").is_dir():
            paths.append(self.root / "src")
        return paths

    # ------------------------------------------------------------------ operations

    def map_path(self, original: Path) -> Path:
        """Path inside the workspace of a file that lives in the original project."""
        absolute = original if original.is_absolute() else self._source / original
        try:
            relative = absolute.resolve().relative_to(self._source)
        except ValueError:
            raise SandboxError(f"{original} is outside the project root {self._source}") from None
        return self.root / relative

    def write_text(self, relative: str | Path, text: str) -> Path:
        """Write ``text`` (UTF-8, LF newlines) to ``relative`` inside the workspace."""
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise SandboxError(f"{relative} escapes the workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        return target

    # ------------------------------------------------------------------ internals

    def _require(self) -> Path:
        if self._base is None:
            raise SandboxError("workspace is not active: use it as a context manager")
        return self._base

    def _copy_project(self, destination: Path) -> None:
        try:
            shutil.copytree(
                self._source,
                destination,
                ignore=self._ignored,
                ignore_dangling_symlinks=True,
            )
        except shutil.Error as exc:  # some files could not be copied; the rest is usable
            _log.warning(
                "workspace_copy_incomplete", source=str(self._source), errors=len(exc.args[0])
            )

    def _ignored(self, directory: str, names: list[str]) -> set[str]:
        skipped: set[str] = set()
        for name in names:
            path = Path(directory, name)
            if any(fnmatch.fnmatch(name, pattern) for pattern in self._ignore):
                if not (path / "__init__.py").is_file():
                    skipped.add(name)
            elif self._too_large(path):
                skipped.add(name)
        return skipped

    def _too_large(self, path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > self._max_file_bytes
        except OSError:
            return False
