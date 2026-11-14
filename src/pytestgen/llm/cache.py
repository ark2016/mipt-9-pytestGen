"""On-disk response cache keyed by the full request content."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pytestgen.domain.llm import ChatRequest, ChatResponse, TokenUsage
from pytestgen.log import get_logger

_log = get_logger(__name__)


def cache_key(model: str, request: ChatRequest) -> str:
    """SHA-256 of the canonical JSON of everything that affects the completion.

    ``purpose`` is deliberately excluded: it is a logging tag only.
    """
    schema = request.response_schema
    material: dict[str, Any] = {
        "model": model,
        "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "seed": request.seed,
        "stop": list(request.stop),
        "response_schema": None
        if schema is None
        else {"name": schema.name, "schema": dict(schema.schema)},
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiskLLMCache:
    """Implements ``ports.LLMCache``: one JSON file per entry, ``<dir>/<aa>/<key>.json``."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def get(self, model: str, request: ChatRequest) -> ChatResponse | None:
        path = self._path(cache_key(model, request))
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            _log.warning("llm_cache_unreadable", path=str(path), error=str(exc))
            return None
        try:
            data = json.loads(raw)
            usage = data["usage"]
            return ChatResponse(
                content=str(data["content"]),
                model=str(data["model"]),
                usage=TokenUsage(int(usage["prompt_tokens"]), int(usage["completion_tokens"])),
                cached=True,
                finish_reason=data.get("finish_reason"),
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            _log.warning("llm_cache_corrupt", path=str(path), error=str(exc))
            return None

    def put(self, model: str, request: ChatRequest, response: ChatResponse) -> None:
        path = self._path(cache_key(model, request))
        entry = {
            "content": response.content,
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            },
            "finish_reason": response.finish_reason,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, json.dumps(entry, ensure_ascii=False))
        except (OSError, ValueError) as exc:  # ValueError: content not encodable as UTF-8
            _log.warning("llm_cache_write_failed", path=str(path), error=str(exc))

    def _path(self, key: str) -> Path:
        return self._dir / key[:2] / f"{key}.json"

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            Path(tmp_name).replace(path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
