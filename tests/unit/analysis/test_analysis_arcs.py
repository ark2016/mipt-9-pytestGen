"""Arcs predicted by the analyzer are checked against what coverage.py really measures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from analysis_support import FIXTURES, analyze_fixture, analyze_text, line_of, measure_arcs

from pytestgen.domain.models import Arc, BranchKind, BranchTarget

# Inputs per function that exercise every reachable outcome of every branch.
COVERING_CALLS: dict[str, dict[str, list[list[Any]]]] = {
    "early_exit": {
        "classify": [[-1, "a"], [20, "admin"], [20, "user"]],
        "sign": [[-1], [0], [1]],
        "inverted": [[-1], [5], [200]],
        "nested_exit": [[1, 1], [1, -1], [-20, 0], [-5, 0]],
    },
    "elif_chain": {
        "grade": [[95], [85], [75], [10]],
        "bucket": [[-1, []], [0, []], [5, []], [50, []]],
        "early_chain": [[-1], [0], [200], [5]],
    },
    "loops": {
        "count_even": [[[]], [[1, 2]]],
        "first_negative": [[[]], [[1, -1]], [[1, 2]]],
        "drain": [[0], [3], [60]],
        "spin": [[0], [10]],
        "sum_positive": [[[]], [[[]]], [[[1, -1]]]],
        "collect": [[[], []], [[1], []]],
        "indirect": [[[]], [[1]]],
        "computed": [[0], [2]],
    },
    "tail_branches": {
        "log_negative": [[-1, []], [1, []]],
        "log_either": [[-1, []], [1, []]],
        "tail_async": [[0, []], [1, []]],
    },
    "decorated": {
        "pick": [[True, 1, 2], [False, 1, 2]],
        "log_big": [[1, []], [20, []]],
    },
    "methods": {
        "Account.withdraw": [[5], [0]],
        "Account.fee": [[200], [1]],
        "Account.tag": [[[], True], [[], False]],
    },
    "substitution": {
        "double_check": [[1], [10]],
        "transitive": [[1, 1], [10, 10]],
        "reassigned": [[0], [10]],
        "loop_assigned": [[[]], [[1, 5]]],
        "after_guard": [[-1], [10], [1]],
    },
    "nonexec": {
        "global_after_if": [[0, []], [1, []]],
        "annotation_after_if": [[0, []], [1, []]],
        "global_first_in_body": [[0], [1]],
    },
    "misc": {
        "guarded": [["ab"], [""], [5]],
        "parse": [["5"], ["abc"], ["20"]],
        "with_if": [[[]], [[1]]],
        "matcher": [["go"], ["stop"], ["x"]],
    },
}

CASES = [(module, name) for module, funcs in COVERING_CALLS.items() for name in funcs]


@pytest.fixture(scope="module")
def measured(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, dict[str, set[tuple[int, int]]]]:
    """coverage.py arcs per fixture module and function (one subprocess per module)."""
    result = {}
    for module, calls in COVERING_CALLS.items():
        workdir = tmp_path_factory.mktemp(f"probe_{module}")
        result[module] = measure_arcs(FIXTURES / f"{module}.py", calls, workdir)
    return result


def _arc_targets(module: str, name: str) -> list[BranchTarget]:
    function = analyze_fixture(module).function(name)
    return [t for t in function.branches if t.arc is not None]


@pytest.mark.parametrize(("module", "name"), CASES)
def test_predicted_arcs_are_executed_by_covering_inputs(
    measured: dict[str, dict[str, set[tuple[int, int]]]], module: str, name: str
) -> None:
    targets = _arc_targets(module, name)
    assert targets, "fixture must contain branches with arcs"
    for branch in targets:
        assert branch.arc is not None
        assert (branch.arc.src, branch.arc.dst) in measured[module][name], branch


@pytest.mark.parametrize(("module", "name"), CASES)
def test_measured_arcs_leaving_branch_lines_are_all_predicted(
    measured: dict[str, dict[str, set[tuple[int, int]]]], module: str, name: str
) -> None:
    """No arc coverage.py reports from a branch line is missed by the analyzer."""
    targets = _arc_targets(module, name)
    predicted = {(t.arc.src, t.arc.dst) for t in targets if t.arc is not None}
    lines = {src for src, _ in predicted}
    actual = {arc for arc in measured[module][name] if arc[0] in lines}
    assert actual == predicted


def test_branches_without_line_arcs_have_none() -> None:
    misc = analyze_fixture("misc")
    for name in ("ternary", "checked", "nested_ternary"):
        assert all(t.arc is None for t in misc.function(name).branches)


# --------------------------------------------------------------------------- explicit shapes


def _arcs(module: str, name: str) -> dict[tuple[int, bool], Arc | None]:
    return {(t.line, t.outcome): t.arc for t in analyze_fixture(module).function(name).branches}


def test_early_return_arcs() -> None:
    path = FIXTURES / "early_exit.py"
    arcs = _arcs("early_exit", "sign")
    first = line_of(path, "if x < 0")
    second = line_of(path, "if x == 0")
    assert arcs[first, True] == Arc(first, line_of(path, "return -1"))
    assert arcs[first, False] == Arc(first, second)
    assert arcs[second, True] == Arc(second, line_of(path, "return 0"))
    assert arcs[second, False] == Arc(second, line_of(path, "return 1"))


def test_elif_chain_arcs() -> None:
    path = FIXTURES / "elif_chain.py"
    arcs = _arcs("elif_chain", "grade")
    top = line_of(path, "if score >= 90")
    middle = line_of(path, "elif score >= 80")
    last = line_of(path, "elif score >= 70")
    assert arcs[top, False] == Arc(top, middle)
    assert arcs[middle, False] == Arc(middle, last)
    assert arcs[last, True] == Arc(last, line_of(path, 'result = "C"'))
    assert arcs[last, False] == Arc(last, line_of(path, 'result = "F"'))


def test_elif_without_else_falls_to_function_exit() -> None:
    path = FIXTURES / "elif_chain.py"
    arcs = _arcs("elif_chain", "bucket")
    last = line_of(path, "elif n < 10")
    assert arcs[last, False] == Arc(last, -line_of(path, "def bucket"))


def test_nested_if_in_for_loops_back_to_the_loop_header() -> None:
    path = FIXTURES / "loops.py"
    arcs = _arcs("loops", "count_even")
    loop = line_of(path, "for item in items")
    cond = line_of(path, "if item % 2 == 0")
    assert arcs[loop, True] == Arc(loop, cond)
    assert arcs[loop, False] == Arc(loop, line_of(path, "return total"))
    assert arcs[cond, True] == Arc(cond, line_of(path, "total += 1"))
    assert arcs[cond, False] == Arc(cond, loop)


def test_for_else_and_break() -> None:
    path = FIXTURES / "loops.py"
    arcs = _arcs("loops", "first_negative")
    loop = line_of(path, "for value in values")
    cond = line_of(path, "if value < 0")
    assert arcs[loop, False] == Arc(loop, line_of(path, "return None"))
    assert arcs[cond, True] == Arc(cond, line_of(path, "break"))
    assert arcs[cond, False] == Arc(cond, loop)


def test_while_with_else() -> None:
    path = FIXTURES / "loops.py"
    arcs = _arcs("loops", "drain")
    loop = line_of(path, "while n > 0")
    assert arcs[loop, True] == Arc(loop, line_of(path, "if steps > 50"))
    assert arcs[loop, False] == Arc(loop, line_of(path, "steps += 100"))


def test_while_true_has_no_exit_arc_and_loops_to_header() -> None:
    path = FIXTURES / "loops.py"
    arcs = _arcs("loops", "spin")
    loop = line_of(path, "while True")
    cond = line_of(path, "if n > 3")
    assert arcs[loop, True] == Arc(loop, cond)
    assert arcs[loop, False] is None
    assert arcs[cond, False] == Arc(cond, line_of(path, "n += 1"))


def test_if_at_function_end_exits_the_code_object() -> None:
    path = FIXTURES / "tail_branches.py"
    arcs = _arcs("tail_branches", "log_negative")
    cond = line_of(path, "if x < 0")
    assert arcs[cond, False] == Arc(cond, -line_of(path, "def log_negative"))
    either = _arcs("tail_branches", "log_either")
    cond = line_of(path, "if x < 0", occurrence=1)
    assert either[cond, False] == Arc(cond, line_of(path, 'sink.append("non-neg")'))


def test_for_at_function_end_exits_the_code_object() -> None:
    path = FIXTURES / "loops.py"
    arcs = _arcs("loops", "collect")
    loop = line_of(path, "for item in items", occurrence=1)
    assert arcs[loop, False] == Arc(loop, -line_of(path, "def collect"))


def test_decorated_function_exits_to_first_decorator() -> None:
    path = FIXTURES / "decorated.py"
    arcs = _arcs("decorated", "log_big")
    cond = line_of(path, "if x > 10")
    assert arcs[cond, False] == Arc(cond, -line_of(path, "@traced", occurrence=1))
    function = analyze_fixture("decorated").function("log_big")
    assert function.lineno == line_of(path, "def log_big")


def test_method_arcs() -> None:
    path = FIXTURES / "methods.py"
    arcs = _arcs("methods", "Account.withdraw")
    cond = line_of(path, "if amount > self.balance")
    assert arcs[cond, True] == Arc(cond, line_of(path, "raise ValueError"))
    assert arcs[cond, False] == Arc(cond, line_of(path, "self.balance -= amount"))
    tag = _arcs("methods", "Account.tag")
    cond = line_of(path, "if loud")
    assert tag[cond, False] == Arc(cond, -line_of(path, "def tag"))


def test_try_finally_and_with_continuations() -> None:
    path = FIXTURES / "misc.py"
    guarded = _arcs("misc", "guarded")
    cond = line_of(path, "if path:")
    assert guarded[cond, True] == Arc(cond, line_of(path, "value = len(path)"))
    assert guarded[cond, False] == Arc(cond, line_of(path, "value = 0"))
    with_if = _arcs("misc", "with_if")
    cond = line_of(path, "if items:")
    assert with_if[cond, False] == Arc(cond, line_of(path, "with contextlib.nullcontext()"))


def test_try_else_and_match_continue_after_the_statement() -> None:
    path = FIXTURES / "misc.py"
    cond = line_of(path, "if number > 9")
    assert _arcs("misc", "parse")[cond, False] == Arc(cond, line_of(path, "return number"))
    cond = line_of(path, 'if cmd == "stop"')
    assert _arcs("misc", "matcher")[cond, False] == Arc(
        cond, line_of(path, "return 0", occurrence=1)
    )


def test_constant_if_has_no_arc(tmp_path: Path) -> None:
    module = analyze_text(tmp_path, "def f():\n    if True:\n        return 1\n    return 2\n")
    assert all(t.arc is None for t in module.function("f").branches)
    assert {t.kind for t in module.function("f").branches} == {BranchKind.IF}


def test_declarations_without_bytecode_are_skipped_as_destinations() -> None:
    path = FIXTURES / "nonexec.py"
    after_global = _arcs("nonexec", "global_after_if")
    cond = line_of(path, "if x:")
    assert after_global[cond, False] == Arc(cond, line_of(path, "G = 1"))
    annotation = _arcs("nonexec", "annotation_after_if")
    cond = line_of(path, "if x:", occurrence=1)
    assert annotation[cond, False] == Arc(cond, line_of(path, "sink.append(2)"))
    first = _arcs("nonexec", "global_first_in_body")
    cond = line_of(path, "if x:", occurrence=2)
    assert first[cond, True] == Arc(cond, line_of(path, "G = 2"))
