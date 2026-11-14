"""Which kind of test (solver, model slot, free-form) earned which arcs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import replace

from pytestgen.domain.models import (
    Arc,
    CaseOrigin,
    FileCoverage,
    OriginStats,
    RunResult,
    TestSuite,
)

_ORIGIN_ORDER = (CaseOrigin.Z3, CaseOrigin.LLM_SLOT, CaseOrigin.LLM_FREE)


class OriginAttribution:
    """Tests per origin and the arcs each origin adds on top of the earlier ones.

    Origins are layered Z3, then LLM slots, then free-form; the layer that is the whole
    suite reuses the final coverage, so at most two extra runs are needed.
    """

    def __init__(self, run_plain: Callable[[TestSuite], RunResult]) -> None:
        self._run = run_plain

    def stats(
        self,
        suite: TestSuite,
        counts: Counter[CaseOrigin],
        before: FileCoverage | None,
        final: FileCoverage | None,
    ) -> tuple[OriginStats, ...]:
        arcs_before = _arcs(before)
        stats: list[OriginStats] = []
        for index, origin in enumerate(_ORIGIN_ORDER):
            if not counts[origin]:
                continue
            later = _ORIGIN_ORDER[index + 1 :]
            if any(counts[o] for o in later):
                arcs = _arcs(self._run(_layer(suite, _ORIGIN_ORDER[: index + 1])).coverage)
            else:
                arcs = _arcs(final)
            stats.append(OriginStats(origin, counts[origin], len(arcs - arcs_before)))
            arcs_before = arcs | arcs_before
        return tuple(stats)


def _layer(suite: TestSuite, origins: tuple[CaseOrigin, ...]) -> TestSuite:
    return replace(
        suite,
        cases=tuple(c for c in suite.cases if c.origin in origins),
        free_tests=tuple(t for t in suite.free_tests if t.origin in origins),
    )


def _arcs(coverage: FileCoverage | None) -> frozenset[Arc]:
    return coverage.executed_arcs if coverage else frozenset()
