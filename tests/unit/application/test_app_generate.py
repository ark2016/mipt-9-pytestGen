from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from fakes_llm import ScriptedLLM
from orchestration_support import (
    CALC_SOURCE,
    CoverageModel,
    FakeAnalyzer,
    FakeIntrospector,
    FakeProposer,
    FakeRepairer,
    FakeReviewer,
    FakeRunner,
    FakeWriter,
    InMemoryFiles,
    InProcessRecorder,
    ScriptedSolver,
    Stages,
    analyze,
    node_names,
    sat,
    status,
    write_project,
)
from structlog.testing import capture_logs

from pytestgen.application import (
    FreeFormWriter,
    GenerationBudget,
    InputProposer,
    OracleReviewer,
    SlotFiller,
    StageAnnouncer,
    SuiteSelector,
    TestRepairer,
)
from pytestgen.application.collaborators import ProjectTestFiles, UsageMeter
from pytestgen.application.generate import GenerateRequest, GenerateTests
from pytestgen.application.skeleton import SkeletonBuilder
from pytestgen.application.targets import select_functions
from pytestgen.concolic import Z3HintSolver
from pytestgen.domain.models import (
    Arc,
    CaseOrigin,
    DropReason,
    LLMUsage,
    ModuleInfo,
    RunResult,
    SolveStatus,
    Strategy,
)
from pytestgen.errors import LLMConnectionError, ModuleImportError, PytestgenError
from pytestgen.ports import RunRequest, Stage
from pytestgen.testfiles import LocalTestFiles

BUMP_TEST = """
from calc import Counter


def test_bump_adds():
    assert Counter().bump(2) == 2
"""
GENERATED = "test_calc_pytestgen.py"


def span(module: ModuleInfo, qualname: str) -> set[int]:
    fn = module.function(qualname)
    return set(range(fn.lineno, fn.end_lineno + 1))


def full_model(module: ModuleInfo) -> CoverageModel:
    return CoverageModel(
        {
            "test_grade_returns": span(module, "grade"),
            "test_ident_returns": span(module, "ident"),
            "test_counter_twice_returns": span(module, "Counter.twice"),
            "test_bump_adds": span(module, "Counter.bump"),
        }
    )


@dataclass
class FakeMeter:
    usage: LLMUsage
    model: str


@dataclass
class Rig:
    root: Path
    module: ModuleInfo
    runner: FakeRunner
    solver: ScriptedSolver | Z3HintSolver
    recorder: InProcessRecorder
    reviewer: FakeReviewer
    proposer: FakeProposer
    writer: FakeWriter
    repairer: FakeRepairer
    stages: Stages
    use_case: GenerateTests

    def request(self, **kw: object) -> GenerateRequest:
        defaults: dict[str, object] = {
            "target": self.module.path,
            "project_root": self.root,
            "existing_tests": (),
        }
        return GenerateRequest(**{**defaults, **kw})  # type: ignore[arg-type]

    def run(self, **kw: object):  # type: ignore[no-untyped-def]
        return self.use_case.execute(self.request(**kw))

    @property
    def default_output(self) -> Path:
        return self.root / "tests" / GENERATED

    @property
    def last_generated(self) -> str:
        return self.runner.generated_sources[-1]


Builder = Callable[..., Rig]


@pytest.fixture
def module(tmp_path: Path) -> ModuleInfo:
    return analyze(tmp_path, write_project(tmp_path, CALC_SOURCE))


@pytest.fixture
def build(tmp_path: Path, module: ModuleInfo) -> Builder:
    def make(
        *,
        solver: ScriptedSolver
        | Z3HintSolver
        | Callable[[ModuleInfo], ScriptedSolver]
        | None = None,
        model: CoverageModel | Callable[[ModuleInfo], CoverageModel] | None = None,
        runner_kw: dict[str, object] | None = None,
        reviewer: FakeReviewer | None = None,
        proposer: FakeProposer | None = None,
        writer: FakeWriter | None = None,
        repairer: FakeRepairer | None = None,
        introspector: FakeIntrospector | None = None,
        files: ProjectTestFiles | None = None,
        meter: UsageMeter | None = None,
        boundary: int = 0,
        runner_factory: Callable[[CoverageModel], FakeRunner] | None = None,
        **budget: object,
    ) -> Rig:
        real_model = model(module) if callable(model) else model or full_model(module)
        runner = (runner_factory or (lambda m: FakeRunner(m, **(runner_kw or {}))))(real_model)  # type: ignore[arg-type]
        the_solver = solver(module) if callable(solver) else solver or Z3HintSolver()
        recorder = InProcessRecorder()
        reviewer = reviewer or FakeReviewer()
        proposer = proposer or FakeProposer()
        writer = writer or FakeWriter(sources={"Counter.bump": BUMP_TEST})
        repairer = repairer or FakeRepairer()
        stages = Stages()
        announce = StageAnnouncer(stages)
        use_case = GenerateTests(
            analyzer=FakeAnalyzer(module),
            introspector=introspector or FakeIntrospector(),
            runner=runner,
            files=files or LocalTestFiles(),
            filler=SlotFiller(
                SkeletonBuilder(the_solver, recorder, max_boundary_vectors=boundary),
                reviewer,
                proposer,
                writer,
                announce,
            ),
            repairer=repairer,
            selector=SuiteSelector(),
            budget=GenerationBudget(**budget),  # type: ignore[arg-type]
            meter=meter,
            announce=announce,
        )
        return Rig(
            tmp_path,
            module,
            runner,
            the_solver,
            recorder,
            reviewer,
            proposer,
            writer,
            repairer,
            stages,
            use_case,
        )

    return make


# --------------------------------------------------------------------------- happy path


