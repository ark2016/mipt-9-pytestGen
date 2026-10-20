"""Z3-backed implementation of the ``HintSolver`` port."""

from __future__ import annotations

import z3

from pytestgen.concolic.environment import VariableEnvironment
from pytestgen.concolic.errors import UnsupportedExpression
from pytestgen.concolic.inference import infer_types
from pytestgen.concolic.inputs import InputBuilder, call_parameters
from pytestgen.concolic.search import ModelSearch
from pytestgen.concolic.translator import ExprTranslator, parse_clause
from pytestgen.concolic.verify import holds_in_python
from pytestgen.domain.models import BranchTarget, FunctionInfo, Solution, SolveStatus
from pytestgen.log import get_logger


class Z3HintSolver:
    """Finds parameter values that drive execution to a branch target.

    The path condition is translated to z3 (see ``ExprTranslator``), solved with
    preferences for small readable values, and the model is turned into Python values.
    ``solve`` never raises for conditions it cannot handle: it reports
    ``UNSUPPORTED``, ``UNSAT`` or ``UNKNOWN`` instead.
    """

    def __init__(self, timeout_ms: int = 2000, max_string_length: int = 32) -> None:
        if timeout_ms <= 0 or max_string_length < 0:
            raise ValueError("timeout_ms must be positive and max_string_length non-negative")
        self._search = ModelSearch(timeout_ms, max_string_length)

    def solve(self, function: FunctionInfo, target: BranchTarget) -> Solution:
        try:
            solution = self._solve(function, target)
        except UnsupportedExpression as exc:
            solution = Solution(target, SolveStatus.UNSUPPORTED, reason=str(exc))
        except z3.Z3Exception as exc:
            solution = Solution(target, SolveStatus.UNKNOWN, reason=f"z3 error: {exc}")
        get_logger(__name__).debug(
            "hint_solved",
            target=target.key,
            status=solution.status.value,
            reason=solution.reason,
        )
        return solution

    def _solve(self, function: FunctionInfo, target: BranchTarget) -> Solution:
        if function.is_method and not function.is_static:
            raise UnsupportedExpression("instance methods are handled by the LLM")
        clauses = target.path.clauses
        trees = [parse_clause(clause) for clause in clauses]
        parameters = call_parameters(function.call_parameters)
        types = infer_types(parameters, trees)
        env = VariableEnvironment(types)
        translator = ExprTranslator(env)
        formulas = [translator.expected(t, c.expected) for t, c in zip(trees, clauses, strict=True)]
        constraints = [
            *formulas,
            *translator.side_conditions,
            *env.constraints,
        ]
        result = self._search.find(constraints, env.touched.values())
        if result.status is SolveStatus.UNSAT:
            return Solution(target, SolveStatus.UNSAT, reason="path condition is unsatisfiable")
        if result.status is not SolveStatus.SAT:
            return Solution(target, SolveStatus.UNKNOWN, reason=result.reason)
        inputs = InputBuilder(parameters, types, env).build(result.model)
        if not holds_in_python(clauses, trees, inputs):
            return Solution(
                target,
                SolveStatus.UNKNOWN,
                reason="the model does not hold under Python semantics (float rounding?)",
            )
        return Solution(target, SolveStatus.SAT, inputs=inputs)
