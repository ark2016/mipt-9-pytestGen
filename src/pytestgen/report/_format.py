"""Formatting helpers shared by the markdown and console renderers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from pytestgen.domain.models import (
    CoverageDelta,
    DroppedTest,
    DropReason,
    FunctionCoverageDelta,
    LLMUsage,
)

MAX_FUNCTION_ROWS = 15


def percent(value: float) -> str:
    return f"{value:.1f}%"


def signed_gain(value: float) -> str:
    """Percentage-point gain with an explicit sign; ``-0.0`` is shown as ``+0.0``."""
    return f"{round(value, 1) + 0.0:+.1f} pp"


def transition(before: float, after: float) -> str:
    """``before -> after``; ASCII only so output is safe on any stream encoding."""
    return f"{percent(before)} -> {percent(after)}"


def more_note(hidden: int) -> str:
    return f"...and {hidden} more"


def function_gain(item: FunctionCoverageDelta) -> float:
    return (
        item.after.line_percent
        - item.before.line_percent
        + item.after.branch_percent
        - item.before.branch_percent
    )


def ranked_functions(
    delta: CoverageDelta, limit: int = MAX_FUNCTION_ROWS
) -> tuple[list[FunctionCoverageDelta], int]:
    """Functions sorted by gain (desc, then name) cut to ``limit``; also the hidden count."""
    ordered = sorted(delta.per_function, key=lambda f: (-function_gain(f), f.function))
    return ordered[:limit], max(0, len(ordered) - limit)


def format_inputs(inputs: Mapping[str, object]) -> str:
    """Inputs as Python-literal-ish text: ``a=1, b='x'``."""
    if not inputs:
        return "(no arguments)"
    return ", ".join(f"{name}={value!r}" for name, value in inputs.items())


def one_line(text: str) -> str:
    return " ".join(text.split())


def format_usage(usage: LLMUsage) -> str:
    total = usage.prompt_tokens + usage.completion_tokens
    return (
        f"{usage.requests} requests, {usage.cache_hits} cache hits, {total:,} tokens "
        f"({usage.prompt_tokens:,} prompt + {usage.completion_tokens:,} completion)"
    )


def group_dropped(dropped: tuple[DroppedTest, ...]) -> dict[DropReason, list[DroppedTest]]:
    groups: dict[DropReason, list[DroppedTest]] = defaultdict(list)
    for item in dropped:
        groups[item.reason].append(item)
    return dict(groups)
