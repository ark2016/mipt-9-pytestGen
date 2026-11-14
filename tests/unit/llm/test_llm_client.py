from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from llm_test_support import BASE_URL, CHAT_URL, SCHEMA, completion_body, json_response
from pydantic import SecretStr
from structlog.testing import capture_logs

from pytestgen.config import JsonMode, Settings
from pytestgen.domain.llm import ChatRequest
from pytestgen.errors import LLMConnectionError, LLMResponseError
from pytestgen.llm import OpenAICompatClient
from pytestgen.ports import LLMClient

ClientFactory = Callable[..., OpenAICompatClient]
RequestFactory = Callable[..., ChatRequest]


def sent_payloads(route: respx.Route) -> list[dict[str, Any]]:
    return [json.loads(call.request.content) for call in route.calls]


def test_implements_llm_client_port(make_client: ClientFactory) -> None:
    client = make_client()
    assert isinstance(client, LLMClient)
    assert client.model == "test-model"


def test_basic_payload_and_parsing(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).respond(json=completion_body("hi", finish_reason="length"))
    response = make_client().complete(make_request(temperature=0.5, max_tokens=64, seed=7))
    payload = sent_payloads(route)[0]
    assert payload == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "say hi"},
        ],
        "temperature": 0.5,
        "max_tokens": 64,
        "seed": 7,
    }
    assert response.content == "hi"
    assert response.model == "test-model"
    assert response.usage.prompt_tokens == 11
    assert response.usage.completion_tokens == 7
    assert response.finish_reason == "length"
    assert response.cached is False


def test_seed_and_stop_omitted_when_unset_and_stop_sent_when_set(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).respond(json=completion_body())
    client = make_client()
    client.complete(make_request(seed=None))
    client.complete(make_request(stop=("```", "###")))
    plain, with_stop = sent_payloads(route)
    assert "seed" not in plain
    assert "stop" not in plain
    assert with_stop["stop"] == ["```", "###"]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            JsonMode.JSON_SCHEMA,
            {
                "type": "json_schema",
                "json_schema": {"name": SCHEMA.name, "schema": dict(SCHEMA.schema), "strict": True},
            },
        ),
        (JsonMode.JSON_OBJECT, {"type": "json_object"}),
        (JsonMode.NONE, None),
    ],
)
def test_response_format_per_json_mode(
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    request_with_schema: ChatRequest,
    mode: JsonMode,
    expected: dict[str, Any] | None,
) -> None:
    route = mock_api.post(CHAT_URL).respond(json=completion_body())
    make_client(json_mode=mode).complete(request_with_schema)
    assert sent_payloads(route)[0].get("response_format") == expected


def test_no_response_format_without_schema(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).respond(json=completion_body())
    make_client().complete(make_request())
    assert "response_format" not in sent_payloads(route)[0]


def test_authorization_header_present_only_with_key(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).respond(json=completion_body())
    make_client(api_key="sk-secret").complete(make_request())
    make_client().complete(make_request())
    with_key, without_key = (call.request.headers for call in route.calls)
    assert with_key["authorization"] == "Bearer sk-secret"
    assert "authorization" not in without_key


def test_trailing_slash_in_base_url(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).respond(json=completion_body())
    make_client(base_url=BASE_URL + "/").complete(make_request())
    assert route.called


def test_from_settings(mock_api: respx.MockRouter, make_request: RequestFactory) -> None:
    route = mock_api.post(CHAT_URL).respond(json=completion_body())
    settings = Settings(
        llm_base_url=BASE_URL,
        llm_model="qwen",
        llm_api_key=SecretStr("sk-from-settings"),
        llm_json_mode=JsonMode.NONE,
        llm_max_retries=1,
        llm_timeout_s=5,
    )
    with OpenAICompatClient.from_settings(settings) as client:
        assert client.model == "qwen"
        assert client.json_mode is JsonMode.NONE
        client.complete(make_request())
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer sk-from-settings"
    assert json.loads(request.content)["model"] == "qwen"


def test_from_settings_without_key(
    mock_api: respx.MockRouter, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).respond(json=completion_body())
    OpenAICompatClient.from_settings(Settings(llm_base_url=BASE_URL)).complete(make_request())
    assert "authorization" not in route.calls[0].request.headers


# --------------------------------------------------------------------------- retries


def test_retry_on_503_then_success(
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    make_request: RequestFactory,
    sleeps: list[float],
) -> None:
    route = mock_api.post(CHAT_URL).mock(
        side_effect=[httpx.Response(503, text="loading model"), json_response(completion_body())]
    )
    response = make_client().complete(make_request())
    assert response.content == "hello"
    assert route.call_count == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_all_transient_statuses_are_retried(
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    make_request: RequestFactory,
    status: int,
) -> None:
    route = mock_api.post(CHAT_URL).mock(
        side_effect=[httpx.Response(status), json_response(completion_body())]
    )
    make_client().complete(make_request())
    assert route.call_count == 2


