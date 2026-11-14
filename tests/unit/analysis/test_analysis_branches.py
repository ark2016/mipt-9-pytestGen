"""Branch targets: kinds, outcomes, raises and path conditions (no coverage involved)."""

from __future__ import annotations

from pathlib import Path

import pytest
from analysis_support import analyze_fixture, analyze_text, clauses, line_of, target

from pytestgen.analysis.branches import BranchExtractor
from pytestgen.domain.models import BranchKind, BranchTarget, Clause, FunctionInfo


def _exprs(branch: BranchTarget) -> list[tuple[str, bool]]:
    return [(c.expr, c.expected) for c in branch.path.clauses]


def _fn(tmp_path: Path, source: str, name: str = "f") -> FunctionInfo:
    return analyze_text(tmp_path, source).function(name)


# --------------------------------------------------------------------------- the classify example


def test_classify_example_matches_the_specification() -> None:
    path = analyze_fixture("early_exit").path
    classify = analyze_fixture("early_exit").function("classify")
    first = line_of(path, "if age < 0")
    second = line_of(path, "if age >= 18")

    first_true = target(classify, first, True)
    assert first_true.raises == "ValueError"
    assert first_true.kind is BranchKind.IF
    assert first_true.condition_source == "age < 0"
    assert clauses(first_true) == [Clause("age < 0", True)]
    assert target(classify, first, False).raises is None

    second_true = target(classify, second, True)
    assert second_true.raises is None
    assert second_true.condition_source == "age >= 18 and role == 'admin'"
    assert clauses(second_true) == [
        Clause("age < 0", False),
        Clause("age >= 18 and role == 'admin'", True),
    ]
    assert clauses(target(classify, second, False)) == [
        Clause("age < 0", False),
        Clause("age >= 18 and role == 'admin'", False),
    ]
    assert classify.raises == ("ValueError",)
    assert len(classify.branches) == 4


def test_every_branch_has_both_outcomes_and_a_stable_key() -> None:
    for name in ("early_exit", "elif_chain", "loops", "misc", "methods"):
        for function in analyze_fixture(name).functions:
            # ternaries are keyed by their statement line, so they may share a key
            keys = [b.key for b in function.branches if b.kind is not BranchKind.IF_EXP]
            assert len(keys) == len(set(keys)), function.qualname
            by_point: dict[tuple[int, BranchKind], set[bool]] = {}
            for b in function.branches:
                by_point.setdefault((b.line, b.kind), set()).add(b.outcome)
                assert b.function == function.qualname
            assert all(outcomes == {True, False} for outcomes in by_point.values())


def test_targets_are_ordered_by_line() -> None:
    for function in analyze_fixture("loops").functions:
        lines = [b.line for b in function.branches]
        assert lines == sorted(lines)


# --------------------------------------------------------------------------- early exits


def test_early_exit_else_branch_terminating_adds_a_true_clause() -> None:
    inverted = analyze_fixture("early_exit").function("inverted")
    path = analyze_fixture("early_exit").path
    later = line_of(path, "if x > 100")
    assert _exprs(target(inverted, later, True)) == [("x > 0", True), ("x > 100", True)]


def test_nested_enclosing_early_exit_applies_to_following_statements() -> None:
    fn = analyze_fixture("early_exit").function("nested_exit")
    path = analyze_fixture("early_exit").path
    inner = line_of(path, "if b > 0")
    after = line_of(path, "if a < -10")
    assert _exprs(target(fn, inner, False)) == [("a > 0", True), ("b > 0", False)]
    # the outer ``if`` terminates in both inner arms but has no else: not an early exit
    assert _exprs(target(fn, after, True)) == [("a > 0", False), ("a < -10", True)]


def test_both_arms_terminating_adds_no_clause(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(x):
    if x:
        return 1
    else:
        return 2
    if x > 5:
        return 3
""",
    )
    branch = target(fn, 7, True)
    assert _exprs(branch) == [("x > 5", True)]


def test_assert_adds_a_true_clause_to_later_branches(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(x):
    assert x >= 0
    if x > 5:
        return 1
    return 0
""",
    )
    assert _exprs(target(fn, 4, True)) == [("x >= 0", True), ("x > 5", True)]
    assert_false = target(fn, 3, False, kind="assert")
    assert assert_false.raises == "AssertionError"
    assert _exprs(assert_false) == [("x >= 0", False)]
    assert target(fn, 3, True, kind="assert").raises is None


