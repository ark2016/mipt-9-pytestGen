from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from llm_test_support import SCHEMA
from structlog.testing import capture_logs

from pytestgen.domain.llm import ChatRequest, ChatResponse, ResponseSchema, TokenUsage
from pytestgen.llm import DiskLLMCache, cache_key
from pytestgen.ports import LLMCache

RequestFactory = Callable[..., ChatRequest]

RESPONSE = ChatResponse(
    content="кэш",
    model="m1",
    usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
    finish_reason="stop",
)


@pytest.fixture
def cache(tmp_path: Path) -> DiskLLMCache:
    return DiskLLMCache(tmp_path / "cache")


def entry_files(cache_dir: Path) -> list[Path]:
    return sorted(cache_dir.glob("*/*.json"))


def test_implements_cache_port(cache: DiskLLMCache) -> None:
    assert isinstance(cache, LLMCache)


def test_miss_then_hit_roundtrip(cache: DiskLLMCache, make_request: RequestFactory) -> None:
    request = make_request()
    assert cache.get("m1", request) is None
    cache.put("m1", request, RESPONSE)
    hit = cache.get("m1", request)
    assert hit is not None
    assert hit.cached is True
    assert (hit.content, hit.model, hit.finish_reason) == ("кэш", "m1", "stop")
    assert hit.usage == TokenUsage(5, 3)


def test_file_layout_and_content(
    tmp_path: Path, cache: DiskLLMCache, make_request: RequestFactory
) -> None:
    request = make_request()
    cache.put("m1", request, RESPONSE)
    key = cache_key("m1", request)
    path = tmp_path / "cache" / key[:2] / f"{key}.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "content": "кэш",
        "model": "m1",
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        "finish_reason": "stop",
    }
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temp files left


@pytest.mark.parametrize(
    "change",
    [
        {"temperature": 0.9},
        {"max_tokens": 7},
        {"seed": 1},
        {"seed": None},
        {"stop": ("x",)},
        {"response_schema": SCHEMA},
        {"response_schema": ResponseSchema("other", SCHEMA.schema)},
        {"response_schema": ResponseSchema(SCHEMA.name, {"type": "object"})},
        {"prompt": "different prompt"},
    ],
    ids=lambda c: next(iter(c)),
)
def test_key_depends_on_request_parameters(make_request: RequestFactory, change: dict) -> None:  # type: ignore[type-arg]
    assert cache_key("m1", make_request(**change)) != cache_key("m1", make_request())


def test_key_depends_on_model(make_request: RequestFactory) -> None:
    assert cache_key("m1", make_request()) != cache_key("m2", make_request())


def test_key_ignores_purpose_and_schema_key_order(make_request: RequestFactory) -> None:
    a = make_request(purpose="repair", response_schema=ResponseSchema("s", {"a": 1, "b": 2}))
    b = make_request(purpose="oracle", response_schema=ResponseSchema("s", {"b": 2, "a": 1}))
    assert cache_key("m", a) == cache_key("m", b)


def test_different_models_do_not_share_entries(
    cache: DiskLLMCache, make_request: RequestFactory
) -> None:
    cache.put("m1", make_request(), RESPONSE)
    assert cache.get("m2", make_request()) is None


@pytest.mark.parametrize(
    "garbage",
    [
        "{not json",
        "",
        "[]",
        json.dumps({"content": "x"}),
        json.dumps({"content": "x", "model": "m", "usage": {"prompt_tokens": "a"}}),
        json.dumps({"content": "x", "model": "m", "usage": None}),
    ],
)
def test_corrupt_entry_is_a_miss_and_logged(
    tmp_path: Path, cache: DiskLLMCache, make_request: RequestFactory, garbage: str
) -> None:
    request = make_request()
    cache.put("m1", request, RESPONSE)
    (path,) = entry_files(tmp_path / "cache")
    path.write_text(garbage, encoding="utf-8")
    with capture_logs() as logs:
        assert cache.get("m1", request) is None
    assert any(e["event"] == "llm_cache_corrupt" for e in logs)


def test_corrupt_entry_is_overwritten_by_put(
    tmp_path: Path, cache: DiskLLMCache, make_request: RequestFactory
) -> None:
    request = make_request()
    cache.put("m1", request, RESPONSE)
    (path,) = entry_files(tmp_path / "cache")
    path.write_text("garbage", encoding="utf-8")
    cache.put("m1", request, RESPONSE)
    assert cache.get("m1", request) is not None


def test_unreadable_entry_is_a_miss(
    tmp_path: Path, cache: DiskLLMCache, make_request: RequestFactory
) -> None:
    request = make_request()
    cache.put("m1", request, RESPONSE)
    (path,) = entry_files(tmp_path / "cache")
    path.unlink()
    path.mkdir()  # reading a directory raises OSError (not FileNotFoundError)
    with capture_logs() as logs:
        assert cache.get("m1", request) is None
    assert any(e["event"] == "llm_cache_unreadable" for e in logs)


def test_write_failure_is_logged_not_raised(tmp_path: Path, make_request: RequestFactory) -> None:
    blocker = tmp_path / "file-in-the-way"
    blocker.write_text("x", encoding="utf-8")
    cache = DiskLLMCache(blocker / "sub")  # cannot mkdir below a regular file
    with capture_logs() as logs:
        cache.put("m1", make_request(), RESPONSE)
    assert any(e["event"] == "llm_cache_write_failed" for e in logs)


def test_atomic_write_cleans_temp_file_on_failure(
    tmp_path: Path,
    cache: DiskLLMCache,
    make_request: RequestFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: Path, target: Path) -> Path:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "replace", boom)
    with capture_logs() as logs:
        cache.put("m1", make_request(), RESPONSE)
    assert any(e["event"] == "llm_cache_write_failed" for e in logs)
    assert list((tmp_path / "cache").rglob("*.json")) == []
    assert list((tmp_path / "cache").rglob(".tmp-*")) == []


def test_unencodable_content_is_logged_not_raised(
    tmp_path: Path, cache: DiskLLMCache, make_request: RequestFactory
) -> None:
    bad = ChatResponse("\ud800bad", "m1", TokenUsage(1, 1))  # lone surrogate from a \ud8xx escape
    request = make_request()
    with capture_logs() as logs:
        cache.put("m1", request, bad)
    assert any(e["event"] == "llm_cache_write_failed" for e in logs)
    assert cache.get("m1", request) is None
    assert list((tmp_path / "cache").rglob(".tmp-*")) == []
