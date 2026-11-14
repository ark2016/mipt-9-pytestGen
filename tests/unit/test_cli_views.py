from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from pytestgen.analysis import AstModuleAnalyzer
from pytestgen.application import AnalyzeModule, AnalyzeRequest, ModuleAnalysis
from pytestgen.cli_views import ConsoleProgress, analysis_to_json, render_analysis
from pytestgen.domain.models import BranchTarget, FunctionInfo, Solution, SolveStatus
from pytestgen.ports import Stage

SOURCE = """\
def sign(x: int, scale: float = 1.0) -> str:
    if x < 0:
        return "-"
    return "+"


def plain(a):
    return a
"""


def wide(out: io.StringIO) -> Console:
    return Console(file=out, width=200, color_system=None)


class StubSolver:
    """Answers SAT for the true outcome and UNSUPPORTED for the false one."""

    def solve(self, function: FunctionInfo, target: BranchTarget) -> Solution:
        if target.outcome:
            return Solution(
                target, SolveStatus.SAT, {"x": -1, "scale": float("nan"), "ids": (1, 2), "o": {1}}
            )
        return Solution(target, SolveStatus.UNSUPPORTED, reason="no [markup] here")


@pytest.fixture
def module_path(tmp_path: Path) -> Path:
    path = tmp_path / "sign.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def analyse(module_path: Path, *, solve: bool = True) -> ModuleAnalysis:
    use_case = AnalyzeModule(AstModuleAnalyzer(), StubSolver())
    return use_case.execute(AnalyzeRequest(module_path, module_path.parent, solve=solve))


@pytest.fixture
def analysis(module_path: Path) -> ModuleAnalysis:
    return analyse(module_path)


def test_progress_prints_one_line_per_stage_and_escapes_markup() -> None:
    out = io.StringIO()
    progress = ConsoleProgress(Console(file=out, width=120, color_system=None))

    progress.stage(Stage.GAP, "iteration [1]")
    progress.stage(Stage.WRITE, "done")

    lines = out.getvalue().splitlines()
    assert lines[0].startswith("gap") and lines[0].endswith("iteration [1]")
    assert lines[1].startswith("write") and lines[1].endswith("done")


class TestAnalysisViews:
    def test_json_is_valid_and_json_safe(self, analysis: ModuleAnalysis) -> None:
        data = json.loads(analysis_to_json(analysis))

        assert data["module"] == "sign"
        sign = data["functions"][0]
        assert sign["qualname"] == "sign"
        assert sign["returns"] == "str"
        assert sign["parameters"][1] == {"name": "scale", "annotation": "float", "default": "1.0"}
        true_branch, false_branch = sign["branches"]
        assert true_branch["solution"]["status"] == "sat"
        inputs = true_branch["solution"]["inputs"]
        assert inputs["x"] == -1
        assert inputs["scale"] == "nan"  # non-finite floats are not valid JSON
        assert inputs["ids"] == [1, 2]
        assert false_branch["solution"] == {
            "status": "unsupported",
            "inputs": {},
            "reason": "no [markup] here",
        }
        assert data["functions"][1]["branches"] == []

    def test_tables_mark_functions_without_branches_and_escape_markup(
        self, analysis: ModuleAnalysis
    ) -> None:
        out = io.StringIO()

        render_analysis(wide(out), analysis)

        text = out.getvalue()
        assert "sign(x: int, scale: float = 1.0) -> str" in text
        assert "sign:2:T" in text
        assert "x=-1" in text
        assert "no [markup] here" in text
        assert "plain(a)" in text
        assert "no branches" in text

    def test_tables_without_solving_have_no_z3_column(self, module_path: Path) -> None:
        out = io.StringIO()

        render_analysis(wide(out), analyse(module_path, solve=False))

        assert "Z3" not in out.getvalue()
