"""Value objects for talking to a chat-completion model.

Kept in the domain so that ports and application code can build requests
without importing any HTTP client.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class ResponseSchema:
    """JSON Schema the model must follow (OpenAI ``response_format`` json_schema)."""

    name: str
    schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...]
    temperature: float = 0.2
    max_tokens: int = 1024
    seed: int | None = 42
    response_schema: ResponseSchema | None = None
    stop: tuple[str, ...] = ()
    purpose: str = "generic"
    """Short tag for logs and stats, e.g. ``"oracle_review"`` or ``"repair"``."""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    model: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    cached: bool = False
    finish_reason: str | None = None
