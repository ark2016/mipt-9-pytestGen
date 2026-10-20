from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from pytestgen.domain.models import (
    CoverageDelta,
    DroppedTest,
    DropReason,
    FunctionCoverageDelta,
    GenerationReport,
    SuspectedBug,
)
from pytestgen.ports import ReportRenderer
from pytestgen.report import MarkdownRenderer
from pytestgen.report._format import signed_gain
from pytestgen.report.markdown import _code


@pytest.fixture
def renderer() -> MarkdownRenderer:
    return MarkdownRenderer()


def test_implements_port(renderer: MarkdownRenderer) -> None:
    assert isinstance(renderer, ReportRenderer)
    assert renderer.format_name == "markdown"


def test_title_and_summary_table(renderer: MarkdownRenderer, full_report: GenerationReport) -> None:
    text = renderer.render(full_report)
    assert text.startswith("### pytestgen: pkg.calc\n")
    assert "| Metric | Before | After | Gain |" in text
    assert "| Lines | 20.0% | 62.0% | +42.0 pp |" in text
    assert "| Branches | 20.0% | 80.0% | +60.0 pp |" in text
    assert "Kept tests: **7**" in text


def test_origin_table(renderer: MarkdownRenderer, full_report: GenerationReport) -> None:
    text = renderer.render(full_report)
    assert "| Origin | Tests | Arcs added |" in text
    assert "| z3 | 4 | 6 |" in text
    assert "| llm_free | 3 | 2 |" in text


def test_function_table_sorted_by_gain(
    renderer: MarkdownRenderer, full_report: GenerationReport
) -> None:
    text = renderer.render(full_report)
    assert (
        text.index("| `divide` |") < text.index("| `Account.withdraw` |") < text.index("| `add` |")
    )
    assert "| `divide` | 0.0% -> 100.0% | 0.0% -> 50.0% |" in text


def test_function_table_truncated_to_15_rows(
    renderer: MarkdownRenderer,
    full_report: GenerationReport,
    make_function_delta: Callable[[str, int, int], FunctionCoverageDelta],
) -> None:
    funcs = tuple(make_function_delta(f"fn{i:02d}", 0, 10 - i % 10) for i in range(20))
    delta = replace(full_report.delta, per_function=funcs)
    text = renderer.render(replace(full_report, delta=delta))
    rows = [ln for ln in text.splitlines() if ln.startswith("| `fn")]
    assert len(rows) == 15
    assert "...and 5 more" in text


def test_no_truncation_note_at_exactly_15(
    renderer: MarkdownRenderer,
    full_report: GenerationReport,
    make_function_delta: Callable[[str, int, int], FunctionCoverageDelta],
) -> None:
    funcs = tuple(make_function_delta(f"fn{i:02d}", 0, 10) for i in range(15))
    delta = replace(full_report.delta, per_function=funcs)
    assert "...and" not in renderer.render(replace(full_report, delta=delta))


def test_pipe_escaping(
    renderer: MarkdownRenderer,
    full_report: GenerationReport,
    make_function_delta: Callable[[str, int, int], FunctionCoverageDelta],
) -> None:
    delta = replace(full_report.delta, per_function=(make_function_delta("weird|name", 0, 10),))
    text = renderer.render(replace(full_report, delta=delta))
    assert "| `weird\\|name` |" in text


def test_suspected_bugs_section(renderer: MarkdownRenderer, full_report: GenerationReport) -> None:
    text = renderer.render(full_report)
    assert "#### Suspected bugs" in text
    assert "`divide` with `a=1, b=0`: observed `returns 0`" in text
    assert "division by zero | should raise" in text


def test_dropped_details_grouped_by_reason(
    renderer: MarkdownRenderer, full_report: GenerationReport
) -> None:
    text = renderer.render(full_report)
    assert "<details>" in text
    assert "<summary>Dropped tests (3)</summary>" in text
    assert text.index("**still failing** (2)") < text.index("**invalid code** (1)")
    assert "- `test_a` (`add`): AssertionError: 1 != 2" in text
    assert "- `test_b` (`divide`)\n" in text
    assert text.rstrip().count("</details>") == 1


def test_footer(renderer: MarkdownRenderer, full_report: GenerationReport) -> None:
    text = renderer.render(full_report)
    assert "5 requests, 2 cache hits, 1,500 tokens" in text
    assert "model `qwen2.5-coder`" in text
    assert "Duration: 12.3 s" in text
    assert "Output: `tests/test_calc_gen.py`" in text


def test_optional_sections_absent(
    renderer: MarkdownRenderer, minimal_report: GenerationReport
) -> None:
    text = renderer.render(minimal_report)
    for absent in (
        "Suspected bugs",
        "Dropped tests",
        "<details>",
        "| Origin",
        "| Function",
        "Output:",
    ):
        assert absent not in text
    assert "model" not in text
    assert "| Lines | 100.0% | 100.0% | +0.0 pp |" in text


