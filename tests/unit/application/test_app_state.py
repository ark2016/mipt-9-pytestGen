from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from app_factories import returned
from orchestration_support import CALC_SOURCE, analyze, write_project

from pytestgen.application.execution import Execution
from pytestgen.application.selection import Minimized, Selection
from pytestgen.application.state import Filled, RunState
from pytestgen.domain.models import (
    DroppedTest,
    DropReason,
    FreeFormTest,
    ModuleInfo,
    RunResult,
    SuspectedBug,
    TestSuite,
)


@pytest.fixture
def module(tmp_path: Path) -> ModuleInfo:
    return analyze(tmp_path, write_project(tmp_path, CALC_SOURCE))


def free(name: str, source: str = "") -> FreeFormTest:
    return FreeFormTest(name, "grade", source or f"def {name}():\n    assert 1\n")


def dropped(name: str) -> DroppedTest:
    return DroppedTest(name, "grade", DropReason.UNSTABLE)


def bug(function: str = "grade") -> SuspectedBug:
    return SuspectedBug(function, {"score": 1}, "'F'", "why")


@pytest.fixture
def state(module: ModuleInfo) -> RunState:
    return RunState(TestSuite(module))


def test_fields_are_read_only_views(state: RunState) -> None:
    for name in ("suite", "dropped", "bugs", "attempted", "infeasible"):
        with pytest.raises(AttributeError):
            setattr(state, name, ())
    assert state.is_empty


def test_absorb_collects_everything_a_step_produced(state: RunState) -> None:
    state.absorb(
        Filled(
            cases=(returned({"score": 1}, "'F'", "grade"),),
            free_tests=(free("test_a"),),
            dropped=(dropped("x"),),
            bugs=(bug(),),
            attempted=frozenset({"k1"}),
            infeasible=frozenset({"k2"}),
        )
    )
    assert not state.is_empty
    assert [t.name for t in state.suite.free_tests] == ["test_a"]
    assert state.dropped == (dropped("x"),)
    assert state.bugs == (bug(),)
    assert (state.attempted, state.infeasible) == (frozenset({"k1"}), frozenset({"k2"}))
    assert state.free_names == {"test_a"}
    assert state.known_vectors("grade") == {"[('score', 1)]"}


def test_execution_selection_and_minimization_replace_the_suite_and_record_drops(
    state: RunState, module: ModuleInfo
) -> None:
    keep = free("test_keep")
    state.absorb(Filled(free_tests=(keep, free("test_gone"))))
    state.apply_execution(
        Execution(RunResult(), replace(state.suite, free_tests=(keep,)), (dropped("a"),))
    )
    assert state.suite.free_tests == (keep,)
    state.apply_selection(Selection(state.suite, (dropped("b"),)))
    state.apply_minimized(Minimized(state.suite, (dropped("c"),), None, 1))
    assert [d.name for d in state.dropped] == ["a", "b", "c"]


def test_give_up_removes_the_test_and_says_why(state: RunState) -> None:
    test = free("test_a")
    state.absorb(Filled(free_tests=(test, free("test_b"))))
    state.give_up(test, "assert 1 == 2")
    assert [t.name for t in state.suite.free_tests] == ["test_b"]
    (record,) = state.dropped
    assert (record.name, record.reason, record.detail) == (
        "test_a",
        DropReason.STILL_FAILING,
        "assert 1 == 2",
    )


def test_replace_free_swaps_one_test(state: RunState) -> None:
    old, other = free("test_a"), free("test_b")
    state.absorb(Filled(free_tests=(old, other)))
    new = replace(old, source="def test_a():\n    assert 2\n", repair_attempts=1)
    state.replace_free(old, new)
    assert state.suite.free_tests == (new, other)


def test_rollback_undoes_the_suite_but_keeps_what_was_learnt(state: RunState) -> None:
    state.absorb(Filled(free_tests=(free("test_a"),), dropped=(dropped("early"),)))
    checkpoint = state.checkpoint()
    state.absorb(
        Filled(
            free_tests=(free("test_b"),),
            dropped=(dropped("late"),),
            bugs=(bug(),),
            attempted=frozenset({"k"}),
        )
    )
    state.rollback(checkpoint)
    assert state.free_names == {"test_a"}
    assert [d.name for d in state.dropped] == ["early"]
    assert state.bugs == (bug(),) and state.attempted == {"k"}


def test_rewrite_notes_are_reported_only_while_their_test_is_kept(state: RunState) -> None:
    test = free("test_a")
    state.absorb(Filled(free_tests=(test,), bugs=(bug("other"),)))
    note = bug("grade")
    state.note_rewrite("test_a", note)
    assert state.bugs == (bug("other"), note)
    state.give_up(test, "still red")
    assert state.bugs == (bug("other"),)