def test_hybrid_happy_path_writes_the_test_file_and_reports(build: Builder) -> None:
    rig = build(meter=FakeMeter(LLMUsage(requests=3), "qwen-test"))
    report = rig.run()

    assert report.output_path == rig.default_output
    text = rig.default_output.read_text(encoding="utf-8")
    assert text.startswith('"""Tests generated by pytestgen for calc.')
    assert "test_grade_returns" in text and "test_bump_adds" in text
    assert report.kept_tests == len(node_names(text))
    assert report.module == "calc" and report.strategy is Strategy.HYBRID
    assert (report.llm_usage, report.model) == (LLMUsage(requests=3), "qwen-test")
    assert report.dropped == () and report.suspected_bugs == ()
    assert report.delta.after.covered_lines > report.delta.before.covered_lines == 0
    assert report.duration_s >= 0
    assert [o.origin for o in report.by_origin] == [CaseOrigin.Z3, CaseOrigin.LLM_FREE]
    assert sum(o.tests for o in report.by_origin) == report.kept_tests


def test_only_default_targets_are_processed_and_methods_go_to_the_writer(build: Builder) -> None:
    rig = build()
    rig.run()
    assert [name for name, *_ in rig.writer.calls] == ["Counter.bump"]
    assert [name for name, _ in rig.recorder.calls] == ["grade", "ident", "Counter.twice"]
    assert rig.proposer.calls == []


def test_stages_are_reported_in_pipeline_order(build: Builder) -> None:
    rig = build()
    rig.run(existing_tests=(rig.root / "tests" / "test_old.py",))
    seen = rig.stages.names
    for earlier, later in [
        (Stage.ANALYZE, Stage.BASELINE),
        (Stage.BASELINE, Stage.SOLVE),
        (Stage.SOLVE, Stage.RECORD),
        (Stage.RECORD, Stage.LLM_SLOTS),
        (Stage.LLM_SLOTS, Stage.RUN),
        (Stage.RUN, Stage.WRITE),
    ]:
        assert seen.index(earlier) < seen.index(later)


def test_instance_methods_are_never_given_to_the_recorder_or_solver(build: Builder) -> None:
    solver = ScriptedSolver(default={"score": 1})
    rig = build(solver=solver)
    rig.run(functions=("Counter.bump",))
    assert rig.recorder.calls == []
    assert solver.targets == []
    assert [name for name, *_ in rig.writer.calls] == ["Counter.bump"]
    assert "test_bump_adds" in rig.last_generated


# --------------------------------------------------------------------------- oracle review


def grade_solver(
    module: ModuleInfo,
    vectors: dict[str, int | SolveStatus],
    reasons: dict[str, str] | None = None,
) -> ScriptedSolver:
    """Solver for ``grade``: a score (SAT) or a status per branch key."""
    targets = {t.key: t for t in module.function("grade").branches}
    answers = {}
    for key, value in vectors.items():
        if isinstance(value, SolveStatus):
            answers[key] = status(targets[key], value, (reasons or {}).get(key, ""))
        else:
            answers[key] = sat(targets[key], score=value)
    return ScriptedSolver(answers)


def test_suspicious_case_becomes_a_suspected_bug_and_is_left_out_of_the_file(
    build: Builder,
) -> None:
    reviewer = FakeReviewer(
        lambda case: "B should start at 60" if case.inputs.get("score") == 55 else None
    )
    vectors = {"grade:6:T": 95, "grade:6:F": 10, "grade:8:T": 55, "grade:8:F": 10}
    rig = build(solver=lambda m: grade_solver(m, vectors), reviewer=reviewer)
    report = rig.run(functions=("grade",))
    (bug,) = report.suspected_bugs
    assert (bug.function, bug.inputs, bug.observed) == ("grade", {"score": 55}, "'B'")
    assert bug.explanation == "B should start at 60"
    assert "55" not in rig.last_generated
    assert report.kept_tests == 2


# --------------------------------------------------------------------------- unsolved branches


def test_unsolved_branches_go_to_the_proposer_and_become_llm_slot_cases(build: Builder) -> None:
    vectors = {
        "grade:6:T": 95,
        "grade:6:F": SolveStatus.UNSUPPORTED,
        "grade:8:T": SolveStatus.UNKNOWN,
        "grade:8:F": 10,
    }
    proposer = FakeProposer([{"score": 70}, {"score": 95}, {"score": 33}])
    rig = build(
        solver=lambda m: grade_solver(m, vectors, {"grade:6:F": "no idea"}), proposer=proposer
    )
    report = rig.run(functions=("grade",))

    (call,) = proposer.calls
    assert call[0] == "grade"
    assert call[1] == ["grade:6:F", "grade:8:T"]
    assert call[2]["grade:6:F"] == "no idea"
    text = rig.default_output.read_text(encoding="utf-8")
    assert "llm_slot" in text and "70" in text
    assert text.count("95") == 1, "a proposed vector equal to a solver vector is not repeated"
    assert [o.tests for o in report.by_origin if o.origin is CaseOrigin.LLM_SLOT] == [2]


def test_unsat_targets_are_not_sent_to_the_proposer(build: Builder) -> None:
    vectors = dict.fromkeys(("grade:6:T", "grade:6:F", "grade:8:T", "grade:8:F"), SolveStatus.UNSAT)
    rig = build(solver=lambda m: grade_solver(m, vectors))
    rig.run(functions=("grade",))
    assert rig.proposer.calls == []


# --------------------------------------------------------------------------- repair


def mark_fixed(source: str) -> str:
    """Add a statement to the test body (trailing comments would not survive splitting)."""
    return source.replace("    assert", "    marker = 'FIXED'" + chr(10) + "    assert", 1)


