"""Fake LLM for application-layer tests: replies are scripted per request purpose."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from pytestgen.domain.llm import ChatRequest, ChatResponse

Reply = str | ChatResponse | Exception | Callable[[ChatRequest], "str | ChatResponse"]


class ScriptedLLM:
    """Implements ``ports.LLMClient``; no network.

    Replies are queued per ``request.purpose`` and consumed in order. A reply is a
    string (becomes the response content), a ``ChatResponse``, an exception instance
    (raised) or a callable receiving the request. When a purpose has no queued reply,
    ``default`` is used if given, otherwise the call fails the test loudly.
    """

    def __init__(self, default: Reply | None = None, *, model: str = "scripted-model") -> None:
        self._model = model
        self._default = default
        self._queues: defaultdict[str, deque[Reply]] = defaultdict(deque)
        self.requests: list[ChatRequest] = []

    @property
    def model(self) -> str:
        return self._model

    def queue(self, purpose: str, *replies: Reply) -> ScriptedLLM:
        """Append replies for ``purpose``; returns self for chaining."""
        self._queues[purpose].extend(replies)
        return self

    def requests_for(self, purpose: str) -> list[ChatRequest]:
        return [r for r in self.requests if r.purpose == purpose]

    def prompt_for(self, purpose: str, index: int = -1) -> str:
        """User-visible text (all messages joined) of the ``index``-th request of ``purpose``."""
        request = self.requests_for(purpose)[index]
        return "\n".join(m.content for m in request.messages)

    @property
    def calls(self) -> int:
        return len(self.requests)

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        queue = self._queues[request.purpose]
        if queue:
            reply = queue.popleft()
        elif self._default is not None:
            reply = self._default
        else:
            msg = f"no scripted reply for purpose {request.purpose!r}"
            raise AssertionError(msg)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, str):
            return ChatResponse(content=reply, model=self._model)
        return reply


def json_reply(payload: Any, *, before: str = "", after: str = "") -> str:
    """A JSON answer, optionally wrapped in chatty prose like small models produce."""
    return f"{before}{json.dumps(payload)}{after}"


def code_reply(code: str, *, before: str = "Here is the code:", after: str = "") -> str:
    """A python code block with prose around it."""
    return f"{before}\n```python\n{code.strip()}\n```\n{after}".strip()
