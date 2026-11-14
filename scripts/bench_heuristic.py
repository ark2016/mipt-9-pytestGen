"""Measure repair warnings and oracle review against independently labelled mutations.

Run with a real OpenAI-compatible endpoint. The synthetic contracts and witness tests
are public benchmark fixtures; existing user project tests are never added to prompts.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from repair_cases import CASES, MutationCase

from pytestgen.analysis import AstModuleAnalyzer
from pytestgen.application.oracle import OracleReviewer
from pytestgen.application.repair import TestRepairer
from pytestgen.application.suspicion import is_suspicious_repair
from pytestgen.config import Settings
from pytestgen.domain.llm import ChatRequest, ChatResponse
from pytestgen.domain.models import (
    CaseOrigin,
    FreeFormTest,
    ObservationKind,
    OracleVerdict,
    RunResult,
    TestCaseSpec,
    TestStatus,
)
from pytestgen.errors import PytestgenError
from pytestgen.llm import CachingLLMClient, OpenAICompatClient
from pytestgen.log import configure_logging, get_logger
from pytestgen.ports import LLMClient, RunRequest
from pytestgen.sandbox import SubprocessExecutionRecorder, SubprocessSuiteRunner

_log = get_logger(__name__)
DESIGN_VERSION = 1


class AuditLLM:
    """Store exact prompts/replies for public benchmark cases, never configuration secrets."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.calls: list[dict[str, object]] = []

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(self, request: ChatRequest) -> ChatResponse:
        entry: dict[str, object] = {"request": asdict(request)}
        self.calls.append(entry)
        try:
            response = self.inner.complete(request)
        except PytestgenError as exc:
            entry["error"] = str(exc)
            raise
        entry["response"] = asdict(response)
        return response


def run_tests(target: Path, source: str) -> RunResult:
    return SubprocessSuiteRunner().run(
        RunRequest(
            project_root=target.parent,
            target=target,
            test_sources={"test_repair.py": source},
            timeout_s=30,
            per_test_timeout_s=3,
        )
    )


def verified(run: RunResult, status: TestStatus) -> bool:
    return (
        bool(run.outcomes)
        and not run.collection_error
        and not run.timed_out
        and all(item.status is status for item in run.outcomes)
    )


def evidence(run: RunResult) -> dict[str, object]:
    return {
        "outcomes": [
            {
                "nodeid": row.nodeid,
                "status": row.status.value,
                "message": row.message,
                "traceback": row.traceback,
            }
            for row in run.outcomes
        ],
        "collection_error": run.collection_error,
        "timed_out": run.timed_out,
    }


def observes_target(source: str, run: RunResult, line: int) -> bool:
    """Reject empty/no-assert tests and tests that never execute the target return."""
    try:
        has_assert = any(
            isinstance(node, ast.Assert)
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "raises"
            )
            for node in ast.walk(ast.parse(source))
        )
    except SyntaxError:
        return False
    return bool(has_assert and run.coverage and line in run.coverage.executed_lines)


def validate_witness(
    target: Path,
    case: MutationCase,
    documented: bool,
) -> tuple[bool, dict[str, object]]:
    target.write_text(case.source(mutant=False, documented=documented), encoding="utf-8")
    good = run_tests(target, case.test(correct=True))
    bad = run_tests(target, case.test(correct=False))
    target.write_text(case.source(mutant=True, documented=documented), encoding="utf-8")
    mutant = run_tests(target, case.test(correct=True))
    valid = (
        verified(good, TestStatus.PASSED)
        and verified(bad, TestStatus.FAILED)
        and verified(mutant, TestStatus.FAILED)
    )
    return valid, {
        "good_on_reference": evidence(good),
        "bad_on_reference": evidence(bad),
        "good_on_mutant": evidence(mutant),
    }


