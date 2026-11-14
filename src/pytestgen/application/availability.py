"""Stop asking a model that is not there."""

from __future__ import annotations

from pytestgen.domain.llm import ChatRequest, ChatResponse
from pytestgen.errors import LLMConnectionError
from pytestgen.log import get_logger
from pytestgen.ports import LLMClient

_log = get_logger(__name__)


class ModelUnavailableError(LLMConnectionError):
    """Raised without any network traffic once the endpoint was found unreachable."""


class CircuitBreakerLLMClient:
    """``LLMClient`` decorator: after the first connection failure further calls fail at once.

    The wrapped client has already retried with backoff, so one ``LLMConnectionError`` means
    the endpoint is down; repeating that for every remaining prompt of the run only burns
    minutes. Answers other than connection failures (HTTP errors, bad payloads) do not trip it.
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self._tripped_by: str | None = None

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def tripped(self) -> bool:
        return self._tripped_by is not None

    def complete(self, request: ChatRequest) -> ChatResponse:
        if self._tripped_by is not None:
            raise ModelUnavailableError(f"model unreachable, call skipped: {self._tripped_by}")
        try:
            return self._inner.complete(request)
        except LLMConnectionError as exc:
            self._tripped_by = str(exc)
            _log.warning(
                "llm_unreachable", purpose=request.purpose, error=str(exc), skipping="further calls"
            )
            raise
