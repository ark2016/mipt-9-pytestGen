"""Substitution of straight-line assignments into path conditions."""

from __future__ import annotations

from pathlib import Path

import pytest
from analysis_support import analyze_fixture, analyze_text, line_of, target

from pytestgen.domain.models import BranchTarget


def _exprs(branch: BranchTarget) -> list[tuple[str, bool]]:
    return [(c.expr, c.expected) for c in branch.path.clauses]


def test_spec_example_double_check() -> None:
    module = analyze_fixture("substitution")
    fn = module.function("double_check")
    line = line_of(module.path, "if y > 10")
    true = target(fn, line, True)
    assert _exprs(true) == [("(x * 2) > 10", True)]
    assert true.condition_source == "y > 10"
    assert _exprs(target(fn, line, False)) == [("(x * 2) > 10", False)]


def test_substitution_is_transitive() -> None:
    module = analyze_fixture("substitution")
    fn = module.function("transitive")
    line = line_of(module.path, "if t > 10")
    assert _exprs(target(fn, line, True)) == [("((a + b) * 2) > 10", True)]


def test_reassigned_names_are_left_alone() -> None:
    module = analyze_fixture("substitution")
    fn = module.function("reassigned")
    line = line_of(module.path, "if y > 3")
    assert _exprs(target(fn, line, True)) == [("y > 3", True)]


def test_names_assigned_in_loops_are_left_alone() -> None:
    module = analyze_fixture("substitution")
    fn = module.function("loop_assigned")
    line = line_of(module.path, "if last > 3")
    assert _exprs(target(fn, line, True))[-1] == ("last > 3", True)


def test_substitution_combines_with_early_exit_clauses() -> None:
    module = analyze_fixture("substitution")
    fn = module.function("after_guard")
    line = line_of(module.path, "if y > 5")
    assert _exprs(target(fn, line, True)) == [("x < 0", False), ("(x + 1) > 5", True)]


def test_mutable_containers_are_never_substituted() -> None:
    module = analyze_fixture("substitution")
    fn = module.function("mutable")
    line = line_of(module.path, "if seen")
    assert _exprs(target(fn, line, True)) == [("seen", True)]


def _single_branch(tmp_path: Path, body: str, params: str = "x") -> list[tuple[str, bool]]:
    source = f"def f({params}):\n{body}\n    if y > 1:\n        return 1\n"
    fn = analyze_text(tmp_path, source).function("f")
    return _exprs(next(t for t in fn.branches if t.outcome and t.condition_source == "y > 1"))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("    y = x", "x > 1"),
        ("    y = 5", "5 > 1"),
        ("    y = -x", "(-x) > 1"),
        ("    y = abs(x)", "abs(x) > 1"),
        ("    y = x.real", "x.real > 1"),
        ("    y = x[0]", "x[0] > 1"),
        ("    y = x + 1 if x else 0", "(x + 1 if x else 0) > 1"),
        ("    y: int = x * 3", "(x * 3) > 1"),
        ("    y = z = x", "y > 1"),
        ("    y, w = x, 1", "y > 1"),
        ("    w = x * 2\n    y = w + 1", "((x * 2) + 1) > 1"),
    ],
)
def test_assignment_forms(tmp_path: Path, body: str, expected: str) -> None:
    assert _single_branch(tmp_path, body) == [(expected, True)]


@pytest.mark.parametrize(
    "body",
    [
        "    y = [x]",
        "    y = {x: 1}",
        "    y = {x}",
        "    y = [i for i in x]",
        "    y = list(x)",
        "    y = dict(x)",
        "    y = (z := x)",
        "    y = yield x",
        "    y = await x",
        "    y = x\n    y += 1",
        "    y = x\n    y: int",
        "    y = x\n    del y",
        "    y = x\n    for y in range(3):\n        pass",
        "    y = x\n    with open(x) as y:\n        pass",
        "    y = x\n    import y",
        "    y = x\n    from os import path as y",
        "    y = x\n    def y(): pass",
        "    y = x\n    class y: pass",
        "    y = x\n    try:\n        pass\n    except Exception as y:\n        pass",
        "    global y\n    y = x",
        "    y = x\n    match x:\n        case y:\n            pass",
        "    y = x\n    match x:\n        case [*y]:\n            pass",
        "    y = x\n    match x:\n        case {**y}:\n            pass",
        "    try:\n        y = x\n    finally:\n        pass",
        "    while x:\n        y = x",
    ],
)
def test_unsafe_bindings_are_not_substituted(tmp_path: Path, body: str) -> None:
    source = f"async def f(x):\n{body}\n    if y > 1:\n        return 1\n"
    if "yield" in body:
        source = source.replace("async def", "def")
    fn = analyze_text(tmp_path, source).function("f")
    branch = next(t for t in fn.branches if t.outcome and t.condition_source == "y > 1")
    assert branch.path.clauses[-1].expr == "y > 1"


