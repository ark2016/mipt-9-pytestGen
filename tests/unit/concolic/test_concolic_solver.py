"""Z3HintSolver: statuses, and the key property that every SAT model satisfies the clauses."""

from __future__ import annotations

import pytest
import structlog

from pytestgen.concolic import Z3HintSolver
from pytestgen.domain.models import Parameter, ParamKind, SolveStatus

SAT = SolveStatus.SAT

# (annotations, clauses): each must be SAT, and the inputs must satisfy the clauses in Python.
SAT_CASES = {
    "classify": (
        {"age": "int", "role": "str"},
        [("age >= 18 and role == 'admin'", True), ("age < 0", False)],
    ),
    "classify-negated-or": (
        {"age": "int", "role": "str"},
        [("age >= 18 and role == 'admin'", False), ("age >= 18", True)],
    ),
    "str-startswith-endswith-in-len": (
        {"s": "str"},
        [
            ("s.startswith('ab') and s.endswith('yz')", True),
            ("'q' in s", True),
            ("len(s) >= 6 and len(s) < 10", True),
        ],
    ),
    "str-not-in": ({"s": "str"}, [("'x' not in s", True), ("len(s) == 3", True)]),
    "str-eq-and-ne": ({"s": "str"}, [("s == 'hello'", True), ("s != 'world'", True)]),
    "str-in-literal-haystack": ({"c": "str"}, [("c in 'abc'", True), ("c != ''", True)]),
    "str-ordering": ({"s": "str"}, [("s >= 'b'", True), ("s < 'c'", True), ("len(s) == 1", True)]),
    "str-len-over-default-bound": ({"s": "str"}, [("len(s) > 40", True)]),
    "optional-is-none": ({"x": "int | None"}, [("x is None", True)]),
    "optional-is-not-none": ({"x": "Optional[int]"}, [("x is not None and x > 10", True)]),
    "optional-value-read": ({"x": "typing.Optional[int]"}, [("x > 3", True)]),
    "optional-eq-literal": ({"x": "Optional[int]"}, [("x == 5", True)]),
    "optional-ne-literal": ({"x": "Optional[int]"}, [("x != 5", True)]),
    "optional-falsy": ({"x": "Optional[int]"}, [("x", False)]),
    "optional-truthy": ({"x": "Optional[str]"}, [("x", True)]),
    "optional-eq-none": ({"x": "int | None"}, [("x == None", True)]),
    "optional-union-syntax": ({"x": "Union[int, None]"}, [("x is None", True)]),
    "optional-quoted": ({"x": "'int | None'"}, [("x is not None", True)]),
    "optional-vs-optional": (
        {"a": "int | None", "b": "int | None"},
        [("a == b", True), ("a is not None", True)],
    ),
    "optional-unmodelled-class-is-none": ({"x": "Foo | None"}, [("x is None", True)]),
    "chained": ({"a": "int", "b": "int", "c": "int"}, [("a < b < c", True), ("c - a == 5", True)]),
    "chained-mixed-ops": ({"a": "int", "b": "int"}, [("0 <= a < b <= 10 != a", True)]),
    "chained-negated": ({"a": "int", "b": "int"}, [("a < b < 5", False), ("a < b", True)]),
    "float-range": ({"x": "float"}, [("x > 0.25 and x < 0.5", True)]),
    "float-division": ({"x": "float"}, [("x / 4 > 1.5", True)]),
    "float-division-by-variable": (
        {"a": "float", "b": "float"},
        [("a / b > 1", True), ("a > 0", True)],
    ),
    "float-eq-literal": ({"x": "float"}, [("x == 0.1", True)]),
    "float-int-mix": ({"x": "float", "n": "int"}, [("x + n == 7.5", True), ("n > 2", True)]),
    "bool-true": ({"flag": "bool"}, [("flag", True)]),
    "bool-false": ({"flag": "bool"}, [("flag", False)]),
    "bool-with-int": ({"flag": "bool", "n": "int"}, [("not flag and n > 0", True)]),
    "bool-eq": ({"flag": "bool"}, [("flag == True", True)]),
    "in-int-tuple": ({"code": "int"}, [("code in (200, 201, 204)", True), ("code != 200", True)]),
    "in-negative-literal": ({"code": "int"}, [("code in (-1, -2)", True)]),
    "not-in-str-list": ({"role": "str"}, [("role in ['a', 'b']", False), ("role != ''", True)]),
    "in-set-with-none": ({"x": "Optional[int]"}, [("x in {None, 3}", True)]),
    "in-empty": ({"x": "int"}, [("x in ()", False)]),
    "mod-and-floordiv": ({"n": "int"}, [("n % 7 == 3 and n // 7 > 2", True)]),
    "floordiv-negative-divisor": ({"n": "int"}, [("n // -4 == 3", True)]),
    "mod-negative-divisor": ({"n": "int"}, [("n % -3 == -1", True), ("n < 0", True)]),
    "mod-negative-dividend": ({"n": "int"}, [("n % 5 == 2", True), ("n < 0", True)]),
    "abs-min-max": (
        {"x": "int", "a": "int", "b": "int"},
        [("abs(x) > 10 and x < 0", True), ("min(a, b) == 5 and max(a, b) == 9", True)],
    ),
    "min-max-mixed-float": ({"a": "int", "b": "float"}, [("max(a, b) < 2.5", True)]),
    "arithmetic": ({"a": "int", "b": "int"}, [("2 * a - b == 7", True), ("-a > b", True)]),
    "truthy-int": ({"n": "int"}, [("n", True)]),
    "falsy-int": ({"n": "int"}, [("n", False)]),
    "truthy-expression": ({"n": "int"}, [("n % 2", True)]),
    "truthy-str": ({"s": "str"}, [("s", True)]),
    "falsy-str": ({"s": "str"}, [("not s", True)]),
    "truthy-float": ({"x": "float"}, [("x", True)]),
    "compare-as-value": ({"x": "int", "y": "int"}, [("(x > y) + (x < 0) == 2", True)]),
    "constant-true": ({"x": "int"}, [("True", True)]),
    "no-clauses": ({"x": "int", "s": "str"}, []),
    "unannotated-int": ({"age": None}, [("age > 5", True)]),
    "unannotated-str-eq": ({"name": None}, [("name == 'bob'", True)]),
    "unannotated-startswith": ({"name": None}, [("name.startswith('a')", True)]),
    "unannotated-len": ({"name": None}, [("len(name) > 2", True)]),
    "unannotated-in-str": ({"name": None}, [("'ob' in name", True)]),
    "unannotated-in-literals": ({"name": None}, [("name in ('x', 'y')", True)]),
    "unannotated-arith": ({"x": None}, [("x + 1 == 5", True)]),
    "unannotated-float": ({"x": None}, [("x > 1.5", True)]),
    "unannotated-linked": ({"a": None, "b": None}, [("a < b", True), ("b == 3", True)]),
    "unannotated-via-abs": ({"x": None}, [("abs(x) > 3", True)]),
    "unannotated-from-annotated": ({"a": "str", "b": None}, [("a == b", True), ("a != ''", True)]),
    "isinstance-true": ({"x": "int"}, [("isinstance(x, int)", True)]),
    "isinstance-false": ({"x": "int"}, [("isinstance(x, str)", False)]),
    "isinstance-tuple": ({"x": "float"}, [("isinstance(x, (int, float))", True)]),
    "isinstance-bool-is-int": ({"x": "bool"}, [("isinstance(x, int)", True)]),
    "isinstance-optional": ({"x": "int | None"}, [("isinstance(x, int)", True)]),
    "list-len": ({"xs": "list[int]"}, [("len(xs) > 2 and len(xs) < 5", True)]),
    "list-empty": ({"xs": "List[str]"}, [("not xs", True)]),
    "list-truthy": ({"xs": "Sequence[float]"}, [("xs", True)]),
    "tuple-len": ({"t": "tuple[int, ...]"}, [("len(t) == 3", True)]),
    "bytes-len": ({"b": "bytes"}, [("len(b) == 2", True)]),
    "list-len-arith": (
        {"xs": "list[str]", "n": "int"},
        [("len(xs) + n == 6", True), ("n > 3", True)],
    ),
    "optional-list-len": (
        {"xs": "Optional[list[int]]"},
        [("xs is not None and len(xs) == 2", True)],
    ),
    "optional-list-none": ({"xs": "Optional[list[int]]"}, [("xs is None", True)]),
    "len-of-expression": ({"s": "str"}, [("len(s) > 2", True), ("s != ''", True)]),
    "unicode-literal": ({"s": "str"}, [(r"s == 'é中\U0001f600'", True)]),
    "escape-literal": ({"s": "str"}, [(r"s == 'a\n\t\\u{41}\x00z'", True)]),
    "lone-backslash": ({"s": "str"}, [(r"s == '\\'", True)]),
    "unicode-startswith": ({"s": "str"}, [(r"s.startswith('ü') and s != ''", True)]),
    "unicode-in": ({"s": "str"}, [("'日本' in s", True)]),
    "non-str-eq-is-false": ({"x": "int"}, [("x == 'a'", False)]),
    "mixed-eq-bool-int": ({"n": "int"}, [("n == True", True)]),
    "variable-division-side-condition": (
        {"a": "int", "b": "int"},
        [("a / b == 2", True), ("b != 7", True)],
    ),
}


