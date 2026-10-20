from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pytestgen import container
from pytestgen.analysis import AstModuleAnalyzer
from pytestgen.application import (
    AnalyzeModule,
    CircuitBreakerLLMClient,
    GenerateTests,
    GenerationBudget,
    SlotFiller,
    SuiteSelector,
    TestRepairer,
)
from pytestgen.concolic import Z3HintSolver
from pytestgen.config import Settings
from pytestgen.container import build_analyze, build_generate
from pytestgen.llm import CachingLLMClient, DiskLLMCache, OpenAICompatClient
from pytestgen.ports import ProgressListener, Stage
from pytestgen.testfiles import LocalTestFiles


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        llm_base_url="http://llm.test/v1",
        llm_model="unit-model",
        cache_dir=tmp_path / "cache",
        repair_attempts=1,
        gap_iterations=0,
        run_timeout_s=33,
        test_timeout_s=4,
        max_cases_per_function=7,
        z3_timeout_ms=1234,
        max_minimize_runs=5,
    )


class SpyCachingClient:
    """Stands in for CachingLLMClient and remembers how it was wired."""

    instances: list[SpyCachingClient] = []  # noqa: RUF012 - reset by the fixture below

    def __init__(self, inner: Any, cache: Any = None) -> None:
        self.inner = inner
        self.cache = cache
        self.model = inner.model
        SpyCachingClient.instances.append(self)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> type[SpyCachingClient]:
    SpyCachingClient.instances = []
    monkeypatch.setattr(container, "CachingLLMClient", SpyCachingClient)
    return SpyCachingClient


def test_build_analyze_wires_the_static_adapters(settings: Settings) -> None:
    use_case = build_analyze(settings)
    assert isinstance(use_case, AnalyzeModule)
    private = vars(use_case)
    assert isinstance(private["_analyzer"], AstModuleAnalyzer)
    assert isinstance(private["_solver"], Z3HintSolver)


def test_build_generate_is_a_context_manager_that_yields_the_use_case(settings: Settings) -> None:
    with build_generate(settings) as use_case:
        assert isinstance(use_case, GenerateTests)


def test_the_http_client_is_closed_when_the_block_ends_even_on_errors(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[int] = []
    real_close = OpenAICompatClient.close
    monkeypatch.setattr(
        OpenAICompatClient, "close", lambda self: (closed.append(1), real_close(self))
    )
    with build_generate(settings):
        assert closed == []
    assert closed == [1]
    with pytest.raises(RuntimeError, match="boom"), build_generate(settings):
        raise RuntimeError("boom")
    assert closed == [1, 1]


def test_budgets_come_from_settings(settings: Settings) -> None:
    with build_generate(settings) as use_case:
        budget = vars(use_case)["_services"].budget
    assert budget == GenerationBudget(
        repair_attempts=1,
        gap_iterations=0,
        run_timeout_s=33,
        test_timeout_s=4,
        max_minimize_runs=5,
    )


def test_collaborators_are_injected_not_built_inside_the_use_case(settings: Settings) -> None:
    with build_generate(settings) as use_case:
        services = vars(use_case)["_services"]
        assert isinstance(services.filler, SlotFiller)
        assert isinstance(services.selector, SuiteSelector)
        assert vars(services.selector)["_max_runs"] == 5
        assert isinstance(services.repairer, TestRepairer)
        assert isinstance(services.files, LocalTestFiles)


def test_the_model_client_is_wrapped_in_cache_and_circuit_breaker(
    settings: Settings, spy: type[SpyCachingClient]
) -> None:
    with build_generate(settings):
        pass
    (caching,) = spy.instances
    assert isinstance(caching.inner, CircuitBreakerLLMClient)
    assert caching.model == "unit-model"


def test_usage_meter_is_the_caching_client(settings: Settings) -> None:
    with build_generate(settings) as use_case:
        meter = vars(use_case)["_meter"]
        assert isinstance(meter, CachingLLMClient)
        assert meter.model == "unit-model"


def test_progress_listener_reaches_the_use_case(settings: Settings) -> None:
    class Listener:
        def stage(self, stage: Stage, message: str) -> None:  # pragma: no cover - not called
            raise AssertionError

    listener: ProgressListener = Listener()
    with build_generate(settings, progress=listener) as use_case:
        assert vars(use_case)["_announce"].listener is listener


def test_disk_cache_is_used_by_default(settings: Settings, spy: type[SpyCachingClient]) -> None:
    with build_generate(settings):
        pass
    cache = spy.instances[0].cache
    assert isinstance(cache, DiskLLMCache)


@pytest.mark.parametrize(
    ("use_cache", "cache_enabled"), [(False, True), (True, False), (False, False)]
)
def test_cache_is_off_when_disabled_by_flag_or_settings(
    settings: Settings, spy: type[SpyCachingClient], use_cache: bool, cache_enabled: bool
) -> None:
    settings = settings.model_copy(update={"cache_enabled": cache_enabled})
    with build_generate(settings, use_cache=use_cache):
        pass
    assert spy.instances[0].cache is None
