"""From a z3 model to concrete Python arguments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction
from typing import Any

import z3

from pytestgen.concolic.environment import Variable, VariableEnvironment
from pytestgen.concolic.errors import UnsupportedExpression
from pytestgen.concolic.ptypes import Kind, ParamType
from pytestgen.concolic.zutil import python_string
from pytestgen.domain.models import Parameter, ParamKind

_VARIADIC = (ParamKind.VAR_POSITIONAL, ParamKind.VAR_KEYWORD)


def call_parameters(parameters: Sequence[Parameter]) -> list[Parameter]:
    """Parameters that take a named argument (``*args``/``**kwargs`` do not)."""
    return [p for p in parameters if p.kind not in _VARIADIC]


class InputBuilder:
    """Builds the ``{parameter: value}`` mapping for a satisfying model.

    * parameters the condition mentions take their model value;
    * unmentioned parameters with a default are left out (the default applies);
    * the remaining ones get a neutral value (``0``, ``""``, ``[]``, ``None``...).
    """

    def __init__(
        self,
        parameters: Sequence[Parameter],
        types: dict[str, ParamType],
        env: VariableEnvironment,
    ) -> None:
        self._parameters = parameters
        self._types = types
        self._env = env

    def build(self, model: Any) -> dict[str, object]:
        inputs: dict[str, object] = {}
        for param in self._parameters:
            var = self._env.touched.get(param.name)
            if var is not None:
                inputs[param.name] = self._model_value(var, model)
            elif param.default is None:
                inputs[param.name] = self._neutral(param)
        return inputs

    def _neutral(self, param: Parameter) -> object:
        ptype = self._types[param.name]
        if not ptype.has_neutral:
            raise _cannot_synthesize(param.name, ptype)
        return ptype.neutral()

    def _model_value(self, var: Variable, model: Any) -> object:
        if var.is_none is not None and z3.is_true(model.eval(var.is_none, model_completion=True)):
            return None
        if var.term is None:
            # an unmodelled `X | None` that must not be None: only a neutral X will do
            base = replace(var.ptype, optional=False)
            if not base.has_neutral:
                raise _cannot_synthesize(var.name, var.ptype)
            return base.neutral()
        value = model.eval(var.term, model_completion=True)
        if var.ptype.is_length_modelled:
            return var.ptype.sized(int(value.as_long()))
        try:
            return _scalar(var.ptype.kind, value)
        except OverflowError:
            raise UnsupportedExpression(
                f"real solution for {var.name} does not fit a float"
            ) from None


def _scalar(kind: Kind, value: Any) -> object:
    if kind is Kind.INT:
        return int(value.as_long())
    if kind is Kind.BOOL:
        return bool(z3.is_true(value))
    if kind is Kind.STR:
        return python_string(value)
    return real_to_float(value)


def real_to_float(value: Any) -> float:
    """Nearest float of a z3 real value."""
    return float(real_fraction(value))


def real_fraction(value: Any) -> Fraction:
    try:
        return Fraction(value.as_fraction())
    except AttributeError:  # algebraic number: fall back to a decimal expansion
        return Fraction(str(value.as_decimal(30)).rstrip("?"))


def _cannot_synthesize(name: str, ptype: ParamType) -> UnsupportedExpression:
    return UnsupportedExpression(
        f"cannot synthesize value for parameter {name}: {ptype.text or 'unannotated'}"
    )
