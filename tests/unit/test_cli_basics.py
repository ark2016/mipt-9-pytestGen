from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from pytestgen import __version__
from pytestgen.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_masks_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTESTGEN_LLM_API_KEY", "sk-very-secret")
    monkeypatch.setenv("PYTESTGEN_LLM_MODEL", "test-model")
    result = runner.invoke(app, ["--log-format", "console", "config"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["llm_model"] == "test-model"
    assert data["llm_api_key"] == "***"
    assert "sk-very-secret" not in result.stdout


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output