def failing_until_fixed(name: str) -> Callable[[str, str], str | None]:
    return lambda node, source: (
        f"assert failed in {node}" if node.startswith(name) and "FIXED" not in source else None
    )


def test_failing_free_test_is_repaired_and_kept(build: Builder) -> None:
    repairer = FakeRepairer(lambda test, outcome: mark_fixed(test.source))
    rig = build(
        runner_kw={"fail": failing_until_fixed("test_bump_adds")},
        repairer=repairer,
        gap_iterations=0,
    )
    report = rig.run()
    assert [name for name, _ in repairer.calls] == ["test_bump_adds"]
    assert "assert failed in test_bump_adds" in repairer.calls[0][1]
    assert "FIXED" in rig.default_output.read_text(encoding="utf-8")
    assert report.dropped == ()
    assert Stage.REPAIR in rig.stages.names


def test_free_test_that_stays_broken_is_dropped_after_the_repair_budget(build: Builder) -> None:
    repairer = FakeRepairer(lambda test, outcome: test.source.replace("assert", "assert 1 and", 1))
    rig = build(
        runner_kw={"fail": failing_until_fixed("test_bump_adds")},
        repairer=repairer,
        repair_attempts=2,
        gap_iterations=0,
    )
    report = rig.run()
    assert len(repairer.calls) == 2
    (dropped,) = report.dropped
    assert (dropped.name, dropped.function) == ("test_bump_adds", "Counter.bump")
    assert dropped.reason is DropReason.STILL_FAILING
    assert dropped.detail == "assert failed in test_bump_adds"
    assert "test_bump_adds" not in rig.default_output.read_text(encoding="utf-8")


def test_repairer_giving_up_drops_the_test_at_once(build: Builder) -> None:
    repairer = FakeRepairer(lambda test, outcome: None)
    rig = build(
        runner_kw={"fail": failing_until_fixed("test_bump_adds")},
        repairer=repairer,
        gap_iterations=0,
    )
    report = rig.run()
    assert len(repairer.calls) == 1
    assert [d.reason for d in report.dropped] == [DropReason.STILL_FAILING]


def test_zero_repair_budget_never_calls_the_repairer(build: Builder) -> None:
    repairer = FakeRepairer(lambda test, outcome: mark_fixed(test.source))
    rig = build(
        runner_kw={"fail": failing_until_fixed("test_bump_adds")},
        repairer=repairer,
        repair_attempts=0,
        gap_iterations=0,
    )
    report = rig.run()
    assert repairer.calls == []
    assert len(report.dropped) == 1


def test_a_repairer_that_does_not_count_its_attempts_cannot_loop_forever(build: Builder) -> None:
    class Sloppy(FakeRepairer):
        def repair(self, module, function, test, outcome):  # type: ignore[no-untyped-def]
            self.calls.append((test.name, outcome.message))
            return test  # unchanged, attempts not incremented

    repairer = Sloppy()
    rig = build(
        runner_kw={"fail": failing_until_fixed("test_bump_adds")},
        repairer=repairer,
        repair_attempts=3,
        gap_iterations=0,
    )
    report = rig.run()
    assert len(repairer.calls) == 3
    assert report.dropped[0].reason is DropReason.STILL_FAILING


def test_failing_recorded_case_is_dropped_as_unstable_and_the_rest_rerun(build: Builder) -> None:
    vectors = {"grade:6:T": 95, "grade:6:F": 10, "grade:8:T": 60, "grade:8:F": 10}
    rig = build(
        solver=lambda m: grade_solver(m, vectors),
        runner_kw={
            "fail": lambda node, source: (
                "flaky" if node == "test_grade_returns[z3-1]" and "95" in source else None
            )
        },
        gap_iterations=0,
    )
    report = rig.run(functions=("grade",))
    (dropped,) = report.dropped
    assert dropped.reason is DropReason.UNSTABLE and dropped.detail == "flaky"
    assert "95" not in rig.last_generated
    assert report.kept_tests == 2
    assert len(rig.runner.generated_sources) == 2


def test_free_test_with_a_hallucinated_import_is_dropped_not_fatal(build: Builder) -> None:
    ghost = """
from calc import phantom


def test_ghost():
    phantom()
"""
    writer = FakeWriter(sources={"Counter.bump": BUMP_TEST, "Counter.twice": ghost})
    rig = build(
        writer=writer,
        runner_kw={
            "collection_error": lambda text: (
                "ImportError: cannot import name 'phantom'" if "phantom" in text else None
            )
        },
        gap_iterations=0,
    )
    report = rig.run(strategy=Strategy.LLM, functions=("Counter",))
    text = rig.default_output.read_text(encoding="utf-8")
    assert "phantom" not in text and "test_bump_adds" in text
    (dropped,) = report.dropped
    assert (dropped.name, dropped.reason) == ("test_ghost", DropReason.INVALID_CODE)
    assert "phantom" in dropped.detail


# --------------------------------------------------------------------------- gap loop


def bump_model(module: ModuleInfo) -> CoverageModel:
    lines = sorted(span(module, "Counter.bump"))
    covers: dict[str, set[int]] = {
        "test_grade_returns": span(module, "grade"),
        "test_ident_returns": span(module, "ident"),
        "test_counter_twice_returns": span(module, "Counter.twice"),
        "test_bump_a": {lines[0]},
        "test_bump_b": {lines[1]},
        "test_bump_c": {lines[0]},
        "test_bump_d": {lines[2]},
    }
    return CoverageModel(covers)


def bump_source(name: str) -> str:
    return f"from calc import Counter\n\n\ndef {name}():\n    assert Counter().bump(1) == 1\n"