def test_continue_and_break_are_early_exits_inside_loops(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(items):
    for item in items:
        if item < 0:
            continue
        if item > 100:
            break
        if item == 7:
            return item
""",
    )
    seven = target(fn, 8, True)
    assert _exprs(seven) == [
        ("len(items) > 0", True),
        ("item < 0", False),
        ("item > 100", False),
        ("item == 7", True),
    ]


def test_elif_chain_ending_in_return_collects_every_failed_test() -> None:
    fn = analyze_fixture("elif_chain").function("early_chain")
    path = analyze_fixture("elif_chain").path
    huge = line_of(path, "if n > 100")
    assert _exprs(target(fn, huge, True)) == [
        ("n < 0", False),
        ("n == 0", False),
        ("n > 100", True),
    ]


def test_elif_chain_polarities() -> None:
    fn = analyze_fixture("elif_chain").function("grade")
    path = analyze_fixture("elif_chain").path
    middle = line_of(path, "elif score >= 80")
    assert _exprs(target(fn, middle, True)) == [("score >= 90", False), ("score >= 80", True)]
    last = line_of(path, "elif score >= 70")
    assert _exprs(target(fn, last, False)) == [
        ("score >= 90", False),
        ("score >= 80", False),
        ("score >= 70", False),
    ]


def test_early_exit_in_else_of_chain_with_assert(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(a, b):
    if a:
        return 1
    else:
        assert b
    if b > 2:
        return 2
""",
    )
    assert _exprs(target(fn, 7, True)) == [("a", False), ("b", True), ("b > 2", True)]


def test_early_exit_not_leaking_out_of_blocks(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(a, b):
    if a:
        if b:
            return 1
        x = 1
    if b > 3:
        return x
""",
    )
    # the guard ``not b`` only holds inside the ``if a`` block
    assert _exprs(target(fn, 7, True)) == [("b > 3", True)]


# --------------------------------------------------------------------------- loops


def test_for_over_name_uses_len_clause() -> None:
    fn = analyze_fixture("loops").function("count_even")
    path = analyze_fixture("loops").path
    loop = line_of(path, "for item in items")
    assert target(fn, loop, True).kind is BranchKind.FOR
    assert target(fn, loop, True).condition_source == "len(items) > 0"
    assert _exprs(target(fn, loop, True)) == [("len(items) > 0", True)]
    assert _exprs(target(fn, loop, False)) == [("len(items) > 0", False)]


def test_for_over_call_has_no_path_clause() -> None:
    fn = analyze_fixture("loops").function("computed")
    path = analyze_fixture("loops").path
    loop = line_of(path, "for i in range(n)")
    for outcome in (True, False):
        branch = target(fn, loop, outcome)
        assert branch.condition_source == "range(n)"
        assert branch.path.clauses == ()


def test_for_over_substituted_name_resolves_the_iterable() -> None:
    fn = analyze_fixture("loops").function("indirect")
    path = analyze_fixture("loops").path
    loop = line_of(path, "for item in items", occurrence=2)
    assert _exprs(target(fn, loop, True)) == [("len(sorted(x)) > 0", True)]


def test_for_over_attribute_is_plain(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(self_like):
    for x in self_like.items:
        pass
""",
    )
    assert target(fn, 3, True).condition_source == "len(self_like.items) > 0"


def test_loop_body_clauses_and_else_blocks(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(n, items):
    while n > 0:
        if n == 5:
            return 1
        n -= 1
    else:
        if n == 0:
            return 2
    for x in items:
        pass
    else:
        if n < -1:
            return 3
""",
    )
    assert _exprs(target(fn, 4, True)) == [("n > 0", True), ("n == 5", True)]
    assert _exprs(target(fn, 8, True)) == [("n > 0", False), ("n == 0", True)]
    assert _exprs(target(fn, 13, True)) == [("n < -1", True)]


def test_while_clause_polarity() -> None:
    fn = analyze_fixture("loops").function("drain")
    path = analyze_fixture("loops").path
    loop = line_of(path, "while n > 0")
    assert target(fn, loop, True).kind is BranchKind.WHILE
    assert _exprs(target(fn, loop, True)) == [("n > 0", True)]
    assert _exprs(target(fn, loop, False)) == [("n > 0", False)]


def test_raise_as_loop_body(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(items, n):
    for x in items:
        raise KeyError(x)
    while n:
        raise errors.Boom
    else:
        raise OSError
""",
    )
    assert target(fn, 3, True).raises == "KeyError"
    assert target(fn, 3, False).raises is None
    assert target(fn, 5, True).raises == "errors.Boom"
    assert target(fn, 5, False).raises == "OSError"


# --------------------------------------------------------------------------- raises


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("raise ValueError('x')", "ValueError"),
        ("raise ValueError", "ValueError"),
        ("raise mod.Err('x') from None", "mod.Err"),
        ("raise", None),
        ("print('x')\n        raise ValueError", None),
        ("pass", None),
    ],
)
def test_raises_only_when_block_starts_with_raise(
    tmp_path: Path, body: str, expected: str | None
) -> None:
    fn = _fn(tmp_path, f"def f(x):\n    if x:\n        {body}\n")
    assert target(fn, 2, True).raises == expected


