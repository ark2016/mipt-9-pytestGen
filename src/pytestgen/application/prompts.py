"""All prompt text of the LLM collaborators, in one place.

The target is a small local model, so every prompt is short, explicit and asks for a
tiny output format. Builders return ready ``ChatMessage`` lists; the response schemas
are constants. Nothing here calls the model or validates its answer.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Collection, Mapping, Sequence
from typing import Final

from pytestgen.domain.llm import ChatMessage, ResponseSchema, Role
from pytestgen.domain.models import (
    BranchTarget,
    FunctionInfo,
    ModuleInfo,
    ObservationKind,
    Parameter,
    TestCaseSpec,
    TestOutcome,
)

MAX_SOURCE_LINES: Final = 120
MAX_FAILURE_CHARS: Final = 2500
MAX_TRACEBACK_LINES: Final = 40
_MAX_REPR_CHARS: Final = 120
_MAX_CONSTANTS: Final = 5
_MAX_CONSTANT_CHARS: Final = 200
_NOT_COVERED_MARK: Final = "  # <- not covered"

SYSTEM_PROMPT: Final = (
    "You are a precise Python testing assistant. "
    "Follow the requested output format exactly and add nothing else."
)

ORACLE_REVIEW_SCHEMA: Final = ResponseSchema(
    name="oracle_review",
    schema={
        "type": "object",
        "properties": {
            "reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "verdict": {"type": "string", "enum": ["ok", "suspicious"]},
                        "explanation": {"type": "string"},
                    },
                    "required": ["index", "verdict", "explanation"],
                },
            }
        },
        "required": ["reviews"],
    },
)

PROPOSE_INPUTS_SCHEMA: Final = ResponseSchema(
    name="propose_inputs",
    schema={
        "type": "object",
        "properties": {
            "inputs": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            }
        },
        "required": ["inputs"],
    },
)


# --------------------------------------------------------------------------- source helpers


def truncate_source(source: str, max_lines: int = MAX_SOURCE_LINES) -> str:
    """Keep the first ``max_lines`` lines and say how many were left out."""
    lines = source.splitlines()
    if len(lines) <= max_lines:
        return source
    omitted = len(lines) - max_lines
    return "\n".join([*lines[:max_lines], f"# ... ({omitted} more lines omitted)"])


def mark_uncovered(function: FunctionInfo, uncovered_lines: Collection[int]) -> str:
    """Function source with ``# <- not covered`` appended to the given absolute lines."""
    lines = function.source.split("\n")
    first = function.end_lineno - len(lines) + 1
    wanted = set(uncovered_lines)
    return "\n".join(
        f"{text}{_NOT_COVERED_MARK}" if first + offset in wanted and text.strip() else text
        for offset, text in enumerate(lines)
    )


def import_line(module: ModuleInfo, function: FunctionInfo) -> str:
    """The import statement a test must use for ``function`` (the class for methods)."""
    name = function.class_name.split(".")[0] if function.class_name else function.name
    return f"from {module.module_name} import {name}"


def class_summary(module: ModuleInfo, function: FunctionInfo, method_source: str) -> str:
    """Class line, first docstring line, ``__init__`` and the method, as readable pseudo-code."""
    class_name = function.class_name or ""
    short_name = class_name.split(".")[-1]
    info = next((c for c in module.classes if c.name == short_name), None)
    head = f"class {short_name}:"
    if info is not None:
        lines = module.source.split("\n")[info.lineno - 1 : info.end_lineno]
        head = next((ln.strip() for ln in lines if ln.lstrip().startswith("class ")), head)
    parts = [head]
    if info is not None and info.docstring:
        summary = info.docstring.strip().splitlines()[0]
        parts.append(textwrap.indent(f'"""{summary}"""', "    "))
    init = next((f for f in module.functions if f.qualname == f"{class_name}.__init__"), None)
    if init is not None and init.qualname != function.qualname:
        parts.append(textwrap.indent(truncate_source(init.source, 30), "    "))
        parts.append("    ...")
    parts.append(textwrap.indent(method_source, "    "))
    return "\n".join(parts)


