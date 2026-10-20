from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pytestgen.domain.models import Arc, TestStatus
from pytestgen.sandbox.console import parse_console
from pytestgen.sandbox.coverage_json import parse_file_coverage
from pytestgen.sandbox.junit import node_id, parse_junit

JUNIT = """\
<testsuites><testsuite name="pytest" tests="7">
<testcase classname="pytestgen_tests.test_x" name="test_ok" @F time="0.5"/>
<testcase classname="pytestgen_tests.test_x.TestA" name="test_m[1-2]" @F time="0.1">
  <failure message="assert 1 == 2">Traceback body</failure>
</testcase>
<testcase classname="pytestgen_tests.test_x" name="test_err" @F time="0">
  <error message="failed on setup with fixture">setup tb</error>
</testcase>
<testcase classname="pytestgen_tests.test_x" name="test_skip" @F>
  <skipped type="pytest.skip" message="because"/>
</testcase>
<testcase classname="pytestgen_tests.test_x" name="test_xf" @F time="bogus">
  <skipped type="pytest.xfail" message=""/>
</testcase>
<testcase classname="pytestgen_tests.test_x" name="test_slow" @F>
  <failure message="Failed: Timeout &gt;2.0s">timeout tb</failure>
</testcase>
<testcase classname="pytestgen_tests.test_x" name="test_slow_text" @F>
  <failure message="Failed">... Failed: Timeout &gt;5.0s ...</failure>
</testcase>
</testsuite></testsuites>
""".replace("@F", 'file="pytestgen_tests\\test_x.py"')


def test_junit_statuses_messages_and_node_ids(tmp_path: Path) -> None:
    report = parse_junit(JUNIT, tmp_path)
    by_id = {o.nodeid: o for o in report.outcomes}

    assert report.collection_error is None
    assert by_id["pytestgen_tests/test_x.py::test_ok"].status is TestStatus.PASSED
    assert by_id["pytestgen_tests/test_x.py::test_ok"].duration_s == 0.5
    failed = by_id["pytestgen_tests/test_x.py::TestA::test_m[1-2]"]
    assert (failed.status, failed.message, failed.traceback) == (
        TestStatus.FAILED,
        "assert 1 == 2",
        "Traceback body",
    )
    assert by_id["pytestgen_tests/test_x.py::test_err"].status is TestStatus.ERROR
    assert by_id["pytestgen_tests/test_x.py::test_skip"].status is TestStatus.SKIPPED
    assert by_id["pytestgen_tests/test_x.py::test_xf"].status is TestStatus.XFAILED
    assert by_id["pytestgen_tests/test_x.py::test_xf"].duration_s == 0.0
    assert by_id["pytestgen_tests/test_x.py::test_slow"].status is TestStatus.TIMEOUT
    assert by_id["pytestgen_tests/test_x.py::test_slow_text"].status is TestStatus.TIMEOUT
    assert failed.test_name == "test_m"


def test_junit_collection_errors_are_not_outcomes(tmp_path: Path) -> None:
    xml = (
        '<testsuites><testsuite><testcase classname="pytestgen_tests.test_bad" name="">'
        '<error message="collection failure">SyntaxError: invalid syntax</error>'
        "</testcase></testsuite></testsuites>"
    )
    report = parse_junit(xml, tmp_path)
    assert report.outcomes == ()
    assert report.collection_error is not None
    assert "SyntaxError" in report.collection_error


def test_junit_garbage_is_an_empty_report(tmp_path: Path) -> None:
    report = parse_junit("<testsuites><unclosed>", tmp_path)
    assert report.outcomes == ()
    assert report.collection_error is None


@pytest.mark.parametrize(
    ("classname", "name", "file", "expected"),
    [
        # junit_family=legacy: the file attribute is authoritative
        ("my.tests.test_x", "test_a[1]", "my.tests\\test_x.py", "my.tests/test_x.py::test_a[1]"),
        (
            "my.tests.test_x.TestA.Inner",
            "test_a",
            "my.tests/test_x.py",
            "my.tests/test_x.py::TestA::Inner::test_a",
        ),
        ("pkg.test_x", "test_a", "other/place.py", "pkg::test_x::test_a"),  # inconsistent: fallback
        ("", "test_a", None, "test_a"),
    ],
)
def test_node_id_with_and_without_file_attribute(
    tmp_path: Path, classname: str, name: str, file: str | None, expected: str
) -> None:
    assert node_id(classname, name, tmp_path, file) == expected


def test_node_id_fallback_finds_the_module_on_disk(tmp_path: Path) -> None:
    (tmp_path / "pytestgen_tests").mkdir()
    (tmp_path / "pytestgen_tests" / "test_x.py").write_text("", encoding="utf-8")
    assert (
        node_id("pytestgen_tests.test_x.TestA", "test_m", tmp_path)
        == "pytestgen_tests/test_x.py::TestA::test_m"
    )
    assert node_id("unknown.module", "test_m", tmp_path) == "unknown::module::test_m"


