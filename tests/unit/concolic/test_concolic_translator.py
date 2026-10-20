"""ExprTranslator, z3 helpers, input extraction and search fallbacks."""

from __future__ import annotations

import ast
from fractions import Fraction

import pytest
import z3

from pytestgen.concolic import ExprTranslator, UnsupportedExpression, Z3HintSolver
from pytestgen.concolic.environment import Variable, VariableEnvironment
from pytestgen.concolic.inputs import real_fraction
from pytestgen.concolic.ptypes import parse_annotation, unknown_type
from pytestgen.concolic.search import ModelSearch
from pytestgen.concolic.translator import parse_clause
from pytestgen.concolic.verify import holds_in_python
from pytestgen.concolic.zutil import as_number, python_string, real_val, string_val, unify
from pytestgen.domain.models import (
    BranchKind,
    BranchTarget,
    Clause,
    FunctionInfo,
    PathCondition,
    SolveStatus,
)
from pytestgen.errors import PytestgenError


@pytest.fixture
def env():
    types = {
        "n": parse_annotation("int"),
        "x": parse_annotation("float"),
        "s": parse_annotation("str"),
        "flag": parse_annotation("bool"),
        "o": parse_annotation("int | None"),
        "xs": parse_annotation("list[int]"),
        "u": unknown_type(),
    }
    return VariableEnvironment(types)


def translate(env: VariableEnvironment, source: str, expected: bool = True):
    return ExprTranslator(env).clause(Clause(source, expected))


def is_sat(formula, *facts) -> bool:
    solver = z3.Solver()
    solver.add(formula, *facts)
    return solver.check() == z3.sat


def is_valid(formula) -> bool:
    return not is_sat(z3.Not(formula))


def test_unsupported_expression_is_a_pytestgen_error():
    assert issubclass(UnsupportedExpression, PytestgenError)


def test_clause_expected_false_negates(env):
    n = env.variable("n").term

    assert is_valid(translate(env, "n > 3", expected=False) == (n <= 3))


@pytest.mark.parametrize(
    ("source", "values", "result"),
    [
        ("n + 2 * 3 == 7", {"n": 1}, True),
        ("n - 1 == 0", {"n": 1}, True),
        ("-n == 2", {"n": -2}, True),
        ("+n == 2", {"n": 2}, True),
        ("n // 2 == 3", {"n": 7}, True),
        ("n // 2 == -4", {"n": -7}, True),
        ("n % 3 == 1", {"n": -2}, True),
        ("n // -2 == -4", {"n": 7}, True),
        ("n % -3 == -2", {"n": 7}, True),
        ("n % -3 == -1", {"n": -4}, True),
        ("abs(n) == 5", {"n": -5}, True),
        ("min(n, 3) == 3", {"n": 9}, True),
        ("max(n, 3) == 9", {"n": 9}, True),
        ("n / 2 == 2.5", {"n": 5}, True),
        ("n / 4 > 1", {"n": 4}, False),
        ("flag + 1 == 2", {"flag": True}, True),
        ("n < 5 < 7", {"n": 4}, True),
        ("n < 5 < 3", {"n": 4}, False),
        ("n not in (1, 2)", {"n": 3}, True),
        ("n in [1, 2]", {"n": 2}, True),
        ("n == 1.0", {"n": 1}, True),
        ("not (n > 1)", {"n": 1}, True),
        ("n > 1 or n < -1", {"n": 0}, False),
        ("not flag", {"flag": False}, True),
        ("(n > 1) == True", {"n": 5}, True),
        ("1 == 'a'", {}, False),
        ("None is None", {}, True),
        ("n is None", {"n": 1}, False),
        ("n is not None", {"n": 1}, True),
        ("1", {}, True),
        ("0", {}, False),
        ("''", {}, False),
        ("None", {}, False),
    ],
)
def test_translation_matches_python_semantics(env, source, values, result):
    formula = translate(env, source)
    pins = [env.variable(name).term == value for name, value in values.items()]

    assert is_sat(formula, *pins) is result
    # the table itself agrees with real Python
    assert bool(eval(source, {}, dict(values))) is result  # noqa: S307