def test_nonlocal_declaration_blocks_substitution(tmp_path: Path) -> None:
    source = (
        "def outer():\n"
        "    y = 0\n"
        "    def f(x):\n"
        "        nonlocal y\n"
        "        y = x\n"
        "        return y\n"
    )
    module = analyze_text(tmp_path, source)
    assert module.function("outer").branches == ()


def test_parameter_reassignment_blocks_substitution(tmp_path: Path) -> None:
    source = "def f(x):\n    x = x + 1\n    if x > 1:\n        return 1\n"
    fn = analyze_text(tmp_path, source).function("f")
    assert _exprs(target(fn, 3, True)) == [("x > 1", True)]


def test_vararg_and_kwonly_parameter_names_count_as_bindings(tmp_path: Path) -> None:
    source = (
        "def f(*args, k=1, **kw):\n"
        "    args = k\n"
        "    kw = k\n"
        "    if args > 1 or kw > 1:\n"
        "        return 1\n"
    )
    fn = analyze_text(tmp_path, source).function("f")
    assert _exprs(target(fn, 4, True)) == [("args > 1 or kw > 1", True)]


def test_assignment_inside_branch_is_scoped_to_that_branch(tmp_path: Path) -> None:
    source = (
        "def f(x):\n"
        "    if x:\n"
        "        y = x * 2\n"
        "        if y > 3:\n"
        "            return 1\n"
        "    if y > 4:\n"
        "        return 2\n"
    )
    fn = analyze_text(tmp_path, source).function("f")
    assert _exprs(target(fn, 4, True)) == [("x", True), ("(x * 2) > 3", True)]
    assert _exprs(target(fn, 6, True)) == [("y > 4", True)]


def test_lambda_and_comprehension_bindings_do_not_count(tmp_path: Path) -> None:
    source = (
        "def f(x):\n"
        "    y = x * 2\n"
        "    g = lambda y: y\n"
        "    h = [y for y in range(3)]\n"
        "    if y > 3:\n"
        "        return g, h\n"
    )
    fn = analyze_text(tmp_path, source).function("f")
    assert _exprs(target(fn, 5, True)) == [("(x * 2) > 3", True)]


def test_only_bound_names_are_replaced(tmp_path: Path) -> None:
    source = "def f(x, z):\n    y = x * 2\n    if y > z:\n        return 1\n"
    fn = analyze_text(tmp_path, source).function("f")
    assert _exprs(target(fn, 3, True)) == [("(x * 2) > z", True)]


def test_anonymous_match_captures_do_not_block_substitution(tmp_path: Path) -> None:
    source = (
        "def f(x, v):\n"
        "    y = x * 2\n"
        "    match v:\n"
        "        case [*_]:\n"
        "            pass\n"
        "        case {'k': 1}:\n"
        "            pass\n"
        "    if y > 1:\n"
        "        return 1\n"
    )
    fn = analyze_text(tmp_path, source).function("f")
    assert _exprs(target(fn, 8, True)) == [("(x * 2) > 1", True)]


@pytest.mark.parametrize(
    ("assign", "test", "expected"),
    [
        ("item = x * 2", "any(item > 1 for item in items)", "any((item > 1 for item in items))"),
        ("item = x * 2", "[item for item in items]", "[item for item in items]"),
        ("y = x * 2", "(lambda y: y > 1)(3)", "(lambda y: y > 1)(3)"),
        ("y = x * 2", "any(y > a for a in items)", "any(((x * 2) > a for a in items))"),
        ("y = x * 2", "any(a > 1 for a in y)", "any((a > 1 for a in (x * 2)))"),
        ("y = x * 2", "(lambda a, b=y: a > b)(3)", "(lambda a, b=(x * 2): a > b)(3)"),
        ("y = x * 2", "{y: v for y, v in items}", "{y: v for y, v in items}"),
    ],
)
def test_comprehension_and_lambda_bindings_shadow_substitution(
    tmp_path: Path, assign: str, test: str, expected: str
) -> None:
    source = f"def f(items, x):\n    {assign}\n    if {test}:\n        return 1\n    return 0\n"
    fn = analyze_text(tmp_path, source).function("f")
    assert _exprs(target(fn, 3, True)) == [(expected, True)]
