"""Builders for application-layer tests: real analysed example modules and small domain objects."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pytestgen.analysis import AstModuleAnalyzer
from pytestgen.domain.models import (
    CaseOrigin,
    ModuleInfo,
    Observation,
    ObservationKind,
    TestCaseSpec,
    TestOutcome,
    TestStatus,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


def analyze_example(relative: str) -> ModuleInfo:
    """Analyse ``examples/<relative>`` with the real AST analyzer."""
    return AstModuleAnalyzer().analyze(EXAMPLES / relative, EXAMPLES)


def returned(inputs: Mapping[str, object], value_repr: str, function: str) -> TestCaseSpec:
    obs = Observation(inputs=inputs, kind=ObservationKind.RETURNED, value_repr=value_repr)
    return TestCaseSpec(function=function, inputs=inputs, observation=obs, origin=CaseOrigin.Z3)


def raised(
    inputs: Mapping[str, object], exc_type: str, message: str, function: str
) -> TestCaseSpec:
    obs = Observation(
        inputs=inputs,
        kind=ObservationKind.RAISED,
        exception_type=exc_type,
        exception_message=message,
    )
    return TestCaseSpec(function=function, inputs=inputs, observation=obs, origin=CaseOrigin.Z3)


def failed_outcome(name: str, message: str = "assert 1 == 2", traceback: str = "") -> TestOutcome:
    return TestOutcome(
        nodeid=f"test_gen.py::{name}",
        status=TestStatus.FAILED,
        message=message,
        traceback=traceback,
    )
