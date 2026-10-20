"""Command-line interface (Typer).

Human-readable output goes to stdout through rich; structured JSON logs go to stderr.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console

from pytestgen import __version__
from pytestgen.config import LogFormat, Settings
from pytestgen.log import configure_logging

app = typer.Typer(
    name="pytestgen",
    help="Generate pytest tests for a Python module with an LLM and concolic-lite path search.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


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
    settings = Settings()
    if log_level:
        settings = settings.model_copy(update={"log_level": log_level.upper()})
    if log_format:
        settings = settings.model_copy(update={"log_format": log_format})
    configure_logging(settings.log_level, settings.log_format)
    ctx.obj = settings


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"pytestgen {__version__}")


@app.command("config")
def show_config(ctx: typer.Context) -> None:
    """Print the effective configuration (API key masked)."""
    settings: Settings = ctx.obj
    console.print_json(json.dumps(settings.public_dict(), ensure_ascii=False))
