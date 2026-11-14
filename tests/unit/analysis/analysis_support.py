"""Helpers shared by the analysis tests (not a test module)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pytestgen.analysis import AstModuleAnalyzer
from pytestgen.domain.models import BranchTarget, Clause, FunctionInfo, ModuleInfo

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
PROBE = HERE / "arc_probe.py"


def analyze_fixture(name: str) -> ModuleInfo:
    return AstModuleAnalyzer().analyze(FIXTURES / f"{name}.py", FIXTURES)


def analyze_text(tmp_path: Path, source: str, name: str = "mod") -> ModuleInfo:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return AstModuleAnalyzer().analyze(path, tmp_path)


def line_of(path_or_text: Path | str, needle: str, occurrence: int = 0) -> int:
    """1-based number of the ``occurrence``-th line containing ``needle``."""
    text = (
        path_or_text.read_text(encoding="utf-8") if isinstance(path_or_text, Path) else path_or_text
    )
    hits = [i for i, line in enumerate(text.split("\n"), start=1) if needle in line]
    return hits[occurrence]


def target(
    function: FunctionInfo, line: int, outcome: bool, kind: str | None = None
) -> BranchTarget:
    found = [
        t
        for t in function.branches
        if t.line == line and t.outcome is outcome and (kind is None or t.kind == kind)
    ]
    assert len(found) == 1, f"expected one target at {line}/{outcome}, got {found}"
    return found[0]


def clauses(branch: BranchTarget) -> list[Clause]:
    return list(branch.path.clauses)


def measure_arcs(
    module_path: Path, calls: Mapping[str, Sequence[Sequence[Any]]], tmp_path: Path
) -> dict[str, set[tuple[int, int]]]:
    """Union of executed arcs per function, measured by coverage.py in a subprocess."""
    calls_file = tmp_path / "calls.json"
    calls_file.write_text(json.dumps(calls), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE", "COVERAGE"))}
    completed = subprocess.run(
        [sys.executable, "-I", str(PROBE), str(module_path), str(calls_file)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    raw: dict[str, list[list[list[int]]]] = json.loads(completed.stdout)
    return {
        name: {(src, dst) for per_call in runs for src, dst in per_call}
        for name, runs in raw.items()
    }
