"""Replay recorded llama.cpp responses (tests/fixtures/llm) through the client."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import respx
from llm_test_support import CHAT_URL

from pytestgen.domain.llm import ChatRequest
from pytestgen.llm import OpenAICompatClient, extract_code, extract_json

ClientFactory = Callable[..., OpenAICompatClient]
RequestFactory = Callable[..., ChatRequest]

FIXTURE_NAMES = ["chat_python_code.json", "chat_json_schema.json", "chat_truncated.json"]


def replay(mock_api: respx.MockRouter, fixture_dir: Path, name: str) -> None:
    body = json.loads((fixture_dir / name).read_text(encoding="utf-8"))
    mock_api.post(CHAT_URL).respond(json=body)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_is_a_valid_chat_completion(
    mock_api: respx.MockRouter,
    llm_fixture_dir: Path,
    make_client: ClientFactory,
    make_request: RequestFactory,
    name: str,
) -> None:
    replay(mock_api, llm_fixture_dir, name)
    raw = json.loads((llm_fixture_dir / name).read_text(encoding="utf-8"))
    response = make_client().complete(make_request())
    assert response.model == "qwen2.5-coder-1.5b-instruct"
    assert response.content == raw["choices"][0]["message"]["content"]
    assert response.usage.prompt_tokens == raw["usage"]["prompt_tokens"]
    assert response.usage.completion_tokens == raw["usage"]["completion_tokens"]
    assert response.finish_reason == raw["choices"][0]["finish_reason"]


def test_python_code_fixture_yields_runnable_test_code(
    mock_api: respx.MockRouter,
    llm_fixture_dir: Path,
    make_client: ClientFactory,
    make_request: RequestFactory,
) -> None:
    replay(mock_api, llm_fixture_dir, "chat_python_code.json")
    code = extract_code(make_client().complete(make_request()).content)
    assert code.startswith("import pytest")
    assert "def test_clamp" in code
    assert "These cover" not in code


def test_json_schema_fixture_yields_object(
    mock_api: respx.MockRouter,
    llm_fixture_dir: Path,
    make_client: ClientFactory,
    make_request: RequestFactory,
) -> None:
    replay(mock_api, llm_fixture_dir, "chat_json_schema.json")
    data = extract_json(make_client().complete(make_request()).content)
    assert [v["case_id"] for v in data["verdicts"]] == ["c1", "c2"]
    assert data["extra_inputs"] == [{"value": 7, "low": 7, "high": 7}]


def test_truncated_fixture_reports_length_and_invalid_code(
    mock_api: respx.MockRouter,
    llm_fixture_dir: Path,
    make_client: ClientFactory,
    make_request: RequestFactory,
) -> None:
    from pytestgen.errors import ExtractionError

    replay(mock_api, llm_fixture_dir, "chat_truncated.json")
    response = make_client().complete(make_request())
    assert response.finish_reason == "length"
    with pytest.raises(ExtractionError, match="not valid Python"):
        extract_code(response.content)
