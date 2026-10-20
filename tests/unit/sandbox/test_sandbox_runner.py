from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pytestgen.domain.models import Arc, RunResult, TestStatus
from pytestgen.errors import SandboxError
from pytestgen.ports import RunRequest, SuiteRunner
from pytestgen.sandbox import SubprocessSuiteRunner

MIXED_TESTS = """\
import pytest

from calc import classify


def test_pass():
    assert classify(5) == "pos"


def test_fail():
    assert classify(5) == "neg"


def test_error_in_body():
    classify(None)


@pytest.fixture
def broken():
    raise RuntimeError("fixture boom")


def test_error_in_fixture(broken):
    pass


@pytest.mark.parametrize(("x", "expected"), [(1, "pos"), (-1, "neg"), (0, "zero")])
def test_param(x, expected):
    assert classify(x) == expected


@pytest.mark.parametrize("label", ["a b", "c/d"])
def test_param_with_odd_ids(label):
    assert label


class TestGroup:
    def test_method(self):
        assert classify(-3) == "neg"


@pytest.mark.skip(reason="skipped on purpose")
def test_skipped():
    pass


@pytest.mark.xfail
def test_expected_failure():
    assert False
"""


def snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def make_request(project: Path, sources: dict[str, str], **overrides: object) -> RunRequest:
    fields: dict[str, object] = {
        "project_root": project,
        "target": project / "calc.py",
        "test_sources": sources,
        "timeout_s": 60.0,
        "per_test_timeout_s": 10.0,
    }
    fields.update(overrides)
    return RunRequest(**fields)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def runner() -> SubprocessSuiteRunner:
    return SubprocessSuiteRunner()


def statuses(result: RunResult) -> dict[str, TestStatus]:
    return {o.nodeid: o.status for o in result.outcomes}


def test_runner_implements_the_port() -> None:
    assert isinstance(SubprocessSuiteRunner(), SuiteRunner)


def test_mixed_outcomes_nodeids_and_coverage(project: Path, runner: SubprocessSuiteRunner) -> None:
    before = snapshot(project)
    result = runner.run(make_request(project, {"test_mixed.py": MIXED_TESTS}))

    base = "pytestgen_tests/test_mixed.py"
    assert statuses(result) == {
        f"{base}::test_pass": TestStatus.PASSED,
        f"{base}::test_fail": TestStatus.FAILED,
        f"{base}::test_error_in_body": TestStatus.FAILED,
        f"{base}::test_error_in_fixture": TestStatus.ERROR,
        f"{base}::test_param[1-pos]": TestStatus.PASSED,
        f"{base}::test_param[-1-neg]": TestStatus.PASSED,
        f"{base}::test_param[0-zero]": TestStatus.PASSED,
        f"{base}::test_param_with_odd_ids[a b]": TestStatus.PASSED,
        f"{base}::test_param_with_odd_ids[c/d]": TestStatus.PASSED,
        f"{base}::TestGroup::test_method": TestStatus.PASSED,
        f"{base}::test_skipped": TestStatus.SKIPPED,
        f"{base}::test_expected_failure": TestStatus.XFAILED,
    }
    failed = next(o for o in result.outcomes if o.nodeid.endswith("::test_fail"))
    assert "assert" in failed.message
    assert "classify" in failed.traceback
    assert failed.duration_s >= 0
    assert result.collection_error is None
    assert not result.timed_out
    assert {o.test_name for o in result.failed} == {
        "test_fail",
        "test_error_in_body",
        "test_error_in_fixture",
    }

    # Coverage is measured on the target only, and arcs come out as domain objects.
    cov = result.coverage
    assert cov is not None
    assert cov.path == "calc.py"
    assert cov.executed_lines == {1, 2, 3, 4, 5, 6, 9}
    assert cov.missing_lines == {10}  # never_called() body
    assert cov.executed_arcs == {Arc(2, 3), Arc(2, 4), Arc(4, 5), Arc(4, 6)}
    assert cov.missing_arcs == frozenset()

    # Nothing leaked back into the original project.
    assert snapshot(project) == before


