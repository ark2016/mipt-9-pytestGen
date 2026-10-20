from __future__ import annotations

import math
from pathlib import Path

import pytest
from orchestration_support import InProcessRecorder, ScriptedSolver, analyze, write_project

from pytestgen.application.boundary import boundary_vectors
from pytestgen.application.skeleton import SkeletonBuilder
from pytestgen.domain.models import FunctionInfo

SOURCE = '''\
def free_shipping(order_total: float) -> bool:
    """True for 100 or more."""
    return order_total > 100


def level(points: int, bonus: int = 0) -> str:
    if points < 0:
        raise ValueError("negative")
    if 0 < points <= 10 and bonus >= 5:
        return "low"
    return "high"


def light(weight: float) -> bool:
    return weight <= 1.0


def untyped(x):
    return x >= 7


def text(code: str, n: int) -> bool:
    return code > "m" or len(code) < 3 or n == 4


def local_only(x: int) -> bool:
    y = x * 2
    return y > 50


class Box:
    @staticmethod
    def fits(width: int) -> bool:
        return width <= 20

    def own(self, size: int) -> bool:
        return size > 3
'''


@pytest.fixture(scope="module")
def functions(tmp_path_factory: pytest.TempPathFactory) -> dict[str, FunctionInfo]:
    root = tmp_path_factory.mktemp("boundary")
    module = analyze(root, write_project(root, SOURCE, "shop.py"))
    return {fn.qualname: fn for fn in module.functions}


def values(vectors: list[dict[str, object]], name: str) -> list[object]:
    return [v[name] for v in vectors]


def test_exact_constant_comes_first_then_neighbours(functions: dict[str, FunctionInfo]) -> None:
    vectors = boundary_vectors(functions["free_shipping"], {"order_total": 0.0}, 10)
    first, *rest = values(vectors, "order_total")
    assert first == 100.0 and isinstance(first, float)
    assert rest == [99.0, 101.0]


def test_float_constants_get_the_adjacent_floats(functions: dict[str, FunctionInfo]) -> None:
    vectors = boundary_vectors(functions["light"], {"weight": 5.0}, 10)
    assert values(vectors, "weight") == [
        1.0,
        math.nextafter(1.0, -math.inf),
        math.nextafter(1.0, math.inf),
    ]


def test_integers_get_plus_and_minus_one(functions: dict[str, FunctionInfo]) -> None:
    vectors = boundary_vectors(functions["level"], {"points": 50, "bonus": 0}, 20)
    assert values(vectors, "points")[:2] == [0, 10]  # `points < 0` and `points <= 10`
    assert {-1, 1, 9, 11} <= {v for v in values(vectors, "points") if isinstance(v, int)}
    assert {5, 4, 6} <= set(values(vectors, "bonus"))


def test_base_inputs_not_in_the_comparison_are_kept(functions: dict[str, FunctionInfo]) -> None:
    vectors = boundary_vectors(functions["level"], {"points": 50, "bonus": 3}, 20)
    assert all(v["bonus"] == 3 for v in vectors if v["points"] != 50)
    assert {"points": 50, "bonus": 3} not in vectors


def test_the_limit_is_respected_and_exact_values_win(functions: dict[str, FunctionInfo]) -> None:
    vectors = boundary_vectors(functions["level"], {"points": 50, "bonus": 0}, 3)
    assert len(vectors) == 3
    assert [v["points"] for v in vectors[:2]] == [0, 10]
    assert vectors[2]["bonus"] == 5


def test_unannotated_parameters_use_the_constants_own_type(
    functions: dict[str, FunctionInfo],
) -> None:
    vectors = boundary_vectors(functions["untyped"], {"x": 0}, 5)
    assert values(vectors, "x") == [7, 6, 8]


def test_only_numeric_parameters_and_ordering_comparisons_count(
    functions: dict[str, FunctionInfo],
) -> None:
    vectors = boundary_vectors(functions["text"], {"code": "a", "n": 0}, 10)
    assert vectors == []  # str compared with str, len(...) and == are not boundaries


def test_comparisons_of_locals_are_ignored(functions: dict[str, FunctionInfo]) -> None:
    assert boundary_vectors(functions["local_only"], {"x": 0}, 10) == []


def test_static_methods_work_and_zero_limit_gives_nothing(
    functions: dict[str, FunctionInfo],
) -> None:
    assert values(boundary_vectors(functions["Box.fits"], {"width": 0}, 5), "width") == [20, 19, 21]
    assert boundary_vectors(functions["Box.fits"], {"width": 0}, 0) == []


def test_a_vector_equal_to_the_base_is_not_repeated(functions: dict[str, FunctionInfo]) -> None:
    vectors = boundary_vectors(functions["Box.fits"], {"width": 20}, 5)
    assert values(vectors, "width") == [19, 21]


# --------------------------------------------------------------------------- in the builder


def build(
    tmp_path: Path, name: str, *, cap: int = 12, boundary: int = 6
) -> tuple[FunctionInfo, SkeletonBuilder]:
    module = analyze(tmp_path, write_project(tmp_path, SOURCE, "shop.py"))
    solver = ScriptedSolver(default={"order_total": 0.0, "points": 50, "bonus": 0, "width": 0})
    skeleton = SkeletonBuilder(
        solver, InProcessRecorder(), max_cases_per_function=cap, max_boundary_vectors=boundary
    )
    return module.function(name), skeleton


def test_branchless_function_gets_one_solved_vector_plus_boundaries(tmp_path: Path) -> None:
    function, skeleton = build(tmp_path, "free_shipping")
    outcome = skeleton.solve_targets(function)
    assert len(outcome.vectors) == 1
    assert values(outcome.boundary, "order_total")[0] == 100.0
    assert len(outcome.boundary) == 3


def test_boundaries_fill_only_the_room_the_case_limit_leaves(tmp_path: Path) -> None:
    function, skeleton = build(tmp_path, "level", cap=3)
    outcome = skeleton.solve_targets(function)
    assert len(outcome.vectors) + len(outcome.boundary) <= 3


def test_no_room_or_switched_off_means_no_boundaries(tmp_path: Path) -> None:
    function, skeleton = build(tmp_path, "level", cap=1)
    assert skeleton.solve_targets(function).boundary == []
    function, skeleton = build(tmp_path, "free_shipping", boundary=0)
    assert skeleton.solve_targets(function).boundary == []


def test_instance_methods_get_no_boundaries(tmp_path: Path) -> None:
    function, skeleton = build(tmp_path, "Box.own")
    assert skeleton.solve_targets(function).boundary == []