def test_gap_loop_adds_tests_for_uncovered_lines_until_nothing_improves(build: Builder) -> None:
    writer = FakeWriter(
        sequence={
            "Counter.bump": [
                bump_source("test_bump_a"),
                bump_source("test_bump_b"),
                bump_source("test_bump_c"),
                bump_source("test_bump_d"),
            ]
        }
    )
    rig = build(model=bump_model, writer=writer, gap_iterations=5)
    report = rig.run(functions=("Counter.bump",))

    assert len(writer.calls) == 3, "initial call, one productive iteration, one that stopped"
    _, uncovered, avoid = writer.calls[1]
    module = rig.module
    lines = sorted(span(module, "Counter.bump"))
    assert uncovered == (lines[1], lines[2])
    assert avoid == {"test_bump_a"}
    text = rig.default_output.read_text(encoding="utf-8")
    assert "test_bump_a" in text and "test_bump_b" in text
    assert "test_bump_c" not in text, "tests of a no-gain iteration are rolled back"
    assert report.kept_tests == 2


def test_gap_loop_respects_the_iteration_budget(build: Builder) -> None:
    writer = FakeWriter(
        sequence={
            "Counter.bump": [
                bump_source("test_bump_a"),
                bump_source("test_bump_b"),
                bump_source("test_bump_d"),
            ]
        }
    )
    rig = build(model=bump_model, writer=writer, gap_iterations=1)
    rig.run(functions=("Counter.bump",))
    assert len(writer.calls) == 2
    assert "test_bump_d" not in rig.default_output.read_text(encoding="utf-8")


def test_gap_loop_is_skipped_when_everything_is_covered(build: Builder) -> None:
    rig = build()
    rig.run()
    assert len(rig.writer.calls) == 1
    assert Stage.GAP not in rig.stages.names


def test_gap_loop_asks_the_proposer_for_missing_arcs_of_solved_targets(
    build: Builder, module: ModuleInfo
) -> None:
    targets = {t.key: t for t in module.function("grade").branches}
    vectors = {"grade:6:T": 95, "grade:6:F": 60, "grade:8:T": 60, "grade:8:F": 10}
    arcs = {k: t.arc for k, t in targets.items()}
    assert all(arcs.values())

    def model(m: ModuleInfo) -> CoverageModel:
        covers: dict[str, set[int | Arc]] = {
            "test_grade_returns[z3-1]": {arcs["grade:6:T"]},  # type: ignore[arg-type]
            "test_grade_returns[z3-2]": {arcs["grade:6:F"]},  # type: ignore[arg-type]
            "test_grade_returns[z3-3]": {arcs["grade:8:F"]},  # type: ignore[arg-type]
            "test_grade_returns[llm_slot-4]": {arcs["grade:8:T"]},  # type: ignore[arg-type]
        }
        return CoverageModel(covers)

    proposer = FakeProposer([{"score": 70}])
    rig = build(
        solver=lambda m: grade_solver(m, vectors),
        model=model,
        proposer=proposer,
        gap_iterations=2,
    )
    report = rig.run(functions=("grade",))

    (call,) = proposer.calls
    assert call[:2] == ("grade", ["grade:8:T"])
    assert "llm_slot-4" in rig.default_output.read_text(encoding="utf-8")
    slot = next(o for o in report.by_origin if o.origin is CaseOrigin.LLM_SLOT)
    assert slot.arcs_added == 1
    z3 = next(o for o in report.by_origin if o.origin is CaseOrigin.Z3)
    assert z3.arcs_added == 3


def test_gap_iteration_without_new_inputs_falls_back_to_the_writer(
    build: Builder, module: ModuleInfo
) -> None:
    grade = module.function("grade")
    missing_line = grade.end_lineno
    covers: dict[str, set[int]] = {
        "test_grade_returns": span(module, "grade") - {missing_line},
        "test_grade_extra": {missing_line},
    }
    writer = FakeWriter(
        sequence={
            "grade": ["from calc import grade\n\n\ndef test_grade_extra():\n    assert grade(1)\n"]
        }
    )
    rig = build(model=CoverageModel(covers), writer=writer, gap_iterations=1)
    rig.run(functions=("grade",))
    assert writer.calls[0][0] == "grade"
    assert writer.calls[0][1] == (missing_line,)
    assert "test_grade_extra" in rig.default_output.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- strategies


def test_llm_strategy_skips_solver_recorder_and_reviewer(build: Builder) -> None:
    solver = ScriptedSolver(default={"score": 1})
    writer = FakeWriter(default=BUMP_TEST)
    rig = build(solver=solver, writer=writer, gap_iterations=0)
    report = rig.run(strategy=Strategy.LLM)
    assert solver.targets == [] and rig.recorder.calls == [] and rig.reviewer.calls == []
    assert [name for name, *_ in writer.calls] == [
        "grade",
        "ident",
        "Counter.bump",
        "Counter.twice",
    ]
    assert report.strategy is Strategy.LLM
    assert [o.origin for o in report.by_origin] == [CaseOrigin.LLM_FREE]
    assert Stage.SOLVE not in rig.stages.names


def test_llm_strategy_gap_loop_uses_only_the_writer(build: Builder) -> None:
    writer = FakeWriter(
        sequence={"Counter.bump": [bump_source("test_bump_a"), bump_source("test_bump_b")]}
    )
    rig = build(model=bump_model, writer=writer, gap_iterations=1, solver=ScriptedSolver())
    rig.run(strategy=Strategy.LLM, functions=("Counter.bump",))
    assert len(writer.calls) == 2
    assert rig.proposer.calls == [] and rig.recorder.calls == []


def test_nothing_generated_writes_no_file(build: Builder) -> None:
    rig = build(writer=FakeWriter(), gap_iterations=0)
    report = rig.run(strategy=Strategy.LLM)
    assert report.kept_tests == 0 and report.output_path is None
    assert not rig.default_output.exists()