def test_string_operations(env):
    s = env.variable("s").term

    assert is_valid(
        z3.Implies(translate(env, "s.startswith('ab')"), z3.PrefixOf(string_val("ab"), s))
    )
    assert is_valid(
        z3.Implies(translate(env, "s.endswith('ab')"), z3.SuffixOf(string_val("ab"), s))
    )
    assert is_valid(translate(env, "'ab' in s") == z3.Contains(s, string_val("ab")))
    assert is_valid(translate(env, "len(s) > 2") == (z3.Length(s) > 2))
    assert is_valid(translate(env, "len('abc') == 3"))
    assert is_valid(translate(env, "s") == (z3.Length(s) > 0))
    assert is_valid(translate(env, "s == 1", expected=False))


def test_variable_division_records_a_side_condition(env):
    translator = ExprTranslator(env)
    translator.clause(Clause("n / n > 0", True))

    assert len(translator.side_conditions) == 1
    assert is_valid(translator.side_conditions[0] == (z3.ToReal(env.variable("n").term) != 0))


def test_constant_division_has_no_side_condition(env):
    translator = ExprTranslator(env)
    translator.clause(Clause("n / 2 > 0", True))

    assert translator.side_conditions == []


def test_optional_equality_is_none_aware(env):
    o = env.variable("o")

    assert not is_sat(translate(env, "o == 5"), o.is_none)
    assert is_sat(translate(env, "o != 5"), o.is_none)
    assert is_valid(translate(env, "o is None") == o.is_none)
    assert is_valid(translate(env, "o == None") == o.is_none)


def test_reading_an_optional_value_requires_it_to_be_present(env):
    o = env.variable("o")

    assert is_valid(translate(env, "o > 3") == z3.And(z3.Not(o.is_none), o.term > 3))
    assert not is_sat(translate(env, "o > 3", expected=False), o.is_none)


@pytest.mark.parametrize(
    ("source", "none_allowed"),
    [
        ("o is None or o > 3", True),
        ("o is not None and o > 3", False),
        ("not (o is not None and o > 3)", True),
        ("o is None or len(xs) > 0 or o > 3", True),
        ("not o > 3", False),
        ("(not o > 3) or o is None", False),
        ("not (o is None or o > 3)", False),
        ("o > 3 or o is None", False),
    ],
)
def test_optional_reads_follow_short_circuit_evaluation(env, source, none_allowed):
    o = env.variable("o")

    assert is_sat(translate(env, source), o.is_none) is none_allowed
    assert bool(eval(source, {"xs": [1]}, {"o": None}) if none_allowed else True)  # noqa: S307


def test_len_of_an_optional_list_requires_it_to_be_present():
    env = VariableEnvironment({"xs": parse_annotation("list[int] | None")})
    xs = env.variable("xs")

    assert not is_sat(translate(env, "len(xs) > 0"), xs.is_none)
    assert is_sat(translate(env, "xs is None or len(xs) > 0"), xs.is_none)


def test_lengths_of_unmodelled_lists_become_non_negative_ints(env):
    translate(env, "len(xs) == 2")

    assert env.variable("xs").term.sort() == z3.IntSort()
    assert len(env.constraints) == 1


def test_none_flag_for_non_optional_parameters_is_absent(env):
    assert env.none_flag("n") is None
    assert env.none_flag("o") is not None


def test_none_flag_for_unmodelled_optionals_has_no_term():
    env = VariableEnvironment({"obj": parse_annotation("Foo | None")})

    flag = env.none_flag("obj")

    assert flag is env.none_flag("obj")
    assert env.touched["obj"].term is None
    with pytest.raises(UnsupportedExpression, match="Foo"):
        env.variable("obj")


def test_unknown_names_are_reported(env):
    with pytest.raises(UnsupportedExpression, match="unknown name 'zzz'"):
        translate(env, "zzz > 1")


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("u > 1", "cannot infer the type of parameter u"),
        ("xs > 1", "list[int] parameter xs is only supported inside len()"),
        ("n > None", "None cannot be used as a value"),
        ("s[0]", "unsupported expression"),
        ("(lambda: 1)()", "not supported"),
        ("f(n)", "call to `f()`"),
        ("n.real", "unsupported expression"),
        ("n @ n", "MatMult"),
        ("x // 2", "integers only"),
        ("n // n", "constant divisor"),
        ("n / 0", "zero"),
        ("s - 1", "not a number"),
        ("s < 1", "string and a number"),
        ("n in n", "literal collection or two strings"),
        ("n in (n, 1)", "literals"),
        ("n is 1", "only supported against None"),
        ("min(n)", "exactly two"),
        ("abs()", "exactly one"),
        ("len()", "exactly one"),
        ("len(n)", "len() of"),
        ("len(n + 1)", "len() of"),
        ("s.startswith()", "exactly one"),
        ("n.startswith('a')", "string operands"),
        ("isinstance(n)", "only supported on a parameter"),
        ("isinstance(u, int)", "unknown for isinstance"),
        ("isinstance(n, Foo)", "isinstance() against"),
        ("isinstance(n, 3)", "isinstance() against"),
        ("n == b'x'", "bytes"),
        ("max(n, 1, key=abs)", "not supported"),
        ("1j > n", "complex"),
    ],
)
def test_unsupported_expressions_explain_themselves(env, source, fragment):
    with pytest.raises(UnsupportedExpression) as info:
        translate(env, source)

    assert fragment in str(info.value)


