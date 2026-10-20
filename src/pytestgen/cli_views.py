"""Presentation helpers of the CLI: progress lines and the ``analyze`` view."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from pytestgen.application import BranchAnalysis, ModuleAnalysis
from pytestgen.domain.models import FunctionInfo, Solution, SolveStatus
from pytestgen.ports import Stage

_STATUS_STYLE = {
    SolveStatus.SAT: "green",
    SolveStatus.UNSAT: "yellow",
    SolveStatus.UNSUPPORTED: "red",
    SolveStatus.UNKNOWN: "red",
}


class ConsoleProgress:
    """``ProgressListener`` that prints one rich line per pipeline event."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def stage(self, stage: Stage, message: str) -> None:
        self._console.print(f"[cyan]{stage.value:<9}[/cyan] {escape(message)}")


def _signature(function: FunctionInfo) -> str:
    params = ", ".join(
        p.name
        + (f": {p.annotation}" if p.annotation else "")
        + (f" = {p.default}" if p.default else "")
        for p in function.parameters
    )
    ret = f" -> {function.return_annotation}" if function.return_annotation else ""
    return f"{function.qualname}({params}){ret}"


def _inputs_text(inputs: Mapping[str, object]) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in inputs.items())


def _solution_cell(solution: Solution) -> str:
    style = _STATUS_STYLE[solution.status]
    detail = (
        _inputs_text(solution.inputs) if solution.status is SolveStatus.SAT else solution.reason
    )
    return f"[{style}]{solution.status.value}[/{style}] {escape(detail)}".rstrip()


def render_analysis(console: Console, analysis: ModuleAnalysis) -> None:
    """Print a table of branch targets per function."""
    module, solved = analysis.module, analysis.solved
    console.print(f"[bold]{escape(module.module_name)}[/bold] ({escape(str(module.path))})")
    for item in analysis.functions:
        console.print(f"\n[bold]{escape(_signature(item.function))}[/bold]")
        if not item.branches:
            console.print("  [dim]no branches[/dim]")
            continue
        table = Table(show_lines=False)
        table.add_column("Target", no_wrap=True)
        for name in ("Kind", "Condition", "Path condition", *(["Z3"] if solved else [])):
            table.add_column(name, overflow="fold")
        for branch in item.branches:
            target = branch.target
            row = [
                escape(target.key),
                target.kind.value,
                escape(target.condition_source),
                escape(str(target.path)),
            ]
            if branch.solution is not None:
                row.append(_solution_cell(branch.solution))
            table.add_row(*row)
        console.print(table)


def _plain(value: object) -> object:
    """JSON-safe copy of a solver input (tuples become lists, unknown types their repr)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(v) for v in value]
    return repr(value)


def _branch_json(branch: BranchAnalysis) -> dict[str, object]:
    target = branch.target
    data: dict[str, object] = {
        "key": target.key,
        "line": target.line,
        "kind": target.kind.value,
        "condition": target.condition_source,
        "outcome": target.outcome,
        "path_condition": str(target.path),
        "raises": target.raises,
    }
    if branch.solution is not None:
        data["solution"] = {
            "status": branch.solution.status.value,
            "inputs": _plain(dict(branch.solution.inputs)),
            "reason": branch.solution.reason,
        }
    return data


def analysis_to_json(analysis: ModuleAnalysis) -> str:
    """Machine-readable form of the analysis (UTF-8 text, indented)."""
    functions = [
        {
            "qualname": item.function.qualname,
            "line": item.function.lineno,
            "parameters": [
                {"name": p.name, "annotation": p.annotation, "default": p.default}
                for p in item.function.parameters
            ],
            "returns": item.function.return_annotation,
            "branches": [_branch_json(b) for b in item.branches],
        }
        for item in analysis.functions
    ]
    module = analysis.module
    payload = {"module": module.module_name, "path": str(module.path), "functions": functions}
    return json.dumps(payload, indent=2, ensure_ascii=False)