@pytest.mark.parametrize(("annotations", "clauses"), SAT_CASES.values(), ids=SAT_CASES.keys())
def test_sat_models_satisfy_the_condition_in_python(harness, annotations, clauses):
    solution = harness.check(annotations, clauses)

    assert set(solution.inputs) <= set(annotations)
    assert not solution.reason


UNSAT_CASES = {
    "empty-interval": ({"x": "int"}, [("x > 5 and x < 3", True)]),
    "contradicting-clauses": ({"x": "int"}, [("x > 5", True), ("x > 5", False)]),
    "string-length": ({"s": "str"}, [("s == 'abc'", True), ("len(s) > 5", True)]),
    "string-prefix": ({"s": "str"}, [("s.startswith('a')", True), ("s.startswith('b')", True)]),
    "parity": ({"n": "int"}, [("n % 2 == 0", True), ("n % 2 == 1", True)]),
    "chained": ({"a": "int", "b": "int"}, [("a < b < a", True)]),
    "optional-none-and-read": ({"x": "int | None"}, [("x is None", True), ("x > 3", True)]),
    "none-and-not-none": ({"x": "int | None"}, [("x is None", True), ("x is not None", True)]),
    "isinstance-constant": ({"x": "int"}, [("isinstance(x, str)", True)]),
    "non-optional-is-none": ({"x": "int"}, [("x is None", True)]),
    "constant-false": ({"x": "int"}, [("False", True)]),
    "negative-length": ({"xs": "list[int]"}, [("len(xs) < 0", True)]),
    "float-interval": ({"x": "float"}, [("x > 1.5 and x < 1.25", True)]),
    "in-literals": ({"x": "int"}, [("x in (1, 2)", True), ("x > 2", True)]),
}


