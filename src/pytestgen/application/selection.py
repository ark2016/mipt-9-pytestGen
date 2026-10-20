"""Step 7: decide which generated tests stay (attribute failures, drop redundant tests)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from pytestgen.application.skeleton import describe_inputs
from pytestgen.application.texts import first_line
from pytestgen.codegen import map_test_nodes
from pytestgen.domain.models import (
    CaseOrigin,
    DroppedTest,
    DropReason,
    FileCoverage,
    FreeFormTest,
    OracleVerdict,
    RunResult,
    TestCaseSpec,
    TestOutcome,
    TestSuite,
)
from pytestgen.log import get_logger

_log = get_logger(__name__)

NodeMap = Mapping[str, TestCaseSpec | FreeFormTest]
_NODE_SEPARATOR = "::"


@dataclass(frozen=True, slots=True)
class FailingFreeTest:
    """A free-form test that failed, with the outcome to repair it from."""

    test: FreeFormTest
    outcome: TestOutcome


@dataclass(frozen=True, slots=True)
class Selection:
    """The suite without its failing data-driven cases, and the failing free-form tests."""

    suite: TestSuite
    dropped: tuple[DroppedTest, ...] = ()
    failing_free: tuple[FailingFreeTest, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.dropped and not self.failing_free


@dataclass(frozen=True, slots=True)
class Minimized:
    suite: TestSuite
    dropped: tuple[DroppedTest, ...]
    coverage: FileCoverage | None
    """Coverage of the minimized suite (the last accepted run), ``None`` if nothing changed."""
    runs: int


class SuiteSelector:
    """Attributes pytest outcomes to suite items and drops what does not earn its place."""

    def __init__(self, *, max_minimize_runs: int = 12) -> None:
        self._max_runs = max_minimize_runs

    def select(self, suite: TestSuite, result: RunResult, file_name: str) -> Selection:
        """Split the failures of the generated file into dropped cases and tests to repair.

        A recorded case that fails means the function is flaky or the environment differs:
        it is dropped as ``UNSTABLE``. Free-form tests are returned for repair, once each
        (the first failing outcome of a test class).
        """
        nodes = map_test_nodes(suite)
        bad_cases: dict[int, TestOutcome] = {}
        bad_free: dict[int, FailingFreeTest] = {}
        for outcome in result.failed:
            item = _resolve(nodes, outcome.nodeid, file_name)
            if isinstance(item, TestCaseSpec):
                bad_cases.setdefault(id(item), outcome)
            elif isinstance(item, FreeFormTest):
                bad_free.setdefault(id(item), FailingFreeTest(item, outcome))
            elif _in_generated_file(outcome, file_name):
                _log.warning("failure_not_attributed", nodeid=outcome.nodeid)
            else:
                _log.warning(
                    "existing_test_failing",
                    nodeid=outcome.nodeid,
                    reason=first_line(outcome.message or outcome.traceback),
                )
        dropped = tuple(
            DroppedTest(
                describe_inputs(case.function, case.inputs),
                case.function,
                DropReason.UNSTABLE,
                first_line(bad_cases[id(case)].message or bad_cases[id(case)].traceback),
            )
            for case in suite.cases
            if id(case) in bad_cases
        )
        if dropped:
            _log.warning("failing_cases_dropped", count=len(dropped))
        kept = replace(suite, cases=tuple(c for c in suite.cases if id(c) not in bad_cases))
        return Selection(kept, dropped, tuple(bad_free.values()))

    def minimize(
        self,
        suite: TestSuite,
        reference: FileCoverage | None,
        run: Callable[[TestSuite], RunResult],
        file_name: str,
    ) -> Minimized:
        """Greedily drop free-form tests and LLM-slot cases that add no executed line or arc.

        ``reference`` is the coverage of ``suite``; a candidate goes when the suite
        without it still executes every reference line and arc and still passes (failures of
        the project's own tests do not count: they are not ours to fix). At most
        ``max_minimize_runs`` trial runs are made, so large suites are only partly
        minimized. Solver cases are always kept.
        """
        if reference is None:
            return Minimized(suite, (), None, 0)
        current, coverage, dropped, runs = suite, None, [], 0
        candidates: list[FreeFormTest | TestCaseSpec] = [
            *reversed(suite.free_tests),
            *(c for c in reversed(suite.cases) if _is_removable_case(c)),
        ]
        for candidate in candidates:
            if runs >= self._max_runs:
                _log.info("minimize_budget_exhausted", runs=runs)
                break
            trial = _without(current, candidate)
            result = run(trial)
            runs += 1
            if _preserves(reference, result, file_name):
                current, coverage = trial, result.coverage
                dropped.append(_no_gain(candidate))
        _log.info("minimized", removed=len(dropped), runs=runs)
        return Minimized(current, tuple(dropped), coverage, runs)


def without_free_test(suite: TestSuite, test: FreeFormTest) -> TestSuite:
    return replace(suite, free_tests=tuple(t for t in suite.free_tests if t is not test))


def replace_free_test(suite: TestSuite, old: FreeFormTest, new: FreeFormTest) -> TestSuite:
    return replace(suite, free_tests=tuple(new if t is old else t for t in suite.free_tests))


# --------------------------------------------------------------------------- helpers


def _file_of(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _in_generated_file(outcome: TestOutcome, file_name: str) -> bool:
    return _file_of(outcome.nodeid.partition(_NODE_SEPARATOR)[0]) == file_name


def _resolve(nodes: NodeMap, nodeid: str, file_name: str) -> TestCaseSpec | FreeFormTest | None:
    """The suite item behind a pytest node id of the generated file, if it is one."""
    path, _, name = nodeid.partition(_NODE_SEPARATOR)
    if _file_of(path) != file_name:
        return None
    if name in nodes:
        return nodes[name]
    top = name.split(_NODE_SEPARATOR, 1)[0].split("[", 1)[0]
    return nodes.get(top)


def _is_removable_case(case: TestCaseSpec) -> bool:
    return case.origin is CaseOrigin.LLM_SLOT and case.verdict is not OracleVerdict.SUSPICIOUS


def _without(suite: TestSuite, item: FreeFormTest | TestCaseSpec) -> TestSuite:
    if isinstance(item, FreeFormTest):
        return without_free_test(suite, item)
    return replace(suite, cases=tuple(c for c in suite.cases if c is not item))


def _preserves(reference: FileCoverage, result: RunResult, file_name: str) -> bool:
    cov = result.coverage
    if cov is None or result.collection_error or result.timed_out:
        return False
    if any(_in_generated_file(outcome, file_name) for outcome in result.failed):
        return False
    return reference.executed_arcs <= cov.executed_arcs and (
        reference.executed_lines <= cov.executed_lines
    )


def _no_gain(item: FreeFormTest | TestCaseSpec) -> DroppedTest:
    detail = "removing it keeps every executed line and branch"
    if isinstance(item, FreeFormTest):
        return DroppedTest(item.name, item.function, DropReason.NO_COVERAGE_GAIN, detail)
    name = describe_inputs(item.function, item.inputs)
    return DroppedTest(name, item.function, DropReason.NO_COVERAGE_GAIN, detail)
