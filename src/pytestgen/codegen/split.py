"""Split an LLM-written test module into imports, helpers and individual tests."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from pytestgen.domain.models import FreeFormTest
from pytestgen.errors import ExtractionError

_FIXTURE = re.compile(r"\bfixture\b")
_AUTOUSE = re.compile(r"\bautouse\b")
_NEWLINE = re.compile(r"\r\n|\r|\n")
_LF = "\n"
_BLOCK_SEPARATOR = "\n\n\n"
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True, slots=True)
class ParsedTestModule:
    """Top-level pieces of a test module, each preserved as original source text."""

    imports: tuple[str, ...] = ()
    helpers: tuple[str, ...] = ()
    """Fixtures, constants, helper functions and non-``Test*`` classes."""
    tests: tuple[tuple[str, str], ...] = ()
    """``(name, source)`` of every ``test_*`` function and ``Test*`` class (with decorators)."""


def split_test_functions(source: str) -> ParsedTestModule:
    """Parse ``source`` and sort its top-level statements into imports, helpers and tests.

    Raises :class:`ExtractionError` when the source is not valid Python.
    """
    source = _NEWLINE.sub(_LF, source)  # CRLF must not leak into the rendered module
    tree = _parse(source)
    lines = source.split(_LF)
    imports: list[str] = []
    helpers: list[str] = []
    tests: list[tuple[str, str]] = []
    for index, node in enumerate(tree.body):
        if index == 0 and _is_docstring(node):
            continue
        text = _segment(source, lines, node)
        if isinstance(node, ast.Import | ast.ImportFrom):
            imports.append(text)
        elif isinstance(node, _DEFINITIONS) and _is_test_unit(node):
            tests.append((node.name, text))
        else:
            helpers.append(text)
    return ParsedTestModule(tuple(imports), tuple(helpers), tuple(tests))


def make_free_form_tests(function: str, source: str) -> tuple[FreeFormTest, ...]:
    """Wrap each test unit of ``source`` into a self-contained :class:`FreeFormTest`.

    Every result carries all imports plus the helpers (fixtures, constants) that
    the test references, directly or through other helpers.
    """
    parsed = split_test_functions(source)
    helper_nodes = [(text, ast.parse(text)) for text in parsed.helpers]
    result: list[FreeFormTest] = []
    for name, text in parsed.tests:
        needed = _needed_helpers(helper_nodes, ast.parse(text))
        body = _BLOCK_SEPARATOR.join([*needed, text])
        prefix = "\n".join(parsed.imports)
        full = f"{prefix}{_BLOCK_SEPARATOR}{body}" if prefix else body
        result.append(FreeFormTest(name=name, function=function, source=full + "\n"))
    return tuple(result)


def rename_definition(source: str, old: str, new: str) -> str:
    """Rename the top-level ``def``/``class`` called ``old`` in ``source`` to ``new``."""
    tree = _parse(source)
    lines = _NEWLINE.split(source)
    for node in tree.body:
        if isinstance(node, _DEFINITIONS) and node.name == old:
            row = node.lineno - 1
            lines[row] = re.sub(rf"\b(def|class)(\s+){re.escape(old)}\b", rf"\1\2{new}", lines[row])
            break
    return "\n".join(lines)


# --------------------------------------------------------------------------- parsing helpers


def _parse(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise ExtractionError(f"test code is not valid Python: {exc}") from exc


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_test_unit(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    if isinstance(node, ast.ClassDef):
        return node.name.startswith("Test")
    return node.name.startswith("test_") and not _is_fixture(node)


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_FIXTURE.search(ast.unparse(dec)) for dec in node.decorator_list)


def _segment(source: str, lines: list[str], node: ast.stmt) -> str:
    """Original text of a top-level statement, including its decorators."""
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        first = min(dec.lineno for dec in decorators)
        return "\n".join(lines[first - 1 : node.end_lineno]).rstrip()
    return (ast.get_source_segment(source, node) or ast.unparse(node)).rstrip()


# --------------------------------------------------------------------------- helper selection


def _needed_helpers(helpers: list[tuple[str, ast.Module]], test: ast.Module) -> list[str]:
    """Helpers reachable from ``test`` by name, plus autouse fixtures, in source order."""
    used = _used_names(test)
    chosen: set[int] = set()
    changed = True
    while changed:
        changed = False
        for index, (text, module) in enumerate(helpers):
            if index in chosen:
                continue
            if _is_ambient(text, module) or _defined_names(module) & used:
                chosen.add(index)
                used |= _used_names(module)
                changed = True
    return [helpers[i][0] for i in sorted(chosen)]


def _is_ambient(text: str, module: ast.Module) -> bool:
    """Helpers that act without being referenced: autouse fixtures, ``pytestmark``, bare code."""
    names = _defined_names(module)
    return not names or "pytestmark" in names or bool(_AUTOUSE.search(text))


def _defined_names(module: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _used_names(module: ast.Module) -> set[str]:
    """Identifiers, argument names and string constants (``usefixtures("x")``) in ``module``."""
    names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names
