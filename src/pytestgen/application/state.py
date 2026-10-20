"""Mutable state of one generation run and the immutable batches that feed it."""

from __future__ import annotations

from dataclasses import dataclass, replace

from pytestgen.application.execution import Execution
from pytestgen.application.selection import (
    Minimized,
    Selection,
    replace_free_test,
    without_free_test,
)
from pytestgen.application.skeleton import vector_key
from pytestgen.domain.models import (
    DroppedTest,
    DropReason,
    FreeFormTest,
    SuspectedBug,
    TestCaseSpec,
    TestSuite,
)


@dataclass(frozen=True, slots=True)
class Filled:
    """What one filling step produced for a function."""

    cases: tuple[TestCaseSpec, ...] = ()
    free_tests: tuple[FreeFormTest, ...] = ()
    dropped: tuple[DroppedTest, ...] = ()
    bugs: tuple[SuspectedBug, ...] = ()
    attempted: frozenset[str] = frozenset()
    """Keys of branch targets handed to the input proposer."""
    infeasible: frozenset[str] = frozenset()
    """Keys of branch targets the solver proved unreachable."""

    @property
    def adds_tests(self) -> bool:
        return bool(self.cases or self.free_tests)

    def __add__(self, other: Filled) -> Filled:
        return Filled(
            self.cases + other.cases,
            self.free_tests + other.free_tests,
            self.dropped + other.dropped,
            self.bugs + other.bugs,
            self.attempted | other.attempted,
            self.infeasible | other.infeasible,
        )


@dataclass(frozen=True, slots=True)
class Checkpoint:
    suite: TestSuite
    dropped: int


class RunState:
    """The suite under construction plus the bookkeeping the loops need.

    The suite only changes through the ``apply_*``, ``give_up`` and ``replace_free``
    methods, so the rules (dropped records follow suite changes, rollback keeps what was
    learnt) live in one place.
    """

    def __init__(self, suite: TestSuite) -> None:
        self._suite = suite
        self._dropped: list[DroppedTest] = []
        self._bugs: list[SuspectedBug] = []
        self._attempted: set[str] = set()
        self._infeasible: set[str] = set()
        self._rewritten: dict[str, SuspectedBug] = {}

    # ------------------------------------------------------------------ reading

    @property
    def suite(self) -> TestSuite:
        return self._suite

    @property
    def is_empty(self) -> bool:
        """Nothing left to run."""
        return not self._suite.cases and not self._suite.free_tests

    @property
    def dropped(self) -> tuple[DroppedTest, ...]:
        return tuple(self._dropped)

    @property
    def bugs(self) -> tuple[SuspectedBug, ...]:
        """Oracle findings, then the notes of tests that were rewritten and are still kept."""
        kept = {test.name for test in self._suite.free_tests}
        notes = tuple(bug for name, bug in self._rewritten.items() if name in kept)
        return (*self._bugs, *notes)

    @property
    def attempted(self) -> frozenset[str]:
        return frozenset(self._attempted)

    @property
    def infeasible(self) -> frozenset[str]:
        return frozenset(self._infeasible)

    @property
    def free_names(self) -> set[str]:
        return {test.name for test in self._suite.free_tests}

    def known_vectors(self, function: str) -> set[str]:
        """Canonical keys of the inputs already used by cases of ``function``."""
        return {vector_key(c.inputs) for c in self._suite.cases if c.function == function}

    # ------------------------------------------------------------------ changing

    def absorb(self, filled: Filled) -> None:
        self._suite = replace(
            self._suite,
            cases=self._suite.cases + filled.cases,
            free_tests=self._suite.free_tests + filled.free_tests,
        )
        self._dropped.extend(filled.dropped)
        self._bugs.extend(filled.bugs)
        self._attempted |= filled.attempted
        self._infeasible |= filled.infeasible

    def apply_execution(self, execution: Execution) -> None:
        """Adopt the suite that was actually run (broken free-form tests removed)."""
        self._suite = execution.suite
        self._dropped.extend(execution.dropped)

    def apply_selection(self, selection: Selection) -> None:
        """Adopt the suite without its failing data-driven cases."""
        self._suite = selection.suite
        self._dropped.extend(selection.dropped)

    def apply_minimized(self, result: Minimized) -> None:
        self._suite = result.suite
        self._dropped.extend(result.dropped)

    def give_up(self, test: FreeFormTest, reason: str) -> None:
        """Remove a free-form test that could not be repaired and record why."""
        self._suite = without_free_test(self._suite, test)
        self._dropped.append(
            DroppedTest(test.name, test.function, DropReason.STILL_FAILING, reason)
        )

    def replace_free(self, old: FreeFormTest, new: FreeFormTest) -> None:
        self._suite = replace_free_test(self._suite, old, new)

    def note_rewrite(self, test_name: str, note: SuspectedBug) -> None:
        """Remember that ``test_name`` was rewritten to match the code's behaviour.

        The note is reported with the bugs for as long as the test is part of the suite.
        """
        self._rewritten[test_name] = note

    # ------------------------------------------------------------------ rollback

    def checkpoint(self) -> Checkpoint:
        return Checkpoint(self._suite, len(self._dropped))

    def rollback(self, checkpoint: Checkpoint) -> None:
        """Undo suite changes and drop records since ``checkpoint``.

        What was learnt (attempted targets, suspected bugs) is kept on purpose.
        """
        self._suite = checkpoint.suite
        del self._dropped[checkpoint.dropped :]
