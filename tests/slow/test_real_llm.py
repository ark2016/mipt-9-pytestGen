"""Smoke tests against a real OpenAI-compatible server (llama.cpp, Ollama, ...).

Run with a server up and the endpoint in the environment::

    PYTESTGEN_LLM_BASE_URL=http://localhost:8080/v1 \
    PYTESTGEN_LLM_MODEL=qwen2.5-coder-1.5b-instruct \
    uv run pytest -m slow -v

Without ``PYTESTGEN_LLM_BASE_URL``, or when ``{base_url}/models`` is unreachable, the tests are
skipped. CI sets ``PYTESTGEN_SLOW_REQUIRE_LLM=1`` so a dead server fails the job instead.

The model is small and runs on a CPU, so the assertions are about the pipeline's guarantees
(valid JSON, a file that passes, coverage that does not drop), never about test quality.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from pytestgen.application import GenerateRequest
from pytestgen.config import Settings
from pytestgen.container import build_generate
from pytestgen.domain.llm import ChatMessage, ChatRequest, ResponseSchema, Role
from pytestgen.llm import OpenAICompatClient, extract_json

pytestmark = pytest.mark.slow

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# tests/conftest.py strips PYTESTGEN_* from the environment before every test, so the
# endpoint is read once, at import time.
_BASE_URL = os.environ.get("PYTESTGEN_LLM_BASE_URL", "").strip().rstrip("/")
_MODEL = os.environ.get("PYTESTGEN_LLM_MODEL", "").strip() or "qwen2.5-coder-1.5b-instruct"
_JSON_MODE = os.environ.get("PYTESTGEN_LLM_JSON_MODE", "").strip() or "json_schema"
_REQUIRE = os.environ.get("PYTESTGEN_SLOW_REQUIRE_LLM", "") not in {"", "0"}
_PROBE_TIMEOUT_S = 10.0
_REQUEST_TIMEOUT_S = 600.0  # a 1.5B model on two CI cores needs minutes for a long prompt

_SCHEMA = ResponseSchema(
    name="verdict",
    schema={
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "reason": {"type": "string"},
        },
        "required": ["verdict", "reason"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    model: str

    def settings(self, cache_dir: Path, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "llm_base_url": self.base_url,
            "llm_model": self.model,
            "llm_json_mode": _JSON_MODE,
            "llm_timeout_s": _REQUEST_TIMEOUT_S,
            "llm_max_retries": 1,
            "cache_dir": cache_dir,
            "cache_enabled": False,
            **overrides,
        }
        return Settings.model_validate(values)


def _unavailable(reason: str) -> None:
    if _REQUIRE:
        pytest.fail(f"real LLM required (PYTESTGEN_SLOW_REQUIRE_LLM) but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def endpoint() -> Endpoint:
    """The configured server, or a skip (a failure when it is required)."""
    if not _BASE_URL:
        _unavailable("PYTESTGEN_LLM_BASE_URL is not set")
    try:
        response = httpx.get(f"{_BASE_URL}/models", timeout=_PROBE_TIMEOUT_S)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _unavailable(f"LLM server at {_BASE_URL} is unreachable: {type(exc).__name__}: {exc}")
    return Endpoint(_BASE_URL, _MODEL)


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    """A private copy of the demo project (``examples/``)."""
    destination = tmp_path / "project"
    shutil.copytree(
        EXAMPLES,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".coverage*"),
    )
    yield destination


def test_structured_completion_returns_parseable_json(endpoint: Endpoint, tmp_path: Path) -> None:
    settings = endpoint.settings(tmp_path / "cache")
    request = ChatRequest(
        messages=(
            ChatMessage(Role.SYSTEM, "You are a strict code reviewer. Answer only in JSON."),
            ChatMessage(
                Role.USER,
                "Function: def is_even(n): return n % 2 == 1\n"
                "Docstring: returns True for even numbers.\n"
                'Reply with {"verdict": "pass" or "fail", "reason": "<one short sentence>"}.',
            ),
        ),
        temperature=0.0,
        max_tokens=128,
        response_schema=_SCHEMA,
        purpose="slow_smoke",
    )
    with OpenAICompatClient.from_settings(settings) as client:
        response = client.complete(request)

    assert response.content.strip(), "the model returned an empty completion"
    # With json_schema the server constrains decoding; json_object / none fall back to a
    # lenient extraction, which is what the pipeline itself uses.
    data = (
        json.loads(response.content)
        if _JSON_MODE == "json_schema"
        else extract_json(response.content)
    )
    assert isinstance(data, dict)
    assert data.get("verdict") in {"pass", "fail"}
    assert isinstance(data.get("reason"), str)
    assert response.usage.completion_tokens > 0


def test_generate_on_demo_module_with_real_model(
    endpoint: Endpoint, project: Path, tmp_path: Path
) -> None:
    """The whole pipeline on two functions of examples/shop/pricing.py."""
    target = project / "shop" / "pricing.py"
    settings = endpoint.settings(
        tmp_path / "cache",
        gap_iterations=1,
        repair_attempts=1,
        run_timeout_s=180,
        max_cases_per_function=6,
    )
    request = GenerateRequest(
        target=target,
        project_root=project,
        functions=("discount", "loyalty_level"),
    )
    with build_generate(settings, use_cache=False) as use_case:
        report = use_case.execute(request)

    # the file exists and is the pytest module the project would run
    assert report.output_path is not None, "no test file was written"
    assert report.output_path.is_file()
    assert report.output_path == project / "tests" / "test_pricing_pytestgen.py"
    assert report.kept_tests > 0
    assert "def test_" in report.output_path.read_text(encoding="utf-8")

    # the real model was actually used
    assert report.llm_usage.requests > 0
    assert report.model == endpoint.model

    # coverage of the module never drops below what the existing tests give
    assert report.delta.after.line_percent >= report.delta.before.line_percent
    assert report.delta.after.covered_lines >= report.delta.before.covered_lines

    # the generated tests pass together with the project's own suite
    result = _run_project_pytest(project)
    assert result.returncode == 0, result.stdout + result.stderr


def _run_project_pytest(project: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "COV_CORE"))}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
