# ruff: noqa: N815  (ast.NodeVisitor dispatches on visit_<NodeClass> names)
"""Facts collected from a function body: explicitly raised exceptions and called names."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from pytestgen.analysis.astutil import FunctionNode, dotted_name, exception_name

MAX_CALLS = 30


@dataclass(frozen=True, slots=True)
class BodyFacts:
    raises: tuple[str, ...]
    """Exception names from ``raise X`` / ``raise X(...)``, unique, in source order."""
    calls: tuple[str, ...]
    """Dotted names of called callables (``foo``, ``obj.method``), unique, capped."""


class _FactsVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.raises: dict[str, None] = {}
        self.calls: dict[str, None] = {}

    def _skip(self, node: ast.AST) -> None:
        """Nested scopes belong to their own function, not to this one."""

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = visit_Lambda = _skip

    def visit_Raise(self, node: ast.Raise) -> None:
        name = exception_name(node.exc) if node.exc is not None else None
        if name is not None:
            self.raises.setdefault(name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted_name(node.func)
        if name is not None:
            self.calls.setdefault(name)
        self.generic_visit(node)


def collect_facts(function: FunctionNode) -> BodyFacts:
    """Collect raises and calls of ``function`` without descending into nested scopes."""
    visitor = _FactsVisitor()
    for stmt in function.body:
        visitor.visit(stmt)
    return BodyFacts(
        raises=tuple(visitor.raises),
        calls=tuple(visitor.calls)[:MAX_CALLS],
    )