def evaluate_repair(
    case: MutationCase,
    *,
    documented: bool,
    bug: bool,
    repairer: TestRepairer,
    attempts: int = 2,
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": f"{case.name}/{'doc' if documented else 'no-doc'}/{'bug' if bug else 'bad-test'}",
        "function": case.name,
        "documented": documented,
        "bug_in_target": bug,
        "status": "invalid_witness",
        "attempts": [],
        "predicted_suspicious": False,
        "reference_source": case.source(mutant=False, documented=documented),
        "target_source": case.source(mutant=bug, documented=documented),
        "original_test": case.test(correct=bug),
    }
    with tempfile.TemporaryDirectory(prefix="pytestgen_repair_eval_") as directory:
        target = Path(directory) / "solution.py"
        valid, validation = validate_witness(target, case, documented)
        row["witness_validation"] = validation
        if not valid:
            return row
        target.write_text(str(row["target_source"]), encoding="utf-8")
        module = AstModuleAnalyzer().analyze(target, target.parent)
        function = module.function(case.name)
        current = FreeFormTest("test_witness", case.name, str(row["original_test"]))
        outcome = run_tests(target, current.source)
        row["status"] = "not_repaired"
        history: list[dict[str, object]] = []
        row["attempts"] = history
        warning = False
        for attempt in range(attempts):
            if not outcome.failed or outcome.timed_out or outcome.collection_error:
                row["status"] = "unverified_failure"
                break
            failure = outcome.failed[0]
            try:
                fixed = repairer.repair(module, function, current, failure)
            except PytestgenError as exc:
                row["status"] = "llm_error"
                row["error"] = str(exc)
                break
            if fixed is None:
                row["status"] = "no_valid_repair"
                break
            warning = warning or is_suspicious_repair(function, failure)
            outcome = run_tests(target, fixed.source)
            history.append(
                {
                    "number": attempt + 1,
                    "source": fixed.source,
                    "warning": warning,
                    "run": evidence(outcome),
                }
            )
            current = fixed
            if verified(outcome, TestStatus.PASSED):
                row.update(repaired_test=fixed.source, predicted_suspicious=warning)
                if not observes_target(fixed.source, outcome, function.end_lineno):
                    row["status"] = "degenerate_repair"
                    row["actual_masking"] = bug
                    break
                row.update(status="classified", actual_masking=bug)
                # Running the repair on the reference distinguishes wrong oracles from
                # changing inputs/weaker assertions that cease to expose the mutation.
                target.write_text(str(row["reference_source"]), encoding="utf-8")
                on_reference = run_tests(target, fixed.source)
                row["repaired_on_reference"] = evidence(on_reference)
                if not bug and not verified(on_reference, TestStatus.PASSED):
                    row["status"] = "unverified_reference"
                    break
                row["repair_effect"] = (
                    "oracle_changed"
                    if bug and verified(on_reference, TestStatus.FAILED)
                    else "witness_evaded"
                    if bug and verified(on_reference, TestStatus.PASSED)
                    else "unverified_reference"
                    if bug
                    else "corrected_bad_test"
                )
                break
            if (
                outcome.timed_out
                or outcome.collection_error
                or not outcome.outcomes
                or any(
                    item.status in (TestStatus.SKIPPED, TestStatus.XFAILED, TestStatus.TIMEOUT)
                    for item in outcome.outcomes
                )
            ):
                row["status"] = "unverified_failure"
                break
    return row


