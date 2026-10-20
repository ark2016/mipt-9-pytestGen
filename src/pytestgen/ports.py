"""Ports: the interfaces the application layer depends on.

Adapters (``analysis``, ``concolic``, ``llm``, ``sandbox``, ``report``) implement
these protocols; the composition root (``container``) wires concrete adapters
into the use cases. Tests substitute fakes at this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pytestgen.domain.llm import ChatRequest, ChatResponse
from pytestgen.domain.models import (
    BranchTarget,
    FunctionInfo,
    GenerationReport,
    IntrospectionResult,
    ModuleInfo,
    Observation,
    RunResult,
    Solution,
)

# --------------------------------------------------------------------------- analysis


@runtime_checkable
class ModuleAnalyzer(Protocol):
    """Static analysis of one Python module. Must not import or execute it."""

    def analyze(self, path: Path, project_root: Path) -> ModuleInfo: ...


@runtime_checkable
class ModuleIntrospector(Protocol):
    """Imports the module in the sandbox and inspects it at runtime (``inspect`` module).

    Used to fail fast on non-importable modules and to resolve signatures that
    decorators or dynamic code change compared to the source text.
    """

    def introspect(self, project_root: Path, module: ModuleInfo) -> IntrospectionResult: ...


# --------------------------------------------------------------------------- symbolic


@runtime_checkable
class HintSolver(Protocol):
    """Finds concrete parameter values that satisfy a branch target's path condition."""

    def solve(self, function: FunctionInfo, target: BranchTarget) -> Solution: ...


# --------------------------------------------------------------------------- LLM


@runtime_checkable
class LLMClient(Protocol):
    """A chat-completion model behind an OpenAI-compatible API."""

    @property
    def model(self) -> str: ...

    def complete(self, request: ChatRequest) -> ChatResponse: ...


@runtime_checkable
class LLMCache(Protocol):
    """Response cache keyed by (model, request)."""

    def get(self, model: str, request: ChatRequest) -> ChatResponse | None: ...

    def put(self, model: str, request: ChatRequest, response: ChatResponse) -> None: ...


# --------------------------------------------------------------------------- sandbox


@dataclass(frozen=True, slots=True)
class RunRequest:
    """What to run in the sandbox.

    The sandbox copies ``project_root`` (or the relevant part of it) into a temporary
    workspace, writes ``test_sources`` there, runs pytest with branch coverage
    measured on ``target`` only, and never touches the original files.
    """

    project_root: Path
    target: Path
    """Module whose coverage is measured (absolute path inside ``project_root``)."""
    test_sources: Mapping[str, str] = field(default_factory=dict)
    """Relative file name -> test module source, e.g. ``{"test_calc_gen.py": "..."}``."""
    existing_tests: tuple[Path, ...] = ()
    """Already existing test files (inside ``project_root``) to run as well."""
    timeout_s: float = 120.0
    per_test_timeout_s: float = 10.0


@runtime_checkable
class SuiteRunner(Protocol):
    """Runs pytest in an isolated subprocess and reports per-test outcomes + coverage."""

    def run(self, request: RunRequest) -> RunResult: ...


@runtime_checkable
class ExecutionRecorder(Protocol):
    """Executes a function on concrete inputs in the sandbox and records what happened.

    Supports module-level functions and static methods. Each input vector is
    executed twice to detect non-determinism (``ObservationKind.UNSTABLE``).
    """

    def observe(
        self,
        project_root: Path,
        module: ModuleInfo,
        function: FunctionInfo,
        inputs: Sequence[Mapping[str, object]],
    ) -> list[Observation]: ...


# --------------------------------------------------------------------------- reporting


@runtime_checkable
class ReportRenderer(Protocol):
    """Turns a GenerationReport into text (markdown, JSON, ...)."""

    @property
    def format_name(self) -> str: ...

    def render(self, report: GenerationReport) -> str: ...


class Stage(StrEnum):
    ANALYZE = "analyze"
    BASELINE = "baseline"
    SOLVE = "solve"
    RECORD = "record"
    LLM_SLOTS = "llm_slots"
    RUN = "run"
    REPAIR = "repair"
    GAP = "gap"
    SELECT = "select"
    WRITE = "write"


@runtime_checkable
class ProgressListener(Protocol):
    """Receives human-oriented progress events (the CLI renders them with rich)."""

    def stage(self, stage: Stage, message: str) -> None: ...
