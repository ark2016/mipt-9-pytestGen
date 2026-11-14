"""Reproducible MBPP mutation evaluation for the public pytestgen API."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.request import urlopen

from pytestgen.application.generate import GenerateRequest
from pytestgen.config import Settings
from pytestgen.container import build_generate
from pytestgen.domain.models import RunResult, Strategy, TestStatus
from pytestgen.errors import PytestgenError
from pytestgen.log import configure_logging, get_logger
from pytestgen.ports import RunRequest, SuiteRunner
from pytestgen.sandbox import SubprocessSuiteRunner

REVISION = "f82046ba5aabbbb427dbfd38a254d26bff08b533"
DATASET_URL = (
    f"https://raw.githubusercontent.com/google-research/google-research/{REVISION}/mbpp/mbpp.jsonl"
)
DATASET_SHA256 = "ccf64ceae9c5403bf50a044cb6d505bfd2a2963ee58338ba268fd65beab92a9f"
SEED = 42
MAX_CANDIDATES = 12
_OPERATOR_SWAPS = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Add,
    ast.And: ast.Or,
    ast.Or: ast.And,
}
_log = get_logger(__name__)


@dataclass(frozen=True)
class Task:
    task_id: int
    code: str
    tests: tuple[str, ...]
    setup: str = ""


@dataclass
class Result:
    task_id: int
    status: str = "ok"
    detail: str = ""
    generated_executed: int = 0
    passed_reference: int = 0
    tests_with_kills: list[str] = field(default_factory=list)
    mutants: list[dict[str, str]] = field(default_factory=list)
    llm_requests: int = 0
    llm_tokens: int = 0


def load_tasks(path: Path) -> list[Task]:
    if not path.exists():
        with urlopen(DATASET_URL, timeout=30) as response:
            data = response.read()
        verify_dataset(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    data = path.read_bytes()
    verify_dataset(data)
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    return sorted(
        (
            Task(row["task_id"], row["code"], tuple(row["test_list"]), row["test_setup_code"])
            for row in rows
            if 11 <= row["task_id"] <= 510
        ),
        key=lambda task: task.task_id,
    )


def verify_dataset(data: bytes) -> None:
    if hashlib.sha256(data).hexdigest() != DATASET_SHA256:
        raise ValueError("MBPP dataset SHA-256 mismatch")


def select_tasks(tasks: list[Task], limit: int, shard: int, shards: int) -> list[Task]:
    if not 1 <= limit <= 100 or not 0 <= shard < shards <= limit:
        raise ValueError("require 1 <= limit <= 100 and 0 <= shard < shards <= limit")
    cohort = random.Random(SEED).sample(tasks, 100)  # noqa: S311 -- reproducible sampling
    return cohort[:limit][shard::shards]


def held_out_tests(task: Task) -> str:
    return "\n\n".join(
        [
            "from solution import *",
            task.setup,
            *(
                f"def test_mbpp_{index}():\n"
                + "\n".join(f"    {line}" for line in source.splitlines())
                for index, source in enumerate(task.tests)
            ),
        ]
    )


def mutations(code: str, task_id: int) -> Iterator[tuple[str, str]]:
    """Yield distinct single-site operator/return mutations in a fixed seeded order."""
    tree = ast.parse(code)
    sites = [
        (node, field_name, index, child)
        for node in ast.walk(tree)
        for field_name, value in ast.iter_fields(node)
        for index, child in (enumerate(value) if isinstance(value, list) else [(None, value)])
        if type(child) in _OPERATOR_SWAPS
        or (
            isinstance(node, ast.Return)
            and isinstance(child, ast.Constant)
            and type(child.value) in (bool, int)
        )
    ]
    random.Random(task_id).shuffle(sites)  # noqa: S311 -- reproducible mutations
    seen = {ast.unparse(tree)}
    for node, name, index, old in sites:
        if isinstance(old, ast.Constant):
            new = ast.Constant(not old.value if isinstance(old.value, bool) else old.value + 1)
        else:
            new = _OPERATOR_SWAPS[type(old)]()
        if index is None:
            setattr(node, name, new)
        else:
            getattr(node, name)[index] = new
        candidate = ast.unparse(tree)
        if index is None:
            setattr(node, name, old)
        else:
            getattr(node, name)[index] = old
        if candidate not in seen:
            seen.add(candidate)
            yield f"{node.lineno}:{name}:{index}:{type(old).__name__}", candidate + "\n"


def run_tests(runner: SuiteRunner, target: Path, source: str) -> RunResult:
    return runner.run(
        RunRequest(
            project_root=target.parent,
            target=target,
            test_sources={"test_benchmark.py": source},
            timeout_s=30,
            per_test_timeout_s=3,
        )
    )


def valid_run(run: RunResult) -> bool:
    return (
        bool(run.outcomes)
        and not run.collection_error
        and not run.timed_out
        and all(case.status is not TestStatus.TIMEOUT for case in run.outcomes)
    )


def evaluate(
    task: Task, settings: Settings, strategy: Strategy, runner: SuiteRunner, max_mutants: int
) -> Result:
    result = Result(task.task_id)
    try:
        with tempfile.TemporaryDirectory(prefix="pytestgen-mbpp-") as tmp:
            target = Path(tmp) / "solution.py"
            target.write_text(task.code, encoding="utf-8")
            held_out = held_out_tests(task)
            reference = run_tests(runner, target, held_out)
            if not valid_run(reference) or len(reference.passed) != len(task.tests):
                result.status = "invalid_reference"
                return result
            try:
                generated = generate_source(target, settings, strategy, result)
            except PytestgenError as exc:
                generated = ""
                result.status = "error"
                result.detail = str(exc)
            passing: set[str] = set()
            if generated:
                reference_generated = run_tests(runner, target, generated)
                if valid_run(reference_generated):
                    result.generated_executed = len(reference_generated.outcomes)
                    passing = {test.nodeid for test in reference_generated.passed}
                    result.passed_reference = len(passing)
                else:
                    result.status = "unverified_generated"
            if not passing and result.status == "ok":
                result.status = "no_passing_tests"
            check_mutations(
                task,
                runner,
                target,
                held_out,
                generated,
                passing=passing,
                max_mutants=max_mutants,
                result=result,
            )
    except (SyntaxError, OSError, PytestgenError) as exc:
        result.status = "error"
        result.detail = str(exc)
    return result


def generate_source(target: Path, settings: Settings, strategy: Strategy, result: Result) -> str:
    with build_generate(settings, use_cache=False) as generator:
        report = generator.execute(
            GenerateRequest(
                target=target,
                project_root=target.parent,
                existing_tests=(),
                strategy=strategy,
            )
        )
    result.llm_requests = report.llm_usage.requests
    result.llm_tokens = report.llm_usage.prompt_tokens + report.llm_usage.completion_tokens
    # Remove the written file from the workspace before reusing the exact source for each run.
    if report.output_path is None:
        return ""
    source = report.output_path.read_text(encoding="utf-8")
    report.output_path.unlink()
    return source


def check_mutations(
    task: Task,
    runner: SuiteRunner,
    target: Path,
    held_out: str,
    generated: str,
    *,
    passing: set[str],
    max_mutants: int,
    result: Result,
) -> None:
    eligible = 0
    killing_tests: set[str] = set()
    for mutation_id, (site, source) in zip(
        range(MAX_CANDIDATES), mutations(task.code, task.task_id), strict=False
    ):
        target.write_text(source, encoding="utf-8")
        probe = run_tests(runner, target, held_out)
        if not valid_run(probe):
            status = "invalid_mutant"
        elif not any(test.status is TestStatus.FAILED for test in probe.outcomes):
            status = "not_detected_by_mbpp"
        else:
            eligible += 1
            status, failed_nodes = mutant_status(runner, target, generated, passing)
            killing_tests.update(failed_nodes)
        result.mutants.append({"id": f"{mutation_id}:{site}", "status": status})
        if eligible >= max_mutants:
            break
    result.tests_with_kills = sorted(killing_tests)


def mutant_status(
    runner: SuiteRunner, target: Path, generated: str, passing: set[str]
) -> tuple[str, set[str]]:
    if not passing:
        return "survived", set()
    run = run_tests(runner, target, generated)
    executed = {
        test.nodeid
        for test in run.outcomes
        if test.status in (TestStatus.PASSED, TestStatus.FAILED)
    }
    if not valid_run(run) or not passing <= executed:
        return "unverified_mutant", set()
    failed = {
        test.nodeid
        for test in run.outcomes
        if test.nodeid in passing and test.status is TestStatus.FAILED
    }
    return ("killed" if failed else "survived"), failed


def summarize(rows: list[Result]) -> dict[str, int | float | None]:
    tests = sum(row.generated_executed for row in rows)
    passed = sum(row.passed_reference for row in rows)
    tests_with_kills = sum(len(row.tests_with_kills) for row in rows)
    killed = sum(mutant["status"] == "killed" for row in rows for mutant in row.mutants)
    survived = sum(mutant["status"] == "survived" for row in rows for mutant in row.mutants)
    unverified = sum(
        mutant["status"] == "unverified_mutant" for row in rows for mutant in row.mutants
    )
    eligible = killed + survived + unverified
    return {
        "tasks": len(rows),
        "tasks_with_passing_tests": sum(row.passed_reference > 0 for row in rows),
        "tasks_with_kills": sum(
            any(mutant["status"] == "killed" for mutant in row.mutants) for row in rows
        ),
        "invalid_references": sum(row.status == "invalid_reference" for row in rows),
        "generation_errors": sum(row.status == "error" for row in rows),
        "unverified_generated_suites": sum(row.status == "unverified_generated" for row in rows),
        "generated_executed": tests,
        "passed_reference": passed,
        "reference_pass_rate": passed / tests if tests else None,
        "tests_passing_reference_and_killing_mutant": tests_with_kills,
        "reference_pass_and_kill_rate": tests_with_kills / tests if tests else None,
        "eligible_mutants": eligible,
        "killed": killed,
        "survived": survived,
        "unverified_mutants": unverified,
        "llm_requests": sum(row.llm_requests for row in rows),
        "mutation_score": killed / eligible if eligible else None,
        "llm_tokens": sum(row.llm_tokens for row in rows),
    }


def combine(paths: list[Path]) -> dict[str, object]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(paths)]
    if not reports:
        raise ValueError("no shard reports found")
    keys = ("dataset_revision", "seed", "strategy", "model", "max_mutants")
    metadata = {key: reports[0][key] for key in keys}
    if any({key: report[key] for key in keys} != metadata for report in reports):
        raise ValueError("shard configurations differ")
    rows = [Result(**row) for report in reports for row in report["tasks"]]
    if len({row.task_id for row in rows}) != len(rows):
        raise ValueError("duplicate MBPP task IDs across shards")
    rows.sort(key=lambda row: row.task_id)
    return {**metadata, "summary": summarize(rows), "tasks": [asdict(row) for row in rows]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("out/mbpp.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("out/mbpp-report.json"))
    parser.add_argument(
        "--combine", type=Path, help="Merge shard JSON reports under this directory"
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--mutants", type=int, default=3)
    parser.add_argument(
        "--strategy", type=Strategy, choices=list(Strategy), default=Strategy.HYBRID
    )
    args = parser.parse_args()
    if args.combine is not None:
        report = combine(list(args.combine.rglob("mbpp-shard-*.json")))
        if len(report["tasks"]) != args.limit:
            parser.error(f"expected {args.limit} tasks across shards")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return
    if not 1 <= args.mutants <= MAX_CANDIDATES:
        parser.error(f"--mutants must be in 1..{MAX_CANDIDATES}")
    tasks = select_tasks(load_tasks(args.dataset), args.limit, args.shard, args.shards)
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    runner = SubprocessSuiteRunner()
    rows: list[Result] = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        row = evaluate(task, settings, args.strategy, runner, args.mutants)
        rows.append(row)
        _log.info("mbpp_task", task_id=row.task_id, status=row.status, passed=row.passed_reference)
        report = {
            "dataset_revision": REVISION,
            "seed": SEED,
            "strategy": args.strategy.value,
            "model": settings.llm_model,
            "max_mutants": args.mutants,
            "summary": summarize(rows),
            "tasks": [asdict(item) for item in rows],
        }
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
