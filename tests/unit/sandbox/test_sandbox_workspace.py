from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pytestgen.errors import SandboxError
from pytestgen.sandbox import DEFAULT_IGNORES, Workspace


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "src_project"
    for name in (".git", ".venv", "__pycache__", "build", "pkg.egg-info", "keep"):
        (root / name).mkdir(parents=True)
        (root / name / "f.txt").write_text("x", encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "__init__.py").write_text("", encoding="utf-8")
    (root / "calc.py").write_text("x = 1\n", encoding="utf-8")
    return root


def test_copy_skips_ignored_directories_but_keeps_packages(tree: Path) -> None:
    with Workspace(tree) as ws:
        copied = {p.name for p in ws.root.iterdir()}
    assert copied == {"keep", "models", "calc.py"}


def test_custom_ignore_patterns(tree: Path) -> None:
    with Workspace(tree, ignore=("keep",)) as ws:
        copied = {p.name for p in ws.root.iterdir()}
    assert "keep" not in copied
    assert ".git" in copied
    assert ".git" in DEFAULT_IGNORES


def test_large_files_are_skipped(tree: Path) -> None:
    (tree / "big.bin").write_bytes(b"0" * 100)
    with Workspace(tree, max_file_bytes=50) as ws:
        assert not (ws.root / "big.bin").exists()
        assert (ws.root / "calc.py").exists()


def test_workspace_is_removed_on_exit(tree: Path) -> None:
    with Workspace(tree) as ws:
        base = ws.root.parent
        assert base.is_dir()
        assert ws.scratch.is_dir()
        assert ws.home.is_dir()
    assert not base.exists()


def test_map_path_absolute_and_relative(tree: Path) -> None:
    with Workspace(tree) as ws:
        assert ws.map_path(tree / "calc.py") == ws.root / "calc.py"
        assert ws.map_path(Path("calc.py")) == ws.root / "calc.py"


def test_map_path_outside_project_is_rejected(tree: Path, tmp_path: Path) -> None:
    with Workspace(tree) as ws, pytest.raises(SandboxError, match="outside the project root"):
        ws.map_path(tmp_path / "elsewhere.py")


def test_write_text_creates_parents_and_uses_lf(tree: Path) -> None:
    with Workspace(tree) as ws:
        target = ws.write_text("a/b/test_x.py", "one\ntwo\n")
        assert target == (ws.root / "a" / "b" / "test_x.py").resolve()
        assert target.read_bytes() == b"one\ntwo\n"
    assert not (tree / "a").exists()


def test_write_text_cannot_escape_workspace(tree: Path) -> None:
    with Workspace(tree) as ws, pytest.raises(SandboxError, match="escapes"):
        ws.write_text("../evil.py", "x")


def test_pythonpath_includes_src_layout(tree: Path) -> None:
    with Workspace(tree) as ws:
        assert ws.pythonpath == [ws.root]
    (tree / "src").mkdir()
    with Workspace(tree) as ws:
        assert ws.pythonpath == [ws.root, ws.root / "src"]


def test_properties_require_active_context(tree: Path) -> None:
    with pytest.raises(SandboxError, match="not active"):
        _ = Workspace(tree).root


def test_missing_project_root(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="not a directory"), Workspace(tmp_path / "nope"):
        pass


def test_partial_copy_failure_is_tolerated(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def partial(*_a: object, **_k: object) -> None:
        raise shutil.Error([("a", "b", "locked")])

    monkeypatch.setattr(shutil, "copytree", partial)
    # copytree failed halfway: the workspace is still created, just without the project files.
    with Workspace(tree) as ws:
        assert ws.scratch.is_dir()


def test_os_failure_during_copy_becomes_sandbox_error(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: object, **_k: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(shutil, "copytree", broken)
    with pytest.raises(SandboxError, match="cannot create workspace"), Workspace(tree):
        pass
