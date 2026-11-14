"""The ``analyze`` use case: what the static analysis and the solver make of a module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pytestgen.application.targets import select_functions
from pytestgen.domain.models import BranchTarget, FunctionInfo, ModuleInfo, Solution
from pytestgen.ports import HintSolver, ModuleAnalyzer


@dataclass(frozen=True, slots=True)
class AnalyzeRequest:
    target: Path
    project_root: Path
    functions: tuple[str, ...] = ()
    """Qualified names (or a class name); empty means every default target."""
    solve: bool = True
    """Ask the solver for inputs of every branch."""


@dataclass(frozen=True, slots=True)
class BranchAnalysis:
    """A branch target with the solver's verdict (``None`` when solving was skipped)."""

    target: BranchTarget
    solution: Solution | None


@dataclass(frozen=True, slots=True)
class FunctionAnalysis:
    function: FunctionInfo
    branches: tuple[BranchAnalysis, ...]


@dataclass(frozen=True, slots=True)
class ModuleAnalysis:
    module: ModuleInfo
    functions: tuple[FunctionAnalysis, ...]
    solved: bool


class AnalyzeModule:
    """Static analysis of a module plus, on request, a solver verdict for every branch."""

    def __init__(self, analyzer: ModuleAnalyzer, solver: HintSolver) -> None:
        self._analyzer = analyzer
        self._solver = solver

    def execute(self, request: AnalyzeRequest) -> ModuleAnalysis:
        """Raises ``PytestgenError`` for an unreadable module or an unusable function filter."""
        module = self._analyzer.analyze(request.target, request.project_root)
        functions = select_functions(module, request.functions)
        return ModuleAnalysis(
            module, tuple(self._analyze(fn, solve=request.solve) for fn in functions), request.solve
        )

    def _analyze(self, function: FunctionInfo, *, solve: bool) -> FunctionAnalysis:
        return FunctionAnalysis(
            function,
            tuple(
                BranchAnalysis(target, self._solver.solve(function, target) if solve else None)
                for target in function.branches
            ),
        )
