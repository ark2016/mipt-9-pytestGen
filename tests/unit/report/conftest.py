from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from pytestgen.domain.models import (
    CaseOrigin,
    CoverageDelta,
    CoverageSummary,
    DroppedTest,
    DropReason,
    FunctionCoverageDelta,
    GenerationReport,
    LLMUsage,
    OriginStats,
    Strategy,
    SuspectedBug,
)


def _make_function_delta(name: str, before: int, after: int) -> FunctionCoverageDelta:
    return FunctionCoverageDelta(
        name,
        CoverageSummary(before, 10, before // 5, 4),
        CoverageSummary(after, 10, after // 5, 4),
    )


FunctionDeltaFactory = Callable[[str, int, int], FunctionCoverageDelta]


@pytest.fixture
def make_function_delta() -> FunctionDeltaFactory:
    """Build a function with 10 lines and 4 branches; ``before``/``after`` lines are covered."""
    return _make_function_delta


@pytest.fixture
def full_report() -> GenerationReport:
    return GenerationReport(
        module="pkg.calc",
        strategy=Strategy.HYBRID,
        output_path=Path("tests") / "test_calc_gen.py",
        delta=CoverageDelta(
            before=CoverageSummary(10, 50, 2, 10),
            after=CoverageSummary(31, 50, 8, 10),
            per_function=(
                _make_function_delta("add", 5, 5),
                _make_function_delta("divide", 0, 10),
                _make_function_delta("Account.withdraw", 5, 8),
            ),
        ),
        kept_tests=7,
        dropped=(
            DroppedTest("test_a", "add", DropReason.STILL_FAILING, "AssertionError: 1 != 2"),
            DroppedTest("test_b", "divide", DropReason.STILL_FAILING),
            DroppedTest("test_c", "add", DropReason.INVALID_CODE, "syntax | error"),
        ),
        suspected_bugs=(
            SuspectedBug(
                function="divide",
                inputs={"a": 1, "b": 0},
                observed="returns 0",
                explanation="division by zero | should raise",
            ),
        ),
        by_origin=(
            OriginStats(CaseOrigin.Z3, 4, 6),
            OriginStats(CaseOrigin.LLM_FREE, 3, 2),
        ),
        llm_usage=LLMUsage(requests=5, cache_hits=2, prompt_tokens=1200, completion_tokens=300),
        duration_s=12.34,
        model="qwen2.5-coder",
    )


@pytest.fixture
def minimal_report() -> GenerationReport:
    return GenerationReport(
        module="m",
        strategy=Strategy.LLM,
        output_path=None,
        delta=CoverageDelta(CoverageSummary(0, 0, 0, 0), CoverageSummary(0, 0, 0, 0)),
        kept_tests=0,
    )