def evaluate_oracle(
    case: MutationCase,
    *,
    documented: bool,
    bug: bool,
    reviewer: OracleReviewer,
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": f"{case.name}/{'doc' if documented else 'no-doc'}/{'mutant' if bug else 'reference'}",
        "function": case.name,
        "documented": documented,
        "actual_bug": bug,
        "status": "unverified_observation",
    }
    with tempfile.TemporaryDirectory(prefix="pytestgen_oracle_eval_") as directory:
        target = Path(directory) / "solution.py"
        target.write_text(case.source(mutant=bug, documented=documented), encoding="utf-8")
        module = AstModuleAnalyzer().analyze(target, target.parent)
        function = module.function(case.name)
        observations = SubprocessExecutionRecorder(timeout_s=5).observe(
            target.parent,
            module,
            function,
            [case.inputs],
        )
        if len(observations) != 1 or observations[0].kind not in (
            ObservationKind.RETURNED,
            ObservationKind.RAISED,
        ):
            return row
        observation = observations[0]
        if case.expected_exception:
            matches_reference = (
                observation.kind is ObservationKind.RAISED
                and (observation.exception_type or "").rsplit(".", 1)[-1] == case.expected_exception
            )
        elif observation.kind is ObservationKind.RAISED:
            matches_reference = False
        else:
            try:
                matches_reference = ast.literal_eval(
                    observation.value_repr or ""
                ) == ast.literal_eval(case.expected)
            except (SyntaxError, ValueError):
                return row
        if matches_reference == bug:
            row["status"] = "invalid_label"
            return row
        reviewed = reviewer.review(
            module,
            function,
            [TestCaseSpec(case.name, case.inputs, observation, CaseOrigin.Z3)],
        )[0]
        row.update(
            status="classified",
            verdict=reviewed.verdict.value,
            predicted_suspicious=reviewed.verdict is OracleVerdict.SUSPICIOUS,
            explanation=reviewed.note,
            observed=observation.value_repr,
            reference_expected=case.expected or f"raises {case.expected_exception}",
            observed_exception=observation.exception_type,
        )
    return row


def confusion(rows: list[dict[str, object]], truth_key: str) -> dict[str, object]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row["status"] != "classified":
            continue
        truth = bool(row[truth_key])
        predicted = bool(row["predicted_suspicious"])
        counts["tp" if truth and predicted else "fn" if truth else "fp" if predicted else "tn"] += 1
    tp, fp, tn, fn = (counts[key] for key in ("tp", "fp", "tn", "fn"))
    return {
        "n": tp + fp + tn + fn,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
    }


def summaries(
    repairs: list[dict[str, object]], oracles: list[dict[str, object]]
) -> dict[str, object]:
    inclusive = [
        {**row, "status": "classified"} if row["status"] == "degenerate_repair" else row
        for row in repairs
    ]
    return {
        "repair_scenarios": len(repairs),
        "repair_status": dict(Counter(str(row["status"]) for row in repairs)),
        "repair_all": confusion(repairs, "actual_masking"),
        "repair_all_passing_rewrites": confusion(inclusive, "actual_masking"),
        "repair_all_passing_documented": confusion(
            [r for r in inclusive if r["documented"]], "actual_masking"
        ),
        "repair_all_passing_undocumented": confusion(
            [r for r in inclusive if not r["documented"]], "actual_masking"
        ),
        "repair_attrition_by_condition": {
            f"{'doc' if doc else 'no-doc'}/{'bug' if bug else 'bad-test'}": dict(
                Counter(
                    str(r["status"])
                    for r in repairs
                    if r["documented"] == doc and r["bug_in_target"] == bug
                )
            )
            for doc in (True, False)
            for bug in (True, False)
        },
        "masked_per_valid_bug_witness": (
            sum(
                r["status"] in ("classified", "degenerate_repair")
                for r in repairs
                if r["bug_in_target"]
            )
            / sum(r["status"] != "invalid_witness" for r in repairs if r["bug_in_target"])
            if any(r["status"] != "invalid_witness" for r in repairs if r["bug_in_target"])
            else None
        ),
        "repair_documented": confusion([r for r in repairs if r["documented"]], "actual_masking"),
        "repair_undocumented": confusion(
            [r for r in repairs if not r["documented"]], "actual_masking"
        ),
        "repair_effect": dict(
            Counter(str(row.get("repair_effect", row["status"])) for row in repairs)
        ),
        "oracle_scenarios": len(oracles),
        "oracle_status": dict(Counter(str(row["status"]) for row in oracles)),
        # UNREVIEWED is an operational "no alert"; report abstentions separately.
        "oracle_all": confusion(oracles, "actual_bug"),
        "oracle_reviewed": confusion(
            [r for r in oracles if r.get("verdict") in ("ok", "suspicious")],
            "actual_bug",
        ),
        "oracle_abstentions": sum(r.get("verdict") == "unreviewed" for r in oracles),
    }


