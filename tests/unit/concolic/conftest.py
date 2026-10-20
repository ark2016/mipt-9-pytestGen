"""Fixtures for the concolic tests: build functions/targets and check solutions in real Python."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeAlias

import pytest

from pytestgen.concolic import Z3HintSolver
from pytestgen.domain.models import (
    BranchKind,
    BranchTarget,
    Clause,
    FunctionInfo,
    Parameter,
    PathCondition,
    Solution,
    SolveStatus,
)

Params: TypeAlias = Mapping[str, str | None] | Sequence[Parameter]
Clauses: TypeAlias = Sequence[tuple[str, bool]]


def make_function(params: Params, **kwargs: object) -> FunctionInfo:
    parameters = (
        tuple(Parameter(name, annotation=ann) for name, ann in params.items())
        if isinstance(params, Mapping)
        else tuple(params)
    )
    return FunctionInfo(
        qualname=str(kwargs.pop("qualname", "f")),
        name="f",
        lineno=1,
        end_lineno=3,
        source="def f(): ...",
        parameters=parameters,
        **kwargs,  # type: ignore[arg-type]
    )


def make_target(clauses: Clauses) -> BranchTarget:
    return BranchTarget(
        function="f",
        line=2,
        kind=BranchKind.IF,
        condition_source="",
        outcome=True,
        arc=None,
        path=PathCondition(tuple(Clause(expr, expected) for expr, expected in clauses)),
    )


def satisfies(function: FunctionInfo, clauses: Clauses, inputs: Mapping[str, object]) -> bool:
    """Evaluate every clause in plain Python with the solver's inputs (test-only eval)."""
    namespace: dict[str, object] = {}
    for param in function.call_parameters:
        if param.name in inputs:
            namespace[param.name] = inputs[param.name]
        elif param.default is not None:
            namespace[param.name] = eval(param.default)  # noqa: S307
    return all(
        bool(eval(expr, {}, namespace)) is expected  # noqa: S307
        for expr, expected in clauses
    )


class Harness:
    """Solve a path condition and verify the answer against real Python semantics."""

    function = staticmethod(make_function)
    target = staticmethod(make_target)
    satisfies = staticmethod(satisfies)

    def __init__(self, solver: Z3HintSolver) -> None:
        self.solver = solver

    def solve(self, params: Params, clauses: Clauses, **function_kwargs: object) -> Solution:
        function = make_function(params, **function_kwargs)
        return self.solver.solve(function, make_target(clauses))

    def check(self, params: Params, clauses: Clauses, **function_kwargs: object) -> Solution:
        """Solve, require SAT, and require that the inputs satisfy the clauses in Python."""
        function = make_function(params, **function_kwargs)
        solution = self.solver.solve(function, make_target(clauses))
        assert solution.status is SolveStatus.SAT, solution.reason
        assert satisfies(function, clauses, solution.inputs), solution.inputs
        return solution


@pytest.fixture(scope="session")
def solver() -> Z3HintSolver:
    return Z3HintSolver()


@pytest.fixture
def harness(solver: Z3HintSolver) -> Harness:
    return Harness(solver)


@pytest.fixture
def harness_for():
    """Factory for harnesses around differently configured solvers."""

    def build(**solver_kwargs: int) -> Harness:
        return Harness(Z3HintSolver(**solver_kwargs))

    return build