@pytest.mark.parametrize(("annotations", "clauses"), UNSAT_CASES.values(), ids=UNSAT_CASES.keys())
def test_contradictory_conditions_are_unsat(harness, annotations, clauses):
    solution = harness.solve(annotations, clauses)

    assert solution.status is SolveStatus.UNSAT
    assert not solution.inputs


UNSUPPORTED_CASES = {
    "unknown-call": ({"x": "int"}, [("foo(x) > 1", True)], "foo"),
    "method-call": ({"s": "str"}, [("s.lower() == 'a'", True)], "s.lower"),
    "upper": ({"s": "str"}, [("s.upper() == 'A'", True)], "upper"),
    "conversion-int": ({"s": "str"}, [("int(s) > 1", True)], "int"),
    "conversion-str": ({"n": "int"}, [("str(n) == '1'", True)], "str"),
    "conversion-float": ({"s": "str"}, [("float(s) > 1", True)], "float"),
    "power": ({"x": "int"}, [("x ** 2 > 4", True)], "Pow"),
    "bit-and": ({"x": "int"}, [("x & 1", True)], "BitAnd"),
    "invert": ({"x": "int"}, [("~x > 1", True)], "~x"),
    "attribute": ({"x": "int"}, [("x.real > 1", True)], "x.real"),
    "subscript": ({"s": "str"}, [("s[0] == 'a'", True)], "s[0]"),
    "ifexp": ({"x": "int"}, [("(1 if x else 2) > 1", True)], "if x"),
    "unknown-name": ({"x": "int"}, [("LIMIT > x", True)], "LIMIT"),
    "list-value": ({"xs": "list[int]"}, [("xs[0] > 1", True)], "xs"),
    "list-compare": ({"xs": "list[int]"}, [("xs == [1]", True)], "xs"),
    "list-membership": ({"xs": "list[int]"}, [("3 in xs", True)], "xs"),
    "dict-param": ({"d": "dict[str, int]"}, [("len(d) > 1", True)], "dict"),
    "dict-truthy": ({"d": "dict[str, int]"}, [("d", True)], "dict"),
    "unmodelled-class": ({"x": "Foo"}, [("x > 1", True)], "Foo"),
    "union-of-types": ({"x": "int | str"}, [("x == 1", True)], "int | str"),
    "no-annotation-no-hint": ({"x": None}, [("x", True)], "infer"),
    "conflicting-hints": ({"x": None}, [("x == 'a' or x > 3", True)], "infer"),
    "len-of-int": ({"n": "int"}, [("len(n) > 1", True)], "len"),
    "len-two-args": ({"s": "str"}, [("len(s, s) > 1", True)], "len"),
    "abs-two-args": ({"x": "int"}, [("abs(x, x) > 1", True)], "abs"),
    "min-three-args": ({"x": "int"}, [("min(x, 1, 2) > 0", True)], "min"),
    "keyword-call": ({"x": "int"}, [("max(x, 1, key=abs) > 0", True)], "max"),
    "starred-call": ({"x": "int"}, [("max(*x) > 0", True)], "max"),
    "floordiv-variable": ({"a": "int", "b": "int"}, [("a // b > 1", True)], "constant divisor"),
    "mod-zero": ({"a": "int"}, [("a % 0 > 1", True)], "constant divisor"),
    "floordiv-float": ({"x": "float"}, [("x // 2 > 1", True)], "integers only"),
    "division-by-zero-constant": ({"x": "float"}, [("x / 0 > 1", True)], "zero"),
    "string-concat": ({"s": "str"}, [("s + 'a' == 'ab'", True)], "not a number"),
    "string-vs-number-order": ({"s": "str"}, [("s < 1", True)], "string and a number"),
    "is-not-none-literal": ({"x": "int"}, [("x is 5", True)], "None"),
    "none-as-value": ({"x": "int"}, [("x > None", True)], "None"),
    "in-non-literal": ({"x": "int", "y": "int"}, [("x in (y, 2)", True)], "literals"),
    "in-numbers": ({"x": "int", "y": "int"}, [("x in y", True)], "in"),
    "bytes-constant": ({"x": "int"}, [("x == b'a'", True)], "bytes"),
    "huge-float-constant": ({"x": "float"}, [("x < 1e999", True)], "exact"),
    "isinstance-unknown-annotation": ({"x": None}, [("isinstance(x, int)", True)], "isinstance"),
    "isinstance-unknown-class": ({"x": "int"}, [("isinstance(x, Foo)", True)], "isinstance"),
    "isinstance-non-name": ({"x": "int"}, [("isinstance(x + 1, int)", True)], "isinstance"),
    "isinstance-unmodelled-optional": (
        {"x": "Foo | None"},
        [("isinstance(x, int)", True)],
        "isinstance",
    ),
    "syntax-error": ({"x": "int"}, [("x >", True)], "cannot parse"),
    "required-unknown-param": (
        {"x": "int", "z": "Foo"},
        [("x > 1", True)],
        "cannot synthesize value for parameter z: Foo",
    ),
    "required-unannotated-param": (
        {"x": "int", "y": None},
        [("x > 1", True)],
        "cannot synthesize value for parameter y",
    ),
    "unmodelled-optional-must-be-present": (
        {"x": "Foo | None"},
        [("x is not None", True)],
        "cannot synthesize value for parameter x: Foo | None",
    ),
}


