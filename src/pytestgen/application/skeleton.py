"""Steps 2-3 of the pipeline: solve branch conditions into inputs, record real behaviour."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pytestgen.application.boundary import boundary_vectors
from pytestgen.domain.models import (
    BranchKind,
    BranchTarget,
    CaseOrigin,
    DroppedTest,
    DropReason,
    FunctionInfo,
    ModuleInfo,
    Observation,
    ObservationKind,
    PathCondition,
    Solution,
    SolveStatus,
    TestCaseSpec,
)
from pytestgen.errors import SandboxError
from pytestgen.log import get_logger
from pytestgen.ports import ExecutionRecorder, HintSolver

_log = get_logger(__name__)

Vector = Mapping[str, object]
KeyedVector = tuple[str | None, Vector]
_LABEL_WIDTH = 60


@dataclass(frozen=True, slots=True)
class SolveOutcome:
    """What the solver made of a function's branch targets."""

    vectors: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    """``(target_key, inputs)`` per distinct input vector, in target order."""
    unsolved: dict[str, str] = field(default_factory=dict)
    """Target key -> reason, for targets the solver could not decide (UNSUPPORTED/UNKNOWN)."""
    infeasible: tuple[str, ...] = ()
    """Target keys whose path condition is unsatisfiable."""
    boundary: list[dict[str, object]] = field(default_factory=list)
    """Extra vectors at comparison constants (``x > 100`` -> ``x = 100``), not tied to a target."""


@dataclass(frozen=True, slots=True)
class RecordOutcome:
    """Recorded behaviour split into usable cases and dropped vectors."""

    cases: tuple[TestCaseSpec, ...] = ()
    dropped: tuple[DroppedTest, ...] = ()


def vector_key(inputs: Vector) -> str:
    """Canonical text of an input vector; equal keys mean identical calls."""
    return repr(sorted(inputs.items(), key=lambda item: item[0]))


def describe_inputs(function: str, inputs: Vector, width: int = _LABEL_WIDTH) -> str:
    """Short human label for a case, e.g. ``discount(price=1, years=2)``."""
    args = ", ".join(f"{name}={value!r}" for name, value in inputs.items())
    label = f"{function}({args})"
    return label if len(label) <= width else label[: width - 3] + "..."