# --------------------------------------------------------------------------- writing


def test_dry_run_writes_nothing_but_still_reports(build: Builder) -> None:
    rig = build()
    report = rig.run(dry_run=True)
    assert report.output_path is None
    assert not rig.default_output.exists()
    assert report.kept_tests > 0


def test_custom_output_creates_parent_directories(build: Builder, tmp_path: Path) -> None:
    rig = build()
    target = tmp_path / "deep" / "dir" / "test_mine.py"
    report = rig.run(output=target)
    assert report.output_path == target and target.is_file()
    assert b"\r\n" not in target.read_bytes()


def test_foreign_file_is_not_overwritten_and_nothing_expensive_happens(build: Builder) -> None:
    rig = build()
    rig.default_output.parent.mkdir(parents=True)
    rig.default_output.write_text("def test_mine():\n    pass\n", encoding="utf-8")
    with pytest.raises(PytestgenError, match="not generated by pytestgen"):
        rig.run()
    assert rig.writer.calls == [] and rig.runner.requests == []
    assert rig.default_output.read_text(encoding="utf-8") == "def test_mine():\n    pass\n"


def test_overwrite_flag_replaces_a_foreign_file(build: Builder) -> None:
    rig = build()
    rig.default_output.parent.mkdir(parents=True)
    rig.default_output.write_text("def test_mine():\n    pass\n", encoding="utf-8")
    rig.run(overwrite=True)
    assert rig.default_output.read_text(encoding="utf-8").startswith('"""Tests generated')


def test_previously_generated_file_is_replaced_without_the_flag(build: Builder) -> None:
    rig = build()
    rig.run()
    first = rig.default_output.read_text(encoding="utf-8")
    rig.run()
    assert rig.default_output.read_text(encoding="utf-8") == first


def test_dry_run_does_not_complain_about_a_foreign_file(build: Builder) -> None:
    rig = build()
    rig.default_output.parent.mkdir(parents=True)
    rig.default_output.write_text("x = 1\n", encoding="utf-8")
    assert rig.run(dry_run=True).output_path is None


# --------------------------------------------------------------------------- function selection


def test_unknown_function_names_are_an_error_listing_the_available_ones(build: Builder) -> None:
    rig = build()
    with pytest.raises(PytestgenError) as info:
        rig.run(functions=("grade", "nope", "Counter.nothing"))
    message = str(info.value)
    assert "nope" in message and "Counter.nothing" in message
    assert "grade" in message and "Counter.bump" in message and "_hidden" not in message
    assert rig.runner.requests == []


def test_function_filter_limits_the_work(build: Builder) -> None:
    rig = build()
    rig.run(functions=("grade",))
    assert [name for name, _ in rig.recorder.calls] == ["grade"]
    assert rig.writer.calls == []


def test_a_class_name_selects_its_public_methods(build: Builder, module: ModuleInfo) -> None:
    chosen = select_functions(module, ("Counter",))
    assert [f.qualname for f in chosen] == ["Counter.bump", "Counter.twice"]


def test_private_functions_are_included_only_when_named(build: Builder, module: ModuleInfo) -> None:
    assert "_hidden" not in [f.qualname for f in select_functions(module, ())]
    assert [f.qualname for f in select_functions(module, ("_hidden",))] == ["_hidden"]


def test_properties_and_dunders_are_never_selected(build: Builder, module: ModuleInfo) -> None:
    default = [f.qualname for f in select_functions(module, ())]
    assert default == ["grade", "ident", "Counter.bump", "Counter.twice"]
    with pytest.raises(PytestgenError, match="cannot be targeted"):
        select_functions(module, ("Counter.doubled", "Counter.__init__"))


# --------------------------------------------------------------------------- import failure


def test_non_importable_module_raises_before_any_work(build: Builder) -> None:
    rig = build(introspector=FakeIntrospector(importable=False, error="No module named 'numpy'"))
    with pytest.raises(ModuleImportError, match="No module named 'numpy'"):
        rig.run()
    assert rig.runner.requests == [] and rig.recorder.calls == [] and rig.writer.calls == []


# --------------------------------------------------------------------------- existing tests


def test_existing_tests_are_discovered_and_provide_the_baseline(build: Builder) -> None:
    def model(module: ModuleInfo) -> CoverageModel:
        covers = dict(full_model(module).covers)
        covers["test_existing"] = {min(span(module, "grade"))}
        return CoverageModel(covers)

    rig = build(model=model)
    tests = rig.root / "tests"
    tests.mkdir()
    (tests / "test_calc_basic.py").write_text("from calc import grade\n", encoding="utf-8")
    (tests / "test_other.py").write_text("import os\n", encoding="utf-8")
    report = rig.use_case.execute(GenerateRequest(target=rig.module.path, project_root=rig.root))
    baseline, *later = rig.runner.requests
    assert baseline.test_sources == {}
    assert baseline.existing_tests == ((tests / "test_calc_basic.py").resolve(),)
    assert all(r.existing_tests == baseline.existing_tests for r in later)
    assert report.delta.before.covered_lines == 1
    assert report.delta.after.covered_lines > 1


def test_explicit_empty_existing_tests_disable_discovery(build: Builder) -> None:
    rig = build()
    (rig.root / "test_calc_basic.py").write_text("import calc\n", encoding="utf-8")
    rig.run(existing_tests=())
    assert all(r.existing_tests == () for r in rig.runner.requests)


def test_explicit_existing_tests_are_used_as_given(build: Builder) -> None:
    rig = build()
    mine = rig.root / "elsewhere.py"
    mine.write_text("x = 1\n", encoding="utf-8")
    rig.run(existing_tests=(mine,))
    assert rig.runner.requests[0].existing_tests == (mine.resolve(),)


