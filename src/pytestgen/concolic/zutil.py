"""Small z3 helpers: exact string conversion and numeric sort coercions."""

from __future__ import annotations

import ctypes
from fractions import Fraction
from typing import Any

import z3

from pytestgen.concolic.errors import UnsupportedExpression


def string_val(value: str) -> Any:
    """A z3 string literal holding exactly ``value`` (no escape-sequence interpretation)."""
    ctx = z3.main_ctx()
    code_points = (ctypes.c_uint * len(value))(*map(ord, value))
    return z3.SeqRef(z3.Z3_mk_u32string(ctx.ref(), len(value), code_points), ctx)


def python_string(term: Any) -> str:
    """Decode a concrete z3 string (e.g. a model value) into the exact Python ``str``."""
    ref = term.ctx.ref()
    length = z3.Z3_get_string_length(ref, term.as_ast())
    code_points = (ctypes.c_uint * length)()
    z3.Z3_get_string_contents(ref, term.as_ast(), length, code_points)
    return "".join(map(chr, code_points))


def real_val(value: float) -> Any:
    """The exact rational value of a Python float."""
    try:
        exact = Fraction(value)
    except (OverflowError, ValueError) as exc:
        raise UnsupportedExpression(f"float constant {value!r} has no exact value") from exc
    return z3.RealVal(f"{exact.numerator}/{exact.denominator}")


def is_string(term: Any) -> bool:
    return bool(term.sort() == z3.StringSort())


def as_number(term: Any) -> Any:
    """Coerce a bool to 0/1; ints and reals pass through; anything else is an error."""
    if z3.is_bool(term):
        return z3.If(term, z3.IntVal(1), z3.IntVal(0))
    if z3.is_arith(term):
        return term
    raise UnsupportedExpression("a number was expected here")


def unify(left: Any, right: Any) -> tuple[Any, Any]:
    """Coerce two numbers to a common sort (int + real -> real)."""
    left, right = as_number(left), as_number(right)
    if z3.is_real(left) != z3.is_real(right):
        left = z3.ToReal(left) if z3.is_int(left) else left
        right = z3.ToReal(right) if z3.is_int(right) else right
    return left, right
