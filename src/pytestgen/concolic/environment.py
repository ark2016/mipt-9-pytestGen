"""Typed variable environment: parameters become z3 variables on first use."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import z3

from pytestgen.concolic.errors import UnsupportedExpression
from pytestgen.concolic.ptypes import Kind, ParamType

_SORTS = {
    Kind.INT: z3.Int,
    Kind.REAL: z3.Real,
    Kind.BOOL: z3.Bool,
    Kind.STR: z3.String,
}


@dataclass(frozen=True, slots=True)
class Variable:
    """The z3 side of one parameter."""

    name: str
    ptype: ParamType
    term: Any
    """The value for scalars; the (non-negative) length for list/tuple/bytes; ``None`` when
    only the ``is None`` flag of an unmodelled ``X | None`` type exists."""
    is_none: Any | None = None
    """Boolean ``<name>__is_none`` for ``X | None`` parameters."""


class VariableEnvironment:
    """Creates z3 variables lazily, so only parameters the condition uses are modelled."""

    def __init__(self, types: Mapping[str, ParamType]) -> None:
        self._types = dict(types)
        self._variables: dict[str, Variable] = {}
        self._constraints: list[Any] = []

    @property
    def touched(self) -> Mapping[str, Variable]:
        """Variables created so far, i.e. parameters mentioned by the condition."""
        return self._variables

    @property
    def constraints(self) -> list[Any]:
        """Facts about the variables themselves (lengths are non-negative)."""
        return list(self._constraints)

    def type_of(self, name: str) -> ParamType:
        try:
            return self._types[name]
        except KeyError:
            raise UnsupportedExpression(f"unknown name {name!r} (not a parameter)") from None

    def variable(self, name: str) -> Variable:
        """The variable of parameter ``name``; raises for types that cannot be modelled."""
        ptype = self.type_of(name)
        existing = self._variables.get(name)
        if existing is not None and existing.term is not None:
            return existing
        if ptype.is_scalar:
            term = _SORTS[ptype.kind](name)
        elif ptype.is_length_modelled:
            term = z3.Int(f"{name}__len")
            self._constraints.append(term >= 0)
        else:
            raise UnsupportedExpression(_unsupported_type_reason(name, ptype))
        is_none = z3.Bool(f"{name}__is_none") if ptype.optional else None
        variable = Variable(name, ptype, term, is_none)
        self._variables[name] = variable
        return variable

    def none_flag(self, name: str) -> Any | None:
        """``<name>__is_none`` for an optional parameter, ``None`` for a non-optional one.

        Works for any ``X | None`` annotation, also when ``X`` itself cannot be modelled:
        the value is then only ``None`` or unknown (see ``Variable.term``).
        """
        ptype = self.type_of(name)
        if not ptype.optional:
            return None
        if ptype.is_scalar or ptype.is_length_modelled:
            return self.variable(name).is_none
        existing = self._variables.get(name)
        if existing is None:
            existing = Variable(name, ptype, None, z3.Bool(f"{name}__is_none"))
            self._variables[name] = existing
        return existing.is_none


def _unsupported_type_reason(name: str, ptype: ParamType) -> str:
    if not ptype.text:
        return f"cannot infer the type of parameter {name}"
    return f"unsupported type {ptype.text} of parameter {name}"
