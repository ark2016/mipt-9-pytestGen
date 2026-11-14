"""Structured logging: JSON lines to stderr (stdout is reserved for the CLI's human output)."""

from __future__ import annotations

import logging
import sys
from typing import Any, TextIO, cast

import structlog

from pytestgen.config import LogFormat


class _CurrentStderr:
    """File-like that writes to whatever ``sys.stderr`` is at the moment of each write.

    A stream captured at configuration time may be closed later (CLI test runners swap and
    close ``sys.stderr``), which would break every subsequent log call.
    """

    def write(self, text: str) -> int:
        return sys.stderr.write(text)

    def flush(self) -> None:
        sys.stderr.flush()


def configure_logging(
    level: str = "INFO",
    fmt: LogFormat = LogFormat.JSON,
    stream: TextIO | None = None,
) -> None:
    """Configure structlog once per process.

    ``stream`` defaults to the current ``sys.stderr``, looked up on every write, so
    redirected or replaced stderr streams (tests, CLI runners) still receive the lines.
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
        logger_factory=structlog.PrintLoggerFactory(file=stream or cast(TextIO, _CurrentStderr())),
        cache_logger_on_first_use=False,
    )


class _LazyLogger:
    """Binds to the current structlog configuration on every call, not at creation time.

    Module-level loggers are created at import time, before the CLI configures logging, so
    a logger bound right away would keep writing with structlog's defaults (stdout, DEBUG).
    """

    def __init__(self, name: str, initial: dict[str, Any]) -> None:
        self._name = name
        self._initial = initial

    def __getattr__(self, attr: str) -> Any:
        bound = structlog.get_logger(self._name).bind(logger=self._name, **self._initial)
        return getattr(bound, attr)


def get_logger(name: str, **initial: Any) -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", _LazyLogger(name, initial))
