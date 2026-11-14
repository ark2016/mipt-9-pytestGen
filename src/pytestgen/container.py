"""Composition root: the only place that knows which adapter implements which port."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from pytestgen.analysis import AstModuleAnalyzer
from pytestgen.application import (
    AnalyzeModule,
    CircuitBreakerLLMClient,
    FreeFormWriter,
    GenerateTests,
    GenerationBudget,
    InputProposer,
    OracleReviewer,
    SkeletonBuilder,
    SlotFiller,
    StageAnnouncer,
    SuiteSelector,
    TestRepairer,
)
from pytestgen.concolic import Z3HintSolver
from pytestgen.config import Settings
from pytestgen.llm import CachingLLMClient, DiskLLMCache, OpenAICompatClient
from pytestgen.ports import LLMCache, ProgressListener
from pytestgen.sandbox import (
    SubprocessExecutionRecorder,
    SubprocessIntrospector,
    SubprocessSuiteRunner,
)
from pytestgen.testfiles import LocalTestFiles


def build_analyze(settings: Settings) -> AnalyzeModule:
    """The ``analyze`` use case: static analysis and the solver, no LLM, no sandbox."""
    return AnalyzeModule(AstModuleAnalyzer(), Z3HintSolver(settings.z3_timeout_ms))


def budget_from(settings: Settings) -> GenerationBudget:
    return GenerationBudget(
        repair_attempts=settings.repair_attempts,
        gap_iterations=settings.gap_iterations,
        run_timeout_s=settings.run_timeout_s,
        test_timeout_s=settings.test_timeout_s,
        max_minimize_runs=settings.max_minimize_runs,
    )


@contextmanager
def build_generate(
    settings: Settings,
    *,
    use_cache: bool = True,
    progress: ProgressListener | None = None,
) -> Iterator[GenerateTests]:
    """Wire the ``generate`` use case; the HTTP client is closed when the block ends."""
    http_client = OpenAICompatClient.from_settings(settings)
    try:
        cache: LLMCache | None = (
            DiskLLMCache(settings.cache_dir) if use_cache and settings.cache_enabled else None
        )
        llm = CachingLLMClient(CircuitBreakerLLMClient(http_client), cache)
        announce = StageAnnouncer(progress)
        budget = budget_from(settings)
        skeleton = SkeletonBuilder(
            Z3HintSolver(settings.z3_timeout_ms),
            SubprocessExecutionRecorder(timeout_s=settings.record_timeout_s),
            max_cases_per_function=settings.max_cases_per_function,
        )
        yield GenerateTests(
            analyzer=AstModuleAnalyzer(),
            introspector=SubprocessIntrospector(),
            runner=SubprocessSuiteRunner(),
            files=LocalTestFiles(),
            filler=SlotFiller(
                skeleton,
                OracleReviewer(llm, max_cases=settings.max_cases_per_function),
                InputProposer(llm),
                FreeFormWriter(llm),
                announce,
            ),
            repairer=TestRepairer(llm),
            selector=SuiteSelector(max_minimize_runs=budget.max_minimize_runs),
            budget=budget,
            meter=llm,
            announce=announce,
        )
    finally:
        http_client.close()
