"""Exercise the benchmark with real subprocesses and deterministic generated code."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from pytestgen.config import Settings
from pytestgen.domain.models import Strategy
from pytestgen.sandbox import SubprocessSuiteRunner


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "bench_mbpp.py"
    spec = importlib.util.spec_from_file_location("bench_mbpp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


def test_shards_cover_the_same_fixed_cohort_without_duplicates() -> None:
    tasks = [bench.Task(i, "", ()) for i in range(500)]
    full = bench.select_tasks(tasks, 100, 0, 1)
    shards = [bench.select_tasks(tasks, 100, shard, 10) for shard in range(10)]
    assert len({task.task_id for shard in shards for task in shard}) == 100
    assert all(shard == full[i::10] for i, shard in enumerate(shards))


def test_counts_mutation_only_for_test_that_passed_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def generated(target: Path, *_: object) -> str:
        assert [file.name for file in target.parent.iterdir()] == ["solution.py"]
        return """\
from solution import adjust

def test_boundary():
    assert adjust(0) is False

def test_already_wrong():
    assert adjust(3) is False
"""

    monkeypatch.setattr(bench, "generate_source", generated)
    task = bench.Task(24, "def adjust(value):\n    return value > 0\n", ("assert not adjust(0)",))
    row = bench.evaluate(task, Settings(), Strategy.HYBRID, SubprocessSuiteRunner(), 1)
    assert row.status == "ok"
    assert row.generated_executed == 2
    assert row.passed_reference == 1
    assert [mutant["status"] for mutant in row.mutants] == ["killed"]
    metrics = bench.summarize([row])
    assert metrics["reference_pass_rate"] == 0.5
    assert metrics["mutation_score"] == 1.0
    assert metrics["reference_pass_and_kill_rate"] == 0.5
    assert len(row.tests_with_kills) == 1
    assert row.tests_with_kills[0].endswith("::test_boundary")
