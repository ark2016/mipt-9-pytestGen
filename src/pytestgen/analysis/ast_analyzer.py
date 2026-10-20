"""Static module analysis with :mod:`ast`; the analysed code is never imported or executed."""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Iterator, Sequence
from pathlib import Path

from pytestgen.analysis.astutil import FunctionNode, first_line
from pytestgen.analysis.branches import BranchExtractor
from pytestgen.analysis.facts import collect_facts
from pytestgen.analysis.module_name import module_name_for
from pytestgen.domain.models import (
    ClassInfo,
    FunctionInfo,
    ModuleInfo,
    Parameter,
    ParamKind,
)
from pytestgen.errors import AnalysisError
from pytestgen.log import get_logger

_log = get_logger(__name__)

_PROPERTY_DECORATORS = frozenset({"property", "cached_property", "abstractproperty"})
_PROPERTY_ACCESSORS = frozenset({"setter", "getter", "deleter"})


class AstModuleAnalyzer:
    """Implements :class:`pytestgen.ports.ModuleAnalyzer` on top of the standard ``ast``.

    Collected functions are the module-level ``def``/``async def`` and the methods of
    module-level classes (qualified ``Class.method``). Nested functions, nested classes
    and definitions placed inside module-level ``if``/``try`` blocks are skipped.
    """

    def __init__(self, branches: BranchExtractor | None = None) -> None:
        self._branches = branches or BranchExtractor()

    def analyze(self, path: Path, project_root: Path) -> ModuleInfo:
        resolved = path.resolve()
        module_name = module_name_for(resolved, project_root)
        source = _read_source(resolved)
        tree = _parse(source, resolved)
        lines = _SourceLines(source)

        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append(self._function(node, node.name, None, lines))
            elif isinstance(node, ast.ClassDef):
                methods = [
                    self._function(item, f"{node.name}.{item.name}", node.name, lines)
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                ]
                functions.extend(methods)
                classes.append(_class_info(node, methods))

        _log.debug("module_analyzed", module=module_name, functions=len(functions))
        return ModuleInfo(
            path=resolved,
            module_name=module_name,
            source=source,
            functions=tuple(functions),
            classes=tuple(classes),
            imports=_imports(tree.body),
            docstring=ast.get_docstring(tree),
        )

    def _function(
        self, node: FunctionNode, qualname: str, class_name: str | None, lines: _SourceLines
    ) -> FunctionInfo:
        decorators = tuple(ast.unparse(d) for d in node.decorator_list)
        paths = _decorator_paths(node.decorator_list)
        names = {path.rsplit(".", 1)[-1] for path in paths}
        facts = collect_facts(node)
        return FunctionInfo(
            qualname=qualname,
            name=node.name,
            lineno=node.lineno,
            end_lineno=node.end_lineno or node.lineno,
            source=lines.segment(node),
            parameters=_parameters(node.args),
            return_annotation=ast.unparse(node.returns) if node.returns else None,
            docstring=ast.get_docstring(node),
            class_name=class_name,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_static="staticmethod" in names,
            is_classmethod="classmethod" in names,
            is_property=_is_property(paths),
            decorators=decorators,
            raises=facts.raises,
            calls=facts.calls,
            branches=self._branches.extract(node, qualname),
        )


def _read_source(path: Path) -> str:
    try:
        with tokenize.open(path) as handle:  # honours BOM and coding cookie
            return handle.read()
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise AnalysisError(f"cannot read {path}: {exc}") from exc


def _parse(source: str, path: Path) -> ast.Module:
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise AnalysisError(f"{path}:{exc.lineno}: syntax error: {exc.msg}") from exc
    except ValueError as exc:  # e.g. null bytes in the source
        raise AnalysisError(f"cannot parse {path}: {exc}") from exc


class _SourceLines:
    """Source split into lines, able to cut out a definition with its indentation removed."""

    def __init__(self, source: str) -> None:
        self._lines = source.split("\n")
        self._inside_string = _string_continuation_lines(source)

    def segment(self, node: FunctionNode) -> str:
        """Text from the first decorator to the last line of ``node``.

        The indentation of the ``def`` line is removed from every line, except lines that
        continue a multi-line string literal: those are part of the literal's value.
        """
        start = first_line(node)
        end = node.end_lineno or node.lineno
        first = self._lines[start - 1]
        indent = first[: len(first) - len(first.lstrip())]
        out = []
        for number in range(start, end + 1):
            line = self._lines[number - 1]
            if number in self._inside_string:
                out.append(line)
            elif line.startswith(indent):
                out.append(line[len(indent) :])
            else:
                out.append(line.lstrip())
        return "\n".join(out)


