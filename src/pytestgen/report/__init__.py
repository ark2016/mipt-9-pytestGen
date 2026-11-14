"""Reporting: coverage deltas and renderers for the generation report."""

from __future__ import annotations

from pytestgen.report.console import render_console
from pytestgen.report.delta import compute_delta
from pytestgen.report.json_renderer import JsonRenderer, ReportFormatError, report_from_json
from pytestgen.report.markdown import MarkdownRenderer

__all__ = [
    "JsonRenderer",
    "MarkdownRenderer",
    "ReportFormatError",
    "compute_delta",
    "render_console",
    "report_from_json",
]
