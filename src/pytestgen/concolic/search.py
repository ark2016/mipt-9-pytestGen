"""Model search: satisfy the constraints, preferring small, readable values."""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import z3

from pytestgen.concolic.environment import Variable
from pytestgen.concolic.ptypes import Kind
from pytestgen.domain.models import SolveStatus

_HARD_LENGTH_CAP = 256
"""Longest string/sequence ever produced, however long the condition would like it.

z3's string solver cannot find much longer strings within a sane time anyway."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    status: SolveStatus
    """``SAT``, ``UNSAT`` or ``UNKNOWN``."""
    model: Any | None = None
    reason: str = ""


class _Budget:
    """One time budget shared by every z3 call of a single search."""

    def __init__(self, timeout_ms: int) -> None:
        self._deadline = time.monotonic() + timeout_ms / 1000

    def remaining_ms(self) -> int:
        return max(1, int((self._deadline - time.monotonic()) * 1000))

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self._deadline


class ModelSearch:
    """Runs z3 ``Optimize`` with soft preferences for "nice" values.

    Preferences, in priority order: strings over ``[a-z0-9_ ]``, non-negative numbers,
    small absolute values, short strings. Strings and sequence lengths are bounded by
    ``max_length``; if that makes the problem unsatisfiable the bound is relaxed to a hard
    cap (a condition like ``len(s) > 100`` is satisfiable, just not with a short string).
    Needing more than the cap is reported as ``UNKNOWN``. ``timeout_ms`` is the budget of
    the whole search, not of each z3 call.
    """

    def __init__(self, timeout_ms: int, max_length: int, length_cap: int | None = None) -> None:
        self._timeout_ms = timeout_ms
        self._max_length = max_length
        self._cap = max(max_length, _HARD_LENGTH_CAP) if length_cap is None else length_cap

    def find(self, constraints: Sequence[Any], variables: Iterable[Variable]) -> SearchResult:
        modelled = [v for v in variables if v.term is not None]
        lengths = _length_terms(modelled)
        budget = _Budget(self._timeout_ms)
        result = self._attempt(
            [*constraints, *_bounds(lengths, self._max_length)], modelled, budget
        )
        if result.status is not SolveStatus.UNSAT or not lengths:
            return result
        result = self._attempt([*constraints, *_bounds(lengths, self._cap)], modelled, budget)
        if result.status is not SolveStatus.UNSAT:
            return result
        return self._beyond_cap(constraints, lengths, budget)

    def _beyond_cap(
        self, constraints: Sequence[Any], lengths: Sequence[Any], budget: _Budget
    ) -> SearchResult:
        """Distinguish "unsatisfiable" from "needs more than the cap" (no model is built)."""
        if budget.expired:
            return SearchResult(SolveStatus.UNKNOWN, reason="z3 gave up: timeout")
        solver = z3.Solver()
        solver.set(timeout=budget.remaining_ms())
        for constraint in [*constraints, z3.Or(*[length > self._cap for length in lengths])]:
            solver.add(constraint)
        outcome = solver.check()
        if outcome == z3.unsat:
            return SearchResult(SolveStatus.UNSAT)
        if outcome == z3.sat:
            reason = f"the condition needs a string or sequence longer than {self._cap}"
            return SearchResult(SolveStatus.UNKNOWN, reason=reason)
        return SearchResult(SolveStatus.UNKNOWN, reason=f"z3 gave up: {solver.reason_unknown()}")

    def _attempt(
        self, constraints: Sequence[Any], variables: Sequence[Variable], budget: _Budget
    ) -> SearchResult:
        optimize = z3.Optimize()
        optimize.set(timeout=budget.remaining_ms())
        for constraint in constraints:
            optimize.add(constraint)
        _add_preferences(optimize, variables)
        outcome = optimize.check()
        if outcome == z3.sat:
            return SearchResult(SolveStatus.SAT, optimize.model())
        if outcome == z3.unsat:
            return SearchResult(SolveStatus.UNSAT)
        return self._plain(constraints, str(optimize.reason_unknown()), budget)

    @staticmethod
    def _plain(constraints: Sequence[Any], optimize_reason: str, budget: _Budget) -> SearchResult:
        """Fallback when optimisation gives up: any model will do."""
        if budget.expired:
            return SearchResult(SolveStatus.UNKNOWN, reason=f"z3 gave up: {optimize_reason}")
        solver = z3.Solver()
        solver.set(timeout=budget.remaining_ms())
        for constraint in constraints:
            solver.add(constraint)
        outcome = solver.check()
        if outcome == z3.sat:
            return SearchResult(SolveStatus.SAT, solver.model())
        if outcome == z3.unsat:
            return SearchResult(SolveStatus.UNSAT)
        reason = str(solver.reason_unknown()) or optimize_reason
        return SearchResult(SolveStatus.UNKNOWN, reason=f"z3 gave up: {reason}")


def _length_terms(variables: Sequence[Variable]) -> list[Any]:
    """Length terms of the strings and sequences among ``variables``."""
    terms = []
    for var in variables:
        if var.ptype.kind is Kind.STR:
            terms.append(z3.Length(var.term))
        elif var.ptype.is_length_modelled:
            terms.append(var.term)
    return terms


def _bounds(lengths: Sequence[Any], limit: int) -> list[Any]:
    return [length <= limit for length in lengths]


def _add_preferences(optimize: Any, variables: Sequence[Variable]) -> None:
    strings = [v.term for v in variables if v.ptype.kind is Kind.STR]
    integers = [v.term for v in variables if v.ptype.kind is Kind.INT or v.ptype.is_length_modelled]
    # a real prefers to be a whole number: tie it to an auxiliary int that is minimised too
    wholes = [z3.Int(f"{v.name}__whole") for v in variables if v.ptype.kind is Kind.REAL]
    reals = [v.term for v in variables if v.ptype.kind is Kind.REAL]
    charset = z3.Star(z3.Union(z3.Range("a", "z"), z3.Range("0", "9"), z3.Re(" "), z3.Re("_")))
    for term in strings:
        optimize.add_soft(z3.InRe(term, charset), id="charset")
    for real, whole in zip(reals, wholes, strict=True):
        optimize.add_soft(real == z3.ToReal(whole), id="whole")
    for term in [*integers, *wholes]:
        optimize.add_soft(term >= 0, id="sign")
    if integers or wholes:
        optimize.minimize(z3.Sum([z3.If(t >= 0, t, -t) for t in [*integers, *wholes]]))
    if strings:
        optimize.minimize(z3.Sum([z3.Length(t) for t in strings]))
