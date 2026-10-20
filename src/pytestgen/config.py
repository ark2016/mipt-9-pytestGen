"""Runtime configuration from environment variables (prefix ``PYTESTGEN_``).

The LLM endpoint and model are never hard-coded in the application: switching
between llama.cpp, Ollama, vLLM or a cloud provider only changes the environment.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class JsonMode(StrEnum):
    """How to ask the server for structured output.

    Providers differ: llama.cpp and vLLM accept ``json_schema``; some cloud APIs only
    accept ``json_object``; ``none`` relies on the prompt alone and parses leniently.
    """

    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    NONE = "none"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PYTESTGEN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM endpoint
    llm_base_url: str = Field(
        default="http://localhost:8080/v1",
        description="OpenAI-compatible base URL, e.g. http://localhost:11434/v1 for Ollama",
    )
    llm_model: str = Field(default="qwen2.5-coder-1.5b-instruct")
    llm_api_key: SecretStr | None = None
    llm_timeout_s: float = Field(default=180.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0, le=10)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=1024, gt=0)
    llm_seed: int | None = 42
    llm_json_mode: JsonMode = JsonMode.JSON_SCHEMA

    # response cache
    cache_enabled: bool = True
    cache_dir: Path = Path(".pytestgen/cache")

    # sandbox
    run_timeout_s: float = Field(default=120.0, gt=0)
    test_timeout_s: float = Field(default=10.0, gt=0)
    record_timeout_s: float = Field(default=10.0, gt=0)

    # pipeline budgets
    repair_attempts: int = Field(default=2, ge=0, le=10)
    gap_iterations: int = Field(default=2, ge=0, le=10)
    max_cases_per_function: int = Field(default=12, gt=0)
    z3_timeout_ms: int = Field(default=2000, gt=0)

    # logging
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON

    @field_validator("llm_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            msg = f"unknown log level: {value}"
            raise ValueError(msg)
        return level

    def public_dict(self) -> dict[str, object]:
        """Settings safe to print: the API key is masked."""
        data: dict[str, object] = self.model_dump(mode="json")
        data["llm_api_key"] = "***" if self.llm_api_key else None
        return data
