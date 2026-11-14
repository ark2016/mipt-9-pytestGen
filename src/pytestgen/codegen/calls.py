"""How a generated test calls a function under test."""

from __future__ import annotations

import ast
from collections.abc import Mapping

from pytestgen.codegen.literals import literal_source
from pytestgen.domain.models import FunctionInfo, ModuleInfo, Parameter, ParamKind

_POSITIONAL = {ParamKind.POSITIONAL_ONLY, ParamKind.POSITIONAL_OR_KEYWORD}
_MUST_BE_POSITIONAL = {ParamKind.POSITIONAL_ONLY, ParamKind.VAR_POSITIONAL}


class UnsupportedCall(ValueError):  # noqa: N818 - reads as a condition, not an error class
    """A call cannot be spelled out in source (e.g. a skipped parameter has an opaque default)."""


def default_source(param: Parameter) -> str | None:
    """Ruff-formatted source of a literal default of ``param``; ``None`` if absent or opaque."""
    if param.default is None:
        return None
    try:
        value = ast.literal_eval(param.default)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    return literal_source(value, double_quotes=True)


class CallBuilder:
    """Builds call expressions for the functions of one module."""

    def __init__(self, module: ModuleInfo) -> None:
        self._module = module

    def target(self, function: FunctionInfo) -> str | None:
        """Callable expression (``fn``, ``Cls.m`` or ``Cls().m``), ``None`` if unusable."""
        if function.is_property or "<locals>" in function.qualname:
            return None
        if function.class_name is None or function.is_static or function.is_classmethod:
            return function.qualname
        if self._constructible_without_arguments(function.class_name):
            return f"{function.class_name}().{function.name}"
        return None

    def imported_name(self, function: FunctionInfo) -> str:
        """Top-level name the test module must import from the module under test."""
        return function.qualname.split(".")[0]

    def call(self, function: FunctionInfo, target: str, variables: Mapping[str, str]) -> str:
        """Call expression passing each parameter in ``variables`` from its test variable.

        Async functions are wrapped into ``asyncio.run(...)``.
        """
        expression = f"{target}({', '.join(self._arguments(function, variables))})"
        return f"asyncio.run({expression})" if function.is_async else expression

    def exploded_call(
        self, function: FunctionInfo, target: str, variables: Mapping[str, str]
    ) -> list[str]:
        """Lines of the call with one argument per line and trailing commas (ruff-stable)."""
        arguments = self._arguments(function, variables)
        if not arguments:
            return [self.call(function, target, variables)]
        lines = [f"{target}(", *(f"    {argument}," for argument in arguments), ")"]
        if function.is_async:
            return ["asyncio.run(", *(f"    {line}" for line in lines), ")"]
        return lines

    def _arguments(self, function: FunctionInfo, variables: Mapping[str, str]) -> list[str]:
        parameters = function.call_parameters
        # Everything before the last positional-only / ``*args`` argument is passed positionally,
        # so skipped parameters in between are filled with their defaults.
        last_positional = max(
            (i for i, p in enumerate(parameters) if p.name in variables and _must_be_positional(p)),
            default=-1,
        )
        arguments: list[str] = []
        gap = False  # a positional parameter was skipped: later ones must go by keyword
        for index, param in enumerate(parameters):
            if param.name in variables:
                arguments.append(_argument(param, variables[param.name], gap=gap))
            elif index < last_positional and param.kind in _POSITIONAL:
                arguments.append(_default_argument(function, param))
            else:
                gap = gap or param.kind in _POSITIONAL
        return arguments

    def _constructible_without_arguments(self, class_name: str) -> bool:
        for info in self._module.classes:
            if info.name == class_name:
                params = [p for p in info.init_parameters if p.name != "self"]
                return all(p.default is not None or _is_variadic(p) for p in params)
        return False


def _is_variadic(param: Parameter) -> bool:
    return param.kind in {ParamKind.VAR_POSITIONAL, ParamKind.VAR_KEYWORD}


def _must_be_positional(param: Parameter) -> bool:
    return param.kind in _MUST_BE_POSITIONAL


def _default_argument(function: FunctionInfo, param: Parameter) -> str:
    if param.default is None:
        msg = f"{function.qualname}: cannot pass a later argument after skipping {param.name!r}"
        raise ValueError(msg)
    source = default_source(param)
    if source is None:
        raise UnsupportedCall(f"{function.qualname}: default of {param.name!r} is not a literal")
    return source


def _argument(param: Parameter, variable: str, *, gap: bool) -> str:
    if param.kind is ParamKind.VAR_KEYWORD:
        return f"**{variable}"
    if param.kind is ParamKind.KEYWORD_ONLY:
        return f"{param.name}={variable}"
    if param.kind is ParamKind.VAR_POSITIONAL:
        return f"*{variable}"
    return f"{param.name}={variable}" if gap else variable
