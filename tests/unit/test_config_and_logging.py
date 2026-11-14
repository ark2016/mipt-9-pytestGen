from __future__ import annotations

import io
import json

import pytest
from pydantic import ValidationError

from pytestgen.config import JsonMode, LogFormat, Settings
from pytestgen.log import configure_logging, get_logger


def test_defaults_point_to_local_llama_cpp() -> None:
    settings = Settings()
    assert settings.llm_base_url == "http://localhost:8080/v1"
    assert settings.llm_json_mode is JsonMode.JSON_SCHEMA
    assert settings.cache_enabled is True


def test_env_overrides_and_normalisation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTESTGEN_LLM_BASE_URL", "https://openrouter.ai/api/v1/")
    monkeypatch.setenv("PYTESTGEN_LLM_MODEL", "qwen/qwen-2.5-coder-32b-instruct")
    monkeypatch.setenv("PYTESTGEN_LLM_API_KEY", "sk-secret")
    monkeypatch.setenv("PYTESTGEN_LOG_LEVEL", "debug")
    settings = Settings()
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_model == "qwen/qwen-2.5-coder-32b-instruct"
    assert settings.log_level == "DEBUG"
    public = settings.public_dict()
    assert public["llm_api_key"] == "***"
    assert "sk-secret" not in json.dumps(public)


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("PYTESTGEN_LOG_LEVEL", "LOUD"),
        ("PYTESTGEN_LLM_TEMPERATURE", "3.5"),
        ("PYTESTGEN_REPAIR_ATTEMPTS", "-1"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch: pytest.MonkeyPatch, var: str, value: str) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings()


def test_json_logs_are_one_object_per_line() -> None:
    stream = io.StringIO()
    configure_logging("INFO", LogFormat.JSON, stream=stream)
    log = get_logger("pytestgen.test", run="r1")
    log.info("stage_done", stage="analyze", functions=3)
    log.debug("hidden")
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "stage_done"
    assert record["level"] == "info"
    assert record["logger"] == "pytestgen.test"
    assert record["run"] == "r1"
    assert record["functions"] == 3
    assert "timestamp" in record


def test_console_format_is_not_json() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", LogFormat.CONSOLE, stream=stream)
    get_logger("x").debug("hello", n=1)
    assert "hello" in stream.getvalue()
    with pytest.raises(json.JSONDecodeError):
        json.loads(stream.getvalue())


def test_logger_created_before_configuration_uses_the_later_configuration() -> None:
    """Module-level loggers are created at import time, before the CLI configures logging."""
    early = get_logger("pytestgen.early", run="r2")
    stream = io.StringIO()
    configure_logging("INFO", LogFormat.JSON, stream=stream)
    early.debug("hidden")
    early.info("visible", n=1)
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert (record["event"], record["logger"], record["run"]) == (
        "visible",
        "pytestgen.early",
        "r2",
    )


def test_default_stream_follows_a_later_replacement_of_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed stderr of an earlier CLI invocation must not break later log calls."""
    first, second = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stderr", first)
    configure_logging("INFO", LogFormat.JSON)
    first.close()
    monkeypatch.setattr("sys.stderr", second)

    get_logger("pytestgen.late").info("after_swap")

    assert json.loads(second.getvalue())["event"] == "after_swap"