# ------------------------------------------------------------------ coverage json

REPORT = {
    "files": {
        "calc.py": {
            "executed_lines": [1, 2, 3],
            "missing_lines": [5],
            "executed_branches": [[2, 3]],
            "missing_branches": [[2, 5], [3, -1]],
        },
        "other.py": {"executed_lines": [1]},
    }
}


def test_coverage_matches_the_target_by_resolved_path(tmp_path: Path) -> None:
    cov = parse_file_coverage(
        json.dumps(REPORT), target=tmp_path / "calc.py", base=tmp_path, report_path="calc.py"
    )
    assert cov is not None
    assert cov.path == "calc.py"
    assert cov.executed_lines == {1, 2, 3}
    assert cov.missing_lines == {5}
    assert cov.executed_arcs == {Arc(2, 3)}
    assert cov.missing_arcs == {Arc(2, 5), Arc(3, -1)}
    assert cov.branch_rate == pytest.approx(1 / 3)


def test_coverage_absolute_keys(tmp_path: Path) -> None:
    report = json.dumps({"files": {str(tmp_path / "calc.py"): REPORT["files"]["calc.py"]}})
    cov = parse_file_coverage(
        report, target=tmp_path / "calc.py", base=tmp_path / "elsewhere", report_path="c"
    )
    assert cov is not None
    assert cov.executed_lines == {1, 2, 3}


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive file system")
def test_coverage_path_match_ignores_case_on_windows(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("", encoding="utf-8")
    report = json.dumps({"files": {str(tmp_path / "calc.py").upper(): REPORT["files"]["calc.py"]}})
    cov = parse_file_coverage(report, target=tmp_path / "calc.py", base=tmp_path, report_path="c")
    assert cov is not None


def test_coverage_for_a_file_that_is_not_in_the_report(tmp_path: Path) -> None:
    cov = parse_file_coverage(
        json.dumps(REPORT), target=tmp_path / "zzz.py", base=tmp_path, report_path="zzz.py"
    )
    assert cov is None


@pytest.mark.parametrize("text", ["not json", "{}", '{"files": 3}', "[]"])
def test_coverage_garbage_is_none(tmp_path: Path, text: str) -> None:
    assert (
        parse_file_coverage(text, target=tmp_path / "calc.py", base=tmp_path, report_path="x")
        is None
    )


# ------------------------------------------------------------------ console fallback

LOG = """\
collected 5 items

pytestgen_tests/test_x.py::test_a PASSED                                 [ 20%]
pytestgen_tests/test_x.py::test_b[a b-1] FAILED                          [ 40%]
pytestgen_tests/test_x.py::TestC::test_c SKIPPED (why)                   [ 60%]
pytestgen_tests/test_x.py::test_d XFAIL                                  [ 80%]
pytestgen_tests/test_x.py::test_e XPASS                                  [ 90%]
pytestgen_tests/test_x.py::test_f ERROR                                  [ 99%]
pytestgen_tests/test_x.py::test_g PASSED                                 [100%]
pytestgen_tests/test_x.py::test_g ERROR
pytestgen_tests/test_x.py::test_hang """

BANNER = "+" * 20 + " Timeout " + "+" * 20


def test_console_verdicts() -> None:
    report = parse_console(LOG)
    statuses = {o.nodeid: o.status for o in report.outcomes}
    assert statuses == {
        "pytestgen_tests/test_x.py::test_a": TestStatus.PASSED,
        "pytestgen_tests/test_x.py::test_b[a b-1]": TestStatus.FAILED,
        "pytestgen_tests/test_x.py::TestC::test_c": TestStatus.SKIPPED,
        "pytestgen_tests/test_x.py::test_d": TestStatus.XFAILED,
        "pytestgen_tests/test_x.py::test_e": TestStatus.PASSED,
        "pytestgen_tests/test_x.py::test_f": TestStatus.ERROR,
        "pytestgen_tests/test_x.py::test_g": TestStatus.ERROR,  # teardown error wins over PASSED
    }


def test_console_without_the_timeout_banner_blames_nobody() -> None:
    assert parse_console(LOG).hanging is None


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        (LOG, BANNER + "\nStack of MainThread\n"),  # banner only on stderr
        (LOG + BANNER + "\n  File x\n", ""),  # banner after the unfinished line
        (LOG.rsplit("\n", 1)[0] + f"\npytestgen_tests/test_x.py::test_hang {BANNER}\n", ""),
    ],
)
def test_console_hanging_test_is_the_unfinished_line(stdout: str, stderr: str) -> None:
    assert parse_console(stdout, stderr).hanging == "pytestgen_tests/test_x.py::test_hang"


def test_console_banner_without_any_test_line() -> None:
    assert parse_console("collecting ...\n", BANNER).hanging is None
