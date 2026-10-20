from __future__ import annotations

import io
import json
import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from rich.console import Console

from pytestgen.domain.models import Arc, FunctionCoverageDelta, GenerationReport
from pytestgen.errors import PytestgenError
from pytestgen.ports import ReportRenderer
from pytestgen.report import (
    JsonRenderer,
    ReportFormatError,
    render_console,
    report_from_json,
)
from pytestgen.report._plain import to_plain

# --------------------------------------------------------------------------- json


def test_json_implements_port() -> None:
    renderer = JsonRenderer()
    assert isinstance(renderer, ReportRenderer)
    assert renderer.format_name == "json"


def test_json_round_trip(full_report: GenerationReport) -> None:
    text = JsonRenderer().render(full_report)
    data = report_from_json(text)
    assert data == json.loads(text)
    assert data["module"] == "pkg.calc"
    assert data["strategy"] == "hybrid"
    assert data["kept_tests"] == 7
    assert data["output_path"] == str(Path("tests") / "test_calc_gen.py")
    assert data["dropped"][0]["reason"] == "still_failing"
    assert data["by_origin"][1] == {"origin": "llm_free", "tests": 3, "arcs_added": 2}
    assert data["suspected_bugs"][0]["inputs"] == {"a": 1, "b": 0}
    assert data["llm_usage"]["prompt_tokens"] == 1200
    assert data["model"] == "qwen2.5-coder"


def test_json_derived_fields(full_report: GenerationReport) -> None:
    delta = report_from_json(JsonRenderer().render(full_report))["delta"]
    assert delta["line_gain"] == pytest.approx(42.0)
    assert delta["branch_gain"] == pytest.approx(60.0)
    assert delta["before"]["line_percent"] == pytest.approx(20.0)
    assert delta["after"]["branch_percent"] == pytest.approx(80.0)
    assert delta["per_function"][0]["before"]["total_lines"] == 10
    assert "line_percent" in delta["per_function"][0]["after"]


def test_json_is_indented_and_unicode(full_report: GenerationReport) -> None:
    bug = replace(full_report.suspected_bugs[0], explanation="делить на ноль нельзя")
    text = JsonRenderer().render(replace(full_report, suspected_bugs=(bug,)))
    assert "делить на ноль нельзя" in text
    assert text.startswith('{\n  "module"')


def test_json_none_output_path(minimal_report: GenerationReport) -> None:
    assert report_from_json(JsonRenderer().render(minimal_report))["output_path"] is None


@pytest.mark.parametrize(
    "text",
    ["not json", "[1, 2]", '{"module": "m"}', "42"],
    ids=["garbage", "list", "missing-keys", "scalar"],
)
def test_report_from_json_rejects(text: str) -> None:
    with pytest.raises(ReportFormatError) as info:
        report_from_json(text)
    assert isinstance(info.value, PytestgenError)


def test_missing_keys_named_in_message() -> None:
    with pytest.raises(ReportFormatError, match="delta, kept_tests, strategy"):
        report_from_json('{"module": "m"}')


class _Opaque:
    def __repr__(self) -> str:
        return "<opaque>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Arc(3, -1), "3->-1"),
        (frozenset({Arc(10, 2), Arc(2, 3), Arc(2, -1)}), ["2->-1", "2->3", "10->2"]),
        (frozenset({3, 1, 2}), [1, 2, 3]),
        (frozenset({1, "a"}), ["a", 1]),  # mixed types: ordered by repr
        (frozenset(), []),
        ({1: (1, 2), Arc(1, 2): {"x"}}, {"1": [1, 2], "1->2": ["x"]}),
        (Path("a"), str(Path("a"))),
        (b"raw", "b'raw'"),
        (_Opaque(), "<opaque>"),
        (None, None),
        (1.5, 1.5),
    ],
)
def test_to_plain(value: object, expected: object) -> None:
    assert to_plain(value) == expected


def test_arcs_in_inputs_are_serialized(minimal_report: GenerationReport) -> None:
    from pytestgen.domain.models import SuspectedBug

    bug = SuspectedBug("f", {"arcs": frozenset({Arc(1, 2)}), "p": Path("x")}, "o", "e")
    data = report_from_json(JsonRenderer().render(replace(minimal_report, suspected_bugs=(bug,))))
    assert data["suspected_bugs"][0]["inputs"] == {"arcs": ["1->2"], "p": str(Path("x"))}