def test_retry_on_transport_error_and_timeout(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).mock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("slow"),
            json_response(completion_body()),
        ]
    )
    assert make_client().complete(make_request()).content == "hello"
    assert route.call_count == 3


def test_exponential_backoff_delays(
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    make_request: RequestFactory,
    sleeps: list[float],
) -> None:
    mock_api.post(CHAT_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(LLMResponseError):
        make_client(backoff_base_s=0.5, max_retries=3).complete(make_request())
    assert sleeps == [0.5, 1.0, 2.0]


def test_backoff_is_capped(
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    make_request: RequestFactory,
    sleeps: list[float],
) -> None:
    mock_api.post(CHAT_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(LLMResponseError):
        make_client(backoff_base_s=20.0, max_retries=2).complete(make_request())
    assert sleeps == [20.0, 30.0]


@pytest.mark.parametrize(
    ("header", "expected_sleep"),
    [("2", 2.0), ("0.5", 0.5), ("120", 30.0), ("-5", 0.0), ("Wed, 21 Oct 2026 07:28:00 GMT", 0.0)],
)
def test_retry_after_is_honoured_and_capped(  # noqa: PLR0917
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    make_request: RequestFactory,
    sleeps: list[float],
    header: str,
    expected_sleep: float,
) -> None:
    mock_api.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": header}),
            json_response(completion_body()),
        ]
    )
    make_client().complete(make_request())
    assert sleeps == [expected_sleep]


