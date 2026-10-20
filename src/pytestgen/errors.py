"""Exception hierarchy shared by all layers."""

from __future__ import annotations


class PytestgenError(Exception):
    """Base class for expected, user-facing failures (the CLI prints them without a traceback)."""


class AnalysisError(PytestgenError):
    """The target module cannot be read or parsed."""


class ModuleImportError(PytestgenError):
    """The target module cannot be imported in the sandbox (missing dependency, side effects)."""


class LLMError(PytestgenError):
    """Base class for LLM endpoint failures."""


class LLMConnectionError(LLMError):
    """The endpoint is unreachable or kept failing after retries."""


class LLMResponseError(LLMError):
    """The endpoint answered with an error status or a malformed body."""


class ExtractionError(PytestgenError):
    """Model output does not contain the expected code or JSON."""


class SandboxError(PytestgenError):
    """The sandbox itself failed (not the tests inside it)."""