def test_long_snippets_are_truncated_in_reasons(env):
    source = "n[" + " + ".join(["n"] * 40) + "]"

    with pytest.raises(UnsupportedExpression) as info:
        translate(env, source)

    assert "..." in str(info.value)
    assert len(str(info.value)) < 120


def test_parse_clause_rejects_garbage():
    with pytest.raises(UnsupportedExpression, match="cannot parse"):
        parse_clause(Clause("x >", True))


def test_parse_clause_returns_the_expression():
    assert isinstance(parse_clause(Clause("x > 1", False)), ast.Compare)


def test_isinstance_with_optional_depends_on_none_flag(env):
    o = env.variable("o")

    formula = translate(env, "isinstance(o, int)")

    assert is_valid(formula == z3.Not(o.is_none))


def test_ordering_strings_beyond_the_z3_character_range_is_unsupported(env):
    with pytest.raises(UnsupportedExpression, match=r"U\+2FFFF"):
        translate(env, r"s > '􏿿'")
    assert is_sat(translate(env, r"s < '𯿾'"))
    with pytest.raises(UnsupportedExpression, match=r"U\+2FFFF"):
        translate(env, r"s < '𰀀'")


# --------------------------------------------------------------------------- z3 helpers


@pytest.mark.parametrize(
    "text", ["", "plain", "é", "\n\t\x00", "\\", "\\u{41}", "\U0001f600", "中文", "퟿"]
)
def test_strings_round_trip_exactly(text):
    solver = z3.Solver()
    var = z3.String("v")
    solver.add(var == string_val(text))
    assert solver.check() == z3.sat

    assert python_string(solver.model().eval(var, model_completion=True)) == text


def test_real_val_is_exact():
    assert z3.is_true(z3.simplify(real_val(0.5) == z3.RealVal("1/2")))
    assert float(real_fraction(z3.simplify(real_val(0.1)))) == 0.1


def test_real_val_rejects_non_finite_floats():
    with pytest.raises(UnsupportedExpression, match="exact"):
        real_val(float("inf"))


def test_real_fraction_falls_back_to_decimals():
    class Algebraic:
        def as_decimal(self, digits: int) -> str:
            assert digits > 0
            return "0.5?"

    assert real_fraction(Algebraic()) == Fraction(1, 2)


def test_as_number_and_unify():
    assert z3.is_int(as_number(z3.IntVal(1)))
    assert z3.is_int(as_number(z3.BoolVal(True)))
    with pytest.raises(UnsupportedExpression):
        as_number(string_val("a"))
    left, right = unify(z3.IntVal(1), z3.RealVal(2))
    assert z3.is_real(left)
    assert z3.is_real(right)
    left, right = unify(z3.IntVal(1), z3.IntVal(2))
    assert z3.is_int(left)
    assert z3.is_int(right)


# --------------------------------------------------------------------------- search


class FakeOptimize:
    """Optimize stand-in that always gives up."""

    def set(self, **_: object) -> None: ...

    def add(self, *_: object) -> None: ...

    def add_soft(self, *_: object, **__: object) -> None: ...

    def minimize(self, *_: object) -> None: ...

    def check(self) -> object:
        return z3.unknown

    def reason_unknown(self) -> str:
        return "fake timeout"


def test_search_falls_back_to_a_plain_solver_when_optimize_gives_up(monkeypatch):
    monkeypatch.setattr(z3, "Optimize", FakeOptimize)
    x = z3.Int("x")

    result = ModelSearch(timeout_ms=1000, max_length=8).find([x > 3], [])

    assert result.status is SolveStatus.SAT
    assert result.model.eval(x, model_completion=True).as_long() > 3


def test_search_fallback_can_prove_unsat(monkeypatch):
    monkeypatch.setattr(z3, "Optimize", FakeOptimize)
    x = z3.Int("x")

    result = ModelSearch(timeout_ms=1000, max_length=8).find([x > 3, x < 3], [])

    assert result.status is SolveStatus.UNSAT


