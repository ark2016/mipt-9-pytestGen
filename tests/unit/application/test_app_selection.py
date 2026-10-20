from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from orchestration_support import CALC_SOURCE, TESTS_DIR, analyze, write_project
from structlog.testing import capture_logs

from pytestgen.application.selection import (
    SuiteSelector,
    replace_free_test,
    without_free_test,
)
from pytestgen.codegen import map_test_nodes
from pytestgen.domain.models import (
    Arc,
    CaseOrigin,
    DropReason,
    FileCoverage,
    FreeFormTest,
    ModuleInfo,
    Observation,
    ObservationKind,
    OracleVerdict,
    RunResult,
    TestCaseSpec,
    TestOutcome,
    TestStatus,
    TestSuite,
)

FILE = "test_calc_pytestgen.py"


@pytest.fixture
def module(tmp_path: Path) -> ModuleInfo:
    return analyze(tmp_path, write_project(tmp_path, CALC_SOURCE))


def case(score: int, value: str, origin: CaseOrigin = CaseOrigin.Z3, **kw: object) -> TestCaseSpec:
    obs = Observation({"score": score}, ObservationKind.RETURNED, value_repr=repr(value))
    return TestCaseSpec("grade", {"score": score}, obs, origin, **kw)  # type: ignore[arg-type]


def free(name: str, body: str = "assert 1") -> FreeFormTest:
    return FreeFormTest(name, "grade", f"def {name}():\n    {body}\n")


def outcome(node: str, status: TestStatus = TestStatus.FAILED, message: str = "") -> TestOutcome:
    return TestOutcome(f"{TESTS_DIR}/{FILE}::{node}", status, message=message)


def result(*outcomes: TestOutcome) -> RunResult:
    return RunResult(outcomes=outcomes)


# --------------------------------------------------------------------------- select


def test_passing_run_is_clean(module: ModuleInfo) -> None:
    suite = TestSuite(module, (case(95, "A"),), (free("test_a"),))
    run = result(outcome("test_grade_returns[z3-1]", TestStatus.PASSED))
    selection = SuiteSelector().select(suite, run, FILE)
    assert selection.clean
    assert selection.suite is not None and selection.suite.cases == suite.cases


def test_failing_case_is_dropped_as_unstable_with_the_message(module: ModuleInfo) -> None:
    bad, good = case(95, "A"), case(10, "F")
    suite = TestSuite(module, (bad, good))
    run = result(
        outcome("test_grade_returns[z3-1]", message="assert 'B' == 'A'\n  extra detail"),
        outcome("test_grade_returns[z3-2]", TestStatus.PASSED),
    )
    selection = SuiteSelector().select(suite, run, FILE)
    assert selection.suite.cases == (good,)
    (dropped,) = selection.dropped
    assert dropped.reason is DropReason.UNSTABLE
    assert dropped.detail == "assert 'B' == 'A'"
    assert dropped.function == "grade"
    assert not selection.failing_free


def test_failing_free_test_is_reported_for_repair_not_dropped(module: ModuleInfo) -> None:
    broken, fine = free("test_broken"), free("test_fine")
    suite = TestSuite(module, (), (broken, fine))
    failed = outcome("test_broken", message="boom")
    selection = SuiteSelector().select(
        suite, result(failed, outcome("test_fine", TestStatus.PASSED)), FILE
    )
    assert [(f.test, f.outcome) for f in selection.failing_free] == [(broken, failed)]
    assert selection.dropped == ()
    assert selection.suite.free_tests == (broken, fine), "repair decides, not select"


def test_errors_and_timeouts_count_as_failures(module: ModuleInfo) -> None:
    suite = TestSuite(module, (case(95, "A"),), (free("test_slow"),))
    run = result(
        outcome("test_grade_returns[z3-1]", TestStatus.ERROR, "fixture error"),
        outcome("test_slow", TestStatus.TIMEOUT, "Timeout"),
    )
    selection = SuiteSelector().select(suite, run, FILE)
    assert len(selection.dropped) == 1
    assert [f.test.name for f in selection.failing_free] == ["test_slow"]


def test_renamed_free_test_is_attributed_through_the_node_map(module: ModuleInfo) -> None:
    first, second = free("test_same"), free("test_same", "assert 2")
    suite = TestSuite(module, (), (first, second))
    assert map_test_nodes(suite)["test_same_2"] is second
    selection = SuiteSelector().select(suite, result(outcome("test_same_2", message="x")), FILE)
    assert [f.test for f in selection.failing_free] == [second]


