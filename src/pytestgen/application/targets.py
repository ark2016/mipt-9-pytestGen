"""Which functions of a module get tests, and how they are generated."""

from __future__ import annotations

from pytestgen.domain.models import FunctionInfo, ModuleInfo
from pytestgen.errors import PytestgenError


def select_functions(module: ModuleInfo, requested: tuple[str, ...]) -> tuple[FunctionInfo, ...]:
    """Functions to generate tests for.

    By default properties, dunder methods and private ``_helpers`` are skipped; a private
    function is included when named explicitly. A requested name is a qualified name or a
    class name (all its public methods).

    Raises:
        PytestgenError: an unknown name, a name that cannot be targeted (property or dunder
            method), or nothing testable left.
    """
    candidates = [fn for fn in module.functions if _is_default_target(fn)]
    if not requested:
        chosen = candidates
    else:
        names = {fn.qualname for fn in module.functions} | {
            fn.class_name for fn in module.functions if fn.class_name
        }
        available = ", ".join(fn.qualname for fn in candidates) or "none"
        unknown = [name for name in requested if name not in names]
        if unknown:
            raise PytestgenError(
                f"unknown function(s): {', '.join(unknown)}; available: {available}"
            )
        chosen = [fn for fn in module.functions if _is_requested(fn, requested)]
        untargetable = [name for name in requested if _is_untargetable(module, name)]
        if untargetable:
            raise PytestgenError(
                f"{', '.join(untargetable)} cannot be targeted (property or dunder method); "
                f"available: {available}"
            )
    if not chosen:
        raise PytestgenError(f"{module.module_name} has no testable functions")
    return tuple(chosen)


def is_hybrid_target(function: FunctionInfo) -> bool:
    """Module-level functions and static methods; everything else goes through the LLM."""
    return not function.is_method or function.is_static


def _is_default_target(fn: FunctionInfo) -> bool:
    return not (fn.is_property or _is_dunder(fn.name) or fn.name.startswith("_"))


def _is_requested(fn: FunctionInfo, requested: tuple[str, ...]) -> bool:
    if _is_untargetable_function(fn):
        return False
    if fn.qualname in requested:
        return True
    return fn.class_name in requested and _is_default_target(fn)


def _is_untargetable_function(fn: FunctionInfo) -> bool:
    return fn.is_property or _is_dunder(fn.name)


def _is_untargetable(module: ModuleInfo, name: str) -> bool:
    return any(fn.qualname == name and _is_untargetable_function(fn) for fn in module.functions)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")
