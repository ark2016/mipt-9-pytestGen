"""Domain model of pytestgen.

Pure data: no I/O, no third-party imports. Every other layer depends on this
module, and this module depends on nothing but the standard library.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# --------------------------------------------------------------------------- analysis


class ParamKind(StrEnum):
    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    VAR_POSITIONAL = "var_positional"
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"


@dataclass(frozen=True, slots=True)
class Parameter:
    """A function parameter as written in the source."""

    name: str
    kind: ParamKind = ParamKind.POSITIONAL_OR_KEYWORD
    annotation: str | None = None
    """Annotation source text, e.g. ``"int"`` or ``"list[str] | None"``."""
    default: str | None = None
    """Default value source text, ``None`` when the parameter has no default."""


@dataclass(frozen=True, slots=True)
class Arc:
    """A control-flow arc in coverage.py terms: ``(from_line, to_line)``.

    Negative ``dst`` means "exit from the code object that starts at ``-dst``",
    exactly as coverage.py reports it.
    """

    src: int
    dst: int

    def __str__(self) -> str:
        return f"{self.src}->{self.dst}"


@dataclass(frozen=True, slots=True)
class Clause:
    """One conjunct of a path condition: ``expr`` must evaluate to ``expected``."""

    expr: str
    """Python expression source text, parseable with ``ast.parse(expr, mode="eval")``."""
    expected: bool


@dataclass(frozen=True, slots=True)
class PathCondition:
    """Conjunction of clauses that must hold for execution to reach a branch outcome.

    Clauses are expressed over the function parameters; straight-line local
    assignments preceding a branch are already substituted by the analyzer.
    """

    clauses: tuple[Clause, ...] = ()

    def __str__(self) -> str:
        if not self.clauses:
            return "True"
        parts = [c.expr if c.expected else f"not ({c.expr})" for c in self.clauses]
        return " and ".join(f"({p})" for p in parts)


class BranchKind(StrEnum):
    IF = "if"
    WHILE = "while"
    FOR = "for"
    IF_EXP = "if_exp"
    MATCH = "match"
    TRY = "try"
    ASSERT = "assert"


@dataclass(frozen=True, slots=True)
class BranchTarget:
    """One outcome of one branch point that a test should exercise."""

    function: str
    """Qualified name of the owning function, e.g. ``"Account.withdraw"``."""
    line: int
    kind: BranchKind
    condition_source: str
    """Source text of the branch condition as written."""
    outcome: bool
    """Which side of the branch: ``True`` for the taken/then side."""
    arc: Arc | None
    """The coverage.py arc this outcome corresponds to, when it can be computed statically."""
    path: PathCondition
    """Condition over parameters to reach this outcome (includes the branch itself)."""
    raises: str | None = None
    """Exception type name when this outcome ends in a ``raise``."""

    @property
    def key(self) -> str:
        return f"{self.function}:{self.line}:{'T' if self.outcome else 'F'}"


@dataclass(frozen=True, slots=True)
class FunctionInfo:
    """A test target: a module-level function or a method."""

    qualname: str
    name: str
    lineno: int
    end_lineno: int
    source: str
    parameters: tuple[Parameter, ...] = ()
    return_annotation: str | None = None
    docstring: str | None = None
    class_name: str | None = None
    is_async: bool = False
    is_static: bool = False
    is_classmethod: bool = False
    is_property: bool = False
    decorators: tuple[str, ...] = ()
    raises: tuple[str, ...] = ()
    """Exception type names raised explicitly in the body."""
    calls: tuple[str, ...] = ()
    """Names of functions called in the body, for prompt context."""
    branches: tuple[BranchTarget, ...] = ()

    @property
    def is_method(self) -> bool:
        return self.class_name is not None

    @property
    def call_parameters(self) -> tuple[Parameter, ...]:
        """Parameters a caller supplies (drops ``self``/``cls`` for bound methods)."""
        if self.is_method and not self.is_static and self.parameters:
            return self.parameters[1:]
        return self.parameters

    def contains_line(self, line: int) -> bool:
        return self.lineno <= line <= self.end_lineno


@dataclass(frozen=True, slots=True)
class ClassInfo:
    name: str
    lineno: int
    end_lineno: int
    bases: tuple[str, ...] = ()
    docstring: str | None = None
    init_parameters: tuple[Parameter, ...] = ()
    methods: tuple[str, ...] = ()
    """Qualified names of methods; the FunctionInfo objects live in ModuleInfo.functions."""


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    path: Path
    module_name: str
    """Dotted import name relative to the project root, e.g. ``"pkg.calc"``."""
    source: str
    functions: tuple[FunctionInfo, ...] = ()
    classes: tuple[ClassInfo, ...] = ()
    imports: tuple[str, ...] = ()
    docstring: str | None = None

    def function(self, qualname: str) -> FunctionInfo:
        for fn in self.functions:
            if fn.qualname == qualname:
                return fn
        raise KeyError(qualname)

    def function_at_line(self, line: int) -> FunctionInfo | None:
        """Innermost function containing ``line``."""
        best: FunctionInfo | None = None
        for fn in self.functions:
            if fn.contains_line(line) and (best is None or fn.lineno >= best.lineno):
                best = fn
        return best


# --------------------------------------------------------------------------- solving and observing


class SolveStatus(StrEnum):
    SAT = "sat"
    UNSAT = "unsat"
    UNSUPPORTED = "unsupported"
    """The condition uses constructs the translator cannot express."""
    UNKNOWN = "unknown"
    """Solver gave up (timeout or incomplete theory)."""


@dataclass(frozen=True, slots=True)
class Solution:
    target: BranchTarget
    status: SolveStatus
    inputs: Mapping[str, object] = field(default_factory=dict)
    """Parameter name -> concrete value; filled only when ``status is SAT``."""
    reason: str = ""


class ObservationKind(StrEnum):
    RETURNED = "returned"
    RAISED = "raised"
    TIMEOUT = "timeout"
    UNSTABLE = "unstable"
    """Two executions on the same input disagreed."""
    NOT_LITERAL = "not_literal"
    """Returned a value whose repr does not round-trip through literal_eval."""


@dataclass(frozen=True, slots=True)
class Observation:
    """Result of executing a function on one input vector in the sandbox."""

    inputs: Mapping[str, object]
    kind: ObservationKind
    value_repr: str | None = None
    """``repr`` of the return value (RETURNED / NOT_LITERAL)."""
    exception_type: str | None = None
    """Qualified exception class name (RAISED)."""
    exception_message: str | None = None
    covered_arcs: tuple[Arc, ...] = ()


# --------------------------------------------------------------------------- tests


class CaseOrigin(StrEnum):
    Z3 = "z3"
    """Inputs came from the solver; expectation recorded from execution."""
    LLM_SLOT = "llm_slot"
    """Inputs proposed by the LLM in a JSON slot; expectation recorded."""
    LLM_FREE = "llm_free"
    """Whole test written by the LLM."""


class OracleVerdict(StrEnum):
    UNREVIEWED = "unreviewed"
    OK = "ok"
    SUSPICIOUS = "suspicious"


@dataclass(frozen=True, slots=True)
class TestCaseSpec:
    """A data-driven test case rendered into a parametrize table."""

    __test__ = False  # not a pytest class

    function: str
    inputs: Mapping[str, object]
    observation: Observation
    origin: CaseOrigin
    target_key: str | None = None
    verdict: OracleVerdict = OracleVerdict.UNREVIEWED
    note: str = ""


@dataclass(frozen=True, slots=True)
class FreeFormTest:
    """A test function written by the LLM as source code."""

    __test__ = False

    name: str
    function: str
    source: str
    origin: CaseOrigin = CaseOrigin.LLM_FREE
    repair_attempts: int = 0


@dataclass(frozen=True, slots=True)
class TestSuite:
    """Everything that will be rendered into one generated test module."""

    __test__ = False

    module: ModuleInfo
    cases: tuple[TestCaseSpec, ...] = ()
    free_tests: tuple[FreeFormTest, ...] = ()


# --------------------------------------------------------------------------- running


class TestStatus(StrEnum):
    __test__ = False

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAILED = "xfailed"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class TestOutcome:
    __test__ = False

    nodeid: str
    status: TestStatus
    message: str = ""
    traceback: str = ""
    duration_s: float = 0.0

    @property
    def test_name(self) -> str:
        """Function name without the parametrize id, e.g. ``test_add`` for ``f.py::test_add[1]``."""
        name = self.nodeid.rsplit("::", 1)[-1]
        return name.split("[", 1)[0]


@dataclass(frozen=True, slots=True)
class FileCoverage:
    path: str
    executed_lines: frozenset[int] = frozenset()
    missing_lines: frozenset[int] = frozenset()
    executed_arcs: frozenset[Arc] = frozenset()
    missing_arcs: frozenset[Arc] = frozenset()

    @property
    def num_statements(self) -> int:
        return len(self.executed_lines) + len(self.missing_lines)

    @property
    def num_branches(self) -> int:
        return len(self.executed_arcs) + len(self.missing_arcs)

    @property
    def line_rate(self) -> float:
        total = self.num_statements
        return len(self.executed_lines) / total if total else 1.0

    @property
    def branch_rate(self) -> float:
        total = self.num_branches
        return len(self.executed_arcs) / total if total else 1.0


@dataclass(frozen=True, slots=True)
class RunResult:
    outcomes: tuple[TestOutcome, ...] = ()
    coverage: FileCoverage | None = None
    duration_s: float = 0.0
    timed_out: bool = False
    collection_error: str | None = None
    """Set when pytest could not even import the test module (syntax/import errors)."""
    output_tail: str = ""

    @property
    def passed(self) -> tuple[TestOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status is TestStatus.PASSED)

    @property
    def failed(self) -> tuple[TestOutcome, ...]:
        bad = {TestStatus.FAILED, TestStatus.ERROR, TestStatus.TIMEOUT}
        return tuple(o for o in self.outcomes if o.status in bad)


# --------------------------------------------------------------------------- reporting


class Strategy(StrEnum):
    HYBRID = "hybrid"
    LLM = "llm"


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    covered_lines: int
    total_lines: int
    covered_branches: int
    total_branches: int

    @property
    def line_percent(self) -> float:
        return 100.0 * self.covered_lines / self.total_lines if self.total_lines else 100.0

    @property
    def branch_percent(self) -> float:
        return 100.0 * self.covered_branches / self.total_branches if self.total_branches else 100.0

    @classmethod
    def from_file(cls, cov: FileCoverage | None) -> CoverageSummary:
        if cov is None:
            return cls(0, 0, 0, 0)
        return cls(
            covered_lines=len(cov.executed_lines),
            total_lines=cov.num_statements,
            covered_branches=len(cov.executed_arcs),
            total_branches=cov.num_branches,
        )


@dataclass(frozen=True, slots=True)
class FunctionCoverageDelta:
    function: str
    before: CoverageSummary
    after: CoverageSummary


@dataclass(frozen=True, slots=True)
class CoverageDelta:
    before: CoverageSummary
    after: CoverageSummary
    per_function: tuple[FunctionCoverageDelta, ...] = ()

    @property
    def line_gain(self) -> float:
        return self.after.line_percent - self.before.line_percent

    @property
    def branch_gain(self) -> float:
        return self.after.branch_percent - self.before.branch_percent


class DropReason(StrEnum):
    STILL_FAILING = "still_failing"
    """Free-form test kept failing after the repair budget."""
    NO_COVERAGE_GAIN = "no_coverage_gain"
    """Removed by --minimize."""
    INVALID_CODE = "invalid_code"
    """LLM output that did not parse or failed the static guard."""
    UNSTABLE = "unstable"
    NOT_LITERAL = "not_literal"


@dataclass(frozen=True, slots=True)
class DroppedTest:
    name: str
    function: str
    reason: DropReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SuspectedBug:
    """A recorded behaviour the LLM oracle reviewer flagged as contradicting intent."""

    function: str
    inputs: Mapping[str, object]
    observed: str
    explanation: str


@dataclass(frozen=True, slots=True)
class LLMUsage:
    requests: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            requests=self.requests + other.requests,
            cache_hits=self.cache_hits + other.cache_hits,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


@dataclass(frozen=True, slots=True)
class OriginStats:
    """How many kept tests came from each source and which arcs they added."""

    origin: CaseOrigin
    tests: int
    arcs_added: int


@dataclass(frozen=True, slots=True)
class GenerationReport:
    module: str
    strategy: Strategy
    output_path: Path | None
    delta: CoverageDelta
    kept_tests: int
    dropped: tuple[DroppedTest, ...] = ()
    suspected_bugs: tuple[SuspectedBug, ...] = ()
    by_origin: tuple[OriginStats, ...] = ()
    llm_usage: LLMUsage = field(default_factory=LLMUsage)
    duration_s: float = 0.0
    model: str = ""