@pytest.mark.parametrize(
    ("annotations", "clauses", "reason"),
    UNSUPPORTED_CASES.values(),
    ids=UNSUPPORTED_CASES.keys(),
)
def test_unsupported_constructs_are_reported_not_raised(harness, annotations, clauses, reason):
    solution = harness.solve(annotations, clauses)

    assert solution.status is SolveStatus.UNSUPPORTED
    assert reason in solution.reason
    assert not solution.inputs


@pytest.mark.parametrize(
    "function_kwargs",
    [{"class_name": "C"}, {"class_name": "C", "is_classmethod": True}],
    ids=["instance-method", "classmethod"],
)
def test_methods_are_left_to_the_llm(harness, function_kwargs):
    params = [Parameter("self"), Parameter("x", annotation="int")]

    solution = harness.solve(params, [("x > 1", True)], **function_kwargs)

    assert solution.status is SolveStatus.UNSUPPORTED
    assert solution.reason == "instance methods are handled by the LLM"


def test_static_methods_are_solved_like_functions(harness):
    solution = harness.check(
        [Parameter("x", annotation="int")], [("x > 1", True)], class_name="C", is_static=True
    )

    assert solution.inputs["x"] > 1


def test_classify_example_picks_small_readable_values(harness):
    solution = harness.check(
        {"age": "int", "role": "str"},
        [("age >= 18 and role == 'admin'", True), ("age < 0", False)],
    )

    assert solution.status is SAT
    assert dict(solution.inputs) == {"age": 18, "role": "admin"}


