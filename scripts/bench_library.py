"""Bounded python-slugify 8.0.4 evaluation; upstream tests are execution-only inputs.

Install text-unidecode==1.3 separately in the interpreter running this script.
--source-root is a verified download/extraction cache (default: ignored out/).
--baseline-only makes no LLM calls. Normal runs use the configured PYTESTGEN_LLM_*
endpoint/model with fixed budgets below; a seed does not guarantee model determinism.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import tarfile
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import TextIO, cast
from urllib.request import urlopen

from pytestgen.application.generate import GenerateRequest
from pytestgen.config import Settings
from pytestgen.container import build_generate
from pytestgen.domain.models import (
    CoverageSummary,
    FileCoverage,
    GenerationReport,
    RunResult,
    Strategy,
    TestStatus,
)
from pytestgen.errors import PytestgenError
from pytestgen.log import configure_logging, get_logger
from pytestgen.ports import RunRequest, SuiteRunner
from pytestgen.report._plain import to_plain
from pytestgen.sandbox import SubprocessSuiteRunner

REVISION = "f85f9488520148d5f6899b5639199882b605e30a"
ARCHIVE_URL = f"https://codeload.github.com/un33k/python-slugify/tar.gz/{REVISION}"
ARCHIVE_SHA256 = "976b48a3bea5356c64e973d1996ccaf9ae8a48bf109fb359384484f5f19b4fc9"
ARCHIVE_PREFIX = f"python-slugify-{REVISION}"
TARGET = "slugify/slugify.py"
FUNCTIONS = ("slugify", "smart_truncate")
UPSTREAM_TESTS = ("test.py",)
MAX_ARCHIVE_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 128
GENERATED_NAME = "test_slugify_pytestgen.py"
BUDGETS = {
    "llm_timeout_s": 60.0,
    "llm_max_retries": 0,
    "repair_attempts": 1,
    "gap_iterations": 1,
    "max_cases_per_function": 8,
    "run_timeout_s": 60.0,
    "test_timeout_s": 3.0,
    "record_timeout_s": 10.0,
    "z3_timeout_ms": 2000,
}
_log = get_logger(__name__)


def redact(text: str, settings: Settings) -> str:
    text = text.replace(settings.llm_base_url, "[LLM endpoint]")
    if settings.llm_api_key is not None:
        key = settings.llm_api_key.get_secret_value()
        if key:
            text = text.replace(key, "[API key]")
    return text


class RedactedLog(io.TextIOBase):
    def __init__(self, stream: TextIO, settings: Settings) -> None:
        self._stream = stream
        self._settings = settings

    def write(self, text: str) -> int:
        self._stream.write(redact(text, self._settings))
        return len(text)

    def flush(self) -> None:
        if not self._stream.closed:
            self._stream.flush()


def application_fingerprint() -> str:
    """Identify working-tree application code and templates, not just its package version."""
    source = Path(__file__).resolve().parents[1] / "src" / "pytestgen"
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".j2"}:
            digest.update(path.relative_to(source).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def verify_archive(data: bytes) -> None:
    if len(data) > MAX_ARCHIVE_BYTES or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("python-slugify archive SHA-256/size mismatch")


def archive_files(data: bytes) -> dict[str, bytes]:
    """Read regular files only; never invoke tar extraction or upstream setup.py."""
    files: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for index, member in enumerate(archive):
            parts = PurePosixPath(member.name).parts
            if (
                index >= MAX_MEMBERS
                or not parts
                or parts[0] != ARCHIVE_PREFIX
                or ".." in parts
                or any("\\" in p or ":" in p or p.endswith((".", " ")) for p in parts)
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError(f"unsafe archive member: {member.name}")
            if member.isdir():
                continue
            total += member.size
            if member.size < 0 or total > MAX_SOURCE_BYTES or len(parts) < 2:
                raise ValueError("invalid archive size/path")
            name = PurePosixPath(*parts[1:]).as_posix()
            if name in files:
                raise ValueError(f"duplicate archive member: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"unreadable archive member: {name}")
            with stream:
                files[name] = stream.read()
    if not {TARGET, "LICENSE", *UPSTREAM_TESTS} <= files.keys():
        raise ValueError("archive lacks target, license or full upstream tests")
    return files


def load_source(cache: Path) -> Path:
    """Verify cached archive and every extracted file, including upstream tests/license."""
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "python-slugify-8.0.4.tar.gz"
    if archive.is_symlink():
        raise ValueError("archive cache must not be a symlink")
    if archive.exists():
        data = archive.read_bytes()
        verify_archive(data)
    else:
        with urlopen(ARCHIVE_URL, timeout=30) as response:
            data = response.read(MAX_ARCHIVE_BYTES + 1)
        verify_archive(data)
        archive.write_bytes(data)
    files = archive_files(data)
    root = cache / "source"
    if root.is_symlink():
        raise ValueError("source cache must not be a symlink")
    root.mkdir(exist_ok=True)
    for path in root.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.relative_to(root).as_posix() not in files):
            raise ValueError(f"unexpected source cache entry: {path}")
    for name, contents in files.items():
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"source path escapes cache: {name}")
        if path.exists():
            if not path.is_file() or path.read_bytes() != contents:
                raise ValueError(f"modified source cache file: {name}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
    return root


def dependency_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in (
        "pytestgen",
        "pytest",
        "coverage",
        "pytest-timeout",
        "text-unidecode",
        "Unidecode",
        "z3-solver",
        "httpx",
        "pydantic",
    ):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def require_backend(packages: dict[str, str | None]) -> None:
    if packages["text-unidecode"] != "1.3" or packages["Unidecode"] is not None:
        raise ValueError(
            "use text-unidecode==1.3 in this interpreter with no Unidecode installed; "
            "install it separately (no dependency/lock changes are needed)"
        )


def valid_run(run: RunResult) -> bool:
    return bool(
        run.passed
        and not run.failed
        and not run.collection_error
        and not run.timed_out
        and run.coverage is not None
    )


def coverage_metrics(cov: FileCoverage | None) -> dict[str, object] | None:
    if cov is None:
        return None
    return {
        "scope": TARGET,
        "covered_lines": len(cov.executed_lines),
        "total_lines": cov.num_statements,
        "line_percent": 100 * cov.line_rate if cov.num_statements else None,
        "covered_branches": len(cov.executed_arcs),
        "total_branches": cov.num_branches,
        "branch_percent": 100 * cov.branch_rate if cov.num_branches else None,
        "executed_lines": sorted(cov.executed_lines),
        "missing_lines": sorted(cov.missing_lines),
        "executed_arcs": to_plain(cov.executed_arcs),
        "missing_arcs": to_plain(cov.missing_arcs),
    }


def run_metrics(run: RunResult) -> dict[str, object]:
    return {
        "verified": valid_run(run),
        "counts": {
            status.value: sum(t.status is status for t in run.outcomes) for status in TestStatus
        },
        "collected": len(run.outcomes),
        "duration_s": run.duration_s,
        "timed_out": run.timed_out,
        "collection_error": run.collection_error,
        "coverage": coverage_metrics(run.coverage),
        "outcomes": to_plain(run.outcomes),
        "output_tail": run.output_tail,
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(to_plain(value), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def run_suite(
    runner: SuiteRunner,
    root: Path,
    settings: Settings,
    output: Path,
    *,
    existing: tuple[Path, ...] = (),
    generated: str | None = None,
) -> RunResult:
    run = runner.run(
        RunRequest(
            project_root=root,
            target=root / TARGET,
            existing_tests=existing,
            test_sources={GENERATED_NAME: generated} if generated is not None else {},
            timeout_s=settings.run_timeout_s,
            per_test_timeout_s=settings.test_timeout_s,
        )
    )
    write_json(output.with_suffix(".json"), run_metrics(run))
    output.with_suffix(".log").write_text(run.output_tail, encoding="utf-8")
    return run


def coverage_gain(before: RunResult, after: RunResult) -> dict[str, object] | None:
    left, right = before.coverage, after.coverage
    if not valid_run(before) or not valid_run(after) or left is None or right is None:
        return None
    if (left.num_statements, left.num_branches) != (right.num_statements, right.num_branches):
        return None
    return {
        "line_percentage_points": 100 * (right.line_rate - left.line_rate),
        "branch_percentage_points": 100 * (right.branch_rate - left.branch_rate),
        "new_lines": sorted(right.executed_lines - left.executed_lines),
        "new_arcs": to_plain(right.executed_arcs - left.executed_arcs),
        "lost_lines": sorted(left.executed_lines - right.executed_lines),
        "lost_arcs": to_plain(left.executed_arcs - right.executed_arcs),
    }


@dataclass
class ModeResult:
    mode: str
    status: str = "error"
    detail: str = ""
    wall_s: float = 0.0
    generation: GenerationReport | None = None
    generated: dict[str, object] | None = None
    combined: dict[str, object] | None = None
    gain_over_upstream: dict[str, object] | None = None
    llm_usage: dict[str, object] | None = None


def evaluate_mode(
    mode: str,
    root: Path,
    output: Path,
    settings: Settings,
    *,
    strategy: Strategy,
    runner: SuiteRunner,
    baseline: RunResult,
) -> ModeResult:
    started = time.monotonic()
    row = ModeResult(mode)
    output.mkdir(parents=True, exist_ok=False)
    upstream = tuple(root / name for name in UPSTREAM_TESTS)
    try:
        if mode == "existing" and not valid_run(baseline):
            row.status = "invalid_baseline"
            row.detail = "Full upstream suite failed verification; existing mode was not attempted."
            return row
        with build_generate(settings, use_cache=False) as generator:
            row.generation = generator.execute(
                GenerateRequest(
                    target=root / TARGET,
                    project_root=root,
                    output=output / GENERATED_NAME,
                    functions=FUNCTIONS,
                    existing_tests=upstream if mode == "existing" else (),
                    strategy=strategy,
                )
            )
        report = row.generation
        baseline_matches = mode != "existing" or report.delta.before == CoverageSummary.from_file(
            baseline.coverage
        )
        row.llm_usage = to_plain(report.llm_usage)
        write_json(output / "generation.json", report)
        if report.output_path is None:
            if report.kept_tests:
                raise ValueError("application retained tests but did not write a test file")
            row.status = "no_generated_tests"
            return row
        source = report.output_path.read_text(encoding="utf-8")
        standalone = run_suite(runner, root, settings, output / "generated", generated=source)
        combined = run_suite(
            runner, root, settings, output / "combined", existing=upstream, generated=source
        )
        row.generated, row.combined = run_metrics(standalone), run_metrics(combined)
        row.status = (
            "ok"
            if baseline_matches and valid_run(standalone) and valid_run(combined)
            else "unverified"
        )
        if not baseline_matches:
            row.detail = (
                "Application baseline differs from the independently verified upstream baseline."
            )
        if row.status == "ok":
            row.gain_over_upstream = coverage_gain(baseline, combined)
    except (OSError, ValueError, PytestgenError) as exc:
        row.detail = redact(str(exc), settings)
    finally:
        row.wall_s = time.monotonic() - started
        write_json(output / "result.json", row)
    return row


def baseline_note(run: RunResult) -> str:
    if not valid_run(run) or run.coverage is None:
        return "Upstream baseline is not verified; no incremental gain claim is valid."
    cov = run.coverage
    if cov.line_rate >= 0.9 and cov.branch_rate >= 0.9:
        return "Upstream coverage is already high; little coverage gain is possible."
    return "Incremental gain must be measured against the complete upstream suite."


def write_summary(output: Path, baseline: RunResult, rows: list[ModeResult]) -> None:
    cov = baseline.coverage
    lines = [
        "# python-slugify 8.0.4 evaluation",
        "",
        f"Coverage scope: `{TARGET}`; generation targets: {', '.join(FUNCTIONS)}.",
        f"Full upstream test.py: {len(baseline.passed)}/{len(baseline.outcomes)} passed.",
        baseline_note(baseline),
        "",
    ]
    if cov is not None:
        lines.append(
            f"Baseline lines: {len(cov.executed_lines)}/{cov.num_statements}; "
            f"branches: {len(cov.executed_arcs)}/{cov.num_branches}."
        )
    if not rows:
        lines.append("Baseline only: generation was not run; LLM requests: 0.")
    for row in rows:
        lines += ["", f"## {row.mode}: {row.status}", row.detail]
        if row.generation is not None:
            report = row.generation
            lines.append(
                f"Retained: {report.kept_tests}; dropped: {len(report.dropped)}; "
                f"LLM requests: {report.llm_usage.requests}; "
                f"tokens: {report.llm_usage.prompt_tokens + report.llm_usage.completion_tokens}; "
                f"wall: {row.wall_s:.3f}s."
            )
        if row.gain_over_upstream is not None:
            lines.append(f"Measured gain over upstream: `{json.dumps(row.gain_over_upstream)}`.")
    lines += [
        "",
        "Cold mode has existing_tests=(); its standalone coverage is not incremental gain.",
        "Generated files are rerun alone and with the full upstream suite; no failure filtering.",
        "Upstream test source is withheld from prompts. Generated tests may be repaired.",
        "Coverage/pass counts do not establish assertion quality or generalize to other libraries.",
        "Runner logs are retained output tails, not complete subprocess transcripts.",
        "See report.json and per-mode JSON for counts, coverage, outcomes, budgets and usage.",
    ]
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def benchmark(
    cache: Path,
    output: Path,
    settings: Settings,
    mode: str,
    strategy: Strategy,
    *,
    baseline_only: bool,
) -> dict[str, object]:
    started = time.monotonic()
    report: dict[str, object] = {
        "library": "python-slugify",
        "version": "8.0.4",
        "release": "https://github.com/un33k/python-slugify/tree/v8.0.4",
        "revision": REVISION,
        "pypi_release": "https://pypi.org/project/python-slugify/8.0.4/",
        "application_sha256": application_fingerprint(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "archive_url": ARCHIVE_URL,
        "archive_sha256": ARCHIVE_SHA256,
        "target": TARGET,
        "functions": FUNCTIONS,
        "upstream_tests": UPSTREAM_TESTS,
        "mode": mode,
        "baseline_only": baseline_only,
        "strategy": strategy.value,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": dependency_versions(),
        # Endpoint URLs may embed credentials: retain model and settings but not endpoint/key.
        "settings": {
            k: v
            for k, v in settings.public_dict().items()
            if k not in {"llm_base_url", "llm_api_key"}
        },
        "llm_cache": False,
        "decoding_note": (
            "The application sets decoding on each ChatRequest (default seed 42); "
            "Settings llm_temperature/llm_max_tokens/llm_seed are not applied by build_generate. "
            "Application fingerprint identifies these parameters and prompts. "
            "Model identifier/seed alone do not guarantee deterministic model output."
        ),
        "usage_note": (
            "Per-mode usage comes from the application report; null means unavailable after "
            "a generation error, never zero. Requests include failed calls, tokens count responses."
        ),
        "upstream_test_source_in_prompts": False,
        "modes": [],
        "status": "error",
    }
    rows: list[ModeResult] = []
    try:
        root = load_source(cache)
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("output and extracted source must be disjoint")
        report["source_root"] = str(root)
        report["license"] = str(root / "LICENSE")
        require_backend(dependency_versions())
        runner = SubprocessSuiteRunner()
        baseline = run_suite(
            runner,
            root,
            settings,
            output / "baseline",
            existing=tuple(root / n for n in UPSTREAM_TESTS),
        )
        report["baseline"] = run_metrics(baseline)
        report["baseline_note"] = baseline_note(baseline)
        write_json(output / "report.json", report)
        if not baseline_only:
            for selected in ("existing", "cold") if mode == "both" else (mode,):
                rows.append(
                    evaluate_mode(
                        selected,
                        root,
                        output / selected,
                        settings,
                        strategy=strategy,
                        runner=runner,
                        baseline=baseline,
                    )
                )
                report["modes"] = to_plain(rows)
                write_json(output / "report.json", report)
        report["status"] = (
            "ok"
            if valid_run(baseline) and all(r.status in {"ok", "no_generated_tests"} for r in rows)
            else "unverified"
        )
        write_summary(output, baseline, rows)
    except (OSError, ValueError, tarfile.TarError, PytestgenError) as exc:
        report["error"] = redact(str(exc), settings)
        _log.error("library_benchmark_error", error=report["error"])
    finally:
        report["wall_s"] = time.monotonic() - started
        write_json(output / "report.json", report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("out/library-source"),
        help="Verified archive and source cache",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/library-benchmark"),
        help="New or empty result directory",
    )
    parser.add_argument("--mode", choices=("existing", "cold", "both"), default="both")
    parser.add_argument(
        "--strategy", type=Strategy, choices=list(Strategy), default=Strategy.HYBRID
    )
    parser.add_argument(
        "--baseline-only", action="store_true", help="Run full upstream suite without LLM calls"
    )
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error("--output must be new or empty; previous evidence is never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    settings = Settings().model_copy(update=BUDGETS)
    with (output / "benchmark.log").open("w", encoding="utf-8") as log:
        configure_logging(
            settings.log_level, settings.log_format, stream=cast(TextIO, RedactedLog(log, settings))
        )
        report = benchmark(
            args.source_root,
            output,
            settings,
            args.mode,
            args.strategy,
            baseline_only=args.baseline_only,
        )
    configure_logging()
    if report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