def combine(paths: list[Path]) -> dict[str, object]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(paths)]
    if not reports:
        raise ValueError("no heuristic shard reports")
    keys = ("design_version", "case_sha256", "model", "max_attempts")
    metadata = {key: reports[0][key] for key in keys}
    if any({key: report[key] for key in keys} != metadata for report in reports):
        raise ValueError("different heuristic experiment configurations")
    repairs = [row for report in reports for row in report["repairs"]]
    oracles = [row for report in reports for row in report["oracles"]]
    for rows in (repairs, oracles):
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("duplicate scenario IDs")
        rows.sort(key=lambda row: row["id"])
    return {
        **metadata,
        "summary": summaries(repairs, oracles),
        "repairs": repairs,
        "oracles": oracles,
        "usage": {
            key: sum(report["usage"][key] for report in reports)
            for key in ("requests", "prompt_tokens", "completion_tokens", "cache_hits")
        },
        "duration_s": sum(report["duration_s"] for report in reports),
        "llm_calls": [call for report in reports for call in report.get("llm_calls", [])],
        "source_commits": sorted({report.get("source_commit", "local") for report in reports}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out/heuristic-report.json"))
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--combine", type=Path)
    args = parser.parse_args()
    if not 0 <= args.shard < args.shards <= len(CASES) or not 1 <= args.attempts <= 3:
        parser.error("invalid shard or repair-attempt budget")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.combine:
        report = combine(list(args.combine.rglob("heuristic-shard-*.json")))
        if len(report["repairs"]) != 4 * len(CASES) or len(report["oracles"]) != 4 * len(CASES):
            parser.error("incomplete scenario set")
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    repairs: list[dict[str, object]] = []
    oracles: list[dict[str, object]] = []
    started = time.monotonic()
    with OpenAICompatClient.from_settings(settings) as client:
        llm = CachingLLMClient(client, None)
        audited = AuditLLM(llm)
        repairer = TestRepairer(audited)
        reviewer = OracleReviewer(audited)
        for case in CASES[args.shard :: args.shards]:
            for documented in (True, False):
                for bug in (True, False):
                    before = len(audited.calls)
                    repair_row = evaluate_repair(
                        case,
                        documented=documented,
                        bug=bug,
                        repairer=repairer,
                        attempts=args.attempts,
                    )
                    repair_row["llm_calls"] = audited.calls[before:]
                    if repair_row["status"] == "no_valid_repair" and any(
                        "error" in call for call in audited.calls[before:]
                    ):
                        repair_row["status"] = "llm_error"
                    repairs.append(repair_row)
                    before = len(audited.calls)
                    oracle_row = evaluate_oracle(
                        case, documented=documented, bug=bug, reviewer=reviewer
                    )
                    oracle_row["llm_calls"] = audited.calls[before:]
                    if oracle_row.get("verdict") == "unreviewed":
                        oracle_row["abstention_reason"] = (
                            "no_docstring"
                            if not documented
                            else "request_failed"
                            if any("error" in c for c in audited.calls[before:])
                            else "invalid_response"
                        )
                    oracles.append(oracle_row)
                    _log.info(
                        "heuristic_case", case=repairs[-1]["id"], status=repairs[-1]["status"]
                    )
                    report = {
                        "design_version": DESIGN_VERSION,
                        "source_commit": os.environ.get("GITHUB_SHA", "local"),
                        "llm_calls": audited.calls,
                        "case_sha256": hashlib.sha256(
                            Path(__file__).with_name("repair_cases.py").read_bytes()
                        ).hexdigest(),
                        "model": settings.llm_model,
                        "max_attempts": args.attempts,
                        "summary": summaries(repairs, oracles),
                        "repairs": repairs,
                        "oracles": oracles,
                        "usage": asdict(llm.usage),
                        "duration_s": time.monotonic() - started,
                    }
                    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
