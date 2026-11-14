from __future__ import annotations

from pathlib import Path

import pytest
from orchestration_support import (
    CALC_SOURCE,
    InProcessRecorder,
    ScriptedSolver,
    analyze,
    sat,
    status,
    write_project,
)

from pytestgen.application.skeleton import (
    SkeletonBuilder,
    describe_inputs,
    vector_key,
)
from pytestgen.domain.models import (
    BranchKind,
    CaseOrigin,
    DropReason,
    FunctionInfo,
    ModuleInfo,
    Observation,
    ObservationKind,
    SolveStatus,
)
from pytestgen.errors import ModuleImportError, SandboxError


@pytest.fixture
def module(tmp_path: Path) -> ModuleInfo:
    return analyze(tmp_path, write_project(tmp_path, CALC_SOURCE))


@pytest.fixture
def grade(module: ModuleInfo) -> FunctionInfo:
    return module.function("grade")


def builder(solver: ScriptedSolver, recorder: InProcessRecorder, cap: int = 12) -> SkeletonBuilder:
    return SkeletonBuilder(solver, recorder, max_cases_per_function=cap)


# --------------------------------------------------------------------------- solve_targets


def test_each_branch_outcome_is_solved_once(grade: FunctionInfo) -> None:
    answers = {t.key: sat(t, score=i) for i, t in enumerate(grade.branches)}
    solver = ScriptedSolver(answers)
    outcome = builder(solver, InProcessRecorder()).solve_targets(grade)
    assert [key for key, _ in outcome.vectors] == [t.key for t in grade.branches]
    assert [v for _, v in outcome.vectors] == [{"score": i} for i in range(len(grade.branches))]
    assert outcome.unsolved == {}
    assert outcome.infeasible == ()
    assert [t.key for t in solver.targets] == [t.key for t in grade.branches]


def test_identical_vectors_are_kept_once_under_the_first_target(grade: FunctionInfo) -> None:
    solver = ScriptedSolver(default={"score": 7})
    outcome = builder(solver, InProcessRecorder()).solve_targets(grade)
    assert outcome.vectors == [(grade.branches[0].key, {"score": 7})]


def test_unsat_is_infeasible_and_unknown_or_unsupported_are_unsolved(grade: FunctionInfo) -> None:
    first, second, third, *_ = grade.branches
    solver = ScriptedSolver(
        {
            first.key: status(first, SolveStatus.UNSAT),
            second.key: status(second, SolveStatus.UNSUPPORTED, "uses a lambda"),
            third.key: status(third, SolveStatus.UNKNOWN),
        },
        default={"score": 1},
    )
    outcome = builder(solver, InProcessRecorder()).solve_targets(grade)
    assert outcome.infeasible == (first.key,)
    assert outcome.unsolved == {second.key: "uses a lambda", third.key: "unknown"}
    assert len(outcome.vectors) == 1


def test_function_without_branches_gets_a_synthetic_entry_target(module: ModuleInfo) -> None:
    ident = module.function("ident")
    assert ident.branches == ()
    solver = ScriptedSolver(default={"x": 0})
    outcome = builder(solver, InProcessRecorder()).solve_targets(ident)
    (target,) = solver.targets
    assert target.path.clauses == ()
    assert target.kind is BranchKind.IF
    assert (target.line, target.outcome, target.condition_source) == (ident.lineno, True, "True")
    assert outcome.vectors == [(target.key, {"x": 0})]


def test_a_crashing_solver_marks_only_that_target_unsolved(grade: FunctionInfo) -> None:
    first = grade.branches[0]
    solver = ScriptedSolver({first.key: RuntimeError("z3 exploded")}, default={"score": 3})
    outcome = builder(solver, InProcessRecorder()).solve_targets(grade)
    assert "z3 exploded" in outcome.unsolved[first.key]
    assert outcome.vectors


# --------------------------------------------------------------------------- record


def test_returned_and_raised_observations_become_cases(
    tmp_path: Path, module: ModuleInfo, grade: FunctionInfo
) -> None:
    recorder = InProcessRecorder()
    outcome = builder(ScriptedSolver(), recorder).record(
        tmp_path,
        module,
        grade,
        [("grade:5:T", {"score": 95}), (None, {"score": "x"})],
        CaseOrigin.LLM_SLOT,
    )
    ok, error = outcome.cases
    assert (ok.function, ok.inputs, ok.origin, ok.target_key) == (
        "grade",
        {"score": 95},
        CaseOrigin.LLM_SLOT,
        "grade:5:T",
    )
    assert ok.observation.kind is ObservationKind.RETURNED
    assert ok.observation.value_repr == "'A'"
    assert error.observation.kind is ObservationKind.RAISED
    assert error.target_key is None
    assert outcome.dropped == ()
    assert len(recorder.calls) == 1, "one batch, one recorder call"