def test_failing_method_of_a_test_class_is_attributed_to_the_class(module: ModuleInfo) -> None:
    cls = FreeFormTest("TestG", "grade", "class TestG:\n    def test_a(self):\n        assert 0\n")
    suite = TestSuite(module, (), (cls,))
    run = result(outcome("TestG::test_a", message="assert 0"), outcome("TestG::test_b"))
    selection = SuiteSelector().select(suite, run, FILE)
    assert len(selection.failing_free) == 1
    assert selection.failing_free[0].outcome.nodeid.endswith("TestG::test_a")


def test_parametrized_free_test_row_is_attributed_to_the_test(module: ModuleInfo) -> None:
    suite = TestSuite(module, (), (free("test_p"),))
    selection = SuiteSelector().select(suite, result(outcome("test_p[3-4]", message="x")), FILE)
    assert [f.test.name for f in selection.failing_free] == ["test_p"]


def test_failures_in_other_files_are_ignored(module: ModuleInfo) -> None:
    suite = TestSuite(module, (case(95, "A"),), (free("test_a"),))
    other = TestOutcome("tests/test_existing.py::test_a", TestStatus.FAILED, message="old")
    unknown = outcome("test_not_in_the_suite", message="?")
    selection = SuiteSelector().select(suite, result(other, unknown), FILE)
    assert selection.clean


def test_windows_style_node_ids_work(module: ModuleInfo) -> None:
    suite = TestSuite(module, (), (free("test_a"),))
    run = result(TestOutcome(f"{TESTS_DIR}\\{FILE}::test_a", TestStatus.FAILED))
    assert len(SuiteSelector().select(suite, run, FILE).failing_free) == 1


def test_free_test_helpers_use_identity(module: ModuleInfo) -> None:
    a, b = free("test_a"), free("test_a")
    suite = TestSuite(module, (), (a, b))
    assert without_free_test(suite, a).free_tests[0] is b
    new = free("test_a", "assert 3")
    assert replace_free_test(suite, b, new).free_tests == (a, new)


# --------------------------------------------------------------------------- minimize


def coverage(lines: set[int], arcs: set[Arc] | None = None) -> FileCoverage:
    return FileCoverage("calc.py", frozenset(lines), frozenset(), frozenset(arcs or ()))


class TrialRunner:
    """Run callback: coverage depends on which named tests are still in the suite."""

    def __init__(self, covers: dict[str, set[int]], base: set[int] | None = None) -> None:
        self.covers = covers
        self.base = base or set()
        self.suites: list[TestSuite] = []

    def __call__(self, suite: TestSuite) -> RunResult:
        self.suites.append(suite)
        lines = set(self.base)
        for test in suite.free_tests:
            lines |= self.covers.get(test.name, set())
        for c in suite.cases:
            lines |= self.covers.get(f"case{c.inputs['score']}", set())
        return RunResult(coverage=coverage(lines))


def test_minimize_drops_free_tests_and_slot_cases_that_add_nothing(module: ModuleInfo) -> None:
    redundant_free, useful_free = free("test_dup"), free("test_new")
    redundant_case = case(60, "B", CaseOrigin.LLM_SLOT)
    useful_case = case(5, "F", CaseOrigin.LLM_SLOT)
    z3_case = case(95, "A")
    suite = TestSuite(module, (z3_case, redundant_case, useful_case), (redundant_free, useful_free))
    covers = {"test_dup": {1}, "test_new": {2}, "case60": {1}, "case5": {3}, "case95": {1}}
    run = TrialRunner(covers)
    reference = run(suite).coverage
    run.suites.clear()
    minimized = SuiteSelector().minimize(suite, reference, run, FILE)
    assert minimized.suite.free_tests == (useful_free,)
    assert minimized.suite.cases == (z3_case, useful_case), "solver cases are never candidates"
    assert {d.name for d in minimized.dropped} == {"test_dup", "grade(score=60)"}
    assert {d.reason for d in minimized.dropped} == {DropReason.NO_COVERAGE_GAIN}
    assert minimized.runs == 4 == len(run.suites)
    assert minimized.coverage == reference


def test_minimize_keeps_everything_that_contributes(module: ModuleInfo) -> None:
    a, b = free("test_a"), free("test_b")
    suite = TestSuite(module, (), (a, b))
    run = TrialRunner({"test_a": {1}, "test_b": {2}})
    minimized = SuiteSelector().minimize(suite, run(suite).coverage, run, FILE)
    assert minimized.suite == suite and minimized.dropped == ()
    assert minimized.coverage is None


