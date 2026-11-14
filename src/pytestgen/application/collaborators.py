"""Ports owned by the application layer: the LLM collaborators, the test-file store, usage.

``OracleReviewer``, ``InputProposer``, ``FreeFormWriter`` and ``TestRepairer`` satisfy the
LLM protocols structurally; ``LocalTestFiles`` satisfies ``ProjectTestFiles``;
``CachingLLMClient`` satisfies ``UsageMeter``. Only domain types are imported here.

Error contract of the LLM collaborators: model trouble (unreachable endpoint, malformed
or unsafe answer) is handled **inside** the collaborator, logged as a warning, and turned
into an empty result. They never raise ``LLMError``; the use cases do not catch it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pytestgen.domain.models import (
    BranchTarget,
    DroppedTest,
    FreeFormTest,
    FunctionInfo,
    LLMUsage,
    ModuleInfo,
    TestCaseSpec,
    TestOutcome,
)


@dataclass(frozen=True, slots=True)
class WrittenTests:
    """Result of :meth:`TestWriter.write`: accepted tests and those the guard refused."""

    tests: tuple[FreeFormTest, ...] = ()
    rejected: tuple[DroppedTest, ...] = ()
    """Reason ``INVALID_CODE``, with the violations as detail."""


class OracleReview(Protocol):
    """Judges recorded behaviours against the function's documented intent."""

    def review(
        self, module: ModuleInfo, function: FunctionInfo, cases: Sequence[TestCaseSpec]
    ) -> tuple[TestCaseSpec, ...]:
        """Same cases in the same order (equal length), with ``verdict`` and ``note`` filled in.

        Cases the model did not judge stay ``UNREVIEWED``. Never raises ``LLMError``: on any
        model failure the cases come back unchanged.
        """
        ...


class InputSource(Protocol):
    """Proposes argument vectors for branches the solver could not handle."""

    def propose(
        self,
        module: ModuleInfo,
        function: FunctionInfo,
        targets: Sequence[BranchTarget],
        reasons: Mapping[str, str],
    ) -> list[dict[str, object]]:
        """Validated, deduplicated vectors (parameter name -> value).

        Never raises ``LLMError``: the result is empty on any model failure.
        """
        ...


class TestWriter(Protocol):
    """Writes free-form pytest functions for one function or method."""

    def write(
        self,
        module: ModuleInfo,
        function: FunctionInfo,
        *,
        uncovered_lines: Sequence[int] = (),
        avoid_names: Collection[str] = (),
    ) -> WrittenTests:
        """Guard-approved tests with names outside ``avoid_names``, plus the rejected ones.

        Never raises ``LLMError``: the result is empty on any model failure.
        """
        ...


class Repairer(Protocol):
    """Fixes a failing free-form test using its traceback."""

    def repair(
        self,
        module: ModuleInfo,
        function: FunctionInfo | None,
        test: FreeFormTest,
        outcome: TestOutcome,
    ) -> FreeFormTest | None:
        """The test with the same name and ``repair_attempts`` increased by one.

        ``None`` when the model failed or produced invalid code. Never raises ``LLMError``.
        """
        ...


class ProjectTestFiles(Protocol):
    """Where test files live: existing ones are found, the generated one is written."""

    def exists(self, path: Path) -> bool: ...

    def is_generated(self, path: Path) -> bool:
        """Whether ``path`` is a file pytestgen wrote earlier (safe to replace)."""
        ...

    def discover(
        self, project_root: Path, module_name: str, exclude: Path, names: Collection[str] = ()
    ) -> tuple[Path, ...]:
        """The project's own test files that import the module, without ``exclude``.

        ``names`` are the module's public top-level names: a test importing one of them
        from a parent package (a re-export) counts as importing the module.
        """
        ...

    def write(self, path: Path, text: str) -> Path:
        """Store ``text`` at ``path`` (creating directories) and return the path."""
        ...


class UsageMeter(Protocol):
    """Accounts for what the run asked of the model."""

    @property
    def usage(self) -> LLMUsage: ...

    @property
    def model(self) -> str: ...
