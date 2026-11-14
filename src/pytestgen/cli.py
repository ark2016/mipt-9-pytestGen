"""Command-line interface (Typer).

Human-readable output goes to stdout through rich; structured JSON logs and error
messages go to stderr.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape

from pytestgen import __version__
from pytestgen.application import AnalyzeRequest, GenerateRequest, find_project_root
from pytestgen.cli_views import ConsoleProgress, analysis_to_json, render_analysis
from pytestgen.config import LogFormat, Settings
from pytestgen.container import build_analyze, build_generate
from pytestgen.domain.models import GenerationReport, Strategy
from pytestgen.errors import PytestgenError
from pytestgen.log import configure_logging
from pytestgen.report import JsonRenderer, MarkdownRenderer, render_console

EXIT_USAGE = 2

app = typer.Typer(
    name="pytestgen",
    help="Generate pytest tests for a Python module with an LLM and concolic-lite path search.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

TargetArg = Annotated[
    Path,
    typer.Argument(help="Python module.", exists=True, dir_okay=False, resolve_path=True),
]
ProjectRootOpt = Annotated[
    Path | None,
    typer.Option(
        "--project-root",
        help="Project root (default: nearest parent with pyproject.toml, setup.cfg, "
        "pytest.ini or .git).",
        file_okay=False,
        resolve_path=True,
    ),
]
FunctionOpt = Annotated[
    list[str] | None,
    typer.Option("--function", "-f", help="Function, Class.method or class name; repeatable."),
]


def _fail(message: str) -> typer.Exit:
    err_console.print(f"[red]error:[/red] {escape(message)}", soft_wrap=True)
    return typer.Exit(EXIT_USAGE)


@app.callback()
def main(
    ctx: typer.Context,
    log_level: Annotated[
        str | None, typer.Option("--log-level", help="Override PYTESTGEN_LOG_LEVEL.")
    ] = None,
    log_format: Annotated[
        LogFormat | None, typer.Option("--log-format", help="json (default) or console.")
    ] = None,
) -> None:
    try:
        settings = Settings()
        if log_level:
            settings = Settings.model_validate({**_current(settings), "log_level": log_level})
        if log_format:
            settings = settings.model_copy(update={"log_format": log_format})
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise _fail(f"invalid configuration: {problems}") from exc
    configure_logging(settings.log_level, settings.log_format)
    ctx.obj = settings


def _current(settings: Settings) -> dict[str, object]:
    """Settings as constructor input, so validators run again on an override."""
    return {name: getattr(settings, name) for name in type(settings).model_fields}


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"pytestgen {__version__}")


@app.command("config")
def show_config(ctx: typer.Context) -> None:
    """Print the effective configuration (API key masked)."""
    settings: Settings = ctx.obj
    console.print_json(json.dumps(settings.public_dict(), ensure_ascii=False))


@app.command()
def analyze(  # noqa: PLR0917 - Typer command: every parameter is a CLI option
    ctx: typer.Context,
    target: TargetArg,
    project_root: ProjectRootOpt = None,
    function: FunctionOpt = None,
    solve: Annotated[
        bool, typer.Option("--solve/--no-solve", help="Ask Z3 for inputs of every branch.")
    ] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of tables.")] = False,
) -> None:
    """Show functions, branch targets and (with --solve) the inputs Z3 finds. No LLM, no sandbox."""
    settings: Settings = ctx.obj
    root = project_root or find_project_root(target)
    try:
        analysis = build_analyze(settings).execute(
            AnalyzeRequest(target, root, tuple(function or ()), solve=solve)
        )
    except PytestgenError as exc:
        raise _fail(str(exc)) from exc
    if as_json:
        typer.echo(analysis_to_json(analysis))
    else:
        render_analysis(console, analysis)


@app.command()
def generate(  # noqa: PLR0917 - Typer command: every parameter is a CLI option
    ctx: typer.Context,
    target: TargetArg,
    project_root: ProjectRootOpt = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", help="Test file to write (default: tests/test_<module>_pytestgen.py)."
        ),
    ] = None,
    function: FunctionOpt = None,
    strategy: Annotated[
        Strategy, typer.Option(help="hybrid: AST+Z3+LLM slots; llm: LLM-written tests only.")
    ] = Strategy.HYBRID,
    minimize: Annotated[
        bool, typer.Option("--minimize", help="Drop tests that add no coverage.")
    ] = False,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Do not read or write the LLM response cache.")
    ] = False,
    existing_tests: Annotated[
        list[Path] | None,
        typer.Option(
            "--existing-tests",
            help="Existing test file counted in the baseline; repeatable.",
            exists=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    no_existing_tests: Annotated[
        bool, typer.Option("--no-existing-tests", help="Baseline without any existing tests.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Run everything but do not write the test file.")
    ] = False,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing file not made by pytestgen.")
    ] = False,
    report_md: Annotated[
        Path | None, typer.Option("--report-md", help="Write the markdown report here.")
    ] = None,
    report_json: Annotated[
        Path | None, typer.Option("--report-json", help="Write the JSON report here.")
    ] = None,
    fail_under_gain: Annotated[
        float | None,
        typer.Option("--fail-under-gain", help="Exit 1 if the line coverage gain (pp) is lower."),
    ] = None,
) -> None:
    """Generate a pytest file for TARGET and report the coverage gain."""
    settings: Settings = ctx.obj
    if existing_tests and no_existing_tests:
        raise _fail("--existing-tests and --no-existing-tests are mutually exclusive")
    request = GenerateRequest(
        target=target,
        project_root=project_root or find_project_root(target),
        output=output.resolve() if output else None,
        functions=tuple(function or ()),
        strategy=strategy,
        minimize=minimize,
        existing_tests=_existing(existing_tests, none=no_existing_tests),
        dry_run=dry_run,
        overwrite=overwrite,
    )
    try:
        with build_generate(
            settings, use_cache=not no_cache, progress=ConsoleProgress(console)
        ) as use_case:
            report = use_case.execute(request)
    except PytestgenError as exc:
        raise _fail(str(exc)) from exc
    render_console(report, console)
    _write_reports(report, report_md, report_json)
    if fail_under_gain is not None and report.delta.line_gain < fail_under_gain:
        err_console.print(
            f"[red]line coverage gain {report.delta.line_gain:+.1f} pp "
            f"is below {fail_under_gain:.1f} pp[/red]",
            soft_wrap=True,
        )
        raise typer.Exit(1)


def _existing(paths: list[Path] | None, *, none: bool) -> tuple[Path, ...] | None:
    """``None`` means "discover"; an empty tuple means "no existing tests"."""
    if none:
        return ()
    return tuple(paths) if paths else None


def _write_reports(report: GenerationReport, markdown: Path | None, json_path: Path | None) -> None:
    if markdown is not None:
        _write_text(markdown, MarkdownRenderer().render(report))
    if json_path is not None:
        _write_text(json_path, JsonRenderer().render(report))


def _write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise _fail(f"cannot write report {path}: {exc}") from exc