def test_values_are_small_and_strings_are_plain_ascii(harness):
    solution = harness.check(
        {"n": "int", "s": "str"}, [("n != 0", True), ("s != ''", True), ("len(s) > 3", True)]
    )

    assert solution.inputs["n"] == 1
    assert len(solution.inputs["s"]) == 4
    assert set(solution.inputs["s"]) <= set("abcdefghijklmnopqrstuvwxyz0123456789_ ")


def test_negative_numbers_are_chosen_only_when_required(harness):
    solution = harness.check({"n": "int"}, [("n < 0", True)])

    assert solution.inputs["n"] == -1


def test_whole_floats_are_preferred(harness):
    solution = harness.check({"x": "float"}, [("x > 2.5", True)])

    assert solution.inputs["x"] == 3.0
    assert isinstance(solution.inputs["x"], float)


def test_value_types_follow_the_annotations(harness):
    solution = harness.check(
        {"i": "int", "f": "float", "b": "bool", "s": "str"},
        [("i > 0 and f > 0 and b and s", True)],
    )

    assert [type(solution.inputs[k]) for k in "ifbs"] == [int, float, bool, str]


def test_parameters_not_in_the_condition_get_neutral_values(harness):
    annotations = {
        "x": "int",
        "i": "int",
        "f": "float",
        "b": "bool",
        "s": "str",
        "opt": "Optional[int]",
        "opt2": "str | None",
        "xs": "list[int]",
        "lst": "List[str]",
        "d": "dict[str, int]",
        "t": "tuple[int, ...]",
        "by": "bytes",
    }

    solution = harness.check(annotations, [("x > 0", True)])

    assert dict(solution.inputs) == {
        "x": 1,
        "i": 0,
        "f": 0.0,
        "b": False,
        "s": "",
        "opt": None,
        "opt2": None,
        "xs": [],
        "lst": [],
        "d": {},
        "t": (),
        "by": b"",
    }


