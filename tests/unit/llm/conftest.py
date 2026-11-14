from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from llm_test_support import BASE_URL, SCHEMA

from pytestgen.config import JsonMode
from pytestgen.domain.llm import ChatMessage, ChatRequest, Role
from pytestgen.llm import OpenAICompatClient


@pytest.fixture
def llm_fixture_dir() -> Path:
    """Recorded llama.cpp chat-completion responses shared with later tests."""
    return Path(__file__).resolve().parents[2] / "fixtures" / "llm"


@pytest.fixture
def make_request() -> Callable[..., ChatRequest]:
    def factory(prompt: str = "say hi", **overrides: Any) -> ChatRequest:
        messages = (
            ChatMessage(Role.SYSTEM, "You are terse."),
            ChatMessage(Role.USER, prompt),
        )
        return ChatRequest(messages=messages, **overrides)

    return factory


@pytest.fixture
def request_with_schema(make_request: Callable[..., ChatRequest]) -> ChatRequest:
    return make_request(response_schema=SCHEMA)


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps() -> list[float]:
    """Delays the client asked to sleep, recorded instead of slept."""
    return []


@pytest.fixture
def make_client(sleeps: list[float]) -> Callable[..., OpenAICompatClient]:
    def factory(**overrides: Any) -> OpenAICompatClient:
        params: dict[str, Any] = {
            "base_url": BASE_URL,
            "model": "test-model",
            "backoff_base_s": 0.0,
            "sleep": sleeps.append,
            "json_mode": JsonMode.JSON_SCHEMA,
        }
        params.update(overrides)
        return OpenAICompatClient(**params)

    return factory