def test_exhausted_http_status_raises_response_error(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    long_body = "overloaded " * 100
    route = mock_api.post(CHAT_URL).mock(return_value=httpx.Response(503, text=long_body))
    with pytest.raises(LLMResponseError) as info:
        make_client(max_retries=2).complete(make_request())
    assert route.call_count == 3
    message = str(info.value)
    assert "503" in message
    assert "overloaded" in message
    assert "3 attempts" in message
    assert len(message) < 400


def test_exhausted_transport_raises_connection_error(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMConnectionError) as info:
        make_client(max_retries=1).complete(make_request())
    assert route.call_count == 2
    assert "ConnectError" in str(info.value)
    assert BASE_URL in str(info.value)


def test_malformed_base_url_is_an_llm_error_without_retries(
    make_client: ClientFactory, make_request: RequestFactory, sleeps: list[float]
) -> None:
    client = make_client(base_url="http://[::1", max_retries=3)
    with pytest.raises(LLMConnectionError, match="invalid LLM server URL"):
        client.complete(make_request())
    assert sleeps == []


def test_zero_retries_means_single_attempt(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    route = mock_api.post(CHAT_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(LLMResponseError):
        make_client(max_retries=0).complete(make_request())
    assert route.call_count == 1


@pytest.mark.parametrize("status", [401, 404, 422])
def test_non_retryable_status_fails_immediately(
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    make_request: RequestFactory,
    status: int,
) -> None:
    route = mock_api.post(CHAT_URL).mock(return_value=httpx.Response(status, text="nope"))
    with pytest.raises(LLMResponseError, match=str(status)):
        make_client().complete(make_request())
    assert route.call_count == 1


# --------------------------------------------------------------------------- downgrade


@pytest.mark.parametrize(
    "body", ["response_format not supported", "Unsupported JSON_SCHEMA", "grammar error"]
)
def test_400_about_format_downgrades_schema_to_object(
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    request_with_schema: ChatRequest,
    sleeps: list[float],
    body: str,
) -> None:
    route = mock_api.post(CHAT_URL).mock(
        side_effect=[httpx.Response(400, text=body), json_response(completion_body("ok"))]
    )
    client = make_client(max_retries=0)
    with capture_logs() as logs:
        response = client.complete(request_with_schema)
    first, second = sent_payloads(route)
    assert first["response_format"]["type"] == "json_schema"
    assert second["response_format"] == {"type": "json_object"}
    assert response.content == "ok"
    assert client.json_mode is JsonMode.JSON_OBJECT
    assert sleeps == []  # immediate, not a counted retry (max_retries=0 still succeeds)
    warning = next(e for e in logs if e["event"] == "llm_json_mode_downgraded")
    assert warning["log_level"] == "warning"
    assert (warning["from_mode"], warning["to_mode"]) == ("json_schema", "json_object")


def test_downgrade_is_sticky_and_goes_down_to_none(
    mock_api: respx.MockRouter, make_client: ClientFactory, request_with_schema: ChatRequest
) -> None:
    route = mock_api.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(400, text="bad response_format"),
            httpx.Response(400, text="json_object response_format unsupported"),
            json_response(completion_body()),
            json_response(completion_body()),
        ]
    )
    client = make_client()
    client.complete(request_with_schema)
    client.complete(request_with_schema)
    formats = [p.get("response_format") for p in sent_payloads(route)]
    assert [f and f["type"] for f in formats] == ["json_schema", "json_object", None, None]
    assert client.json_mode is JsonMode.NONE


def test_400_in_none_mode_is_an_error(
    mock_api: respx.MockRouter, make_client: ClientFactory, request_with_schema: ChatRequest
) -> None:
    route = mock_api.post(CHAT_URL).mock(
        return_value=httpx.Response(400, text="bad response_format")
    )
    with pytest.raises(LLMResponseError, match="400"):
        make_client(json_mode=JsonMode.NONE).complete(request_with_schema)
    assert route.call_count == 1


def test_unrelated_400_does_not_downgrade(
    mock_api: respx.MockRouter, make_client: ClientFactory, request_with_schema: ChatRequest
) -> None:
    route = mock_api.post(CHAT_URL).mock(
        return_value=httpx.Response(400, text="context length exceeded")
    )
    client = make_client()
    with pytest.raises(LLMResponseError, match="context length"):
        client.complete(request_with_schema)
    assert route.call_count == 1
    assert client.json_mode is JsonMode.JSON_SCHEMA


def test_400_without_schema_in_request_does_not_downgrade(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    mock_api.post(CHAT_URL).mock(return_value=httpx.Response(400, text="bad response_format"))
    client = make_client()
    with pytest.raises(LLMResponseError):
        client.complete(make_request())
    assert client.json_mode is JsonMode.JSON_SCHEMA


# --------------------------------------------------------------------------- parsing


def test_null_content_becomes_empty_string(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    mock_api.post(CHAT_URL).respond(json=completion_body(None))
    assert make_client().complete(make_request()).content == ""


def test_missing_usage_model_and_finish_reason_use_defaults(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    mock_api.post(CHAT_URL).respond(json={"choices": [{"message": {"content": "x"}}]})
    response = make_client().complete(make_request())
    assert response.usage.prompt_tokens == 0
    assert response.usage.completion_tokens == 0
    assert response.model == "test-model"
    assert response.finish_reason is None


def test_garbage_usage_values_are_zero(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    body = completion_body()
    body["usage"] = {"prompt_tokens": "many", "completion_tokens": None}
    mock_api.post(CHAT_URL).respond(json=body)
    response = make_client().complete(make_request())
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (0, 0)


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"error": "weird"}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": ["a"]}}]}),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
    ids=["html", "no-choices-key", "empty-choices", "no-content", "list-content", "array"],
)
def test_malformed_body_raises_response_error(
    mock_api: respx.MockRouter,
    make_client: ClientFactory,
    make_request: RequestFactory,
    reply: httpx.Response,
) -> None:
    mock_api.post(CHAT_URL).mock(return_value=reply)
    with pytest.raises(LLMResponseError, match="malformed"):
        make_client().complete(make_request())


# --------------------------------------------------------------------------- logging


def test_info_event_per_request_without_secrets_or_prompts(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    mock_api.post(CHAT_URL).respond(json=completion_body("answer"))
    client = make_client(api_key="sk-very-secret")
    with capture_logs() as logs:
        client.complete(make_request("TOP SECRET PROMPT TEXT", purpose="oracle_review"))
    infos = [e for e in logs if e["log_level"] == "info"]
    assert len(infos) == 1
    event = infos[0]
    assert event["event"] == "llm_request"
    assert event["purpose"] == "oracle_review"
    assert event["model"] == "test-model"
    assert event["prompt_tokens"] == 11
    assert event["completion_tokens"] == 7
    assert event["finish_reason"] == "stop"
    assert event["latency_ms"] >= 0
    dumped = json.dumps(logs, default=str)
    assert "sk-very-secret" not in dumped
    assert "TOP SECRET PROMPT TEXT" not in dumped


def test_retry_is_logged_as_warning(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    mock_api.post(CHAT_URL).mock(
        side_effect=[httpx.Response(503, text="busy"), json_response(completion_body())]
    )
    with capture_logs() as logs:
        make_client().complete(make_request())
    retry = next(e for e in logs if e["event"] == "llm_retry")
    assert retry["log_level"] == "warning"
    assert "503" in retry["reason"]


# --------------------------------------------------------------------------- lifecycle


def test_injected_http_client_is_not_closed(
    mock_api: respx.MockRouter, make_client: ClientFactory, make_request: RequestFactory
) -> None:
    mock_api.post(CHAT_URL).respond(json=completion_body())
    with httpx.Client() as http:
        with make_client(http_client=http) as client:
            client.complete(make_request())
        assert not http.is_closed


def test_owned_http_client_is_closed_by_context_manager(make_client: ClientFactory) -> None:
    client = make_client()
    with client:
        pass
    assert client._http.is_closed
