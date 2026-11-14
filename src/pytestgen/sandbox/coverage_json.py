"""Extract per-file branch coverage from ``coverage json`` output."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pytestgen.domain.models import Arc, FileCoverage


def parse_file_coverage(
    json_text: str, *, target: Path, base: Path, report_path: str
) -> FileCoverage | None:
    """Coverage of ``target`` (compared by resolved path, case-insensitively on Windows).

    ``base`` resolves relative keys of the report; ``report_path`` becomes ``FileCoverage.path``.
    """
    try:
        files: object = json.loads(json_text)["files"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(files, Mapping):
        return None
    wanted = _normal(target)
    for name, data in files.items():
        candidate = Path(name)
        if _normal(candidate if candidate.is_absolute() else base / candidate) == wanted:
            return _file_coverage(report_path, data)
    return None


def _normal(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _file_coverage(path: str, data: Mapping[str, Any]) -> FileCoverage:
    return FileCoverage(
        path=path,
        executed_lines=frozenset(data.get("executed_lines", ())),
        missing_lines=frozenset(data.get("missing_lines", ())),
        executed_arcs=frozenset(Arc(a, b) for a, b in data.get("executed_branches", ())),
        missing_arcs=frozenset(Arc(a, b) for a, b in data.get("missing_branches", ())),
    )