@pytest.mark.parametrize(
    ("observation", "reason", "detail"),
    [
        (
            Observation({}, ObservationKind.UNSTABLE, exception_message="1 != 2"),
            DropReason.UNSTABLE,
            "1 != 2",
        ),
        (
            Observation({}, ObservationKind.NOT_LITERAL, value_repr="<object at 0x1>"),
            DropReason.NOT_LITERAL,
            "<object at 0x1>",
        ),
        (Observation({}, ObservationKind.TIMEOUT), DropReason.STILL_FAILING, "timeout"),
    ],
)
def test_unusable_observations_are_dropped_with_a_reason(
    tmp_path: Path,
    module: ModuleInfo,
    grade: FunctionInfo,
    observation: Observation,
    *,
    reason: DropReason,
    detail: str,
) -> None:
    recorder = InProcessRecorder(scripted={"score': 1": observation})
    outcome = builder(ScriptedSolver(), recorder).record(
        tmp_path, module, grade, [(None, {"score": 1})], CaseOrigin.Z3
    )
    assert outcome.cases == ()
    (dropped,) = outcome.dropped
    assert dropped.reason is reason
    assert detail in dropped.detail
    assert dropped.function == "grade"
    assert "score=1" in dropped.name


def test_vectors_beyond_the_cap_are_not_executed(
    tmp_path: Path, module: ModuleInfo, grade: FunctionInfo
) -> None:
    recorder = InProcessRecorder()
    vectors = [(None, {"score": n}) for n in range(10)]
    outcome = builder(ScriptedSolver(), recorder, cap=4).record(
        tmp_path, module, grade, vectors, CaseOrigin.Z3
    )
    assert len(recorder.calls) == 1
    assert [v["score"] for v in recorder.calls[0][1]] == [0, 1, 2, 3]
    assert len(outcome.cases) == 4


def test_instance_methods_are_never_recorded(tmp_path: Path, module: ModuleInfo) -> None:
    recorder = InProcessRecorder()
    outcome = builder(ScriptedSolver(), recorder).record(
        tmp_path, module, module.function("Counter.bump"), [(None, {"by": 1})], CaseOrigin.Z3
    )
    assert outcome.cases == () and outcome.dropped == ()
    assert recorder.calls == []


def test_static_methods_are_recorded(tmp_path: Path, module: ModuleInfo) -> None:
    recorder = InProcessRecorder()
    outcome = builder(ScriptedSolver(), recorder).record(
        tmp_path, module, module.function("Counter.twice"), [(None, {"n": 4})], CaseOrigin.Z3
    )
    assert outcome.cases[0].observation.value_repr == "8"


def test_no_vectors_means_no_recorder_call(
    tmp_path: Path, module: ModuleInfo, grade: FunctionInfo
) -> None:
    recorder = InProcessRecorder()
    outcome = builder(ScriptedSolver(), recorder).record(tmp_path, module, grade, [], CaseOrigin.Z3)
    assert outcome.cases == () and recorder.calls == []


def test_failed_batch_falls_back_to_one_vector_at_a_time(
    tmp_path: Path, module: ModuleInfo, grade: FunctionInfo
) -> None:
    class FlakyBatch(InProcessRecorder):
        def observe(self, project_root, module, function, inputs):  # type: ignore[no-untyped-def]
            if len(inputs) > 1:
                raise SandboxError("inputs must be plain Python literals")
            if inputs[0] == {"score": 2}:
                raise SandboxError("this one is bad")
            return super().observe(project_root, module, function, inputs)

    outcome = builder(ScriptedSolver(), FlakyBatch()).record(
        tmp_path,
        module,
        grade,
        [(None, {"score": 1}), (None, {"score": 2}), (None, {"score": 99})],
        CaseOrigin.Z3,
    )
    assert [c.inputs["score"] for c in outcome.cases] == [1, 99]


def test_import_errors_propagate(tmp_path: Path, module: ModuleInfo, grade: FunctionInfo) -> None:
    recorder = InProcessRecorder(error=ModuleImportError("no module named x"))
    with pytest.raises(ModuleImportError):
        builder(ScriptedSolver(), recorder).record(
            tmp_path, module, grade, [(None, {"score": 1})], CaseOrigin.Z3
        )


# --------------------------------------------------------------------------- helpers


def test_vector_key_ignores_order_but_not_types() -> None:
    assert vector_key({"a": 1, "b": 2}) == vector_key({"b": 2, "a": 1})
    assert vector_key({"a": 1}) != vector_key({"a": 1.0})
    assert vector_key({"a": 1}) != vector_key({"a": True})


def test_describe_inputs_is_short() -> None:
    assert describe_inputs("f", {"a": 1, "b": "x"}) == "f(a=1, b='x')"
    long = describe_inputs("f", {"a": "y" * 200})
    assert len(long) == 60 and long.endswith("...")