def _string_continuation_lines(source: str) -> frozenset[int]:
    """Numbers of lines that continue a multi-line string / f-string token."""
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    lines: set[int] = set()
    depth = 0
    opened = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == fstring_start:
                if depth == 0:
                    opened = token.start[0]
                depth += 1
            elif token.type == fstring_end:
                depth -= 1
                if depth == 0:
                    lines.update(range(opened + 1, token.end[0] + 1))
            elif token.type == tokenize.STRING and depth == 0:
                lines.update(range(token.start[0] + 1, token.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):  # the source parsed, so this is unexpected
        return frozenset()
    return frozenset(lines)


def _parameters(args: ast.arguments) -> tuple[Parameter, ...]:
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults.extend(args.defaults)
    result = [
        _parameter(arg, ParamKind.POSITIONAL_ONLY, default)
        for arg, default in zip(args.posonlyargs, defaults[: len(args.posonlyargs)], strict=True)
    ]
    result += [
        _parameter(arg, ParamKind.POSITIONAL_OR_KEYWORD, default)
        for arg, default in zip(args.args, defaults[len(args.posonlyargs) :], strict=True)
    ]
    if args.vararg:
        result.append(_parameter(args.vararg, ParamKind.VAR_POSITIONAL, None))
    result += [
        _parameter(arg, ParamKind.KEYWORD_ONLY, default)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    ]
    if args.kwarg:
        result.append(_parameter(args.kwarg, ParamKind.VAR_KEYWORD, None))
    return tuple(result)


def _parameter(arg: ast.arg, kind: ParamKind, default: ast.expr | None) -> Parameter:
    return Parameter(
        name=arg.arg,
        kind=kind,
        annotation=ast.unparse(arg.annotation) if arg.annotation else None,
        default=ast.unparse(default) if default is not None else None,
    )


def _decorator_paths(decorators: Sequence[ast.expr]) -> list[str]:
    """Dotted decorator names with call arguments dropped (``@a.b(1)`` -> ``a.b``)."""
    return [ast.unparse(d.func if isinstance(d, ast.Call) else d) for d in decorators]


def _is_property(decorator_paths: Sequence[str]) -> bool:
    for path in decorator_paths:
        name = path.rsplit(".", 1)[-1]
        if name in _PROPERTY_DECORATORS or ("." in path and name in _PROPERTY_ACCESSORS):
            return True
    return False


def _class_info(node: ast.ClassDef, methods: Sequence[FunctionInfo]) -> ClassInfo:
    init = next((m for m in methods if m.name == "__init__"), None)
    return ClassInfo(
        name=node.name,
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
        bases=tuple(ast.unparse(b) for b in node.bases),
        docstring=ast.get_docstring(node),
        init_parameters=init.parameters[1:] if init else (),
        methods=tuple(m.qualname for m in methods),
    )


def _imports(body: Sequence[ast.stmt]) -> tuple[str, ...]:
    """Modules imported at module level, also inside top-level ``if``/``try``/``with``."""
    names: dict[str, None] = {}
    for stmt in _module_level_statements(body):
        if isinstance(stmt, ast.Import):
            names.update(dict.fromkeys(alias.name for alias in stmt.names))
        elif isinstance(stmt, ast.ImportFrom):
            names.setdefault("." * stmt.level + (stmt.module or ""))
    return tuple(names)


def _module_level_statements(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    for stmt in body:
        yield stmt
        blocks: list[list[ast.stmt]]
        if isinstance(stmt, ast.If):
            blocks = [stmt.body, stmt.orelse]
        elif isinstance(stmt, ast.With):
            blocks = [stmt.body]
        elif isinstance(stmt, ast.Try | ast.TryStar):
            blocks = [stmt.body, *(h.body for h in stmt.handlers), stmt.orelse, stmt.finalbody]
        else:
            continue
        for block in blocks:
            yield from _module_level_statements(block)
