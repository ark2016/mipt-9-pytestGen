"""Fallback parser for ``pytest -v`` output, used when no junit XML was written.

pytest-timeout's *thread* method (the only one that works on Windows) ends the whole
process with ``os._exit``: pytest never reaches its session finish, so ``junit.xml`` is
never written. The verbose console log is then the only record of what already ran.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pytestgen.domain.models import TestOutcome, TestStatus

_VERDICT = re.compile(
    r"^(?P<id>\S+\.py::\S.*?)\s+(?P<verdict>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)(?:\s|$)"
)
_PENDING = re.compile(r"^(?P<id>\S+\.py::\S.*?)\s*$")
_HANG_MARK = re.compile(r"\+{3,} Timeout \+{3,}")
_STATUS = {
    "PASSED": TestStatus.PASSED,
    "XPASS": TestStatus.PASSED,
    "FAILED": TestStatus.FAILED,
    "ERROR": TestStatus.ERROR,
    "SKIPPED": TestStatus.SKIPPED,
    "XFAIL": TestStatus.XFAILED,
}


@dataclass(frozen=True, slots=True)
class ConsoleReport:
    outcomes: tuple[TestOutcome, ...] = ()
    """Tests that printed a verdict before the process ended."""
    hanging: str | None = None
    """Node id of the test pytest-timeout killed the process in, when it can be told."""


def parse_console(stdout: str, stderr: str = "") -> ConsoleReport:
    """Outcomes with a printed verdict, plus the test that was running when the run was cut.

    The "hanging" test is the last ``-v`` line without a verdict, reported only when the
    pytest-timeout banner shows up (on ``stdout`` or ``stderr``): a run killed by anything
    else must not blame an innocent test.
    """
    verdicts: dict[str, TestStatus] = {}
    pending: str | None = None
    for line in stdout.splitlines():
        done = _VERDICT.match(line)
        if done is not None:
            nodeid = done["id"]
            status = _STATUS[done["verdict"]]
            if verdicts.get(nodeid, TestStatus.PASSED) is TestStatus.PASSED:
                verdicts[nodeid] = status
            pending = None
            continue
        started = _PENDING.match(line)
        banner = _HANG_MARK.search(line)
        if banner is not None:
            head = _PENDING.match(line[: banner.start()])
            pending = head["id"] if head is not None else pending
        elif started is not None:
            pending = started["id"]
    marked = _HANG_MARK.search(stdout) is not None or _HANG_MARK.search(stderr) is not None
    outcomes = tuple(TestOutcome(nodeid, status) for nodeid, status in verdicts.items())
    return ConsoleReport(outcomes, pending if marked else None)
