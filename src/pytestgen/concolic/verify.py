"""Final check of a model: the clauses must hold for the inputs under real Python semantics.

z3 reasons about exact reals and its own string ordering; Python rounds floats and compares
code points. Evaluating the (already translated, hence restricted to a small safe subset)
clause expressions on the concrete inputs catches every disagreement.
"""

from __future__ import annotations

import ast
import builtins
from collections.abc import Mapping, Sequence

from pytestgen.domain.models import Clause

_NAMES = (
    "len", "abs", "min", "max", "isinstance",
    "int", "float", "str", "bool", "list", "tuple", "dict", "set", "frozenset", "bytes", "object",
)  # fmt: skip
_BUILTINS = {name: getattr(builtins, name) for name in _NAMES}
_EVALUATION_ERRORS = (ArithmeticError, TypeError, ValueError, AttributeError, NameError)


def holds_in_python(
    clauses: Sequence[Clause], trees: Sequence[ast.expr], inputs: Mapping[str, object]
) -> bool:
    """Whether every clause evaluates to its expected truth value for ``inputs``."""
    for clause, tree in zip(clauses, trees, strict=True):
        code = compile(ast.Expression(tree), "<clause>", "eval")
        try:
            value = eval(code, {"__builtins__": _BUILTINS}, dict(inputs))  # noqa: S307
        except _EVALUATION_ERRORS:
            return False
        if bool(value) is not clause.expected:
            return False
    return True
