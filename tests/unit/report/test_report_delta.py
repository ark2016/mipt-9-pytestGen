from __future__ import annotations

from pathlib import Path

import pytest

from pytestgen.domain.models import (
    Arc,
    CoverageSummary,
    FileCoverage,
    FunctionInfo,
    ModuleInfo,
)
from pytestgen.report import compute_delta


def _fn(qualname: str, lineno: int, end: int) -> FunctionInfo:
    return FunctionInfo(
        qualname=qualname,
        name=qualname.rsplit(".", 1)[-1],
        lineno=lineno,
        end_lineno=end,
        source="",
    )


@pytest.fixture
def module() -> ModuleInfo:
    return ModuleInfo(
        path=Path("calc.py"),
        module_name="calc",
        source="",
        functions=(_fn("first", 1, 5), _fn("second", 7, 12)),
    )


def _cov(
    executed: set[int],
    missing: set[int],
    arcs: set[tuple[int, int]],
    miss_arcs: set[tuple[int, int]],
) -> FileCoverage:
    return FileCoverage(
        path="calc.py",
        executed_lines=frozenset(executed),
        missing_lines=frozenset(missing),
        executed_arcs=frozenset(Arc(*a) for a in arcs),
        missing_arcs=frozenset(Arc(*a) for a in miss_arcs),
    )


@pytest.fixture
def before_cov() -> FileCoverage:
    return _cov({1, 2}, {3, 4, 8, 9}, {(1, 2)}, {(2, 3), (2, 4), (8, 9), (8, -7)})


@pytest.fixture
def after_cov() -> FileCoverage:
    return _cov({1, 2, 3, 4, 8}, {9}, {(1, 2), (2, 3), (2, 4), (8, -7)}, {(8, 9)})


def test_both_present_totals(
    module: ModuleInfo, before_cov: FileCoverage, after_cov: FileCoverage
) -> None:
    delta = compute_delta(module, before_cov, after_cov)
    assert delta.before == CoverageSummary(2, 6, 1, 5)
    assert delta.after == CoverageSummary(5, 6, 4, 5)
    assert delta.line_gain == pytest.approx(100 * (5 / 6 - 2 / 6))
    assert delta.branch_gain == pytest.approx(100 * (4 / 5 - 1 / 5))


def test_per_function_ranges_and_arc_attribution(
    module: ModuleInfo, before_cov: FileCoverage, after_cov: FileCoverage
) -> None:
    delta = compute_delta(module, before_cov, after_cov)
    first, second = delta.per_function
    assert first.function == "first"
    assert first.before == CoverageSummary(2, 4, 1, 3)
    assert first.after == CoverageSummary(4, 4, 3, 3)
    assert second.function == "second"
    assert second.before == CoverageSummary(0, 2, 0, 2)
    assert second.after == CoverageSummary(1, 2, 1, 2)


def test_negative_dst_arc_belongs_to_source_function(module: ModuleInfo) -> None:
    cov = _cov({8}, set(), {(8, -7)}, set())
    delta = compute_delta(module, cov, cov)
    assert delta.per_function[1].after.covered_branches == 1
    assert delta.per_function[0].after.total_branches == 0


def test_before_none_means_nothing_covered(module: ModuleInfo, after_cov: FileCoverage) -> None:
    delta = compute_delta(module, None, after_cov)
    assert delta.before == CoverageSummary(0, 6, 0, 5)
    assert delta.after == CoverageSummary(5, 6, 4, 5)
    first, second = delta.per_function
    assert first.before == CoverageSummary(0, 4, 0, 3)
    assert first.after == CoverageSummary(4, 4, 3, 3)
    assert second.before == CoverageSummary(0, 2, 0, 2)


@pytest.mark.parametrize("before_present", [False, True])
def test_after_none_yields_zero_totals(
    module: ModuleInfo, before_cov: FileCoverage, before_present: bool
) -> None:
    delta = compute_delta(module, before_cov if before_present else None, None)
    assert delta.after == CoverageSummary(0, 0, 0, 0)
    assert delta.before == (
        CoverageSummary(2, 6, 1, 5) if before_present else CoverageSummary(0, 0, 0, 0)
    )
    assert all(f.after == CoverageSummary(0, 0, 0, 0) for f in delta.per_function)


def test_module_without_functions(after_cov: FileCoverage) -> None:
    empty = ModuleInfo(path=Path("m.py"), module_name="m", source="")
    assert compute_delta(empty, None, after_cov).per_function == ()
