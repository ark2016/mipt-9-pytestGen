"""Decorator adding a response cache and usage accounting to any ``LLMClient``."""

from __future__ import annotations

from pytestgen.domain.llm import ChatRequest, ChatResponse
from pytestgen.domain.models import LLMUsage
from pytestgen.ports import LLMCache, LLMClient


class CachingLLMClient:
    """Serves repeated requests from ``cache`` and counts what the run consumed.

    ``usage.requests`` includes cache hits; token counters only count responses
    that actually came from the model, since cached ones cost nothing.
    """

    def __init__(self, inner: LLMClient, cache: LLMCache | None = None) -> None:
        self._inner = inner
        self._cache = cache
        self._usage = LLMUsage()

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def usage(self) -> LLMUsage:
        return self._usage

    def complete(self, request: ChatRequest) -> ChatResponse:
        if self._cache is not None:
            hit = self._cache.get(self.model, request)
            if hit is not None:
                self._usage += LLMUsage(requests=1, cache_hits=1)
                return hit
        response = self._inner.complete(request)
        self._usage += LLMUsage(
            requests=1,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )
        if self._cache is not None and response.content.strip():
            self._cache.put(self.model, request, response)
        return response
