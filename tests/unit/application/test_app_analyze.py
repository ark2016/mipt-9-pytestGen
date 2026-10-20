from __future__ import annotations

from pathlib import Path

import pytest
from orchestration_support import (
    CALC_SOURCE,
    FakeAnalyzer,
    ScriptedSolver,
    analyze,
    sat,
    write_project,
)

from pytestgen.application import AnalyzeModule, AnalyzeRequest, find_project_root, select_functions
from pytestgen.domain.models import ModuleInfo, SolveStatus
from pytestgen.errors import PytestgenError


@pytest.fixture
def module(tmp_path: Path) -> ModuleInfo:
    return analyze(tmp_path, write_project(tmp_path, CALC_SOURCE))


def request(module: ModuleInfo, **kw: object) -> AnalyzeRequest:
    return AnalyzeRequest(module.path, module.path.parent, **kw)  # type: ignore[arg-type]


def test_every_branch_gets_the_solvers_verdict(module: ModuleInfo) -> None:
    grade = module.function("grade")
    answers = {t.key: sat(t, score=i) for i, t in enumerate(grade.branches)}
    solver = ScriptedSolver(answers)
    result = AnalyzeModule(FakeAnalyzer(module), solver).execute(request(module))
    assert result.module is module and result.solved
    by_name = {item.function.qualname: item for item in result.functions}
    assert list(by_name) == ["grade", "ident", "Counter.bump", "Counter.twice"]
    verdicts = [b.solution.inputs for b in by_name["grade"].branches if b.solution]
    assert verdicts == [{"score": i} for i in range(len(grade.branches))]
    assert by_name["ident"].branches == ()


def test_no_solve_never_calls_the_solver(module: ModuleInfo) -> None:
    solver = ScriptedSolver(default={"score": 1})
    result = AnalyzeModule(FakeAnalyzer(module), solver).execute(request(module, solve=False))
    assert solver.targets == [] and not result.solved
    assert all(b.solution is None for item in result.functions for b in item.branches)


def test_function_filter_is_applied_before_solving(module: ModuleInfo) -> None:
    solver = ScriptedSolver()
    result = AnalyzeModule(FakeAnalyzer(module), solver).execute(
        request(module, functions=("ident",))
    )
    assert [item.function.qualname for item in result.functions] == ["ident"]
    assert solver.targets == []


def test_unknown_function_is_a_user_error(module: ModuleInfo) -> None:
    with pytest.raises(PytestgenError, match="unknown function"):
        AnalyzeModule(FakeAnalyzer(module), ScriptedSolver()).execute(
            request(module, functions=("nope",))
        )


def test_solver_statuses_are_passed_through(module: ModuleInfo) -> None:
    grade = module.function("grade")
    solver = ScriptedSolver(default=None)
    result = AnalyzeModule(FakeAnalyzer(module), solver).execute(
        request(module, functions=("grade",))
    )
    statuses = {b.solution.status for b in result.functions[0].branches if b.solution}
    assert statuses == {SolveStatus.UNKNOWN}
    assert len(result.functions[0].branches) == len(grade.branches)


# --------------------------------------------------------------------------- select_functions


@pytest.mark.parametrize("name", ["Counter.doubled", "Counter.__init__"])
def test_properties_and_dunders_get_a_precise_error(module: ModuleInfo, name: str) -> None:
    """F7: the old message claimed the module had no testable functions at all."""
    with pytest.raises(PytestgenError) as info:
        select_functions(module, (name,))
    message = str(info.value)
    assert name in message and "cannot be targeted" in message
    assert "available: grade, ident, Counter.bump, Counter.twice" in message
    assert "no testable functions" not in message


def test_one_untargetable_name_among_good_ones_is_still_an_error(module: ModuleInfo) -> None:
    with pytest.raises(PytestgenError, match=r"Counter\.doubled cannot be targeted"):
        select_functions(module, ("grade", "Counter.doubled"))


def test_a_class_name_does_not_trip_the_untargetable_check(module: ModuleInfo) -> None:
    chosen = select_functions(module, ("Counter",))
    assert [f.qualname for f in chosen] == ["Counter.bump", "Counter.twice"]


# --------------------------------------------------------------------------- project root


def test_project_root_is_the_nearest_marker_directory(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    (tmp_path / "pkg" / "pytest.ini").write_text("", encoding="utf-8")
    target = nested / "mod.py"
    target.write_text("", encoding="utf-8")
    assert find_project_root(target) == (tmp_path / "pkg").resolve()


def test_project_root_without_markers_contains_the_module(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("", encoding="utf-8")
    root = find_project_root(target)
    assert target.resolve().parent.is_relative_to(root)
