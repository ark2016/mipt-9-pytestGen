"""Rendering a suite and running it in the sandbox, with recovery from collection errors."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from pytestgen.application.texts import last_line
from pytestgen.codegen import render_test_module
from pytestgen.domain.models import (
    DroppedTest,
    DropReason,
    FreeFormTest,
    RunResult,
    TestSuite,
)
from pytestgen.errors import PytestgenError
from pytestgen.log import get_logger
from pytestgen.ports import RunRequest, SuiteRunner

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Execution:
    """A run plus the suite that was actually run (broken free-form tests removed)."""

    result: RunResult
    suite: TestSuite
    dropped: tuple[DroppedTest, ...] = ()


class SuiteExecutor:
    """Renders a :class:`TestSuite` into the generated test file and runs it with the rest.

    The generated module always goes into the sandbox under ``file_name``. When pytest
    cannot even import it, the culprits are free-form tests (a hallucinated import, for
    example): they are found by bisection, dropped as ``INVALID_CODE``, and the rest is
    run again.
    """

    def __init__(
        self,
        runner: SuiteRunner,
        *,
        project_root: Path,
        target: Path,
        file_name: str,
        existing_tests: tuple[Path, ...] = (),
        timeout_s: float = 120.0,
        per_test_timeout_s: float = 10.0,
    ) -> None:
        self._runner = runner
        self._project_root = project_root
        self._target = target
        self._file_name = file_name
        self._existing = existing_tests
        self._timeout_s = timeout_s
        self._per_test_timeout_s = per_test_timeout_s

    @property
    def file_name(self) -> str:
        return self._file_name

    @property
    def existing_tests(self) -> tuple[Path, ...]:
        return self._existing

    def ignore_existing_tests(self) -> None:
        """Stop running the project's own tests (they are broken and would abort the run)."""
        self._existing = ()

    def run_existing(self) -> RunResult | None:
        """Run only the project's own tests; ``None`` when there are none."""
        if not self._existing:
            return None
        return self._execute({})

    def render(self, suite: TestSuite) -> str:
        """Text of the generated test module for ``suite``."""
        try:
            return render_test_module(suite)
        except ValueError as exc:
            raise PytestgenError(f"cannot render the generated tests: {exc}") from exc

    def run_plain(self, suite: TestSuite) -> RunResult:
        """Run ``suite`` as is, without recovering from collection errors."""
        return self._execute(self._sources(suite))

    def run(self, suite: TestSuite) -> Execution:
        """Run ``suite`` together with the existing tests."""
        result = self._execute(self._sources(suite))
        if not result.collection_error or not suite.free_tests:
            return Execution(result, suite)
        return self._isolate(suite, result.collection_error)

    # ------------------------------------------------------------------ internals

    def _execute(self, sources: dict[str, str]) -> RunResult:
        request = RunRequest(
            project_root=self._project_root,
            target=self._target,
            test_sources=sources,
            existing_tests=self._existing,
            timeout_s=self._timeout_s,
            per_test_timeout_s=self._per_test_timeout_s,
        )
        result = self._runner.run(request)
        _log.info(
            "suite_run",
            tests=len(result.outcomes),
            failed=len(result.failed),
            timed_out=result.timed_out,
            collection_error=bool(result.collection_error),
            duration_s=round(result.duration_s, 2),
        )
        return result

    def _sources(self, suite: TestSuite) -> dict[str, str]:
        if not suite.cases and not suite.free_tests:
            return {}
        return {self._file_name: self.render(suite)}

    def _isolate(self, suite: TestSuite, error: str) -> Execution:
        _log.warning("generated_module_not_collectable", error=last_line(error))
        culprits = self._culprits(suite, suite.free_tests)
        bad = {id(test) for test, _ in culprits}
        kept = replace(suite, free_tests=tuple(t for t in suite.free_tests if id(t) not in bad))
        result = self._execute(self._sources(kept))
        if result.collection_error:
            raise PytestgenError(
                f"generated tests cannot be collected: {last_line(result.collection_error)}"
            )
        dropped = tuple(
            DroppedTest(test.name, test.function, DropReason.INVALID_CODE, last_line(why))
            for test, why in culprits
        )
        return Execution(result, kept, dropped)

    def _culprits(
        self, suite: TestSuite, group: tuple[FreeFormTest, ...]
    ) -> list[tuple[FreeFormTest, str]]:
        """Free-form tests that break collection on their own (bisection)."""
        probe = replace(suite, cases=(), free_tests=group)
        why = self._execute(self._sources(probe)).collection_error
        if not why:
            return []
        if len(group) == 1:
            return [(group[0], why)]
        middle = len(group) // 2
        return self._culprits(suite, group[:middle]) + self._culprits(suite, group[middle:])
