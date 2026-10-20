"""GitHub-flavoured markdown report, suited for a pull-request comment."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence

from pytestgen.domain.models import (
    DroppedTest,
    DropReason,
    GenerationReport,
    OriginStats,
    SuspectedBug,
)
from pytestgen.report._format import (
    format_inputs,
    format_usage,
    group_dropped,
    more_note,
    one_line,
    percent,
    ranked_functions,
    signed_gain,
    transition,
)


def _cell(text: str) -> str:
    """Make ``text`` safe inside a table cell."""
    return one_line(text).replace("|", r"\|")


def _text(text: str) -> str:
    """Free text on one line with ``<``, ``>`` and ``&`` escaped (no raw inline HTML)."""
    return html.escape(one_line(text), quote=False)


def _code(text: str) -> str:
    """Valid inline code span for any ``text``; inner whitespace is kept, newlines are not."""
    text = " ".join(text.replace(chr(9), " ").splitlines())
    if not text:
        return "`(empty)`"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text[0] in "` " or text[-1] in "` " else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _code_cell(text: str) -> str:
    """Code span in a table cell; GitHub wants ``|`` backslash-escaped even in code."""
    return _code(text).replace("|", r"\|")


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = [f"| {' | '.join(header)} |", f"|{'|'.join(' --- ' for _ in header)}|"]
    lines.extend(f"| {' | '.join(row)} |" for row in rows)
    return lines


class MarkdownRenderer:
    """Renders a :class:`GenerationReport` as markdown (``ReportRenderer`` port)."""

    @property
    def format_name(self) -> str:
        return "markdown"

    def render(self, report: GenerationReport) -> str:
        blocks = [
            [f"### pytestgen: {report.module}"],
            self._summary(report),
            self._origins(report.by_origin),
            self._functions(report),
            self._bugs(report.suspected_bugs),
            self._dropped(report.dropped),
            self._footer(report),
        ]
        return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"

    @staticmethod
    def _summary(report: GenerationReport) -> list[str]:
        delta = report.delta
        rows = [
            (
                "Lines",
                percent(delta.before.line_percent),
                percent(delta.after.line_percent),
                signed_gain(delta.line_gain),
            ),
            (
                "Branches",
                percent(delta.before.branch_percent),
                percent(delta.after.branch_percent),
                signed_gain(delta.branch_gain),
            ),
        ]
        table = _table(("Metric", "Before", "After", "Gain"), rows)
        return [*table, "", f"Kept tests: **{report.kept_tests}**"]

    @staticmethod
    def _origins(stats: Sequence[OriginStats]) -> list[str]:
        if not stats:
            return []
        rows = [(_cell(s.origin.value), str(s.tests), str(s.arcs_added)) for s in stats]
        return _table(("Origin", "Tests", "Arcs added"), rows)

    @staticmethod
    def _functions(report: GenerationReport) -> list[str]:
        shown, hidden = ranked_functions(report.delta)
        if not shown:
            return []
        rows = [
            (
                _code_cell(f.function),
                transition(f.before.line_percent, f.after.line_percent),
                transition(f.before.branch_percent, f.after.branch_percent),
            )
            for f in shown
        ]
        lines = _table(("Function", "Lines", "Branches"), rows)
        if hidden:
            lines.extend(["", f"*{more_note(hidden)}*"])
        return lines

    @staticmethod
    def _bugs(bugs: Sequence[SuspectedBug]) -> list[str]:
        if not bugs:
            return []
        lines = ["#### Suspected bugs", ""]
        for bug in bugs:
            lines.append(
                f"- {_code(bug.function)} with {_code(format_inputs(bug.inputs))}: "
                f"observed {_code(bug.observed)}"
            )
            lines.append(f"  - {_text(bug.explanation)}")
        return lines

    @staticmethod
    def _dropped(dropped: tuple[DroppedTest, ...]) -> list[str]:
        if not dropped:
            return []
        lines = ["<details>", f"<summary>Dropped tests ({len(dropped)})</summary>", ""]
        for reason, items in group_dropped(dropped).items():
            lines.extend([f"**{_reason_title(reason)}** ({len(items)})", ""])
            for item in items:
                detail = f": {_text(item.detail)}" if item.detail else ""
                lines.append(f"- {_code(item.name)} ({_code(item.function)}){detail}")
            lines.append("")
        lines.append("</details>")
        return lines

    @staticmethod
    def _footer(report: GenerationReport) -> list[str]:
        model = f", model {_code(report.model)}" if report.model else ""
        lines = [
            f"- LLM usage: {format_usage(report.llm_usage)}{model}",
            f"- Strategy: {report.strategy.value}",
            f"- Duration: {report.duration_s:.1f} s",
        ]
        if report.output_path is not None:
            lines.append(f"- Output: {_code(report.output_path.as_posix())}")
        return lines


def _reason_title(reason: DropReason) -> str:
    return reason.value.replace("_", " ")