def test_broken_existing_tests_are_ignored_after_the_baseline(build: Builder) -> None:
    runner_kw: dict[str, object] = {
        "collection_error": lambda text: None if text else "SyntaxError in old tests"
    }
    rig = build(runner_kw=runner_kw)
    rig.run(existing_tests=(rig.root / "test_old.py",))
    baseline, *later = rig.runner.requests
    assert baseline.existing_tests and all(r.existing_tests == () for r in later)


# --------------------------------------------------------------------------- selection and stats


def test_minimize_drops_free_tests_without_coverage_gain(
    build: Builder, module: ModuleInfo
) -> None:
    covers = dict(full_model(module).covers)
    covers["test_bump_dup"] = span(module, "Counter.bump")
    writer = FakeWriter(
        sources={
            "Counter.bump": BUMP_TEST
            + "\n\ndef test_bump_dup():\n    assert Counter().bump(3) == 3\n"
        }
    )
    rig = build(model=CoverageModel(covers), writer=writer, gap_iterations=0)
    report = rig.run(minimize=True)
    dropped = [d for d in report.dropped if d.reason is DropReason.NO_COVERAGE_GAIN]
    assert len(dropped) == 1 and dropped[0].name in {"test_bump_adds", "test_bump_dup"}
    text = rig.default_output.read_text(encoding="utf-8")
    assert text.count("def test_bump_") == 1
    assert report.kept_tests == len(node_names(text))


def test_without_minimize_redundant_tests_stay(build: Builder, module: ModuleInfo) -> None:
    covers = dict(full_model(module).covers)
    covers["test_bump_dup"] = span(module, "Counter.bump")
    writer = FakeWriter(
        sources={
            "Counter.bump": BUMP_TEST
            + "\n\ndef test_bump_dup():\n    assert Counter().bump(3) == 3\n"
        }
    )
    rig = build(model=CoverageModel(covers), writer=writer, gap_iterations=0)
    report = rig.run()
    assert report.dropped == ()
    assert "test_bump_dup" in rig.default_output.read_text(encoding="utf-8")


def test_arcs_added_are_attributed_origin_by_origin(build: Builder) -> None:
    z3_arc, slot_arc, free_arc = Arc(1, 2), Arc(2, 3), Arc(3, 4)
    base_arc = Arc(0, 1)
    vectors = {
        "grade:6:T": 95,
        "grade:6:F": 10,
        "grade:8:T": SolveStatus.UNKNOWN,
        "grade:8:F": SolveStatus.UNSAT,
    }
    covers: dict[str, set[int | Arc]] = {
        "test_existing": {base_arc},
        "test_grade_returns[z3-1]": {z3_arc, base_arc},
        "test_grade_returns[z3-2]": set(),
        "test_grade_returns[llm_slot-3]": {slot_arc},
        "test_bump_adds": {free_arc, z3_arc},
    }
    rig = build(
        solver=lambda m: grade_solver(m, vectors),
        model=CoverageModel(covers),
        proposer=FakeProposer([{"score": 70}]),
        gap_iterations=0,
    )
    report = rig.run(functions=("grade", "Counter.bump"), existing_tests=(rig.root / "t.py",))
    by_origin = {o.origin: o for o in report.by_origin}
    assert by_origin[CaseOrigin.Z3].arcs_added == 1
    assert by_origin[CaseOrigin.LLM_SLOT].arcs_added == 1
    assert by_origin[CaseOrigin.LLM_FREE].arcs_added == 1
    # baseline + final run + the z3 layer + the z3+slot layer
    assert len(rig.runner.requests) == 4


def test_origin_stats_need_no_extra_run_when_only_solver_tests_exist(build: Builder) -> None:
    rig = build(writer=FakeWriter(), gap_iterations=0)
    rig.run(functions=("grade",))
    assert len(rig.runner.requests) == 1


def test_run_timeout_is_an_error_and_nothing_is_written(build: Builder, module: ModuleInfo) -> None:
    """A run that never finished verified nothing: no file, no bogus 100% -> 100% report."""
    rig = build(
        model=CoverageModel({"test_grade_returns": span(module, "grade")}, extra_lines={999}),
        runner_kw={"timed_out": True},
        gap_iterations=3,
        run_timeout_s=7,
    )
    with pytest.raises(
        PytestgenError, match=r"did not finish \(limit 7s\).*PYTESTGEN_RUN_TIMEOUT_S"
    ):
        rig.run(functions=("grade",))
    assert not rig.default_output.exists()
    assert Stage.GAP not in rig.stages.names
    assert Stage.WRITE not in rig.stages.names


def test_run_without_any_result_is_an_error_too(build: Builder) -> None:
    """pytest crashed hard: no outcomes and no coverage cannot count as a clean suite."""

    class Silent(FakeRunner):
        def run(self, request: RunRequest) -> RunResult:
            self.requests.append(request)
            return RunResult()

    rig = build(gap_iterations=0, runner_factory=Silent)
    with pytest.raises(PytestgenError, match="did not finish"):
        rig.run(functions=("grade",))
    assert not rig.default_output.exists()


def test_timeout_in_a_gap_iteration_keeps_the_verified_suite(build: Builder) -> None:
    """Only the extra round is lost when it times out; the earlier verified result stays."""

    class TimesOutOnSecondRun(FakeRunner):
        def run(self, request: RunRequest) -> RunResult:
            result = super().run(request)
            if len(self.requests) >= 2 and request.test_sources:
                return replace(result, timed_out=True, coverage=None)
            return result

    rig = build(
        model=bump_model,
        writer=FakeWriter(sequence={"Counter.bump": [BUMP_TEST]}),
        gap_iterations=2,
        solver=ScriptedSolver(),
        runner_factory=TimesOutOnSecondRun,
    )
    report = rig.run(functions=("Counter.bump",))
    assert report.output_path is not None
    assert Stage.GAP in rig.stages.names
    assert "def test_bump_adds" in rig.default_output.read_text(encoding="utf-8")


