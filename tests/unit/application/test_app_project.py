from __future__ import annotations

from pathlib import Path

import pytest

from pytestgen.application import find_project_root


class TestFindProjectRoot:
    @pytest.mark.parametrize("marker", ["pyproject.toml", "setup.cfg", "pytest.ini", ".git"])
    def test_nearest_parent_with_a_marker(self, tmp_path: Path, marker: str) -> None:
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        inner = tmp_path / "inner"
        (inner / "pkg").mkdir(parents=True)
        if marker == ".git":
            (inner / marker).mkdir()
        else:
            (inner / marker).write_text("", encoding="utf-8")
        target = inner / "pkg" / "mod.py"
        target.write_text("", encoding="utf-8")

        assert find_project_root(target) == inner.resolve()

    def test_marker_next_to_the_target_wins(self, tmp_path: Path) -> None:
        (tmp_path / "setup.cfg").write_text("", encoding="utf-8")
        target = tmp_path / "mod.py"
        target.write_text("", encoding="utf-8")
        assert find_project_root(target) == tmp_path.resolve()

    def test_without_markers_it_is_the_targets_directory(self, tmp_path: Path) -> None:
        directory = tmp_path / "loose"
        directory.mkdir()
        target = directory / "mod.py"
        target.write_text("", encoding="utf-8")
        root = find_project_root(target)
        # no marker below tmp_path; a marker far above (CI checkout) may legitimately win
        assert root == directory.resolve() or root in directory.resolve().parents
