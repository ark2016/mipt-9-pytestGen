"""Small stateless AST helpers shared by the analysis modules."""

from __future__ import annotations

import ast
from collections.abc import Sequence

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
"""Either flavour of function definition."""

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def first_line(stmt: ast.stmt) -> int:
    """Line coverage.py attributes to ``stmt``: the first decorator for decorated definitions."""
    if isinstance(stmt, _DEFINITIONS) and stmt.decorator_list:
        return stmt.decorator_list[0].lineno
    return stmt.lineno


def terminates(stmts: Sequence[ast.stmt]) -> bool:
    """Whether a block always ends in ``return``/``raise``/``continue``/``break``."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, _TERMINATORS):
        return True
    if isinstance(last, ast.If):
        return terminates(last.body) and terminates(last.orelse)
    return False


def exception_name(exc: ast.expr) -> str | None:
    """Type name in ``raise X`` / ``raise X(...)``; ``None`` when ``X`` is not a class name.

    ``raise err`` re-raises a variable: its name says nothing about the exception type,
    so only names whose last component starts with an upper-case letter are accepted.
    """
    target = exc.func if isinstance(exc, ast.Call) else exc
    if not isinstance(target, ast.Name | ast.Attribute):
        return None
    last = target.id if isinstance(target, ast.Name) else target.attr
    return ast.unparse(target) if last[:1].isupper() else None


def raised_name(stmts: Sequence[ast.stmt]) -> str | None:
    """Exception name when the block starts with ``raise X``; ``None`` otherwise."""
    if stmts and isinstance(stmts[0], ast.Raise) and stmts[0].exc is not None:
        return exception_name(stmts[0].exc)
    return None


_NO_BYTECODE = (ast.Global, ast.Nonlocal)


def is_executable(stmt: ast.stmt) -> bool:
    """Whether coverage.py can report ``stmt``'s line (declarations compile to nothing)."""
    if isinstance(stmt, _NO_BYTECODE):
        return False
    return not (isinstance(stmt, ast.AnnAssign) and stmt.value is None)


def landing_line(stmts: Sequence[ast.stmt], fallback: int | None) -> int | None:
    """Line control lands on when entering ``stmts``: the first executable statement.

    ``fallback`` is used when the block holds nothing executable (or is empty).
    """
    for stmt in stmts:
        if is_executable(stmt):
            return first_line(stmt)
    return fallback


def dotted_name(node: ast.expr) -> str | None:
    """``a.b.c`` text for a Name / attribute chain over Names, ``None`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None