def test_else_block_raise_is_attached_to_the_false_outcome(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(x):
    if x:
        return 1
    else:
        raise LookupError("no")
""",
    )
    assert target(fn, 3, True).raises is None
    assert target(fn, 3, False).raises == "LookupError"


# --------------------------------------------------------------------------- ternary


def test_ternary_targets_use_the_statement_line() -> None:
    fn = analyze_fixture("misc").function("ternary")
    path = analyze_fixture("misc").path
    line = line_of(path, 'return "pos" if x > 0')
    true = target(fn, line, True)
    assert true.kind is BranchKind.IF_EXP
    assert true.condition_source == "x > 0"
    assert true.arc is None
    assert _exprs(true) == [("x > 0", True)]
    assert _exprs(target(fn, line, False)) == [("x > 0", False)]


def test_nested_ternary_inner_target_requires_outer_polarity() -> None:
    fn = analyze_fixture("misc").function("nested_ternary")
    path = analyze_fixture("misc").path
    line = line_of(path, 'return "a" if')
    inner = [t for t in fn.branches if t.line == line and t.condition_source == "x < -5"]
    assert [_exprs(t) for t in inner] == [
        [("x > 0", False), ("x < -5", True)],
        [("x > 0", False), ("x < -5", False)],
    ]


def test_ternary_in_if_test_and_after_early_exit(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(a, b):
    if a < 0:
        return 0
    if (b if a else -b) > 3:
        return 1
    value = 10 if b else 20
    return value
""",
    )
    ternaries = [t for t in fn.branches if t.kind is BranchKind.IF_EXP]
    assert [(t.line, t.condition_source, t.outcome) for t in ternaries] == [
        (5, "a", True),
        (5, "a", False),
        (7, "b", True),
        (7, "b", False),
    ]
    assert _exprs(ternaries[0]) == [("a < 0", False), ("a", True)]
    assert _exprs(ternaries[2]) == [("a < 0", False), ("(b if a else -b) > 3", False), ("b", True)]


def test_lambdas_and_comprehensions_are_not_scanned(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(items):
    key = lambda v: 1 if v else 2
    return [1 if v else 2 for v in items], key
""",
    )
    assert fn.branches == ()


def test_ternary_in_with_item_and_match_subject(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(a, ctx):
    with (ctx if a else None) as handle:
        pass
    match (1 if a else 2):
        case 1:
            pass
""",
    )
    assert [(t.line, t.condition_source) for t in fn.branches] == [(3, "a")] * 2 + [(5, "a")] * 2


# --------------------------------------------------------------------------- scoping


def test_nested_functions_and_classes_are_not_entered(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(x):
    def inner(y):
        if y:
            return 1
        return 2

    class Local:
        def m(self, z):
            if z:
                return 1

    return inner
""",
    )
    assert fn.branches == ()


def test_branches_inside_try_with_and_match_blocks(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
def f(x, y, z):
    try:
        if x:
            pass
    except ValueError:
        if y:
            pass
    else:
        if z:
            pass
    finally:
        if x and y:
            pass
    with open(x) as handle:
        if handle:
            pass
    match z:
        case 1:
            if x > 1:
                pass
        case _:
            if y > 1:
                pass
""",
    )
    assert sorted({t.line for t in fn.branches}) == [4, 7, 10, 13, 16, 20, 23]
    assert all(t.kind is BranchKind.IF for t in fn.branches)
    assert _exprs(target(fn, 16, True)) == [("handle", True)]


def test_async_function_and_async_constructs(tmp_path: Path) -> None:
    fn = _fn(
        tmp_path,
        """
async def f(items, ctx):
    async for item in items:
        if item:
            pass
    async with ctx as c:
        if c:
            pass
""",
    )
    assert [(t.line, t.kind) for t in fn.branches[::2]] == [
        (3, BranchKind.FOR),
        (4, BranchKind.IF),
        (7, BranchKind.IF),
    ]


def test_constant_conditions_still_produce_targets(tmp_path: Path) -> None:
    fn = _fn(tmp_path, "def f():\n    while True:\n        pass\n")
    assert target(fn, 2, True).arc is not None
    assert target(fn, 2, False).arc is None
    assert target(fn, 2, False).condition_source == "True"


def test_extractor_is_usable_standalone(tmp_path: Path) -> None:
    import ast

    tree = ast.parse("def f(x):\n    if x:\n        return 1\n    return 2\n")
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    targets = BranchExtractor().extract(node, "f")
    assert [(t.line, t.outcome) for t in targets] == [(2, True), (2, False)]
