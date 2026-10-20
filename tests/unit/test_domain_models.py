from __future__ import annotations

from pathlib import Path

import pytest

from pytestgen.domain.models import (
    Arc,
    BranchKind,
    BranchTarget,
    Clause,
    CoverageDelta,
    CoverageSummary,
    FileCoverage,
    FunctionInfo,
    LLMUsage,
    ModuleInfo,
    Parameter,
    PathCondition,
    RunResult,
    TestOutcome,
    TestStatus,
)


def _fn(
    qualname: str, lineno: int, end: int, *, cls: str | None = None, **kw: object
) -> FunctionInfo:
    return FunctionInfo(
        qualname=qualname,
        name=qualname.rsplit(".", 1)[-1],
        lineno=lineno,
        end_lineno=end,
        source="",
        class_name=cls,
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("clauses", "expected"),
    [
        ((), "True"),
        ((Clause("x > 0", True),), "(x > 0)"),
        ((Clause("x > 0", False), Clause("y == 'a'", True)), "(not (x > 0)) and (y == 'a')"),
    ],
)
def test_path_condition_str(clauses: tuple[Clause, ...], expected: str) -> None:
    assert str(PathCondition(clauses)) == expected


def test_branch_target_key_encodes_outcome() -> None:
    target = BranchTarget(
        function="f",
        line=3,
        kind=BranchKind.IF,
        condition_source="x",
        outcome=False,
        arc=Arc(3, 5),
        path=PathCondition(),
    )
    assert target.key == "f:3:F"
    assert str(target.arc) == "3->5"


def test_call_parameters_drop_self_for_methods_only() -> None:
    params = (Parameter("self"), Parameter("x"))
    method = _fn("A.m", 1, 2, cls="A", parameters=params)
    static = _fn("A.s", 1, 2, cls="A", parameters=(Parameter("x"),), is_static=True)
    free = _fn("f", 1, 2, parameters=(Parameter("x"),))
    assert [p.name for p in method.call_parameters] == ["x"]
    assert [p.name for p in static.call_parameters] == ["x"]
    assert [p.name for p in free.call_parameters] == ["x"]
    assert method.is_method and not free.is_method


def test_module_function_lookup_and_innermost_at_line() -> None:
    outer = _fn("outer", 1, 10)
    inner = _fn("outer.inner", 3, 5)
    module = ModuleInfo(path=Path("m.py"), module_name="m", source="", functions=(outer, inner))
    assert module.function("outer") is outer
    assert module.function_at_line(4) is inner
    assert module.function_at_line(8) is outer
    assert module.function_at_line(42) is None
    with pytest.raises(KeyError):
        module.function("missing")


def test_file_coverage_rates() -> None:
    cov = FileCoverage(
        path="m.py",
        executed_lines=frozenset({1, 2, 3}),
        missing_lines=frozenset({4}),
        executed_arcs=frozenset({Arc(1, 2)}),
        missing_arcs=frozenset({Arc(1, 3)}),
    )
    assert cov.line_rate == pytest.approx(0.75)
    assert cov.branch_rate == pytest.approx(0.5)
    assert FileCoverage(path="empty.py").line_rate == 1.0


def test_coverage_summary_and_delta() -> None:
    before = CoverageSummary.from_file(None)
    after = CoverageSummary(covered_lines=8, total_lines=10, covered_branches=3, total_branches=4)
    delta = CoverageDelta(before=CoverageSummary(4, 10, 1, 4), after=after)
    assert before.line_percent == 100.0  # nothing measured counts as fully covered
    assert delta.line_gain == pytest.approx(40.0)
    assert delta.branch_gain == pytest.approx(50.0)


def test_run_result_partitions_outcomes() -> None:
    result = RunResult(
        outcomes=(
            TestOutcome("t.py::test_a", TestStatus.PASSED),
            TestOutcome("t.py::test_b[1-2]", TestStatus.FAILED),
            TestOutcome("t.py::test_c", TestStatus.TIMEOUT),
            TestOutcome("t.py::test_d", TestStatus.SKIPPED),
        )
    )
    assert [o.test_name for o in result.passed] == ["test_a"]
    assert [o.test_name for o in result.failed] == ["test_b", "test_c"]


def test_llm_usage_adds_up() -> None:
    total = LLMUsage(1, 0, 10, 5) + LLMUsage(2, 1, 20, 7)
    assert total == LLMUsage(requests=3, cache_hits=1, prompt_tokens=30, completion_tokens=12)
