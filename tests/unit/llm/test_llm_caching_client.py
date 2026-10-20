from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from pytestgen.domain.llm import ChatRequest, ChatResponse, TokenUsage
from pytestgen.domain.models import LLMUsage
from pytestgen.llm import CachingLLMClient, DiskLLMCache
from pytestgen.ports import LLMClient

RequestFactory = Callable[..., ChatRequest]


class FakeLLM:
    """Scripted inner client that records calls."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.calls: list[ChatRequest] = []

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        content = self._replies.pop(0) if self._replies else "default"
        return ChatResponse(content, "fake-model", TokenUsage(10, 4), finish_reason="stop")


@pytest.fixture
def cache(tmp_path: Path) -> DiskLLMCache:
    return DiskLLMCache(tmp_path / "cache")


def test_passes_through_without_cache(make_request: RequestFactory) -> None:
    inner = FakeLLM("a", "b")
    client = CachingLLMClient(inner)
    assert isinstance(client, LLMClient)
    assert client.model == "fake-model"
    assert client.complete(make_request()).content == "a"
    assert client.complete(make_request()).content == "b"
    assert len(inner.calls) == 2
    assert client.usage == LLMUsage(requests=2, cache_hits=0, prompt_tokens=20, completion_tokens=8)


def test_second_identical_request_is_served_from_cache(
    cache: DiskLLMCache, make_request: RequestFactory
) -> None:
    inner = FakeLLM("first", "second")
    client = CachingLLMClient(inner, cache)
    first = client.complete(make_request())
    again = client.complete(make_request(purpose="other-purpose"))
    assert (first.content, first.cached) == ("first", False)
    assert (again.content, again.cached) == ("first", True)
    assert len(inner.calls) == 1
    # requests include the hit; tokens are only counted for the real call
    assert client.usage == LLMUsage(requests=2, cache_hits=1, prompt_tokens=10, completion_tokens=4)


def test_different_request_misses(cache: DiskLLMCache, make_request: RequestFactory) -> None:
    inner = FakeLLM("one", "two")
    client = CachingLLMClient(inner, cache)
    client.complete(make_request("p1"))
    client.complete(make_request("p2"))
    assert len(inner.calls) == 2
    assert client.usage.cache_hits == 0


def test_cache_is_shared_between_client_instances(
    cache: DiskLLMCache, make_request: RequestFactory
) -> None:
    CachingLLMClient(FakeLLM("persisted"), cache).complete(make_request())
    inner = FakeLLM("must not be called")
    client = CachingLLMClient(inner, cache)
    assert client.complete(make_request()).content == "persisted"
    assert inner.calls == []
    assert client.usage == LLMUsage(requests=1, cache_hits=1)


@pytest.mark.parametrize("content", ["", "   \n"])
def test_empty_responses_are_not_cached(
    cache: DiskLLMCache, make_request: RequestFactory, content: str
) -> None:
    inner = FakeLLM(content, "real answer")
    client = CachingLLMClient(inner, cache)
    assert client.complete(make_request()).content == content
    assert client.complete(make_request()).content == "real answer"
    assert len(inner.calls) == 2
    assert client.usage.cache_hits == 0
    assert client.complete(make_request()).cached is True  # the good answer was stored


def test_errors_propagate_and_are_not_counted_as_cache_entries(
    cache: DiskLLMCache, make_request: RequestFactory
) -> None:
    class Failing:
        model = "fake-model"

        def complete(self, request: ChatRequest) -> ChatResponse:
            raise RuntimeError("down")

    client = CachingLLMClient(Failing(), cache)
    with pytest.raises(RuntimeError, match="down"):
        client.complete(make_request())
    assert client.usage == LLMUsage()