def test_timed_out_baseline_is_ignored_like_a_broken_one(build: Builder) -> None:
    class TimesOutOnBaseline(FakeRunner):
        def run(self, request: RunRequest) -> RunResult:
            if not request.test_sources and request.existing_tests:
                self.requests.append(request)
                return RunResult(timed_out=True)
            return super().run(request)

    rig = build(gap_iterations=0, runner_factory=TimesOutOnBaseline)
    report = rig.run(functions=("grade",), existing_tests=(rig.root / "test_slow.py",))
    baseline, *later = rig.runner.requests
    assert baseline.existing_tests and all(r.existing_tests == () for r in later)
    assert report.kept_tests > 0


def test_fully_empty_coverage_still_produces_a_report(build: Builder) -> None:
    rig = build(model=CoverageModel({}), gap_iterations=0)
    report = rig.run(functions=("grade",))
    assert report.delta.after == report.delta.before


# --------------------------------------------------------------------------- collaborator failures


def dead_llm() -> ScriptedLLM:
    """A model endpoint that is down: every call raises."""
    return ScriptedLLM(LLMConnectionError("endpoint is down"))


def test_llm_failures_degrade_to_what_the_solver_found(build: Builder) -> None:
    vectors = {
        "grade:6:T": 95,
        "grade:6:F": SolveStatus.UNKNOWN,
        "grade:8:T": 60,
        "grade:8:F": 10,
    }
    llm = dead_llm()
    rig = build(
        solver=lambda m: grade_solver(m, vectors),
        proposer=InputProposer(llm),
        reviewer=OracleReviewer(llm),
        writer=FreeFormWriter(llm),
        gap_iterations=1,
    )
    report = rig.run(functions=("grade", "Counter.bump"))
    assert report.kept_tests > 0
    assert all(o.origin is CaseOrigin.Z3 for o in report.by_origin)
    assert report.suspected_bugs == ()
    assert {r.purpose for r in llm.requests} >= {"propose_inputs", "write_tests"}


def test_repairer_failure_drops_the_test(build: Builder) -> None:
    rig = build(
        runner_kw={"fail": failing_until_fixed("test_bump_adds")},
        repairer=TestRepairer(dead_llm()),
        gap_iterations=0,
    )
    report = rig.run(functions=("Counter.bump",))
    assert [d.reason for d in report.dropped] == [DropReason.STILL_FAILING]


def test_reviewer_returning_the_wrong_number_of_cases_is_ignored(build: Builder) -> None:
    class Truncating(FakeReviewer):
        def review(self, module, function, cases):  # type: ignore[no-untyped-def]
            return super().review(module, function, cases)[:1]

    rig = build(reviewer=Truncating(lambda case: "always"), gap_iterations=0)
    report = rig.run(functions=("grade",))
    assert report.suspected_bugs == ()
    assert report.kept_tests > 1


# --------------------------------------------------------------------------- review regressions


def test_minimize_still_drops_redundant_tests_when_a_project_test_fails(
    build: Builder, module: ModuleInfo
) -> None:
    """F2: a red pre-existing test used to veto every trial run, silently disabling --minimize."""
    covers = dict(full_model(module).covers)
    covers["test_bump_dup"] = span(module, "Counter.bump")
    writer = FakeWriter(
        sources={
            "Counter.bump": BUMP_TEST
            + "\n\ndef test_bump_dup():\n    assert Counter().bump(3) == 3\n"
        }
    )
    rig = build(
        model=CoverageModel(covers),
        writer=writer,
        gap_iterations=0,
        runner_kw={"failing_existing": ("test_existing",)},
    )
    report = rig.run(minimize=True, existing_tests=(rig.root / "test_old.py",))
    dropped = [d for d in report.dropped if d.reason is DropReason.NO_COVERAGE_GAIN]
    assert len(dropped) == 1


