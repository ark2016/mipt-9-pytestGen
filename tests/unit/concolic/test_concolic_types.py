"""Annotation parsing, usage-based inference and neutral values."""

from __future__ import annotations

import ast

import pytest

from pytestgen.concolic.inference import infer_types
from pytestgen.concolic.ptypes import Kind, ParamType, parse_annotation
from pytestgen.domain.models import Parameter

I, R, B, S = Kind.INT, Kind.REAL, Kind.BOOL, Kind.STR  # noqa: E741


@pytest.mark.parametrize(
    ("text", "kind", "optional"),
    [
        ("int", I, False),
        ("float", R, False),
        ("bool", B, False),
        ("str", S, False),
        ("bytes", Kind.BYTES, False),
        ("int | None", I, True),
        ("None | int", I, True),
        ("Optional[str]", S, True),
        ("typing.Optional[int]", I, True),
        ("Union[int, None]", I, True),
        ("Union[None, float]", R, True),
        ("Optional[Union[int, None]]", I, True),
        ("'int | None'", I, True),
        ("Annotated[int, 'positive']", I, False),
        ("Annotated[Optional[str], 1]", S, True),
        ("list[int]", Kind.LIST, False),
        ("List[str]", Kind.LIST, False),
        ("Sequence[float]", Kind.LIST, False),
        ("tuple[int, ...]", Kind.TUPLE, False),
        ("Tuple[str, int]", Kind.TUPLE, False),
        ("dict[str, int]", Kind.DICT, False),
        ("Mapping[str, int]", Kind.DICT, False),
        ("list[int] | None", Kind.LIST, True),
        ("Foo", Kind.OTHER, False),
        ("Foo | None", Kind.OTHER, True),
        ("Any", Kind.OTHER, False),
        ("int | str", Kind.OTHER, False),
        ("int | str | None", Kind.OTHER, True),
        ("None", Kind.OTHER, True),
        ("int |", Kind.OTHER, False),
        ("'not valid python ?'", Kind.OTHER, False),
    ],
)
def test_parse_annotation(text, kind, optional):
    parsed = parse_annotation(text)

    assert (parsed.kind, parsed.optional) == (kind, optional)
    assert parsed.text == text


@pytest.mark.parametrize(
    ("text", "element"),
    [
        ("list[int]", I),
        ("List[str]", S),
        ("tuple[float, ...]", R),
        ("Sequence[bool]", B),
        ("list[Foo]", Kind.OTHER),
        ("list[list[int]]", Kind.OTHER),
        ("list", Kind.OTHER),
    ],
)
def test_element_kinds(text, element):
    assert parse_annotation(text).element is element


@pytest.mark.parametrize(
    ("ptype", "expected"),
    [
        (ParamType(I), 0),
        (ParamType(R), 0.0),
        (ParamType(B), False),
        (ParamType(S), ""),
        (ParamType(Kind.LIST), []),
        (ParamType(Kind.TUPLE), ()),
        (ParamType(Kind.BYTES), b""),
        (ParamType(Kind.DICT), {}),
        (ParamType(I, optional=True), None),
        (ParamType(Kind.OTHER, optional=True), None),
    ],
)
def test_neutral_values(ptype, expected):
    assert ptype.has_neutral
    assert ptype.neutral() == expected
    assert type(ptype.neutral()) is type(expected)


def test_unknown_types_have_no_neutral_value():
    assert not ParamType(Kind.OTHER).has_neutral


@pytest.mark.parametrize(
    ("ptype", "expected"),
    [
        (ParamType(Kind.LIST, element=I), [0, 0]),
        (ParamType(Kind.LIST, element=S), ["", ""]),
        (ParamType(Kind.LIST, element=R), [0.0, 0.0]),
        (ParamType(Kind.LIST, element=B), [False, False]),
        (ParamType(Kind.LIST, element=Kind.OTHER), [None, None]),
        (ParamType(Kind.TUPLE, element=I), (0, 0)),
        (ParamType(Kind.BYTES), b"\x00\x00"),
    ],
)
def test_sized_values_hold_neutral_elements(ptype, expected):
    assert ptype.sized(2) == expected


def test_sized_lists_do_not_share_state():
    first, second = ParamType(Kind.LIST).sized(1), ParamType(Kind.LIST).sized(1)

    assert first is not second


@pytest.mark.parametrize(
    ("ptype", "classes"),
    [
        (ParamType(I), {"int", "object"}),
        (ParamType(B), {"bool", "int", "object"}),
        (ParamType(Kind.LIST), {"list", "object"}),
        (ParamType(Kind.OTHER), None),
    ],
)
def test_runtime_classes(ptype, classes):
    assert ptype.runtime_classes == (frozenset(classes) if classes else None)


def infer(params: dict[str, str | None], *conditions: str) -> dict[str, Kind]:
    parameters = [Parameter(n, annotation=a) for n, a in params.items()]
    trees = [ast.parse(c, mode="eval").body for c in conditions]
    return {name: t.kind for name, t in infer_types(parameters, trees).items()}


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        (["x > 5"], I),
        (["x == -5"], I),
        (["x + 1 == 5"], I),
        (["x * 2 > y + 1"], I),
        (["x // 2 > 1"], I),
        (["x % 3 == 0"], I),
        (["5 < x"], I),
        (["abs(x) > 3"], I),
        (["max(x, 3) > 4"], I),
        (["-x > 3"], I),
        (["x == len('abc')"], I),
        (["x in (1, 2)"], I),
        (["x > 1.5"], R),
        (["x / 2 > 1"], I),
        (["x > 1 and x < 2.5"], R),
        (["x == 'a'"], S),
        (["x != 'a'"], S),
        (["len(x) > 2"], S),
        (["x.startswith('a')"], S),
        (["x.endswith('a')"], S),
        (["x.lower() == 'a'"], S),
        (["'a' in x"], S),
        (["x in 'abc'"], S),
        (["x in ('a', 'b')"], S),
        (["x in ['a']"], S),
        (["x == True"], B),
        (["x == True or x == 1"], I),
        (["x"], Kind.OTHER),
        (["x is None"], Kind.OTHER),
        (["x == 'a' or x > 3"], Kind.OTHER),
        (["x in (1, 'a')"], Kind.OTHER),
        (["x > f(1)"], Kind.OTHER),
        (["x + y"], Kind.OTHER),
    ],
)
def test_unannotated_parameters_are_typed_from_usage(conditions, expected):
    assert infer({"x": None, "y": None}, *conditions)["x"] is expected


def test_hints_flow_between_linked_names():
    kinds = infer({"a": None, "b": None, "c": None}, "a < b", "b == 3", "c == a")

    assert kinds == {"a": I, "b": I, "c": I}


def test_hints_flow_from_annotated_parameters():
    kinds = infer({"a": "str", "b": None}, "a == b")

    assert kinds == {"a": S, "b": S}


def test_annotations_win_over_usage():
    assert infer({"x": "str"}, "x > 5")["x"] is S
    assert infer({"x": "Foo"}, "x > 5")["x"] is Kind.OTHER
