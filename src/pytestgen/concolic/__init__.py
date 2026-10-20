"""Concolic-lite hint solver: path conditions -> z3 -> concrete inputs.

``Z3HintSolver`` implements ``pytestgen.ports.HintSolver``. ``ExprTranslator`` is the
reusable part that turns Python expressions into z3 terms.
"""

from __future__ import annotations

from pytestgen.concolic.errors import UnsupportedExpression
from pytestgen.concolic.solver import Z3HintSolver
from pytestgen.concolic.translator import ExprTranslator

__all__ = ["ExprTranslator", "UnsupportedExpression", "Z3HintSolver"]