def test_unmentioned_parameters_with_defaults_are_omitted(harness):
    params = [
        Parameter("x", annotation="int"),
        Parameter("y", annotation="int", default="3"),
        Parameter("name", annotation="str", default="'bob'"),
        Parameter("z", default="1"),  # unannotated with a default: never needs a value
        Parameter("z2", default="None"),
    ]

    solution = harness.check(params, [("x > 1", True)])

    assert set(solution.inputs) == {"x"}


def test_mentioned_parameters_with_defaults_are_included(harness):
    params = [Parameter("x", annotation="int"), Parameter("y", annotation="int", default="3")]

    solution = harness.check(params, [("x > 1 and y > 10", True)])

    assert solution.inputs["y"] > 10


def test_variadic_parameters_are_never_synthesized(harness):
    params = [
        Parameter("x", annotation="int"),
        Parameter("args", kind=ParamKind.VAR_POSITIONAL),
        Parameter("opts", kind=ParamKind.VAR_KEYWORD, annotation="int"),
    ]

    solution = harness.check(params, [("x > 1", True)])

    assert set(solution.inputs) == {"x"}


def test_variadic_parameters_in_conditions_are_unsupported(harness):
    params = [Parameter("args", kind=ParamKind.VAR_POSITIONAL)]

    solution = harness.solve(params, [("len(args) > 1", True)])

    assert solution.status is SolveStatus.UNSUPPORTED


def test_keyword_only_parameters_are_included(harness):
    params = [Parameter("x", annotation="int", kind=ParamKind.KEYWORD_ONLY)]

    solution = harness.check(params, [("x > 1", True)])

    assert solution.inputs["x"] > 1


def test_solution_carries_its_target(harness):
    function = harness.function({"x": "int"})
    target = harness.target([("x > 1", True)])

    solution = harness.solver.solve(function, target)

    assert solution.target is target


def test_long_strings_are_found_beyond_the_default_bound(harness):
    solution = harness.check({"s": "str"}, [("len(s) > 40", True)])

    assert len(solution.inputs["s"]) == 41


def test_string_bound_applies_when_the_condition_allows_it(harness_for):
    harness = harness_for(max_string_length=3)

    solution = harness.check({"s": "str", "t": "str"}, [("len(s) >= 2 and len(t) >= 1", True)])

    assert len(solution.inputs["s"]) == 2
    assert len(solution.inputs["t"]) == 1


def test_string_bound_is_dropped_when_the_condition_needs_more(harness_for):
    solution = harness_for(max_string_length=3).check({"s": "str"}, [("len(s) > 5", True)])

    assert len(solution.inputs["s"]) == 6


@pytest.mark.parametrize("timeout_ms", [0, -5])
def test_invalid_timeout_is_rejected(timeout_ms):
    with pytest.raises(ValueError, match="timeout_ms"):
        Z3HintSolver(timeout_ms=timeout_ms)


def test_negative_string_bound_is_rejected():
    with pytest.raises(ValueError, match="max_string_length"):
        Z3HintSolver(max_string_length=-1)


def test_floats_are_judged_by_python_arithmetic_not_exact_reals(harness):
    # 1/3 * 3 == 1 holds in exact arithmetic and also for the double nearest to 1/3
    solution = harness.check({"x": "float"}, [("x * 3 == 1", True)])

    assert solution.inputs["x"] == 1 / 3


