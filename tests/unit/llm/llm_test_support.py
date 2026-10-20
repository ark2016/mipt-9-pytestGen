"""Shared constants and builders for the llm unit tests."""

from __future__ import annotations

from typing import Any

import httpx

from pytestgen.domain.llm import ResponseSchema

BASE_URL = "http://llm.test/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"

SCHEMA = ResponseSchema(
    name="verdicts",
    schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
)


def completion_body(
    content: str | None = "hello",
    *,
    model: str = "test-model",
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
    finish_reason: str | None = "stop",
) -> dict[str, Any]:
    """A minimal chat-completion body."""
    return {
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def json_response(body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)
