"""Translate pytest's junit XML (xunit2) into domain outcomes."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from pytestgen.domain.models import TestOutcome, TestStatus

_TIMEOUT = re.compile(r"Timeout\s*\(?>\s*\d")
"""pytest-timeout reports ``Failed: Timeout (>10.0s) from pytest-timeout.`` (older: ``>10.0s``)."""
_COLLECTION = "collection failure"


@dataclass(frozen=True, slots=True)
class JunitReport:
    outcomes: tuple[TestOutcome, ...] = ()
    collection_error: str | None = None


def parse_junit(xml_text: str, workspace_root: Path) -> JunitReport:
    """Parse ``xml_text``; node ids are relative to ``workspace_root``, ``/``-separated.

    Needs ``junit_family=legacy``: only that family records the ``file`` of each testcase.
    """
    try:
        # The file is written by pytest into the private scratch directory.
        suite = ET.fromstring(xml_text)  # noqa: S314
    except ET.ParseError:
        return JunitReport()
    outcomes: list[TestOutcome] = []
    collection_errors: list[str] = []
    for case in suite.iter("testcase"):
        verdict = _verdict(case)
        if verdict is None:
            outcomes.append(_outcome(case, workspace_root, TestStatus.PASSED, "", ""))
            continue
        tag, status, message, text = verdict
        if tag == "error" and _COLLECTION in message:
            collection_errors.append(f"{message}: {case.get('name', '')}\n{text}".strip())
        else:
            outcomes.append(_outcome(case, workspace_root, status, message, text))
    return JunitReport(tuple(outcomes), "\n\n".join(collection_errors) or None)


def _verdict(case: ET.Element) -> tuple[str, TestStatus, str, str] | None:
    """``(tag, status, message, traceback)`` of the first problem element, ``None`` if passed."""
    for tag in ("failure", "error", "skipped"):
        element = case.find(tag)
        if element is None:
            continue
        message = element.get("message", "")
        text = element.text or ""
        if tag == "skipped":
            xfail = element.get("type") == "pytest.xfail"
            return tag, TestStatus.XFAILED if xfail else TestStatus.SKIPPED, message, text
        if _TIMEOUT.search(message) or _TIMEOUT.search(text):
            return tag, TestStatus.TIMEOUT, message, text
        return tag, TestStatus.FAILED if tag == "failure" else TestStatus.ERROR, message, text
    return None


def _outcome(
    case: ET.Element, root: Path, status: TestStatus, message: str, text: str
) -> TestOutcome:
    return TestOutcome(
        nodeid=node_id(case.get("classname", ""), case.get("name", ""), root, case.get("file")),
        status=status,
        message=message,
        traceback=text,
        duration_s=_seconds(case.get("time")),
    )


def _seconds(raw: str | None) -> float:
    try:
        return float(raw) if raw else 0.0
    except ValueError:
        return 0.0


def node_id(classname: str, name: str, root: Path, file: str | None = None) -> str:
    """Build ``pytestgen_tests/test_x.py::TestA::test_a[1]`` from junit attributes.

    With ``file`` (junit legacy family) the module is exact and the classes are what
    follows the module's dotted name in ``classname``; directories with dots are fine.
    Without it the longest dotted prefix of ``classname`` that names a ``.py`` file in
    ``root`` is the module.
    """
    if file and file.endswith(".py"):
        module = file.replace("\\", "/")
        dotted = module[: -len(".py")].replace("/", ".")
        if classname == dotted or classname.startswith(dotted + "."):
            classes = classname[len(dotted) :].split(".")[1:]
            return "::".join([module, *classes, name])
    parts = classname.split(".") if classname else []
    for size in range(len(parts), 0, -1):
        if root.joinpath(*parts[: size - 1], f"{parts[size - 1]}.py").is_file():
            module = "/".join(parts[:size]) + ".py"
            return "::".join([module, *parts[size:], name])
    return "::".join([*parts, name])