def test_minimize_stops_at_the_run_budget(module: ModuleInfo) -> None:
    tests = tuple(free(f"test_{i}") for i in range(6))
    suite = TestSuite(module, (), tests)
    run = TrialRunner({}, base={1})
    minimized = SuiteSelector(max_minimize_runs=2).minimize(suite, run(suite).coverage, run, FILE)
    assert minimized.runs == 2
    assert len(minimized.suite.free_tests) == 4


def test_minimize_without_reference_coverage_changes_nothing(module: ModuleInfo) -> None:
    suite = TestSuite(module, (), (free("test_a"),))
    called: list[TestSuite] = []

    def run(s: TestSuite) -> RunResult:
        called.append(s)
        return RunResult()

    minimized = SuiteSelector().minimize(suite, None, run, FILE)
    assert minimized.suite is suite and called == []


@pytest.mark.parametrize(
    "bad_run",
    [
        lambda cov: RunResult(coverage=cov, timed_out=True),
        lambda cov: RunResult(coverage=cov, collection_error="boom"),
        lambda cov: RunResult(
            coverage=cov, outcomes=(TestOutcome(f"tests/{FILE}::test", TestStatus.FAILED),)
        ),
        lambda cov: RunResult(coverage=None),
    ],
    ids=["timeout", "collection", "failure", "no-coverage"],
)
def test_minimize_never_accepts_a_broken_trial_run(
    module: ModuleInfo, bad_run: Callable[[FileCoverage], RunResult]
) -> None:
    suite = TestSuite(module, (), (free("test_a"),))
    reference = coverage({1})
    minimized = SuiteSelector().minimize(suite, reference, lambda s: bad_run(reference), FILE)
    assert minimized.suite == suite and minimized.dropped == ()


def test_minimize_requires_arcs_too(module: ModuleInfo) -> None:
    test = free("test_a")
    suite = TestSuite(module, (), (test,))
    reference = coverage({1}, {Arc(1, 2)})
    minimized = SuiteSelector().minimize(
        suite, reference, lambda s: RunResult(coverage=coverage({1})), FILE
    )
    assert minimized.suite.free_tests == (test,)


def test_suspicious_slot_cases_are_not_minimization_candidates(module: ModuleInfo) -> None:
    flagged = case(7, "A", CaseOrigin.LLM_SLOT, verdict=OracleVerdict.SUSPICIOUS)
    suite = TestSuite(module, (flagged,))
    run = TrialRunner({})
    assert SuiteSelector().minimize(suite, coverage({1}), run, FILE).runs == 0


# --------------------------------------------------------------------------- existing tests fail


def existing_failure() -> TestOutcome:
    return TestOutcome("tests/test_calc_basic.py::test_old", TestStatus.FAILED, message="assert 1")


def test_minimize_ignores_a_failing_project_test(module: ModuleInfo) -> None:
    """A red pre-existing test must not switch minimization off (it was red before us)."""
    redundant, extra = free("test_dup"), free("test_new")
    suite = TestSuite(module, (), (redundant, extra))
    runner = TrialRunner({"test_dup": {1}, "test_new": {1}})
    reference = runner(suite).coverage

    def run(trial: TestSuite) -> RunResult:
        done = runner(trial)
        return RunResult(outcomes=(existing_failure(),), coverage=done.coverage)

    assert reference is not None
    minimized = SuiteSelector().minimize(suite, reference, run, FILE)
    assert minimized.suite.free_tests == (redundant,)
    assert [d.name for d in minimized.dropped] == ["test_new"]


def test_minimize_still_rejects_a_trial_that_breaks_a_generated_test(module: ModuleInfo) -> None:
    test = free("test_a")
    suite = TestSuite(module, (), (test,))
    reference = coverage({1})
    broken = RunResult(outcomes=(existing_failure(), outcome("test_a")), coverage=reference)
    minimized = SuiteSelector().minimize(suite, reference, lambda s: broken, FILE)
    assert minimized.suite == suite and minimized.dropped == ()


def test_failing_project_test_is_reported_but_does_not_make_the_selection_unclean(
    module: ModuleInfo,
) -> None:
    suite = TestSuite(module, (), (free("test_a"),))
    with capture_logs() as logs:
        selection = SuiteSelector().select(suite, result(existing_failure()), FILE)
    assert selection.clean
    warnings = [e for e in logs if e["event"] == "existing_test_failing"]
    assert [e["nodeid"] for e in warnings] == ["tests/test_calc_basic.py::test_old"]