def module_constants(module: ModuleInfo, function: FunctionInfo) -> str:
    """Top-level assignments (``RESERVED = {...}``) whose names the function reads."""
    try:
        tree = ast.parse(module.source)
        used = {
            n.id
            for n in ast.walk(ast.parse(function.source))
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
    except SyntaxError:
        return ""
    found: list[str] = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if isinstance(node, ast.Assign | ast.AnnAssign) and names & used:
            text = " ".join((ast.get_source_segment(module.source, node) or "").split())
            if len(text) > _MAX_CONSTANT_CHARS:
                text = f"{text[: _MAX_CONSTANT_CHARS - 3]}..."
            found.append(text)
    return "\n".join(found[:_MAX_CONSTANTS])


def _constants_block(module: ModuleInfo, function: FunctionInfo) -> str:
    constants = module_constants(module, function)
    return f"Module constants used:\n{_fenced(constants)}\n\n" if constants else ""


def describe_params(parameters: Sequence[Parameter]) -> str:
    """``price: float, years: int = 0`` style one-liner."""
    items = []
    for param in parameters:
        text = param.name if param.annotation is None else f"{param.name}: {param.annotation}"
        items.append(text if param.default is None else f"{text} = {param.default}")
    return ", ".join(items) or "(none)"


def _short(value: object, limit: int = _MAX_REPR_CHARS) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _fenced(code: str) -> str:
    return f"```python\n{code}\n```"


def _messages(user: str) -> list[ChatMessage]:
    return [ChatMessage(Role.SYSTEM, SYSTEM_PROMPT), ChatMessage(Role.USER, user)]


# --------------------------------------------------------------------------- oracle review


def describe_case(function: FunctionInfo, case: TestCaseSpec) -> str:
    """One recorded behaviour as ``f(a=1) -> 2`` or ``f(a=1) raises ValueError: message``."""
    args = ", ".join(f"{key}={_short(repr(value), 60)}" for key, value in case.inputs.items())
    call = f"{function.qualname}({args})"
    obs = case.observation
    if obs.kind is ObservationKind.RAISED:
        message = f": {_short(obs.exception_message, 80)}" if obs.exception_message else ""
        return f"{call} raises {obs.exception_type}{message}"
    return f"{call} -> {_short(obs.value_repr)}"


def oracle_review_messages(
    module: ModuleInfo, function: FunctionInfo, numbered: Sequence[tuple[int, TestCaseSpec]]
) -> list[ChatMessage]:
    """Prompt asking whether each recorded ``(index, case)`` agrees with the documentation."""
    behaviours = "\n".join(f"[{index}] {describe_case(function, case)}" for index, case in numbered)
    user = (
        f"Function under test (module {module.module_name}):\n"
        f"{_fenced(truncate_source(function.source))}\n\n"
        f"Recorded behaviour of the current implementation:\n{behaviours}\n\n"
        "For each numbered line decide whether the result is what the docstring and the "
        "function name promise.\n"
        '- "ok": consistent with the documented intent.\n'
        '- "suspicious": clearly contradicts the documentation, so the code may have a bug. '
        "Give a one-sentence explanation.\n"
        'If unsure, answer "ok". Ignore code style.\n'
        "Reply with JSON only, one entry per line above:\n"
        '{"reviews": [{"index": 0, "verdict": "ok", "explanation": ""}]}'
    )
    return _messages(user)


# --------------------------------------------------------------------------- input proposals


def describe_target(target: BranchTarget, reason: str) -> str:
    """One branch outcome with its path condition and why the solver failed on it."""
    effect = f", then it raises {target.raises}" if target.raises else ""
    text = (
        f"line {target.line}: `{target.condition_source}` must be {target.outcome}{effect}. "
        f"Path condition: {target.path}"
    )
    return f"{text}. Solver: {reason}" if reason else text


def propose_inputs_messages(
    module: ModuleInfo,
    function: FunctionInfo,
    targets: Sequence[BranchTarget],
    reasons: Mapping[str, str],
    max_inputs: int,
) -> list[ChatMessage]:
    """Prompt asking for concrete arguments that reach the given branch outcomes."""
    listing = "\n".join(
        f"{number}. {describe_target(target, reasons.get(target.key, ''))}"
        for number, target in enumerate(targets, start=1)
    )
    fields = ", ".join(f'"{p.name}": <value>' for p in function.call_parameters)
    user = (
        f"Function under test (module {module.module_name}):\n"
        f"{_fenced(truncate_source(function.source))}\n\n"
        f"{_constants_block(module, function)}"
        f"Parameters: {describe_params(function.call_parameters)}\n"
        "An automatic solver could not find arguments for these branch outcomes:\n"
        f"{listing}\n\n"
        f"Give up to {max_inputs} different argument sets that make execution take these "
        "outcomes. Each set must satisfy the path condition of its target. "
        "Use only JSON values (numbers, strings, true, false, null, lists, objects) and include "
        "every parameter that has no default.\n"
        "Reply with JSON only:\n"
        f'{{"inputs": [{{{fields}}}]}}'
    )
    return _messages(user)


# --------------------------------------------------------------------------- free-form tests


def write_tests_messages(
    module: ModuleInfo,
    function: FunctionInfo,
    *,
    uncovered_lines: Collection[int] = (),
    avoid_names: Collection[str] = (),
    max_tests: int = 5,
) -> list[ChatMessage]:
    """Prompt asking for a small pytest module testing exactly one function or method."""
    source = truncate_source(mark_uncovered(function, uncovered_lines))
    kind = "method" if function.is_method else "function"
    if function.is_method:
        source = class_summary(module, function, source)
    parts = [
        f"Write pytest tests for ONE {kind}: {function.qualname}.",
        f"Import line (use exactly this): {import_line(module, function)}",
        f"Source:\n{_fenced(source)}",
    ]
    if constants := module_constants(module, function):
        parts.append(f"Module constants used:\n{_fenced(constants)}")
    if uncovered_lines:
        parts.append(
            f'Lines marked "{_NOT_COVERED_MARK.strip()}" are not run by existing tests: '
            "write tests that reach them."
        )
    if hint := _construction_hint(function):
        parts.append(hint)
    rules = [
        "- Use only pytest, the standard library and the module above.",
        "- No network, no files outside tmp_path, no sleep, no print, no randomness.",
        "- Deterministic, small tests with plain assert statements; "
        "use pytest.raises for expected exceptions.",
        f"- At most {max_tests} tests, named test_{function.name}_<something>.",
    ]
    if avoid_names:
        rules.append(f"- Do not reuse these test names: {', '.join(sorted(avoid_names))}.")
    parts.append("Rules:\n" + "\n".join(rules))
    parts.append("Answer with one python code block containing the imports and the tests.")
    return _messages("\n\n".join(parts))


def _construction_hint(function: FunctionInfo) -> str:
    if not function.is_method or function.is_static or function.is_classmethod:
        return ""
    cls = (function.class_name or "").split(".")[-1]
    action = "read the attribute" if function.is_property else f"call .{function.name}(...)"
    return f"First create the object with {cls}(...) as __init__ requires, then {action}."


# --------------------------------------------------------------------------- repair


def failure_text(outcome: TestOutcome) -> str:
    """Failure message plus the end of the traceback, cut to ``MAX_FAILURE_CHARS``."""
    tail = "\n".join(outcome.traceback.strip().splitlines()[-MAX_TRACEBACK_LINES:])
    message = outcome.message.strip()
    text = tail if message and message in tail else "\n".join(p for p in (message, tail) if p)
    if len(text) > MAX_FAILURE_CHARS:
        text = f"...{text[-MAX_FAILURE_CHARS:]}"
    return text or "(no message)"


def repair_messages(
    function: FunctionInfo | None, test_name: str, test_source: str, outcome: TestOutcome
) -> list[ChatMessage]:
    """Prompt asking to fix a failing test so that it matches the code's actual behaviour."""
    parts = [
        "This pytest test does not pass. Fix the TEST, not the code: the code under test is "
        "the source of truth, so change inputs or expected values to match what it actually "
        "does.",
        f"Test:\n{_fenced(test_source.strip())}",
        f"Result: {outcome.status.value}\n```\n{failure_text(outcome)}\n```",
    ]
    if function is not None:
        parts.append(f"Code under test:\n{_fenced(truncate_source(function.source))}")
    parts.append(
        f"Keep the test name `{test_name}`. No network, no files outside tmp_path, no sleep.\n"
        "Answer with one python code block containing the complete fixed test with its imports."
    )
    return _messages("\n\n".join(parts))
