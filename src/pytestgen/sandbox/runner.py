"""Runs a pytest suite with branch coverage in a throw-away workspace (``SuiteRunner`` port)."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pytestgen.domain.models import FileCoverage, RunResult, TestOutcome, TestStatus
from pytestgen.errors import SandboxError
from pytestgen.log import get_logger
from pytestgen.ports import RunRequest
from pytestgen.sandbox.console import ConsoleReport, parse_console
from pytestgen.sandbox.coverage_json import parse_file_coverage
from pytestgen.sandbox.env import build_sandbox_env
from pytestgen.sandbox.junit import JunitReport, parse_junit
from pytestgen.sandbox.process import ProcessResult, run_process
from pytestgen.sandbox.workspace import Workspace

TESTS_DIR = "pytestgen_tests"
OUTPUT_TAIL = 6000
MAX_TIMEOUT_RERUNS = 3
_REPORT_TIMEOUT_S = 30.0
_OK_EXIT_CODES = frozenset({0, 5})  # 5: no tests collected
_PARTIAL_NOTE = (
    "[pytestgen] {count} test(s) hit the per-test timeout and killed the pytest process; "
    "they were deselected and the rest re-run, so results and coverage cover the remaining "
    "tests only (coverage may be missing or partial)."
)
_COVERAGE_RC = "[run]\nbranch = True\n"
"""Private coverage config: the project's own ``[tool.coverage]`` must not change the data file."""

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One pytest invocation plus the reports it left behind."""

    process: ProcessResult
    junit: JunitReport | None
    console: ConsoleReport
    coverage: FileCoverage | None


class _Session:
    """Everything needed to (re)run pytest inside one workspace."""

    def __init__(
        self, python: str, ws: Workspace, request: RunRequest, files: list[str], target: Path
    ) -> None:
        self._python = python
        self._ws = ws
        self._request = request
        self._files = files
        self._target = target
        self._env = build_sandbox_env(ws.pythonpath, ws.home)
        self._env["PYTHONUNBUFFERED"] = "1"  # keep the -v log complete if the process is killed
        self._rc = ws.scratch / "coveragerc"
        self._rc.write_text(_COVERAGE_RC, encoding="utf-8")
        self._data = ws.scratch / ".coverage"
        self._junit = ws.scratch / "junit.xml"
        self._json = ws.scratch / "coverage.json"

    def attempt(self, deselect: Iterable[str], timeout_s: float) -> _Attempt:
        for stale in (self._data, self._junit, self._json):
            stale.unlink(missing_ok=True)
        process = run_process(
            self._pytest_cmd(deselect), cwd=self._ws.root, env=self._env, timeout_s=timeout_s
        )
        junit = self._read_junit()
        console = ConsoleReport() if junit else parse_console(process.stdout, process.stderr)
        coverage = None if process.timed_out else self._read_coverage()
        return _Attempt(process, junit, console, coverage)

    # ------------------------------------------------------------------ commands

    def _coverage_cmd(self, *args: str) -> list[str]:
        return [self._python, "-m", "coverage", *args, f"--rcfile={self._rc}"]

    def _pytest_cmd(self, deselect: Iterable[str]) -> list[str]:
        per_test = self._request.per_test_timeout_s
        method = "thread" if sys.platform == "win32" else "signal"
        cmd = self._coverage_cmd(
            "run", "--branch", f"--data-file={self._data}", f"--include={self._target}"
        )
        cmd += ["-m", "pytest", *self._files]
        cmd += ["-p", "no:cacheprovider", "-o", "addopts=", "-o", "junit_family=legacy", "-v"]
        cmd += [f"--rootdir={self._ws.root}", f"--junitxml={self._junit}"]
        cmd += [f"--timeout={per_test:g}", f"--timeout-method={method}"]
        cmd += ["-W", "ignore::DeprecationWarning"]
        for nodeid in deselect:
            cmd += ["--deselect", nodeid]
        return cmd

    # ------------------------------------------------------------------ reports

    def _read_junit(self) -> JunitReport | None:
        try:
            text = self._junit.read_text(encoding="utf-8")
        except OSError:
            return None
        return parse_junit(text, self._ws.root)

    def _read_coverage(self) -> FileCoverage | None:
        if not self._data.is_file():
            return None
        report = run_process(
            self._coverage_cmd(
                "json",
                f"--data-file={self._data}",
                "-o",
                str(self._json),
                f"--include={self._target}",
            ),
            cwd=self._ws.root,
            env=self._env,
            timeout_s=_REPORT_TIMEOUT_S,
        )
        if report.returncode != 0:
            _log.warning("coverage_json_failed", stderr=report.stderr[-300:])
            return None
        try:
            text = self._json.read_text(encoding="utf-8")
        except OSError:
            return None
        return parse_file_coverage(
            text, target=self._target, base=self._ws.root, report_path=_report_path(self._request)
        )


class SubprocessSuiteRunner:
    """Copies the project, runs the given tests under ``coverage run --branch -m pytest``.

    Branch coverage is reported for ``request.target`` only. On Windows a test that
    exceeds its time limit makes pytest-timeout kill the whole pytest process, which
    loses ``junit.xml`` and the coverage data. The runner then reads the ``-v`` log,
    records the culprit as ``TIMEOUT``, deselects it and runs again (at most
    ``MAX_TIMEOUT_RERUNS`` times) so the remaining tests still produce a signal.
    """

    def __init__(self, python_executable: str | None = None) -> None:
        self._python = python_executable or sys.executable

    def run(self, request: RunRequest) -> RunResult:
        started = time.monotonic()
        with Workspace(request.project_root) as ws:
            files = self._materialize(ws, request)
            if not files:
                return RunResult()
            session = _Session(self._python, ws, request, files, ws.map_path(request.target))
            attempt, hung = self._run_with_reruns(session, request.timeout_s, started)
        return _result(attempt, hung, time.monotonic() - started)

    @staticmethod
    def _run_with_reruns(
        session: _Session, budget_s: float, started: float
    ) -> tuple[_Attempt, list[str]]:
        hung: list[str] = []
        while True:
            remaining = budget_s - (time.monotonic() - started)
            attempt = session.attempt(hung, max(remaining, 1.0))
            culprit = attempt.console.hanging
            if (
                culprit is None
                or culprit in hung
                or attempt.process.timed_out
                or len(hung) >= MAX_TIMEOUT_RERUNS
                or budget_s - (time.monotonic() - started) <= 0
            ):
                return attempt, hung
            _log.warning("test_timeout_killed_run", nodeid=culprit)
            hung.append(culprit)

    @staticmethod
    def _materialize(ws: Workspace, request: RunRequest) -> list[str]:
        """Write the generated tests; return every test file to run, relative to the workspace."""
        files: list[str] = []
        if request.test_sources:
            init = ws.root / TESTS_DIR / "__init__.py"
            if not init.exists():
                ws.write_text(f"{TESTS_DIR}/__init__.py", "")
            for name, source in request.test_sources.items():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise SandboxError(f"invalid test file name: {name}")
                path = ws.write_text(f"{TESTS_DIR}/{name}", source)
                files.append(path.relative_to(ws.root).as_posix())
        for original in request.existing_tests:
            mapped = ws.map_path(original)
            if mapped.is_file():
                files.append(mapped.relative_to(ws.root).as_posix())
            else:
                _log.warning("existing_test_missing", path=str(original))
        return files


def _report_path(request: RunRequest) -> str:
    try:
        return request.target.resolve().relative_to(request.project_root.resolve()).as_posix()
    except ValueError:
        return str(request.target)


def _result(attempt: _Attempt, hung: list[str], duration_s: float) -> RunResult:
    process = attempt.process
    reported = attempt.junit.outcomes if attempt.junit else attempt.console.outcomes
    outcomes = list(reported)
    seen = {o.nodeid for o in outcomes}
    culprits = [*hung, *([attempt.console.hanging] if attempt.console.hanging else [])]
    for nodeid in dict.fromkeys(culprits):
        if nodeid not in seen:
            outcomes.append(_timeout_outcome(nodeid))
    collection_error = _collection_error(attempt, outcomes)
    tail = f"{process.stdout}{process.stderr}"[-OUTPUT_TAIL:]
    if culprits:
        tail = f"{_PARTIAL_NOTE.format(count=len(set(culprits)))}\n{tail}"
    return RunResult(
        outcomes=tuple(outcomes),
        coverage=attempt.coverage,
        duration_s=duration_s,
        timed_out=process.timed_out,
        collection_error=collection_error,
        output_tail=tail,
    )


def _timeout_outcome(nodeid: str) -> TestOutcome:
    return TestOutcome(
        nodeid=nodeid,
        status=TestStatus.TIMEOUT,
        message="Failed: Timeout (pytest-timeout thread method killed the run)",
    )


def _collection_error(attempt: _Attempt, outcomes: list[TestOutcome]) -> str | None:
    process = attempt.process
    if attempt.junit and attempt.junit.collection_error:
        return attempt.junit.collection_error
    if process.timed_out or outcomes or process.returncode in _OK_EXIT_CODES:
        return None
    # Exit codes 2/3/4 are pytest's own "interrupted / internal error / usage error";
    # any other failure without a single test outcome means nothing could be run either.
    return f"{process.stderr}{process.stdout}"[-OUTPUT_TAIL:].strip() or (
        f"pytest exited with code {process.returncode}"
    )
