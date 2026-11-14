"""``map_test_nodes``: pytest node names of the rendered module -> the items they came from."""

from __future__ import annotations

from codegen_factories import function, module, param, raised, returned

from pytestgen.codegen import map_test_nodes, render_test_module
from pytestgen.domain.models import CaseOrigin, FreeFormTest, OracleVerdict, TestSuite

ADD = function("add", param("a"), param("b"))
DIV = function("div", param("a"), param("b"))
ANSWER = function("answer")


def suite(*cases, free: tuple[FreeFormTest, ...] = (), functions=(ADD, DIV, ANSWER)) -> TestSuite:
    return TestSuite(module(tuple(functions)), tuple(cases), free)


def test_parametrize_rows_are_keyed_by_origin_and_row_number() -> None:
    first = returned("add", {"a": 1, "b": 2}, 3)
    second = returned("add", {"a": 5, "b": 5}, 10, CaseOrigin.LLM_SLOT)
    error = raised("div", {"a": 1, "b": 0}, "ZeroDivisionError")
    nodes = map_test_nodes(suite(first, second, error))
    assert nodes == {
        "test_add_returns[z3-1]": first,
        "test_add_returns[llm_slot-2]": second,
        "test_div_raises[z3-1]": error,
    }


def test_duplicate_rows_are_numbered_after_deduplication() -> None:
    first = returned("add", {"a": 1, "b": 2}, 3)
    duplicate = returned("add", {"a": 1, "b": 2}, 3, CaseOrigin.LLM_SLOT)
    third = returned("add", {"a": 2, "b": 2}, 4)
    nodes = map_test_nodes(suite(first, duplicate, third))
    assert nodes == {"test_add_returns[z3-1]": first, "test_add_returns[z3-2]": third}


def test_function_without_parameters_maps_by_bare_name() -> None:
    case = returned("answer", {}, 42)
    assert map_test_nodes(suite(case)) == {"test_answer_returns": case}


def test_suspicious_cases_are_not_rendered_so_not_mapped() -> None:
    bad = returned("add", {"a": 1, "b": 2}, 4, verdict=OracleVerdict.SUSPICIOUS)
    assert map_test_nodes(suite(bad)) == {}


def test_free_tests_use_the_name_after_collision_renaming() -> None:
    clash = FreeFormTest("test_add_returns", "add", "def test_add_returns():\n    assert 1\n")
    other = FreeFormTest("test_other", "add", "def test_other():\n    assert 1\n")
    again = FreeFormTest("test_other", "add", "def test_other():\n    assert 2\n")
    case = returned("add", {"a": 1, "b": 2}, 3)
    nodes = map_test_nodes(suite(case, free=(clash, other, again)))
    assert nodes["test_add_returns[z3-1]"] is case
    assert nodes["test_add_returns_2"] is clash
    assert nodes["test_other"] is other
    assert nodes["test_other_2"] is again


def test_test_classes_are_mapped_by_class_name() -> None:
    cls = FreeFormTest(
        "TestAdd", "add", "class TestAdd:\n    def test_a(self):\n        assert 1\n"
    )
    assert map_test_nodes(suite(free=(cls,))) == {"TestAdd": cls}


def test_every_mapped_name_appears_in_the_rendered_text() -> None:
    cases = (
        returned("add", {"a": 1, "b": 2}, 3),
        raised("div", {"a": 1, "b": 0}, "ZeroDivisionError"),
    )
    s = suite(*cases)
    text = render_test_module(s)
    for node in map_test_nodes(s):
        assert node.split("[", 1)[0] in text
