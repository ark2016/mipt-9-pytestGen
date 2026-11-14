"""Steps 2-5 for one function: solver inputs, recorded behaviour, LLM slots and free tests."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

from pytestgen.application.collaborators import InputSource, OracleReview, TestWriter
from pytestgen.application.skeleton import KeyedVector, SkeletonBuilder, vector_key
from pytestgen.application.state import Filled
from pytestgen.domain.models import (
    BranchTarget,
    CaseOrigin,
    FunctionInfo,
    ModuleInfo,
    ObservationKind,
    OracleVerdict,
    SuspectedBug,
    TestCaseSpec,
)
from pytestgen.log import get_logger
from pytestgen.ports import Stage

_log = get_logger(__name__)

Announce = Callable[[Stage, str], None]
_UNREACHED = "the solver's input did not reach this branch"


class SlotFiller:
    """Produces the tests of one function: data-driven cases (hybrid) or free-form tests."""

    def __init__(
        self,
        skeleton: SkeletonBuilder,
        reviewer: OracleReview,
        proposer: InputSource,
        writer: TestWriter,
        announce: Announce,
    ) -> None:
        self._skeleton = skeleton
        self._reviewer = reviewer
        self._proposer = proposer
        self._writer = writer
        self._announce = announce

    def hybrid(self, root: Path, module: ModuleInfo, function: FunctionInfo) -> Filled:
        """Solve, record, let the model propose inputs for unsolved branches, then review."""
        self._announce(Stage.SOLVE, f"solving branch conditions of {function.qualname}")
        solved = self._skeleton.solve_targets(function)
        self._announce(Stage.RECORD, f"recording behaviour of {function.qualname}")
        vectors: list[KeyedVector] = [*solved.vectors, *((None, v) for v in solved.boundary)]
        recorded = self._skeleton.record(root, module, function, vectors, CaseOrigin.Z3)
        unsolved = [t for t in function.branches if t.key in solved.unsolved]
        proposed = self._propose(
            root,
            module,
            function,
            unsolved,
            known={vector_key(inputs) for _, inputs in vectors},
            reasons=solved.unsolved,
        )
        raw = Filled(
            cases=recorded.cases,
            dropped=recorded.dropped,
            infeasible=frozenset(solved.infeasible),
        )
        return self._reviewed(module, function, raw + proposed)

    def refine(
        self,
        root: Path,
        module: ModuleInfo,
        function: FunctionInfo,
        targets: Sequence[BranchTarget],
        known: Collection[str],
    ) -> Filled:
        """Ask the model for inputs reaching ``targets`` (a gap-loop step), record and review."""
        reasons = dict.fromkeys((t.key for t in targets), _UNREACHED)
        proposed = self._propose(root, module, function, targets, known=known, reasons=reasons)
        return self._reviewed(module, function, proposed)

    def free_form(
        self,
        module: ModuleInfo,
        function: FunctionInfo,
        *,
        uncovered_lines: Sequence[int] = (),
        avoid_names: Collection[str] = (),
    ) -> Filled:
        """Let the model write whole tests; rejected ones are reported as dropped."""
        self._announce(Stage.LLM_SLOTS, f"asking the model for tests of {function.qualname}")
        written = self._writer.write(
            module, function, uncovered_lines=uncovered_lines, avoid_names=avoid_names
        )
        _log.info(
            "free_tests_written",
            function=function.qualname,
            tests=len(written.tests),
            rejected=len(written.rejected),
        )
        return Filled(free_tests=written.tests, dropped=written.rejected)

    # ------------------------------------------------------------------ internals

    def _propose(
        self,
        root: Path,
        module: ModuleInfo,
        function: FunctionInfo,
        targets: Sequence[BranchTarget],
        *,
        known: Collection[str],
        reasons: Mapping[str, str],
    ) -> Filled:
        if not targets:
            return Filled()
        self._announce(Stage.LLM_SLOTS, f"asking the model for inputs: {function.qualname}")
        attempted = frozenset(t.key for t in targets)
        proposals = self._proposer.propose(module, function, targets, reasons)
        seen = set(known)
        fresh = []
        for inputs in proposals:
            if vector_key(inputs) not in seen:
                seen.add(vector_key(inputs))
                fresh.append((None, inputs))
        recorded = self._skeleton.record(root, module, function, fresh, CaseOrigin.LLM_SLOT)
        return Filled(cases=recorded.cases, dropped=recorded.dropped, attempted=attempted)

    def _reviewed(self, module: ModuleInfo, function: FunctionInfo, filled: Filled) -> Filled:
        """Apply the oracle review to ``filled.cases`` and collect the suspected bugs."""
        cases = filled.cases
        if not cases:
            return filled
        reviewed = self._review(module, function, cases)
        bugs = tuple(_bug(c) for c in reviewed if c.verdict is OracleVerdict.SUSPICIOUS)
        if bugs:
            _log.info("suspected_bugs", function=function.qualname, count=len(bugs))
        return Filled(
            reviewed,
            filled.free_tests,
            filled.dropped,
            filled.bugs + bugs,
            filled.attempted,
            filled.infeasible,
        )

    def _review(
        self, module: ModuleInfo, function: FunctionInfo, cases: Sequence[TestCaseSpec]
    ) -> tuple[TestCaseSpec, ...]:
        self._announce(Stage.LLM_SLOTS, f"reviewing recorded behaviour of {function.qualname}")
        reviewed = tuple(self._reviewer.review(module, function, cases))
        if len(reviewed) != len(cases):  # a verdict must stay attached to its case
            _log.warning("review_size_mismatch", function=function.qualname)
            return tuple(cases)
        return reviewed


def _bug(case: TestCaseSpec) -> SuspectedBug:
    obs = case.observation
    if obs.kind is ObservationKind.RAISED:
        name = obs.exception_type or "an exception"
        observed = (
            f"raises {name}: {obs.exception_message}" if obs.exception_message else f"raises {name}"
        )
    else:
        observed = obs.value_repr or ""
    return SuspectedBug(case.function, dict(case.inputs), observed, case.note)
