"""Structured logging: JSON lines to stderr (stdout is reserved for the CLI's human output)."""

from __future__ import annotations

import logging
import sys
from typing import Any, TextIO

import structlog

from pytestgen.config import LogFormat


def configure_logging(
    level: str = "INFO",
    fmt: LogFormat = LogFormat.JSON,
    stream: TextIO | None = None,
) -> None:
    """Configure structlog once per process.

    ``stream`` defaults to ``sys.stderr`` resolved at call time, so tests that
    capture stderr see the log lines.
    """
    renderer: Any = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if fmt is LogFormat.JSON
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str, **initial: Any) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name).bind(logger=name, **initial)
    return logger
