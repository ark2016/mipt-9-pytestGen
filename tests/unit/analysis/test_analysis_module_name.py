from __future__ import annotations

from pathlib import Path

import pytest

from pytestgen.analysis import module_name_for
from pytestgen.errors import AnalysisError


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("calc.py", "calc"),
        ("pkg/calc.py", "pkg.calc"),
        ("pkg/sub/deep.py", "pkg.sub.deep"),
        ("pkg/__init__.py", "pkg"),
        ("pkg/sub/__init__.py", "pkg.sub"),
        ("src/pkg/calc.py", "pkg.calc"),
        ("src/pkg/__init__.py", "pkg"),
        ("src/calc.py", "calc"),
        ("pkg/src/calc.py", "pkg.src.calc"),
        ("srcs/calc.py", "srcs.calc"),
    ],
)
def test_module_name_for_layouts(tmp_path: Path, relative: str, expected: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    assert module_name_for(path, tmp_path) == expected


def test_root_level_init_maps_to_project_directory_name(tmp_path: Path) -> None:
    root = tmp_path / "mypkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    assert module_name_for(root / "__init__.py", root) == "mypkg"


def test_path_outside_project_root_is_an_analysis_error(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "other.py"
    outside.write_text("", encoding="utf-8")
    with pytest.raises(AnalysisError, match="outside the project root"):
        module_name_for(outside, root)


def test_relative_paths_are_resolved_against_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert module_name_for(Path("pkg/m.py"), Path()) == "pkg.m"


def test_project_root_itself_is_an_analysis_error(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError, match="project root"):
        module_name_for(tmp_path, tmp_path)
