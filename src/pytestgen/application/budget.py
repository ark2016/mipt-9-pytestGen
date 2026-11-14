"""Limits of one generation run."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenerationBudget:
    """How much effort a run may spend; the defaults match :class:`pytestgen.config.Settings`."""

    repair_attempts: int = 2
    """Repairs per failing free-form test before it is dropped."""
    gap_iterations: int = 2
    """Rounds of aiming new tests at uncovered code."""
    run_timeout_s: float = 120.0
    """Wall-clock limit of one sandboxed pytest run."""
    test_timeout_s: float = 10.0
    """Limit for a single test."""
    max_minimize_runs: int = 12
    """Trial runs ``--minimize`` may spend."""