def test_import_failure_message_is_one_line_with_the_cause(build: Builder) -> None:
    """F4: the sandbox traceback excerpt must not become the user-facing error."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "    return importlib.import_module(name), None\n"
        "ModuleNotFoundError: No module named 'nonexistent_pkg_xyz_123'\n"
    )
    rig = build(introspector=FakeIntrospector(importable=False, error=traceback))
    with pytest.raises(ModuleImportError) as info:
        rig.run()
    message = str(info.value)
    assert "\n" not in message
    assert message == (
        "cannot import calc: ModuleNotFoundError: No module named 'nonexistent_pkg_xyz_123'"
    )


def test_import_failure_without_detail_still_names_the_module(build: Builder) -> None:
    rig = build(introspector=FakeIntrospector(importable=False, error=None))
    with pytest.raises(ModuleImportError, match="cannot import calc: unknown import error"):
        rig.run()


def test_import_failure_keeps_the_full_detail_in_the_log(build: Builder) -> None:
    rig = build(introspector=FakeIntrospector(importable=False, error="line 1\nValueError: boom"))
    with capture_logs() as logs, pytest.raises(ModuleImportError):
        rig.run()
    (event,) = [e for e in logs if e["event"] == "module_import_failed"]
    assert event["detail"] == "line 1\nValueError: boom"


# --------------------------------------------------------------------------- boundary values


def grade_edges(module: ModuleInfo) -> ScriptedSolver:
    return grade_solver(
        module, {"grade:6:T": 95, "grade:6:F": 10, "grade:8:T": 60, "grade:8:F": 10}
    )


def test_boundary_inputs_reach_the_oracle_even_when_the_solver_never_picks_them(
    build: Builder,
) -> None:
    """F3: `score >= 90` is only wrong at exactly 90; the reviewer must see that call."""
    reviewer = FakeReviewer(
        lambda case: "A starts at 91" if case.inputs.get("score") == 90 else None
    )
    rig = build(solver=grade_edges, reviewer=reviewer, boundary=6)
    report = rig.run(functions=("grade",))
    reviewed = {c.inputs["score"] for call in reviewer.calls for c in call}
    assert {90, 89, 50} <= reviewed
    assert [b.inputs for b in report.suspected_bugs] == [{"score": 90}]


def test_functions_without_branches_get_boundary_inputs_too(tmp_path: Path) -> None:
    source = (
        'def free(total: float) -> bool:\n    """True for 100 or more."""\n    return total > 100\n'
    )
    free = analyze(tmp_path, write_project(tmp_path, source, "shop.py")).function("free")
    assert free.branches == ()
    skeleton = SkeletonBuilder(ScriptedSolver(default={"total": 0.0}), InProcessRecorder())
    outcome = skeleton.solve_targets(free)
    assert outcome.vectors and next(v["total"] for v in outcome.boundary) == 100.0


# --------------------------------------------------------------------------- masked bugs

GRADE_TEST = "from calc import grade\n\n\ndef test_grade_a():\n    assert grade(90) == 'A'\n"


def rewriting_rig(build: Builder, fail: Callable[[str, str], str | None], **kw: object) -> Rig:
    return build(
        runner_kw={"fail": fail},
        repairer=FakeRepairer(lambda test, outcome: mark_fixed(test.source)),
        gap_iterations=0,
        **kw,
    )


def test_a_test_rewritten_to_match_the_code_is_reported_for_documented_functions(
    build: Builder,
) -> None:
    """F3: aligning a failing assertion with the code can hide a bug; say so in the report."""
    rig = rewriting_rig(
        build,
        failing_until_fixed("test_grade_a"),
        writer=FakeWriter(sources={"grade": GRADE_TEST}),
    )
    report = rig.run(functions=("grade",), strategy=Strategy.LLM)
    (note,) = report.suspected_bugs
    assert note.function == "grade"
    assert note.inputs == {"test": "test_grade_a"}
    assert note.observed == "assert failed in test_grade_a"
    assert "rewritten to match the current behaviour" in note.explanation
    assert report.dropped == () and "FIXED" in rig.last_generated


def test_no_rewrite_note_without_a_docstring_to_compare_with(build: Builder) -> None:
    rig = rewriting_rig(build, failing_until_fixed("test_bump_adds"))
    report = rig.run(functions=("Counter.bump",))
    assert report.suspected_bugs == ()


def test_no_rewrite_note_for_failures_that_are_not_assertions(build: Builder) -> None:
    def crash(node: str, source: str) -> str | None:
        return "TypeError: bump() takes 2 positional arguments" if "FIXED" not in source else None

    rig = rewriting_rig(build, crash, writer=FakeWriter(sources={"grade": GRADE_TEST}))
    report = rig.run(functions=("grade",), strategy=Strategy.LLM)
    assert report.suspected_bugs == ()


def test_rewrite_note_disappears_with_the_test_that_is_given_up(build: Builder) -> None:
    rig = build(
        runner_kw={"fail": lambda node, source: "assert still wrong"},
        repairer=FakeRepairer(lambda test, outcome: test.source),
        writer=FakeWriter(sources={"grade": GRADE_TEST}),
        gap_iterations=0,
        repair_attempts=1,
    )
    report = rig.run(functions=("grade",), strategy=Strategy.LLM)
    assert report.suspected_bugs == ()
    assert [d.reason for d in report.dropped] == [DropReason.STILL_FAILING]


# --------------------------------------------------------------------------- the file-store port


def test_the_use_case_touches_files_only_through_the_store(build: Builder) -> None:
    files = InMemoryFiles()
    rig = build(files=files)
    report = rig.run()
    assert report.output_path == rig.default_output
    assert list(files.written) == [rig.default_output]
    assert files.written[rig.default_output].startswith('"""Tests generated by pytestgen')
    assert not rig.default_output.exists(), "nothing reached the real disk"


def test_dry_run_writes_nothing_to_the_store(build: Builder) -> None:
    files = InMemoryFiles()
    build(files=files).run(dry_run=True)
    assert files.written == {}


def test_a_foreign_file_in_the_store_blocks_the_run_before_any_work(build: Builder) -> None:
    rig = build(files=InMemoryFiles())
    rig.use_case._services.files.foreign[rig.default_output] = "x"  # type: ignore[attr-defined]
    with pytest.raises(PytestgenError, match="not generated by pytestgen"):
        rig.run()
    assert rig.runner.requests == []


def test_overwrite_and_generated_files_pass_the_check(build: Builder) -> None:
    previous = InMemoryFiles()
    rig = build(files=previous)
    previous.generated.add(rig.default_output)
    assert rig.run().output_path == rig.default_output
    rig = build(files=InMemoryFiles())
    rig.use_case._services.files.foreign[rig.default_output] = "x"  # type: ignore[attr-defined]
    assert rig.run(overwrite=True).output_path == rig.default_output


def test_existing_tests_come_from_the_store_and_the_output_is_excluded(
    build: Builder, tmp_path: Path
) -> None:
    found = (tmp_path / "tests" / "test_calc_basic.py").resolve()
    files = InMemoryFiles(discovered=[found])
    rig = build(files=files)
    rig.run(existing_tests=None)
    ((root, module_name, exclude),) = files.discover_calls
    assert (root, module_name, exclude) == (rig.root.resolve(), "calc", rig.default_output)
    assert rig.runner.requests[0].existing_tests == (found,)