def test_search_reports_unknown_with_a_reason(monkeypatch):
    monkeypatch.setattr(z3, "Optimize", FakeOptimize)
    a, b, c = z3.Ints("a b c")
    hard = [a > 0, b > 0, a * a * a + b * b * b == c * c * c]

    result = ModelSearch(timeout_ms=10, max_length=8).find(hard, [])

    assert result.status is SolveStatus.UNKNOWN
    assert result.reason.startswith("z3 gave up")


class FakeClock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr("pytestgen.concolic.search.time.monotonic", fake)
    return fake


def fake_optimize(clock, outcome):
    """Optimize stand-in that burns ten seconds of the fake clock, then answers ``outcome``."""

    class Slow(FakeOptimize):
        def check(self) -> object:
            clock.now += 10
            return outcome

    return Slow


def test_search_budget_is_shared_between_attempts(monkeypatch, clock):
    monkeypatch.setattr(z3, "Optimize", fake_optimize(clock, z3.unsat))
    s = z3.String("s")
    string = Variable("s", parse_annotation("str"), s)

    result = ModelSearch(timeout_ms=1000, max_length=4).find([z3.Length(s) > 9], [string])

    assert result.status is SolveStatus.UNKNOWN
    assert "timeout" in result.reason


def test_search_does_not_start_the_fallback_after_the_deadline(monkeypatch, clock):
    monkeypatch.setattr(z3, "Optimize", fake_optimize(clock, z3.unknown))

    result = ModelSearch(timeout_ms=1000, max_length=4).find([z3.Int("x") > 3], [])

    assert result.status is SolveStatus.UNKNOWN
    assert result.reason == "z3 gave up: fake timeout"


@pytest.mark.parametrize(
    ("annotation", "build_constraint", "expected"),
    [
        ("str", lambda t: z3.Length(t) > 5000, SolveStatus.UNKNOWN),
        ("list[int]", lambda t: t > 5000, SolveStatus.UNKNOWN),
        ("str", lambda t: z3.And(z3.Length(t) > 5, z3.Length(t) < 3), SolveStatus.UNSAT),
        ("str", lambda t: z3.Length(t) > 20, SolveStatus.SAT),
    ],
    ids=["string-too-long", "list-too-long", "unsat-for-any-length", "within-cap"],
)
def test_search_never_goes_beyond_the_length_cap(annotation, build_constraint, expected):
    term = z3.String("v") if annotation == "str" else z3.Int("v__len")
    variable = Variable("v", parse_annotation(annotation), term)
    extra = [term >= 0] if annotation != "str" else []

    result = ModelSearch(timeout_ms=500, max_length=4, length_cap=200).find(
        [build_constraint(term), *extra], [variable]
    )

    assert result.status is expected
    if annotation != "str" and expected is SolveStatus.UNKNOWN:  # z3 strings may just time out
        assert "longer than 200" in result.reason


def test_z3_exceptions_become_unknown(monkeypatch):
    def boom(*_: object, **__: object) -> None:
        raise z3.Z3Exception("kaboom")

    monkeypatch.setattr(ModelSearch, "find", boom)
    function = FunctionInfo(qualname="f", name="f", lineno=1, end_lineno=2, source="")
    target = BranchTarget("f", 1, BranchKind.IF, "", True, None, PathCondition())

    solution = Z3HintSolver().solve(function, target)

    assert solution.status is SolveStatus.UNKNOWN
    assert "kaboom" in solution.reason


# --------------------------------------------------------------------------- python check


@pytest.mark.parametrize(
    ("clauses", "inputs", "result"),
    [
        ([Clause("x > 1", True)], {"x": 2}, True),
        ([Clause("x > 1", False)], {"x": 2}, False),
        ([Clause("x > 1", True), Clause("x < 5", False)], {"x": 2}, False),
        ([Clause("x > 1", True)], {"x": None}, False),  # TypeError: not a model
        ([Clause("x / y > 1", True)], {"x": 1, "y": 0}, False),  # ZeroDivisionError
        ([Clause("x > 1", True)], {}, False),  # NameError
        ([Clause("open('f')", True)], {}, False),  # no builtins beyond the safe subset
    ],
)
def test_python_check_evaluates_clauses_on_the_inputs(clauses, inputs, result):
    trees = [parse_clause(clause) for clause in clauses]

    assert holds_in_python(clauses, trees, inputs) is result