def test_models_that_fail_under_float_rounding_are_reported_as_unknown(harness):
    # exact: x = 1; Python: 1.0 + 1e16 == 1e16 != 10000000000000001
    solution = harness.solve({"x": "float"}, [("x + 1e16 == 10000000000000001", True)])

    assert solution.status is SolveStatus.UNKNOWN
    assert "Python semantics" in solution.reason
    assert not solution.inputs


def test_real_solutions_beyond_the_float_range_are_unsupported_not_raised(harness):
    solution = harness.solve({"x": "float"}, [("x > 1.7976931348623157e308 + 1e300", True)])

    assert solution.status is SolveStatus.UNSUPPORTED
    assert "does not fit a float" in solution.reason


def test_string_ordering_above_the_z3_character_range_is_unsupported(harness):
    solution = harness.solve({"s": "str"}, [(r"s > '\U0010ffff'", True)])

    assert solution.status is SolveStatus.UNSUPPORTED
    assert "U+2FFFF" in solution.reason


@pytest.mark.parametrize(
    "clauses",
    [
        [("len(xs) > 3000000", True)],
        [("len(xs) > 5000 and len(xs) < 9000", True)],
    ],
)
def test_sequences_are_never_materialised_beyond_the_hard_cap(harness, clauses):
    solution = harness.solve({"xs": "list[int]"}, clauses)

    assert solution.status is SolveStatus.UNKNOWN
    assert "longer than 256" in solution.reason
    assert not solution.inputs


def test_strings_are_never_materialised_beyond_the_hard_cap(harness):
    solution = harness.solve({"s": "str"}, [("len(s) > 300000", True)])

    assert solution.status is SolveStatus.UNKNOWN


def test_contradictory_lengths_stay_unsat_after_relaxing_the_bound(harness):
    solution = harness.solve({"s": "str"}, [("len(s) > 50 and len(s) < 10", True)])

    assert solution.status is SolveStatus.UNSAT


def test_lengths_well_beyond_the_default_bound_are_found(harness):
    solution = harness.check({"s": "str"}, [("len(s) > 100", True)])

    assert len(solution.inputs["s"]) == 101


def test_hard_nonlinear_problems_time_out_as_unknown(harness_for):
    harness = harness_for(timeout_ms=20)

    solution = harness.solve(
        {"a": "int", "b": "int", "c": "int"},
        [("a * a * a + b * b * b == c * c * c", True), ("a > 0 and b > 0", True)],
    )

    assert solution.status is SolveStatus.UNKNOWN
    assert solution.reason.startswith("z3 gave up")


def test_every_solve_logs_one_debug_event(harness):
    with structlog.testing.capture_logs() as logs:
        harness.solve({"x": "int"}, [("x > 1", True)])
        harness.solve({"x": "int"}, [("x > 1 and x < 0", True)])
        harness.solve({"x": "int"}, [("foo(x)", True)])

    events = [entry for entry in logs if entry["event"] == "hint_solved"]
    assert [e["status"] for e in events] == ["sat", "unsat", "unsupported"]
    assert {e["target"] for e in events} == {"f:2:T"}
    assert all(e["log_level"] == "debug" for e in events)


def test_solver_implements_the_hint_solver_port(solver):
    from pytestgen.ports import HintSolver

    assert isinstance(solver, HintSolver)


# --------------------------------------------------------------------------- `is None`

NULLABLE_CASES = {
    "default-none-unannotated": [Parameter("name", default="None")],
    "default-none-int": [Parameter("name", annotation="int", default="None")],
    "default-none-class": [Parameter("name", annotation="Foo", default="None")],
    "unannotated-no-default": [Parameter("name")],
    "any": [Parameter("name", annotation="Any")],
    "object": [Parameter("name", annotation="object")],
    "union-without-none": [Parameter("name", annotation="int | str")],
}


@pytest.mark.parametrize("params", NULLABLE_CASES.values(), ids=NULLABLE_CASES.keys())
def test_is_none_is_solvable_when_nullability_is_unknown(harness, params):
    solution = harness.check(params, [("name is None", True)])

    assert dict(solution.inputs) == {"name": None}


