"""Fakes and builders for the orchestration tests (no sandbox, no network, no LLM)."""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pytestgen.analysis import AstModuleAnalyzer
from pytestgen.application.collaborators import WrittenTests
from pytestgen.codegen import make_free_form_tests
from pytestgen.domain.models import (
    Arc,
    BranchTarget,
    FileCoverage,
    FreeFormTest,
    FunctionInfo,
    IntrospectionResult,
    ModuleInfo,
    Observation,
    ObservationKind,
    OracleVerdict,
    RunResult,
    Solution,
    SolveStatus,
    TestCaseSpec,
    TestOutcome,
    TestStatus,
)
from pytestgen.ports import RunRequest, Stage

CALC_SOURCE = '''\
"""Small module the orchestration tests generate tests for."""


def grade(score: int) -> str:
    """Return the letter grade for a score: A from 90, B from 50, otherwise F."""
    if score >= 90:
        return "A"
    if score >= 50:
        return "B"
    return "F"


def ident(x: int) -> int:
    return x


def _hidden(x: int) -> int:
    return x


class Counter:
    def __init__(self, start: int = 0) -> None:
        self.value = start

    def bump(self, by: int) -> int:
        self.value += by
        return self.value

    @staticmethod
    def twice(n: int) -> int:
        return n * 2

    @property
    def doubled(self) -> int:
        return self.value * 2
'''

TESTS_DIR = "pytestgen_tests"