def test_text_is_ascii_free_of_emoji(
    renderer: MarkdownRenderer, full_report: GenerationReport
) -> None:
    assert renderer.render(full_report).isascii()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(42.0, "+42.0 pp"), (-3.25, "-3.2 pp"), (0.0, "+0.0 pp"), (-0.01, "+0.0 pp")],
)
def test_signed_gain(value: float, expected: str) -> None:
    assert signed_gain(value) == expected


def test_code_span_with_backticks() -> None:
    assert _code("a `b`") == "`` a `b` ``"
    assert _code("a\n b") == "`a  b`"


def test_bug_with_empty_inputs_and_multiline_text(
    renderer: MarkdownRenderer, minimal_report: GenerationReport
) -> None:
    bug = SuspectedBug("f", {}, "x\ny", "why\nnot")
    drop = DroppedTest("t", "f", DropReason.UNSTABLE)
    delta = CoverageDelta(minimal_report.delta.before, minimal_report.delta.after)
    text = renderer.render(
        replace(minimal_report, suspected_bugs=(bug,), dropped=(drop,), delta=delta)
    )
    assert "`(no arguments)`" in text
    assert "observed `x y`" in text
    assert "  - why not" in text
    assert "**unstable** (1)" in text


def test_output_path_rendered_posix(
    renderer: MarkdownRenderer, minimal_report: GenerationReport
) -> None:
    report = replace(minimal_report, output_path=Path("a") / "b" / "t.py")
    assert "`a/b/t.py`" in renderer.render(report)


def _rendered_with(report: GenerationReport, **changes: object) -> str:
    return MarkdownRenderer().render(replace(report, **changes))  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["Account.__init__", "Account.__eq__", "f*x*y", "_a_"])
def test_function_names_are_code_wrapped(
    renderer: MarkdownRenderer,
    full_report: GenerationReport,
    make_function_delta: Callable[[str, int, int], FunctionCoverageDelta],
    name: str,
) -> None:
    delta = replace(full_report.delta, per_function=(make_function_delta(name, 0, 10),))
    text = renderer.render(replace(full_report, delta=delta))
    assert f"| `{name}` |" in text


_FENCE = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")


@pytest.mark.parametrize("text", ["a``b", "```x", "x`", "`", " a ", "a  b", "a\nb\tc"])
def test_code_is_a_valid_span(text: str) -> None:
    """Decode the span the CommonMark way and expect the one-line input back."""
    match = _FENCE.fullmatch(_code(text))
    assert match is not None
    inner = match.group(2)
    if inner.startswith(" ") and inner.endswith(" ") and inner.strip(" "):
        inner = inner[1:-1]
    assert inner == " ".join(text.replace(chr(9), " ").splitlines())


def test_code_empty_uses_placeholder() -> None:
    assert _code("") == "`(empty)`"


def test_code_fence_longer_than_inner_run() -> None:
    assert _code("a``b") == "```a``b```"
    assert _code("```x") == "```` ```x ````"


def test_empty_observed_and_name_render_placeholder(
    minimal_report: GenerationReport,
) -> None:
    bug = SuspectedBug("f", {}, "", "e")
    drop = DroppedTest("", "f", DropReason.UNSTABLE)
    text = _rendered_with(minimal_report, suspected_bugs=(bug,), dropped=(drop,))
    assert "observed `(empty)`" in text
    assert "- `(empty)` (`f`)" in text


def test_angle_brackets_in_free_text_are_escaped(minimal_report: GenerationReport) -> None:
    bug = SuspectedBug("f", {}, "o", "bad <lambda> & more")
    drop = DroppedTest("t", "f", DropReason.STILL_FAILING, "NameError in <module> at <lambda>")
    text = _rendered_with(minimal_report, suspected_bugs=(bug,), dropped=(drop,))
    assert "NameError in &lt;module&gt; at &lt;lambda&gt;" in text
    assert "  - bad &lt;lambda&gt; &amp; more" in text
    assert "<module>" not in text
    assert "<lambda>" not in text


def test_bug_inputs_keep_internal_whitespace(minimal_report: GenerationReport) -> None:
    bug = SuspectedBug("f", {"s": "a  b", "t": "x   y"}, "returned 'a  b'", "e")
    text = _rendered_with(minimal_report, suspected_bugs=(bug,))
    assert "`s='a  b', t='x   y'`" in text
    assert "observed `returned 'a  b'`" in text


def test_free_text_still_collapsed(minimal_report: GenerationReport) -> None:
    bug = SuspectedBug("f", {}, "o", "why \n   not")
    assert "  - why not" in _rendered_with(minimal_report, suspected_bugs=(bug,))