@pytest.mark.parametrize(
    "params",
    [
        [Parameter("name", annotation="int", default="None")],
        [Parameter("name", default="None")],
    ],
    ids=["int", "unannotated-inferred-from-comparison"],
)
def test_is_not_none_gets_a_real_value_for_nullable_parameters(harness, params):
    solution = harness.check(params, [("name is not None and name > 2", True)])

    assert solution.inputs["name"] > 2


def test_is_none_false_side_never_leaves_the_default_in_place(harness):
    params = [Parameter("name", annotation="int", default="None")]

    solution = harness.check(params, [("name is None", False)])

    assert solution.inputs["name"] is not None


@pytest.mark.parametrize("params", NULLABLE_CASES.values(), ids=NULLABLE_CASES.keys())
def test_is_none_false_side_is_never_a_wrong_sat(harness, params):
    clauses = [("name is None", False)]

    solution = harness.solve(params, clauses)

    if solution.status is SAT:
        assert harness.satisfies(harness.function(params), clauses, solution.inputs)
    else:
        assert solution.status is SolveStatus.UNSUPPORTED
        assert "cannot synthesize value for parameter name" in solution.reason


@pytest.mark.parametrize("annotation", ["int", "str", "list[int]"])
def test_annotated_non_optional_parameters_still_cannot_be_none(harness, annotation):
    solution = harness.solve({"x": annotation}, [("x is None", True)])

    assert solution.status is SolveStatus.UNSAT


def test_unannotated_parameter_compared_to_none_and_a_number(harness):
    solution = harness.check({"x": None}, [("x is None or x > 3", True), ("x is None", False)])

    assert solution.inputs["x"] > 3


def test_undecidable_unannotated_value_with_is_not_none_is_unsupported(harness):
    solution = harness.solve({"x": None}, [("x is not None", True)])

    assert solution.status is SolveStatus.UNSUPPORTED
    assert "cannot synthesize value for parameter x" in solution.reason


# --------------------------------------------------------------------------- short-circuit reads

SHORT_CIRCUIT_CASES = {
    # (annotations, clauses, None is the only solution)
    "guard-then-or": (
        {"x": "int | None"},
        [("x is not None and x > 3", False), ("x is None or x > 3", True)],
        True,
    ),
    "guard-then-or-list": (
        {"xs": "list[int] | None"},
        [("xs is not None and len(xs) > 0", False), ("xs is None or len(xs) > 0", True)],
        True,
    ),
    "none-or-big": ({"x": "Optional[int]"}, [("x is None or x > 3", True)], False),
    "negated-guarded-read": (
        {"x": "int | None"},
        [("not (x is not None and x > 3)", True)],
        False,
    ),
}


@pytest.mark.parametrize(
    ("annotations", "clauses", "only_none"),
    SHORT_CIRCUIT_CASES.values(),
    ids=SHORT_CIRCUIT_CASES.keys(),
)
def test_none_stays_admissible_when_the_read_is_short_circuited(
    harness, annotations, clauses, only_none
):
    solution = harness.check(annotations, clauses)

    if only_none:
        assert next(iter(solution.inputs.values())) is None


def test_unguarded_negated_read_does_not_admit_none(harness):
    # `not x > 3` raises TypeError for None, so only a real number may be returned
    solution = harness.check({"x": "int | None"}, [("not x > 3", True)])

    assert solution.inputs["x"] is not None


def test_reads_after_an_or_branch_that_is_not_taken_still_need_a_value(harness):
    solution = harness.solve(
        {"x": "int | None"}, [("x is None or x > 3", True), ("x is None", False)]
    )

    assert solution.status is SAT
    assert solution.inputs["x"] > 3


def test_unmodelled_optional_that_must_be_present_gets_its_neutral_value(harness):
    solution = harness.check({"d": "dict[str, int] | None"}, [("d is not None", True)])

    assert solution.inputs["d"] == {}
