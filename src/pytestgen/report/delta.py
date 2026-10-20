"""Coverage delta: how much a generated suite added on top of the baseline."""

from __future__ import annotations

from pytestgen.domain.models import (
    CoverageDelta,
    CoverageSummary,
    FileCoverage,
    FunctionCoverageDelta,
    FunctionInfo,
    ModuleInfo,
)


def compute_delta(
    module: ModuleInfo, before: FileCoverage | None, after: FileCoverage | None
) -> CoverageDelta:
    """Compare baseline and final coverage, overall and per function.

    A missing baseline means nothing was covered before: the ``before`` side then
    has the same totals as ``after`` with zero covered statements and arcs.
    """
    return CoverageDelta(
        before=_baseline(CoverageSummary.from_file(after), before),
        after=CoverageSummary.from_file(after),
        per_function=tuple(_function_delta(fn, before, after) for fn in module.functions),
    )


def _baseline(after: CoverageSummary, before: FileCoverage | None) -> CoverageSummary:
    if before is None:
        return CoverageSummary(0, after.total_lines, 0, after.total_branches)
    return CoverageSummary.from_file(before)


def _function_delta(
    fn: FunctionInfo, before: FileCoverage | None, after: FileCoverage | None
) -> FunctionCoverageDelta:
    after_summary = _summarize(fn, after)
    before_summary = (
        CoverageSummary(0, after_summary.total_lines, 0, after_summary.total_branches)
        if before is None
        else _summarize(fn, before)
    )
    return FunctionCoverageDelta(fn.qualname, before_summary, after_summary)


def _summarize(fn: FunctionInfo, cov: FileCoverage | None) -> CoverageSummary:
    """Coverage restricted to the lines of ``fn`` and the arcs that start inside it."""
    if cov is None:
        return CoverageSummary(0, 0, 0, 0)
    executed = sum(1 for line in cov.executed_lines if fn.contains_line(line))
    missing = sum(1 for line in cov.missing_lines if fn.contains_line(line))
    arcs_hit = sum(1 for arc in cov.executed_arcs if fn.contains_line(arc.src))
    arcs_missed = sum(1 for arc in cov.missing_arcs if fn.contains_line(arc.src))
    return CoverageSummary(executed, executed + missing, arcs_hit, arcs_hit + arcs_missed)
