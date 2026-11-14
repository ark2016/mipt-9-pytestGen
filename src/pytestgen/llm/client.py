"""Chat-completion client for any OpenAI-compatible server (llama.cpp, Ollama, vLLM, cloud)."""

from __future__ import annotations

import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
)

from pytestgen.config import JsonMode, Settings
from pytestgen.domain.llm import ChatRequest, ChatResponse, TokenUsage
from pytestgen.errors import LLMConnectionError, LLMError, LLMResponseError
from pytestgen.log import get_logger

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_WAIT_S = 30.0
_EXCERPT_CHARS = 200
_FORMAT_HINTS = ("response_format", "json_schema", "grammar")
_DOWNGRADE = {JsonMode.JSON_SCHEMA: JsonMode.JSON_OBJECT, JsonMode.JSON_OBJECT: JsonMode.NONE}

_log = get_logger(__name__)


class _RetryableError(Exception):
    """A failed attempt worth repeating; ``status`` is None for transport failures."""

    def __init__(
        self, message: str, *, status: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _excerpt(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _EXCERPT_CHARS else flat[:_EXCERPT_CHARS] + "..."


def _parse_retry_after(value: str | None) -> float | None:
    """Seconds from a ``Retry-After`` header; HTTP-date values are ignored."""
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class OpenAICompatClient:
    """Implements ``ports.LLMClient`` over ``POST {base_url}/chat/completions``.

    Transient failures (transport errors, 429, 5xx) are retried with exponential
    backoff. If the server rejects ``response_format`` with HTTP 400, the client
    permanently downgrades ``json_schema -> json_object -> none`` and resends.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        *,
        timeout_s: float = 180.0,
        max_retries: int = 3,
        json_mode: JsonMode = JsonMode.JSON_SCHEMA,
        http_client: httpx.Client | None = None,
        backoff_base_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._json_mode = json_mode
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout_s)

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAICompatClient:
        key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        return cls(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=key,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            json_mode=settings.llm_json_mode,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def json_mode(self) -> JsonMode:
        """Current mode; may be lower than configured after a server-side downgrade."""
        return self._json_mode

    def complete(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        retrying = Retrying(
            stop=stop_after_attempt(self._max_retries + 1),
            wait=self._wait,
            retry=retry_if_exception_type(_RetryableError),
            sleep=self._sleep,
            before_sleep=self._log_retry,
            reraise=True,
        )
        try:
            http_response = retrying(self._post_once, request)
        except _RetryableError as exc:
            raise self._exhausted(exc) from exc
        response = self._parse(http_response)
        _log.info(
            "llm_request",
            purpose=request.purpose,
            model=response.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            finish_reason=response.finish_reason,
        )
        return response

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------ request

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.stop:
            payload["stop"] = list(request.stop)
        schema = request.response_schema
        if schema is not None:
            if self._json_mode is JsonMode.JSON_SCHEMA:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.name,
                        "schema": dict(schema.schema),
                        "strict": True,
                    },
                }
            elif self._json_mode is JsonMode.JSON_OBJECT:
                payload["response_format"] = {"type": "json_object"}
        _log.debug("llm_payload", purpose=request.purpose, chars=_prompt_chars(request))
        return payload

    def _post_once(self, request: ChatRequest) -> httpx.Response:
        """One logical attempt; a format downgrade resends without consuming a retry."""
        while True:
            try:
                response = self._http.post(
                    self._url,
                    json=self._payload(request),
                    headers=self._headers,
                    timeout=self._timeout_s,
                )
            except httpx.InvalidURL as exc:
                msg = f"invalid LLM server URL {self._url!r}: {exc}"
                raise LLMConnectionError(msg) from exc
            except httpx.TransportError as exc:
                msg = f"cannot reach LLM server at {self._url}: {exc.__class__.__name__}: {exc}"
                raise _RetryableError(msg) from exc
            status = response.status_code
            if status == 400 and self._downgrade_for(request, response):
                continue
            if status in _RETRYABLE_STATUS:
                raise _RetryableError(
                    f"LLM server returned HTTP {status}: {_excerpt(response.text)}",
                    status=status,
                    retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                )
            if status >= 400:
                raise LLMResponseError(
                    f"LLM server returned HTTP {status}: {_excerpt(response.text)}"
                )
            return response

    def _downgrade_for(self, request: ChatRequest, response: httpx.Response) -> bool:
        """Step ``json_mode`` down if a 400 blames the structured-output parameters."""
        next_mode = _DOWNGRADE.get(self._json_mode)
        if request.response_schema is None or next_mode is None:
            return False
        lowered = response.text.lower()
        if not any(hint in lowered for hint in _FORMAT_HINTS):
            return False
        _log.warning(
            "llm_json_mode_downgraded",
            from_mode=self._json_mode.value,
            to_mode=next_mode.value,
            detail=_excerpt(response.text),
        )
        self._json_mode = next_mode
        return True

    # ------------------------------------------------------------------ retries

    def _wait(self, state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome is not None else None
        if isinstance(exc, _RetryableError) and exc.retry_after is not None:
            return min(exc.retry_after, _MAX_WAIT_S)
        return min(self._backoff_base_s * float(2 ** (state.attempt_number - 1)), _MAX_WAIT_S)

    def _log_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome is not None else None
        _log.warning(
            "llm_retry",
            attempt=state.attempt_number,
            max_attempts=self._max_retries + 1,
            reason=str(exc),
        )

    def _exhausted(self, exc: _RetryableError) -> LLMError:
        message = f"{exc} (gave up after {self._max_retries + 1} attempts)"
        if exc.status is None:
            return LLMConnectionError(message)
        return LLMResponseError(message)

    # ------------------------------------------------------------------ response

    def _parse(self, response: httpx.Response) -> ChatResponse:
        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if content is None:
                content = ""
            if not isinstance(content, str):
                msg = f"message.content is {type(content).__name__}, expected string"
                raise TypeError(msg)
            usage = data.get("usage") or {}
            finish = choice.get("finish_reason")
            model = data.get("model")
            return ChatResponse(
                content=content,
                model=model if isinstance(model, str) and model else self._model,
                usage=TokenUsage(
                    prompt_tokens=_as_int(usage.get("prompt_tokens")),
                    completion_tokens=_as_int(usage.get("completion_tokens")),
                ),
                finish_reason=finish if isinstance(finish, str) else None,
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            msg = f"malformed chat completion ({exc.__class__.__name__}: {exc}): "
            raise LLMResponseError(msg + _excerpt(response.text)) from exc


def _prompt_chars(request: ChatRequest) -> int:
    return sum(len(m.content) for m in request.messages)
