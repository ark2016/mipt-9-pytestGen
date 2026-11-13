"""Production rule for warning about repaired assertions.

This is a warning heuristic, not a proof that the function contains a bug.
"""

from __future__ import annotations

from pytestgen.domain.models import FunctionInfo, TestOutcome


def is_suspicious_repair(function: FunctionInfo | None, outcome: TestOutcome) -> bool:
    """Preserve the existing docstring + assertion-failure rule."""
    if function is None or not function.docstring:
        return False
    text = f"{outcome.message}\n{outcome.traceback}"
    return outcome.message.lstrip().startswith("assert") or "AssertionError" in text
