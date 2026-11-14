from __future__ import annotations

import ast
import enum
from collections import OrderedDict
from decimal import Decimal

import pytest

from pytestgen.codegen import to_literal_source
from pytestgen.codegen.literals import literal_source


class _Color(enum.IntEnum):
    RED = 1


class _MyStr(str):
    __slots__ = ()


@pytest.mark.parametrize(
    ("value", "source"),
    [
        pytest.param(None, "None", id="none"),
        pytest.param(True, "True", id="bool-true"),
        pytest.param(False, "False", id="bool-false"),
        pytest.param(0, "0", id="int-zero"),
        pytest.param(-7, "-7", id="int-negative"),
        pytest.param(10**30, "1000000000000000000000000000000", id="int-big"),
        pytest.param(1.5, "1.5", id="float"),
        pytest.param(complex(1, 2), "(1+2j)", id="complex"),
        pytest.param(2j, "2j", id="complex-imaginary-only"),
        pytest.param(complex(-0.0, -1), "(-0-1j)", id="complex-negative"),
        pytest.param([1j, {"z": (1 - 1j,)}], "[1j, {'z': ((1-1j),)}]", id="complex-nested"),
        pytest.param(-0.0, "-0.0", id="float-negative-zero"),
        pytest.param(1e20, "1e+20", id="float-exponent"),
        pytest.param("abc", "'abc'", id="str"),
        pytest.param("it's", '"it\'s"', id="str-with-apostrophe"),
        pytest.param("line\nbreak", "'line\\nbreak'", id="str-newline"),
        pytest.param("", "''", id="str-empty"),
        pytest.param("привет", "'привет'", id="str-unicode"),
        pytest.param(b"\x00ab", "b'\\x00ab'", id="bytes"),
        pytest.param([], "[]", id="list-empty"),
        pytest.param([1, [2, [3]]], "[1, [2, [3]]]", id="list-nested"),
        pytest.param((), "()", id="tuple-empty"),
        pytest.param((1,), "(1,)", id="tuple-single"),
        pytest.param((1, "a"), "(1, 'a')", id="tuple-pair"),
        pytest.param({}, "{}", id="dict-empty"),
        pytest.param({"a": [1, (2,)], 3: None}, "{'a': [1, (2,)], 3: None}", id="dict-nested"),
        pytest.param({3, 1, 2}, "{1, 2, 3}", id="set-sorted-by-source"),
        pytest.param({"b", "a"}, "{'a', 'b'}", id="set-of-str"),
        pytest.param({(1, 2)}, "{(1, 2)}", id="set-of-tuple"),
    ],
)
def test_literal_source_is_repr_for_literals(value: object, source: str) -> None:
    assert to_literal_source(value) == source
    assert ast.literal_eval(source) == value


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="inf"),
        pytest.param(float("-inf"), id="minus-inf"),
        pytest.param(set(), id="empty-set"),
        pytest.param(frozenset({1}), id="frozenset"),
        pytest.param(object(), id="object"),
        pytest.param(complex(float("nan"), 1), id="complex-nan"),
        pytest.param(complex(1, float("inf")), id="complex-inf"),
        pytest.param(Decimal("1.5"), id="decimal"),
        pytest.param(OrderedDict(a=1), id="dict-subclass"),
        pytest.param(_Color.RED, id="int-enum"),
        pytest.param(_MyStr("x"), id="str-subclass"),
        pytest.param([1, float("nan")], id="nan-inside-list"),
        pytest.param({"k": object()}, id="object-inside-dict"),
        pytest.param({float("inf"): 1}, id="inf-as-key"),
        pytest.param({frozenset(): 1}, id="frozenset-as-key"),
        pytest.param(lambda: 1, id="lambda"),
        pytest.param(10**5000, id="int-beyond-literal-eval-limit"),
    ],
)
def test_literal_source_rejects_non_literals(value: object) -> None:
    assert to_literal_source(value) is None


def test_literal_source_rejects_too_deep_nesting() -> None:
    value: list[object] = []
    for _ in range(5000):
        value = [value]
    assert to_literal_source(value) is None


@pytest.mark.parametrize("value", [True, False])
def test_bool_is_not_confused_with_int(value: bool) -> None:
    source = to_literal_source(value)
    assert source == str(value)
    assert type(ast.literal_eval(source)) is bool
    assert to_literal_source(int(value)) == str(int(value))


def test_bool_inside_containers_stays_bool() -> None:
    source = to_literal_source([True, 1, {1: False, 2: True}])
    assert source is not None
    parsed = ast.literal_eval(source)
    assert [type(x) for x in parsed[:2]] == [bool, int]


@pytest.mark.parametrize(
    ("value", "source"),
    [
        pytest.param("abc", '"abc"', id="plain"),
        pytest.param("it's", '"it\'s"', id="apostrophe-already-double"),
        pytest.param('say "hi"', "'say \"hi\"'", id="contains-double-quote-stays-single"),
        pytest.param("a\\b", '"a\\\\b"', id="backslash"),
        pytest.param(b"ab", 'b"ab"', id="bytes"),
        pytest.param(["x", ("y",), {"k": "v"}], '["x", ("y",), {"k": "v"}]', id="nested"),
    ],
)
def test_double_quote_style(value: object, source: str) -> None:
    assert literal_source(value, double_quotes=True) == source
    assert ast.literal_eval(source) == value
