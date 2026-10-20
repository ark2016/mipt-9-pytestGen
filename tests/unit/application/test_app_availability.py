from __future__ import annotations

from pathlib import Path

import pytest
from app_factories import returned
from fakes_llm import ScriptedLLM
from orchestration_support import CALC_SOURCE, analyze, write_project
from structlog.testing import capture_logs

from pytestgen.application import (
    CircuitBreakerLLMClient,
    FreeFormWriter,
    InputProposer,
    OracleReviewer,
)
from pytestgen.application.availability import ModelUnavailableError
from pytestgen.domain.llm import ChatMessage, ChatRequest, Role
from pytestgen.errors import LLMConnectionError, LLMResponseError

REQUEST = ChatRequest(messages=(ChatMessage(Role.USER, "hi"),), purpose="ping")


def test_calls_pass_through_while_the_endpoint_works() -> None:
    inner = ScriptedLLM("pong")
    breaker = CircuitBreakerLLMClient(inner)
    assert breaker.complete(REQUEST).content == "pong"
    assert breaker.complete(REQUEST).content == "pong"
    assert breaker.model == "scripted-model"
    assert not breaker.tripped
    assert inner.calls == 2


def test_first_connection_failure_stops_all_further_calls() -> None:
    inner = ScriptedLLM(LLMConnectionError("connection refused"))
    breaker = CircuitBreakerLLMClient(inner)
    with capture_logs() as logs, pytest.raises(LLMConnectionError, match="connection refused"):
        breaker.complete(REQUEST)
    assert breaker.tripped
    for _ in range(3):
        with pytest.raises(ModelUnavailableError, match="connection refused"):
            breaker.complete(REQUEST)
    assert inner.calls == 1, "the dead endpoint is asked once"
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == ["llm_unreachable"]


def test_skipped_calls_are_still_connection_errors() -> None:
    breaker = CircuitBreakerLLMClient(ScriptedLLM(LLMConnectionError("down")))
    with pytest.raises(LLMConnectionError):
        breaker.complete(REQUEST)
    with pytest.raises(LLMConnectionError):
        breaker.complete(REQUEST)


def test_http_and_payload_errors_do_not_trip_it() -> None:
    inner = ScriptedLLM()
    inner.queue("ping", LLMResponseError("HTTP 500"), "fine")
    breaker = CircuitBreakerLLMClient(inner)
    with pytest.raises(LLMResponseError):
        breaker.complete(REQUEST)
    assert breaker.complete(REQUEST).content == "fine"
    assert not breaker.tripped


def test_collaborators_degrade_and_stop_hammering_a_dead_model(tmp_path: Path) -> None:
    module = analyze(tmp_path, write_project(tmp_path, CALC_SOURCE))
    grade = module.function("grade")
    cases = [returned({"score": 95}, "'A'", "grade")]
    inner = ScriptedLLM(LLMConnectionError("connection refused"))
    llm = CircuitBreakerLLMClient(inner)
    assert OracleReviewer(llm).review(module, grade, cases) == tuple(cases)
    assert InputProposer(llm).propose(module, grade, list(grade.branches), {}) == []
    assert FreeFormWriter(llm).write(module, grade).tests == ()
    assert FreeFormWriter(llm).write(module, grade).tests == ()
    assert inner.calls == 1
