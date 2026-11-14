"""Rich console view of a generation report."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from pytestgen.domain.models import FunctionCoverageDelta, GenerationReport
from pytestgen.report._format import (
    branch_gain,
    branches_text,
    branches_transition,
    format_inputs,
    format_usage,
    group_dropped,
    line_gain,
    lines_text,
    lines_transition,
    more_note,
    one_line,
    ranked_functions,
    signed_gain,
)


def _gain_text(value: float | None) -> str:
    """Gain as rich markup: green when positive, red when negative."""
    text = signed_gain(value)
    if value is None:
        return text
    if round(value, 1) > 0:
        return f"[green]{text}[/green]"
    if round(value, 1) < 0:
        return f"[red]{text}[/red]"
    return text


def render_console(report: GenerationReport, console: Console) -> None:
    """Print the report with rich tables (same information as the markdown report)."""
    console.print(f"[bold]pytestgen: {escape(report.module)}[/bold]")
    console.print(_summary_table(report))
    console.print(f"Kept tests: [bold]{report.kept_tests}[/bold]")
    if report.by_origin:
        console.print(_origin_table(report))
    if report.delta.per_function:
        _print_functions(report, console)
    _print_bugs(report, console)
    _print_dropped(report, console)
    _print_footer(report, console)


def _summary_table(report: GenerationReport) -> Table:
    delta = report.delta
    table = Table("Metric", "Before", "After", "Gain", title="Coverage")
    table.add_row(
        "Lines",
        lines_text(delta.before),
        lines_text(delta.after),
        _gain_text(line_gain(delta)),
    )
    table.add_row(
        "Branches",
        branches_text(delta.before),
        branches_text(delta.after),
        _gain_text(branch_gain(delta)),
    )
    return table


def _origin_table(report: GenerationReport) -> Table:
    table = Table("Origin", "Tests", "Arcs added", title="Kept tests by origin")
    for stats in report.by_origin:
        table.add_row(stats.origin.value, str(stats.tests), str(stats.arcs_added))
    return table


def _function_row(item: FunctionCoverageDelta) -> tuple[str, str, str]:
    return (
        escape(item.function),
        lines_transition(item.before, item.after),
        branches_transition(item.before, item.after),
    )


def _print_functions(report: GenerationReport, console: Console) -> None:
    shown, hidden = ranked_functions(report.delta)
    table = Table("Function", "Lines", "Branches", title="Per function")
    for item in shown:
        table.add_row(*_function_row(item))
    console.print(table)
    if hidden:
        console.print(f"[dim]{more_note(hidden)}[/dim]")


def _print_bugs(report: GenerationReport, console: Console) -> None:
    if not report.suspected_bugs:
        return
    console.print("[bold yellow]Suspected bugs[/bold yellow]")
    for bug in report.suspected_bugs:
        console.print(
            f"  [bold]{escape(bug.function)}[/bold]({escape(format_inputs(bug.inputs))}) "
            f"observed {escape(bug.observed)}"
        )
        console.print(f"    {escape(one_line(bug.explanation))}")


def _print_dropped(report: GenerationReport, console: Console) -> None:
    if not report.dropped:
        return
    console.print(f"[bold]Dropped tests ({len(report.dropped)})[/bold]")
    for reason, items in group_dropped(report.dropped).items():
        console.print(f"  {reason.value} ({len(items)})")
        for item in items:
            detail = f": {one_line(item.detail)}" if item.detail else ""
            console.print(f"    {escape(item.name)} ({escape(item.function)}){escape(detail)}")


def _print_footer(report: GenerationReport, console: Console) -> None:
    model = f", model {escape(report.model)}" if report.model else ""
    console.print(f"LLM usage: {format_usage(report.llm_usage)}{model}")
    console.print(f"Strategy: {report.strategy.value}, duration: {report.duration_s:.1f} s")
    if report.output_path is not None:
        console.print(f"Output: {escape(report.output_path.as_posix())}")
