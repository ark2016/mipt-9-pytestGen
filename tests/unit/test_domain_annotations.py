from __future__ import annotations

import ast

import pytest

from pytestgen.concolic.ptypes import Kind, parse_annotation
from pytestgen.domain.annotations import (
    base_name,
    coerce_json_value,
    fit_scalar,
    is_none,
    union_members,
)


def node(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


def dump(members: list[ast.expr]) -> list[str]:
    return [ast.unparse(m) for m in members]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("int", ["int"]),
        ("int | None", ["int", "None"]),
        ("Optional[int]", ["int", "None"]),
        ("typing.Optional[int]", ["int", "None"]),
        ("Union[int, str]", ["int", "str"]),
        ("Union[int, Optional[str]]", ["int", "str", "None"]),
        ("int | str | None", ["int", "str", "None"]),
        ("list[int | None]", ["list[int | None]"]),
    ],
)
def test_union_members_flatten_every_spelling(text: str, expected: list[str]) -> None:
    assert dump(union_members(node(text))) == expected


def test_member_names_are_case_sensitive_like_python() -> None:
    """The input validator and the solver used to disagree on `optional[int]`."""
    assert dump(union_members(node("optional[int]"))) == ["optional[int]"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("int", "int"), ("list[int]", "list"), ("typing.Dict[str, int]", "Dict"), ("1", None)],
)
def test_base_name(text: str, expected: str | None) -> None:
    assert base_name(node(text)) == expected


def test_none_detection() -> None:
    assert is_none(node("None")) and not is_none(node("int"))


@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
    [
        ("tuple[int, int]", [1, 2], (1, 2)),
        ("tuple[int, ...]", [1, 2, 3], (1, 2, 3)),
        ("tuple", [1], (1,)),
        ("typing.Tuple[int, str]", [1, "a"], (1, "a")),
        ("tuple[int, int] | None", [1, 2], (1, 2)),
        ("list[tuple[int, int]]", [[1, 2], [3, 4]], [(1, 2), (3, 4)]),
        ("dict[str, tuple[int, int]]", {"a": [1, 2]}, {"a": (1, 2)}),
        ("Sequence[int]", [1], [1]),
        ("int", 5, 5),
        (None, [1], [1]),
        ("not valid python (", [1], [1]),
    ],
)
def test_json_lists_become_tuples_where_the_annotation_says_so(
    annotation: str | None, value: object, expected: object
) -> None:
    assert coerce_json_value(value, annotation) == expected


@pytest.mark.parametrize(
    ("annotation", "value", "fits", "result"),
    [
        ("int", 3, True, 3),
        ("int", 3.0, True, 3),
        ("int", 3.5, False, 3.5),
        ("float", 3, True, 3),
        ("int | float", 3.0, True, 3.0),
        ("str", 3, False, 3),
        ("bool", 1, False, 1),
        ("int", True, False, True),
        ("int | None", None, True, None),
        ("Optional[str]", None, True, None),
        ("list[int]", [1], True, [1]),
        (None, "anything", True, "anything"),
    ],
)
def test_fit_scalar_is_loose_but_catches_wrong_types(
    annotation: str | None, value: object, fits: bool, result: object
) -> None:
    assert fit_scalar(value, annotation) == (fits, result)


@pytest.mark.parametrize(
    ("text", "kind", "optional"),
    [
        ("int | None", Kind.INT, True),
        ("Optional[float]", Kind.REAL, True),
        ("typing.Union[str, None]", Kind.STR, True),
        ("Union[int, str]", Kind.OTHER, False),
        ("Annotated[int, 'x']", Kind.INT, False),
        ("Optional['int']", Kind.INT, True),
    ],
)
def test_the_solver_reads_annotations_through_the_same_helpers(
    text: str, kind: Kind, optional: bool
) -> None:
    parsed = parse_annotation(text)
    assert (parsed.kind, parsed.optional) == (kind, optional)