def test_target_is_the_only_measured_file(project: Path, runner: SubprocessSuiteRunner) -> None:
    source = "import other\n\ndef test_a():\n    assert other.helper() == 42\n"
    request = make_request(project, {"test_other.py": source}, target=project / "other.py")
    result = runner.run(request)
    assert result.coverage is not None
    assert result.coverage.path == "other.py"
    assert result.coverage.missing_lines == frozenset()
    assert statuses(result) == {"pytestgen_tests/test_other.py::test_a": TestStatus.PASSED}


def test_secrets_do_not_reach_the_tests(
    project: Path, runner: SubprocessSuiteRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOME_API_KEY", "k")
    monkeypatch.setenv("PYTESTGEN_LLM_API_KEY", "k")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy")
    source = (
        "import os\n\n"
        "def test_env():\n"
        "    assert 'SOME_API_KEY' not in os.environ\n"
        "    assert not [k for k in os.environ if k.startswith('PYTESTGEN_')]\n"
        "    assert 'HTTPS_PROXY' not in os.environ\n"
        "    assert os.environ['PYTHONHASHSEED'] == '0'\n"
    )
    result = runner.run(make_request(project, {"test_env.py": source}))
    assert statuses(result) == {"pytestgen_tests/test_env.py::test_env": TestStatus.PASSED}


def test_tests_cannot_modify_the_original_project(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    source = (
        "import pathlib\n\n"
        "def test_write():\n"
        "    pathlib.Path('calc.py').write_text('broken(')\n"
        "    pathlib.Path('created.txt').write_text('x')\n"
    )
    before = snapshot(project)
    result = runner.run(make_request(project, {"test_write.py": source}))
    assert statuses(result) == {"pytestgen_tests/test_write.py::test_write": TestStatus.PASSED}
    assert snapshot(project) == before


def test_syntax_error_in_test_is_a_collection_error(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    result = runner.run(make_request(project, {"test_bad.py": "def test_x(:\n    pass\n"}))
    assert result.outcomes == ()
    assert result.collection_error is not None
    assert "SyntaxError" in result.collection_error


def test_import_error_in_test_is_a_collection_error(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    result = runner.run(make_request(project, {"test_bad.py": "import nonexistent_xyz\n"}))
    assert result.collection_error is not None
    assert "nonexistent_xyz" in result.collection_error


def test_process_that_dies_without_a_report_counts_as_collection_error(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    result = runner.run(make_request(project, {"test_die.py": "import os\nos._exit(3)\n"}))
    assert result.outcomes == ()
    assert result.collection_error


def test_no_tests_collected_is_not_an_error(project: Path, runner: SubprocessSuiteRunner) -> None:
    result = runner.run(make_request(project, {"test_empty.py": "x = 1\n"}))
    assert result.outcomes == ()
    assert result.collection_error is None
    assert not result.timed_out


def test_nothing_to_run_returns_an_empty_result(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    assert runner.run(make_request(project, {})) == RunResult()


def test_existing_tests_run_with_their_project_conftest(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    tests = project / "my.tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef five():\n    return 5\n", encoding="utf-8"
    )
    existing = tests / "test_existing.py"
    existing.write_text(
        "import pytest\nfrom calc import classify\n\n"
        "@pytest.mark.parametrize('x', [1, 2])\n"
        "class TestPos:\n"
        "    def test_pos(self, x, five):\n"
        "        assert classify(x * five) == 'pos'\n",
        encoding="utf-8",
    )
    request = make_request(
        project,
        {"test_gen.py": "def test_g():\n    pass\n"},
        existing_tests=(existing, project / "my.tests" / "test_gone.py"),
    )
    result = runner.run(request)
    assert statuses(result) == {
        "my.tests/test_existing.py::TestPos::test_pos[1]": TestStatus.PASSED,
        "my.tests/test_existing.py::TestPos::test_pos[2]": TestStatus.PASSED,
        "pytestgen_tests/test_gen.py::test_g": TestStatus.PASSED,
    }


def test_target_without_coverage_data_yields_none(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    request = make_request(
        project,
        {"test_a.py": "def test_a():\n    pass\n"},
        target=project / "never_imported.py",
    )
    result = runner.run(request)
    assert statuses(result) == {"pytestgen_tests/test_a.py::test_a": TestStatus.PASSED}
    assert result.coverage is None


def test_target_outside_the_project_is_rejected(
    project: Path, runner: SubprocessSuiteRunner, tmp_path: Path
) -> None:
    request = make_request(
        project, {"test_a.py": "def test_a():\n    pass\n"}, target=tmp_path / "x.py"
    )
    with pytest.raises(SandboxError, match="outside the project root"):
        runner.run(request)


def test_test_source_names_cannot_escape_the_workspace(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    with pytest.raises(SandboxError, match="invalid test file name"):
        runner.run(make_request(project, {"../evil.py": "x = 1\n"}))


# ------------------------------------------------------------------ timeouts

HANGING = """\
import calc


def test_before():
    assert calc.classify(1) == "pos"


def test_hang():
    while True:
        pass


def test_after():
    assert calc.classify(-1) == "neg"
"""


def test_hanging_test_is_marked_timeout_and_the_rest_still_reports(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    result = runner.run(make_request(project, {"test_hang.py": HANGING}, per_test_timeout_s=0.5))

    base = "pytestgen_tests/test_hang.py"
    assert statuses(result) == {
        f"{base}::test_before": TestStatus.PASSED,
        f"{base}::test_hang": TestStatus.TIMEOUT,
        f"{base}::test_after": TestStatus.PASSED,
    }
    assert not result.timed_out
    assert result.collection_error is None
    assert result.coverage is not None  # the surviving tests still produced coverage
    assert Arc(2, 3) in result.coverage.executed_arcs
    assert Arc(4, 5) in result.coverage.executed_arcs


@pytest.mark.skipif(sys.platform != "win32", reason="thread timeout method kills the process")
def test_thread_timeout_rerun_is_documented_in_output_tail(
    project: Path, runner: SubprocessSuiteRunner
) -> None:
    result = runner.run(make_request(project, {"test_hang.py": HANGING}, per_test_timeout_s=0.5))
    assert "coverage may be missing or partial" in result.output_tail
    assert "deselected" in result.output_tail


@pytest.mark.skipif(sys.platform != "win32", reason="thread timeout method kills the process")
def test_repeated_hangs_are_all_recovered(project: Path, runner: SubprocessSuiteRunner) -> None:
    source = (
        "def test_ok():\n    pass\n\n"
        "def test_hang_a():\n    while True:\n        pass\n\n"
        "def test_hang_b():\n    while True:\n        pass\n"
    )
    result = runner.run(make_request(project, {"test_two.py": source}, per_test_timeout_s=0.5))
    assert sorted(o.status for o in result.outcomes) == [
        TestStatus.PASSED,
        TestStatus.TIMEOUT,
        TestStatus.TIMEOUT,
    ]


def test_whole_run_timeout(project: Path, runner: SubprocessSuiteRunner) -> None:
    source = "import time\n\ndef test_slow():\n    time.sleep(60)\n"
    request = make_request(
        project, {"test_slow.py": source}, timeout_s=2.0, per_test_timeout_s=120.0
    )
    result = runner.run(request)
    assert result.timed_out
    assert result.coverage is None
    assert result.collection_error is None
    assert result.duration_s < 15


def test_custom_python_executable_is_used(project: Path) -> None:
    runner = SubprocessSuiteRunner(python_executable=sys.executable)
    result = runner.run(make_request(project, {"test_a.py": "def test_a():\n    pass\n"}))
    assert len(result.passed) == 1