def write_project(root: Path, source: str = CALC_SOURCE, relative: str = "calc.py") -> Path:
    """Write ``source`` as a module of a throw-away project; returns the module path."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def analyze(root: Path, path: Path) -> ModuleInfo:
    return AstModuleAnalyzer().analyze(path, root)


# --------------------------------------------------------------------------- analysis fakes


class FakeAnalyzer:
    def __init__(self, module: ModuleInfo) -> None:
        self.module = module
        self.calls = 0

    def analyze(self, path: Path, project_root: Path) -> ModuleInfo:
        self.calls += 1
        return self.module


class FakeIntrospector:
    def __init__(self, *, importable: bool = True, error: str | None = None) -> None:
        self._result = IntrospectionResult(importable=importable, error=error)

    def introspect(self, project_root: Path, module: ModuleInfo) -> IntrospectionResult:
        return self._result


class Stages:
    """ProgressListener that remembers the stages it saw."""

    def __init__(self) -> None:
        self.seen: list[tuple[Stage, str]] = []

    def stage(self, stage: Stage, message: str) -> None:
        self.seen.append((stage, message))

    @property
    def names(self) -> list[Stage]:
        return [s for s, _ in self.seen]


# --------------------------------------------------------------------------- symbolic fakes


class ScriptedSolver:
    """Answers per target key; unlisted targets are SAT with ``default`` inputs."""

    def __init__(
        self,
        answers: Mapping[str, Solution | Exception] | None = None,
        default: Mapping[str, object] | None = None,
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self.targets: list[BranchTarget] = []

    def solve(self, function: FunctionInfo, target: BranchTarget) -> Solution:
        self.targets.append(target)
        answer = self._answers.get(target.key)
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            return answer
        if self._default is None:
            return Solution(target, SolveStatus.UNKNOWN, reason="no script")
        return Solution(target, SolveStatus.SAT, inputs=dict(self._default))


def sat(target: BranchTarget, **inputs: object) -> Solution:
    return Solution(target, SolveStatus.SAT, inputs=inputs)


def status(target: BranchTarget, state: SolveStatus, reason: str = "") -> Solution:
    return Solution(target, state, reason=reason)


class InProcessRecorder:
    """Records behaviour by running the module source in this process (tests only)."""

    def __init__(
        self,
        *,
        scripted: Mapping[str, Observation] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, list[dict[str, object]]]] = []
        self._scripted = scripted or {}
        self._error = error

    def observe(
        self,
        project_root: Path,
        module: ModuleInfo,
        function: FunctionInfo,
        inputs: Sequence[Mapping[str, object]],
    ) -> list[Observation]:
        self.calls.append((function.qualname, [dict(v) for v in inputs]))
        if self._error is not None:
            raise self._error
        namespace: dict[str, Any] = {"__name__": module.module_name}
        exec(compile(module.source, str(module.path), "exec"), namespace)  # noqa: S102
        target: Any = namespace[function.qualname.split(".")[0]]
        for part in function.qualname.split(".")[1:]:
            target = getattr(target, part)
        return [self._observe(target, dict(vector)) for vector in inputs]

    def _observe(self, target: Any, vector: dict[str, object]) -> Observation:
        for fragment, observation in self._scripted.items():
            if fragment in repr(vector):
                return replace(observation, inputs=vector)
        try:
            value = target(**vector)
        except Exception as exc:  # the function under test may raise anything
            return Observation(
                vector,
                ObservationKind.RAISED,
                exception_type=type(exc).__qualname__,
                exception_message=str(exc),
            )
        return Observation(vector, ObservationKind.RETURNED, value_repr=repr(value))


# --------------------------------------------------------------------------- the runner fake


def _parametrize_ids(node: ast.FunctionDef) -> list[str]:
    ids: list[str] = []
    for decorator in node.decorator_list:
        for call in ast.walk(decorator):
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "param":
                for keyword in call.keywords:
                    if keyword.arg == "id" and isinstance(keyword.value, ast.Constant):
                        ids.append(str(keyword.value.value))
    return ids


def node_names(source: str) -> list[str]:
    """Pytest node names (without file) the module would produce."""
    names: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            ids = _parametrize_ids(node)
            names.extend([f"{node.name}[{i}]" for i in ids] or [node.name])
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names.extend(
                f"{node.name}::{item.name}"
                for item in node.body
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
            )
    return names


@dataclass(slots=True)
class CoverageModel:
    """Which lines/arcs each test covers; the universe is everything ever mentioned.

    ``covers`` maps a node name (``test_x[z3-1]``), its function name (``test_x``) or an
    existing test's name to the lines (ints) and arcs it executes.
    """

    covers: Mapping[str, Collection[int | Arc]]
    extra_lines: Collection[int] = ()
    extra_arcs: Collection[Arc] = ()

    def of(self, nodes: Iterable[str]) -> FileCoverage:
        universe = [item for items in self.covers.values() for item in items]
        all_lines = {i for i in universe if isinstance(i, int)} | set(self.extra_lines)
        all_arcs = {i for i in universe if isinstance(i, Arc)} | set(self.extra_arcs)
        hit: set[int | Arc] = set()
        for node in nodes:
            base = node.split("[", 1)[0].split("::")[-1]
            hit |= set(self.covers.get(node, ())) | set(self.covers.get(base, ()))
            hit |= set(self.covers.get(node.split("[", 1)[0], ()))
        lines = {i for i in hit if isinstance(i, int)}
        arcs = {i for i in hit if isinstance(i, Arc)}
        return FileCoverage(
            path="calc.py",
            executed_lines=frozenset(lines),
            missing_lines=frozenset(all_lines - lines),
            executed_arcs=frozenset(arcs),
            missing_arcs=frozenset(all_arcs - arcs),
        )


FailPolicy = Callable[[str, str], str | None]
"""``(node name, generated source) -> failure message`` or ``None`` when the test passes."""


class FakeRunner:
    """SuiteRunner with scripted coverage and failures; remembers every request."""

    def __init__(
        self,
        model: CoverageModel | None = None,
        *,
        fail: FailPolicy | None = None,
        collection_error: Callable[[str], str | None] | None = None,
        timed_out: bool = False,
        existing_names: Sequence[str] = ("test_existing",),
        failing_existing: Collection[str] = (),
    ) -> None:
        self.model = model or CoverageModel({})
        self._fail = fail
        self._collection_error = collection_error
        self._timed_out = timed_out
        self._existing_names = existing_names
        self._failing_existing = frozenset(failing_existing)
        self.requests: list[RunRequest] = []

    def run(self, request: RunRequest) -> RunResult:
        self.requests.append(request)
        sources = request.test_sources
        text = "\n".join(sources.values())
        if self._collection_error:
            error = self._collection_error(text)
            if error:
                return RunResult(collection_error=error)
        outcomes: list[TestOutcome] = []
        names: list[str] = []
        for file_name, source in sources.items():
            for name in node_names(source):
                names.append(name)
                message = self._fail(name, source) if self._fail else None
                outcomes.append(_outcome(f"{TESTS_DIR}/{file_name}::{name}", message))
        for path in request.existing_tests:
            for name in self._existing_names:
                names.append(name)
                broken = "existing test is red" if name in self._failing_existing else None
                outcomes.append(_outcome(f"{path.name}::{name}", broken))
        if not outcomes:
            return RunResult()
        return RunResult(
            outcomes=tuple(outcomes),
            coverage=self.model.of(names),
            timed_out=self._timed_out,
        )

    @property
    def generated_sources(self) -> list[str]:
        return ["\n".join(r.test_sources.values()) for r in self.requests if r.test_sources]


def _outcome(nodeid: str, message: str | None) -> TestOutcome:
    if message is None:
        return TestOutcome(nodeid, TestStatus.PASSED)
    return TestOutcome(nodeid, TestStatus.FAILED, message=message, traceback=f"E   {message}")


# --------------------------------------------------------------------------- LLM collaborator fakes


@dataclass(slots=True)
class FakeReviewer:
    """Marks cases suspicious when ``flag(case)`` returns an explanation."""

    flag: Callable[[TestCaseSpec], str | None] = lambda case: None
    calls: list[list[TestCaseSpec]] = field(default_factory=list)

    def review(
        self, module: ModuleInfo, function: FunctionInfo, cases: Sequence[TestCaseSpec]
    ) -> tuple[TestCaseSpec, ...]:
        self.calls.append(list(cases))
        result = []
        for case in cases:
            why = self.flag(case)
            if why:
                result.append(replace(case, verdict=OracleVerdict.SUSPICIOUS, note=why))
            else:
                result.append(replace(case, verdict=OracleVerdict.OK))
        return tuple(result)


@dataclass(slots=True)
class FakeProposer:
    """Returns ``vectors`` (or ``vectors(function, targets)``) and remembers what it was asked."""

    vectors: (
        list[dict[str, object]]
        | Callable[[FunctionInfo, Sequence[BranchTarget]], list[dict[str, object]]]
    ) = field(default_factory=list)
    calls: list[tuple[str, list[str], dict[str, str]]] = field(default_factory=list)

    def propose(
        self,
        module: ModuleInfo,
        function: FunctionInfo,
        targets: Sequence[BranchTarget],
        reasons: Mapping[str, str],
    ) -> list[dict[str, object]]:
        self.calls.append((function.qualname, [t.key for t in targets], dict(reasons)))
        if callable(self.vectors):
            return [dict(v) for v in self.vectors(function, targets)]
        return [dict(v) for v in self.vectors]


@dataclass(slots=True)
class FakeWriter:
    """Writes the canned module source per function qualname (or ``default``)."""

    sources: Mapping[str, str] = field(default_factory=dict)
    default: str | None = None
    sequence: dict[str, list[str]] = field(default_factory=dict)
    """Per function, sources handed out call by call (before ``sources``/``default``)."""
    calls: list[tuple[str, tuple[int, ...], frozenset[str]]] = field(default_factory=list)

    def write(
        self,
        module: ModuleInfo,
        function: FunctionInfo,
        *,
        uncovered_lines: Sequence[int] = (),
        avoid_names: Collection[str] = (),
    ) -> WrittenTests:
        self.calls.append((function.qualname, tuple(uncovered_lines), frozenset(avoid_names)))
        queue = self.sequence.get(function.qualname)
        source = queue.pop(0) if queue else self.sources.get(function.qualname, self.default)
        if source is None:
            return WrittenTests()
        return WrittenTests(tests=make_free_form_tests(function.qualname, textwrap.dedent(source)))


@dataclass(slots=True)
class FakeRepairer:
    """Replaces a test's source with ``fix(test, outcome)``; ``None`` means the model failed."""

    fix: Callable[[FreeFormTest, TestOutcome], str | None] = lambda test, outcome: None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def repair(
        self,
        module: ModuleInfo,
        function: FunctionInfo | None,
        test: FreeFormTest,
        outcome: TestOutcome,
    ) -> FreeFormTest | None:
        self.calls.append((test.name, outcome.message))
        source = self.fix(test, outcome)
        if source is None:
            return None
        return replace(test, source=source, repair_attempts=test.repair_attempts + 1)


class InMemoryFiles:
    """``ProjectTestFiles`` without a file system: what was written is in ``written``."""

    def __init__(
        self,
        *,
        foreign: Collection[Path] = (),
        generated: Collection[Path] = (),
        discovered: Sequence[Path] = (),
    ) -> None:
        self.foreign = dict.fromkeys(foreign, "def test_mine(): pass\n")
        self.generated = set(generated)
        self.discovered = tuple(discovered)
        self.written: dict[Path, str] = {}
        self.discover_calls: list[tuple[Path, str, Path]] = []

    def exists(self, path: Path) -> bool:
        return path in self.foreign or path in self.generated or path in self.written

    def is_generated(self, path: Path) -> bool:
        return path in self.generated or path in self.written

    def discover(
        self, project_root: Path, module_name: str, exclude: Path, names: Collection[str] = ()
    ) -> tuple[Path, ...]:
        self.discover_calls.append((project_root, module_name, exclude))
        return self.discovered

    def write(self, path: Path, text: str) -> Path:
        self.written[path] = text
        return path