# --------------------------------------------------------------------------- console


def _render(report: GenerationReport) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    render_console(report, console)
    return console.export_text()


def test_console_full(full_report: GenerationReport) -> None:
    text = _render(full_report)
    for expected in (
        "pytestgen: pkg.calc",
        "Lines",
        "20.0%",
        "62.0%",
        "+42.0 pp",
        "+60.0 pp",
        "Kept tests: 7",
        "llm_free",
        "divide",
        "Account.withdraw",
        "0.0% -> 100.0%",
        "Suspected bugs",
        "a=1, b=0",
        "returns 0",
        "Dropped tests (3)",
        "still_failing (2)",
        "test_a (add): AssertionError: 1 != 2",
        "5 requests, 2 cache hits, 1,500 tokens",
        "model qwen2.5-coder",
        "duration: 12.3 s",
        "test_calc_gen.py",
    ):
        assert expected in text, expected


def test_console_minimal(minimal_report: GenerationReport) -> None:
    text = _render(minimal_report)
    assert "pytestgen: m" in text
    for absent in ("Suspected bugs", "Dropped", "Per function", "by origin", "Output:", "model"):
        assert absent not in text


def test_console_colours_gain_green_and_red(full_report: GenerationReport) -> None:
    console = Console(record=True, width=120, force_terminal=True, color_system="standard")
    render_console(full_report, console)
    assert "\x1b[32m+42.0 pp" in console.export_text(styles=True)

    worse = replace(
        full_report,
        delta=replace(
            full_report.delta, before=full_report.delta.after, after=full_report.delta.before
        ),
    )
    console = Console(record=True, width=120, force_terminal=True, color_system="standard")
    render_console(worse, console)
    assert "\x1b[31m-42.0 pp" in console.export_text(styles=True)


def test_console_truncates_function_rows(
    full_report: GenerationReport,
    make_function_delta: Callable[[str, int, int], FunctionCoverageDelta],
) -> None:
    funcs = tuple(make_function_delta(f"fn{i:02d}", 0, 10) for i in range(18))
    text = _render(replace(full_report, delta=replace(full_report.delta, per_function=funcs)))
    assert "...and 3 more" in text


def test_console_does_not_interpret_markup(minimal_report: GenerationReport) -> None:
    from pytestgen.domain.models import SuspectedBug

    bug = SuspectedBug("f", {"x": "[bold]"}, "[red]boom[/red]", "see [link]")
    text = _render(replace(minimal_report, module="[x]", suspected_bugs=(bug,)))
    assert "[x]" in text
    assert "[red]boom[/red]" in text
    assert "see [link]" in text


@pytest.mark.parametrize("encoding", ["cp1251", "cp1252", "ascii", "utf-8"])
def test_console_never_fails_on_narrow_streams(
    full_report: GenerationReport, encoding: str
) -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=encoding)
    console = Console(file=stream, width=120, legacy_windows=False)
    render_console(full_report, console)
    stream.flush()
    assert b"0.0% -> 100.0%" in raw.getvalue()


# --------------------------------------------------------------------------- non-finite floats


@pytest.mark.parametrize(
    ("value", "expected"),
    [(math.nan, "nan"), (math.inf, "inf"), (-math.inf, "-inf")],
)
def test_non_finite_floats_are_strict_json(
    minimal_report: GenerationReport, value: float, expected: str
) -> None:
    from pytestgen.domain.models import SuspectedBug

    bug = SuspectedBug("f", {"x": value, "ok": 1.5}, "o", "e")
    text = JsonRenderer().render(replace(minimal_report, suspected_bugs=(bug,)))

    def reject(constant: str) -> object:
        raise AssertionError(constant)

    data = json.loads(text, parse_constant=reject)
    assert data["suspected_bugs"][0]["inputs"] == {"x": expected, "ok": 1.5}


def test_to_plain_non_finite_in_nested_structures() -> None:
    assert to_plain({"a": [math.inf, (math.nan,)]}) == {"a": ["inf", ["nan"]]}