class SkeletonBuilder:
    """Turns path conditions into inputs (solver) and inputs into expectations (recorder)."""

    def __init__(
        self,
        solver: HintSolver,
        recorder: ExecutionRecorder,
        *,
        max_cases_per_function: int = 12,
        max_boundary_vectors: int = 6,
    ) -> None:
        self._solver = solver
        self._recorder = recorder
        self._max_cases = max_cases_per_function
        self._max_boundary = max_boundary_vectors

    def solve_targets(self, function: FunctionInfo) -> SolveOutcome:
        """Solve every branch target of ``function``.

        A function without branches gets one synthetic entry target, so it still yields
        a single input vector. Identical vectors are kept once, under the first target.
        Numeric comparison constants add boundary vectors (see :mod:`.boundary`) as far as
        ``max_cases_per_function`` leaves room.
        """
        vectors: list[tuple[str, dict[str, object]]] = []
        unsolved: dict[str, str] = {}
        infeasible: list[str] = []
        seen: set[str] = set()
        for target in function.branches or (_entry_target(function),):
            solution = self._solve(function, target)
            if solution.status is SolveStatus.SAT:
                inputs = dict(solution.inputs)
                key = vector_key(inputs)
                if key not in seen:
                    seen.add(key)
                    vectors.append((target.key, inputs))
            elif solution.status is SolveStatus.UNSAT:
                infeasible.append(target.key)
            else:
                unsolved[target.key] = solution.reason or solution.status.value
        boundary = self._boundary(function, vectors)
        _log.info(
            "targets_solved",
            function=function.qualname,
            vectors=len(vectors),
            boundary=len(boundary),
            unsolved=len(unsolved),
            infeasible=len(infeasible),
        )
        return SolveOutcome(vectors, unsolved, tuple(infeasible), boundary)

    def record(
        self,
        project_root: Path,
        module: ModuleInfo,
        function: FunctionInfo,
        vectors: Sequence[KeyedVector],
        origin: CaseOrigin,
    ) -> RecordOutcome:
        """Execute ``function`` on ``vectors`` in the sandbox and keep what it did.

        At most ``max_cases_per_function`` vectors are run. Instance methods are never
        executed (the recorder cannot construct their objects): the result is empty.
        """
        if function.is_method and not function.is_static:
            _log.warning("record_skipped_instance_method", function=function.qualname)
            return RecordOutcome()
        if len(vectors) > self._max_cases:
            _log.info(
                "record_cut",
                function=function.qualname,
                kept=self._max_cases,
                cut=len(vectors) - self._max_cases,
            )
        batch = list(vectors[: self._max_cases])
        if not batch:
            return RecordOutcome()
        cases: list[TestCaseSpec] = []
        dropped: list[DroppedTest] = []
        for (target_key, inputs), observation in self._observe(
            project_root, module, function, batch
        ):
            case = _classify(
                function.qualname,
                inputs,
                observation,
                origin=origin,
                target_key=target_key,
                dropped=dropped,
            )
            if case is not None:
                cases.append(case)
        _log.info(
            "recorded",
            function=function.qualname,
            origin=origin.value,
            cases=len(cases),
            dropped=len(dropped),
        )
        return RecordOutcome(tuple(cases), tuple(dropped))

    # ------------------------------------------------------------------ internals

    def _boundary(
        self, function: FunctionInfo, solved: Sequence[tuple[str, dict[str, object]]]
    ) -> list[dict[str, object]]:
        room = min(self._max_boundary, self._max_cases - len(solved))
        if room <= 0 or not solved or (function.is_method and not function.is_static):
            return []
        known = {vector_key(inputs) for _, inputs in solved}
        candidates = boundary_vectors(function, solved[0][1], room + len(known))
        return [v for v in candidates if vector_key(v) not in known][:room]

    def _solve(self, function: FunctionInfo, target: BranchTarget) -> Solution:
        try:
            return self._solver.solve(function, target)
        except Exception as exc:  # a solver crash on one target must not stop the run
            _log.warning("solver_crashed", target=target.key, error=repr(exc))
            return Solution(target, SolveStatus.UNKNOWN, reason=f"solver error: {exc}")

    def _observe(
        self,
        project_root: Path,
        module: ModuleInfo,
        function: FunctionInfo,
        batch: Sequence[KeyedVector],
    ) -> list[tuple[KeyedVector, Observation]]:
        try:
            observations = self._recorder.observe(
                project_root, module, function, [inputs for _, inputs in batch]
            )
        except SandboxError as exc:
            _log.warning("record_batch_failed", function=function.qualname, error=str(exc))
            return self._observe_each(project_root, module, function, batch)
        if len(observations) != len(batch):
            _log.error("record_count_mismatch", function=function.qualname)
            return []
        return list(zip(batch, observations, strict=True))

    def _observe_each(
        self,
        project_root: Path,
        module: ModuleInfo,
        function: FunctionInfo,
        batch: Sequence[KeyedVector],
    ) -> list[tuple[KeyedVector, Observation]]:
        """Fallback after a failed batch: one vector at a time, so one bad vector costs one."""
        result: list[tuple[KeyedVector, Observation]] = []
        for item in batch:
            try:
                single = self._recorder.observe(project_root, module, function, [item[1]])
            except SandboxError as exc:
                _log.warning("record_vector_failed", function=function.qualname, error=str(exc))
                continue
            if len(single) == 1:
                result.append((item, single[0]))
        return result


def _entry_target(function: FunctionInfo) -> BranchTarget:
    return BranchTarget(
        function=function.qualname,
        line=function.lineno,
        kind=BranchKind.IF,
        condition_source="True",
        outcome=True,
        arc=None,
        path=PathCondition(),
    )


def _classify(
    function: str,
    inputs: Vector,
    observation: Observation,
    *,
    origin: CaseOrigin,
    target_key: str | None,
    dropped: list[DroppedTest],
) -> TestCaseSpec | None:
    kind = observation.kind
    if kind in (ObservationKind.RETURNED, ObservationKind.RAISED):
        return TestCaseSpec(function, dict(inputs), observation, origin, target_key=target_key)
    label = describe_inputs(function, inputs)
    if kind is ObservationKind.UNSTABLE:
        detail = observation.exception_message or "two executions disagreed"
        dropped.append(DroppedTest(label, function, DropReason.UNSTABLE, detail))
    elif kind is ObservationKind.NOT_LITERAL:
        detail = f"result is not a literal: {(observation.value_repr or '')[:80]}"
        dropped.append(DroppedTest(label, function, DropReason.NOT_LITERAL, detail))
    else:
        dropped.append(DroppedTest(label, function, DropReason.STILL_FAILING, "timeout"))
    return None
