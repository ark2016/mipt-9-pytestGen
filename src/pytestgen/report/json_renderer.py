"""JSON report and its reader."""

from __future__ import annotations

import json
from typing import Any

from pytestgen.domain.models import GenerationReport
from pytestgen.errors import PytestgenError
from pytestgen.report._plain import to_plain

REQUIRED_KEYS = frozenset({"module", "strategy", "delta", "kept_tests"})


class ReportFormatError(PytestgenError):
    """The text is not a pytestgen JSON report."""


class JsonRenderer:
    """Renders a :class:`GenerationReport` as indented JSON (``ReportRenderer`` port).

    The text keeps non-ASCII characters, so write it to a file with ``encoding="utf-8"``.
    """

    @property
    def format_name(self) -> str:
        return "json"

    def render(self, report: GenerationReport) -> str:
        return json.dumps(to_plain(report), indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def report_from_json(text: str) -> dict[str, Any]:
    """Parse a JSON report; checks that the top-level keys of a report are present."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportFormatError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportFormatError("a report must be a JSON object")
    missing = sorted(REQUIRED_KEYS - data.keys())
    if missing:
        raise ReportFormatError(f"report is missing keys: {', '.join(missing)}")
    return data
