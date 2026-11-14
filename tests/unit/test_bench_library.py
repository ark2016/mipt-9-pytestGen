"""Library benchmark checks: fake downloads/LLM, real application and subprocess runner."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from pytestgen.config import Settings
from pytestgen.domain.llm import ChatRequest, ChatResponse, TokenUsage
from pytestgen.domain.models import (
    CoverageDelta,
    CoverageSummary,
    FileCoverage,
    GenerationReport,
    RunResult,
    Strategy,
    TestOutcome,
    TestStatus,
)
from pytestgen.errors import PytestgenError
from pytestgen.sandbox import SubprocessSuiteRunner


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "bench_library.py"
    spec = importlib.util.spec_from_file_location("bench_library", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()
SOURCE = """\
def slugify(text: str, lowercase: bool = True) -> str:
    if lowercase:
        text = text.lower()
    return text.replace(' ', '-')


def smart_truncate(string: str, max_length: int = 0) -> str:
    return string[:max_length] if max_length else string
"""
UPSTREAM = """\
from slugify.slugify import slugify


def test_upstream():
    private_marker = 'UPSTREAM_TEST_SOURCE_MUST_STAY_PRIVATE'
    assert private_marker
    assert slugify('HI there') == 'hi-there'
"""
GENERATED = """\
from slugify.slugify import slugify, smart_truncate


def test_generated_slugify():
    assert slugify('HI there', lowercase=False) == 'HI-there'


def test_generated_truncate():
    assert smart_truncate('abc', 2) == 'ab'
"""


def _tar(*extra: tarfile.TarInfo) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, contents in {
            bench.TARGET: SOURCE,
            "slugify/__init__.py": "",
            "LICENSE": "upstream license retained verbatim\n",
            "test.py": UPSTREAM,
        }.items():
            raw = contents.encode()
            member = tarfile.TarInfo(f"{bench.ARCHIVE_PREFIX}/{name}")
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        for member in extra:
            archive.addfile(member, io.BytesIO(b""))
    return buffer.getvalue()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "slugify").mkdir(parents=True)
    (root / "slugify" / "__init__.py").write_text("", encoding="utf-8")
    (root / bench.TARGET).write_text(SOURCE, encoding="utf-8")
    (root / "test.py").write_text(UPSTREAM, encoding="utf-8")
    return root


def _verified() -> RunResult:
    return RunResult(
        outcomes=(TestOutcome("test.py::test_upstream", TestStatus.PASSED),),
        coverage=FileCoverage(
            bench.TARGET, executed_lines=frozenset({1}), missing_lines=frozenset({2})
        ),
    )


def _report(output: Path | None, kept: int = 1) -> GenerationReport:
    cov = CoverageSummary(1, 2, 0, 0)
    return GenerationReport("slugify.slugify", Strategy.LLM, output, CoverageDelta(cov, cov), kept)


def test_verified_download_cache_and_license(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = _tar()
    monkeypatch.setattr(bench, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())
    download = MagicMock(return_value=io.BytesIO(data))
    monkeypatch.setattr(bench, "urlopen", download)
    root = bench.load_source(tmp_path)
    assert (root / "LICENSE").read_text() == "upstream license retained verbatim\n"
    assert (root / "test.py").read_text() == UPSTREAM
    assert bench.load_source(tmp_path) == root
    download.assert_called_once_with(bench.ARCHIVE_URL, timeout=30)
    (root / bench.TARGET).write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="modified source cache"):
        bench.load_source(tmp_path)


def test_checksum_failure_never_extracts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bench, "urlopen", MagicMock(return_value=io.BytesIO(_tar())))
    with pytest.raises(ValueError, match="SHA-256"):
        bench.load_source(tmp_path)
    assert not (tmp_path / "source").exists()
    assert not (tmp_path / "python-slugify-8.0.4.tar.gz").exists()


@pytest.mark.parametrize(
    "kind", ["traversal", "absolute", "backslash", "drive", "symlink", "hardlink"]
)
def test_rejects_unsafe_archive_members(kind: str) -> None:
    name = {
        "traversal": f"{bench.ARCHIVE_PREFIX}/../escaped.py",
        "absolute": "/escaped.py",
        "backslash": f"{bench.ARCHIVE_PREFIX}/..\\escaped.py",
        "drive": f"{bench.ARCHIVE_PREFIX}/C:escaped.py",
    }.get(kind, f"{bench.ARCHIVE_PREFIX}/link")
    member = tarfile.TarInfo(name)
    if kind in {"symlink", "hardlink"}:
        member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
        member.linkname = "../outside"
    with pytest.raises(ValueError, match="unsafe archive member"):
        bench.archive_files(_tar(member))


def test_extra_cached_code_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = _tar()
    monkeypatch.setattr(bench, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(bench, "urlopen", MagicMock(return_value=io.BytesIO(data)))
    root = bench.load_source(tmp_path)
    (root / "conftest.py").write_text("raise RuntimeError()", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected source cache"):
        bench.load_source(tmp_path)


@pytest.mark.parametrize("mode", ["existing", "cold"])
def test_actual_pipeline_and_reruns_with_fake_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project: Path, mode: str
) -> None:
    from pytestgen.llm import OpenAICompatClient

    requests: list[ChatRequest] = []

    def complete(request: ChatRequest) -> ChatResponse:
        requests.append(request)
        assert all(
            "UPSTREAM_TEST_SOURCE_MUST_STAY_PRIVATE" not in m.content for m in request.messages
        )
        return ChatResponse(GENERATED, "mock-model", TokenUsage(10, 20))

    client = MagicMock()
    client.model = "mock-model"
    client.complete.side_effect = complete
    monkeypatch.setattr(OpenAICompatClient, "from_settings", lambda _: client)
    settings = Settings(gap_iterations=0, repair_attempts=0, run_timeout_s=30)
    runner = SubprocessSuiteRunner()
    baseline = bench.run_suite(
        runner, project, settings, tmp_path / "baseline", existing=(project / "test.py",)
    )
    assert bench.valid_run(baseline)
    row = bench.evaluate_mode(
        mode,
        project,
        tmp_path / mode,
        settings,
        strategy=Strategy.LLM,
        runner=runner,
        baseline=baseline,
    )
    assert row.status == "ok", row.detail
    assert row.generation.output_path.is_file()
    assert row.generated["collected"] == row.generation.kept_tests == 4
    assert row.combined["collected"] == row.generated["collected"] + 1
    assert row.llm_usage == {
        "requests": 2,
        "cache_hits": 0,
        "prompt_tokens": 20,
        "completion_tokens": 40,
    }
    assert len(requests) == 2
    before = row.generation.delta.before
    assert before.covered_lines == (
        len(baseline.coverage.executed_lines) if mode == "existing" else 0
    )
    assert row.gain_over_upstream["new_lines"]
    assert (tmp_path / mode / "generated.log").is_file()
    assert (tmp_path / mode / "generation.json").is_file()
    assert not (project / "tests").exists()
    assert "slugify.slugify" not in sys.modules
    client.close.assert_called_once()


def test_invalid_existing_baseline_prevents_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    build = MagicMock()
    monkeypatch.setattr(bench, "build_generate", build)
    row = bench.evaluate_mode(
        "existing",
        tmp_path,
        tmp_path / "existing",
        Settings(),
        strategy=Strategy.HYBRID,
        runner=MagicMock(),
        baseline=RunResult(),
    )
    assert row.status == "invalid_baseline"
    assert row.llm_usage is None
    build.assert_not_called()


def test_failing_generated_rerun_does_not_claim_gain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project: Path
) -> None:
    output = tmp_path / "existing"

    def generate(request: object) -> GenerationReport:
        path = output / bench.GENERATED_NAME
        path.write_text(GENERATED, encoding="utf-8")
        return _report(path)

    build = MagicMock()
    build.return_value.__enter__.return_value.execute.side_effect = generate
    monkeypatch.setattr(bench, "build_generate", build)
    runner = MagicMock()
    failure = RunResult(
        outcomes=(TestOutcome("generated::test_bad", TestStatus.FAILED),),
        coverage=_verified().coverage,
    )
    runner.run.side_effect = [failure, _verified()]
    row = bench.evaluate_mode(
        "existing",
        project,
        output,
        Settings(),
        strategy=Strategy.LLM,
        runner=runner,
        baseline=_verified(),
    )
    assert row.status == "unverified"
    assert row.generated["counts"]["failed"] == 1
    assert row.gain_over_upstream is None
    request = build.return_value.__enter__.return_value.execute.call_args.args[0]
    assert request.functions == ("slugify", "smart_truncate")
    assert request.existing_tests == (project / "test.py",)
    assert all(
        c.args[0].test_sources[bench.GENERATED_NAME] == GENERATED for c in runner.run.call_args_list
    )


def test_empty_generation_is_not_a_perfect_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    build = MagicMock()
    build.return_value.__enter__.return_value.execute.return_value = _report(None, kept=0)
    monkeypatch.setattr(bench, "build_generate", build)
    runner = MagicMock()
    row = bench.evaluate_mode(
        "cold",
        tmp_path,
        tmp_path / "cold",
        Settings(),
        strategy=Strategy.LLM,
        runner=runner,
        baseline=_verified(),
    )
    assert row.status == "no_generated_tests"
    assert row.generated is None and row.gain_over_upstream is None
    assert bench.coverage_metrics(None) is None
    runner.run.assert_not_called()
    request = build.return_value.__enter__.return_value.execute.call_args.args[0]
    assert request.existing_tests == ()


def test_generation_error_preserves_unknown_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    build = MagicMock()
    build.return_value.__enter__.return_value.execute.side_effect = PytestgenError(
        "failed after calls"
    )
    monkeypatch.setattr(bench, "build_generate", build)
    row = bench.evaluate_mode(
        "cold",
        tmp_path,
        tmp_path / "cold",
        Settings(),
        strategy=Strategy.LLM,
        runner=MagicMock(),
        baseline=_verified(),
    )
    saved = json.loads((tmp_path / "cold" / "result.json").read_text())
    assert row.status == "error"
    assert saved["llm_usage"] is None
    assert saved["detail"] == "failed after calls"
    assert saved["wall_s"] >= 0


@pytest.mark.parametrize(
    "run", [RunResult(), RunResult(timed_out=True), RunResult(collection_error="bad")]
)
def test_missing_or_partial_results_have_no_gain(run: RunResult) -> None:
    assert not bench.valid_run(run)
    assert bench.coverage_gain(_verified(), run) is None


def test_cli_baseline_only_never_builds_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project: Path
) -> None:
    monkeypatch.setattr(bench, "load_source", lambda _: project)
    monkeypatch.setattr(bench, "require_backend", lambda _: None)
    build = MagicMock()
    monkeypatch.setattr(bench, "build_generate", build)
    runner = MagicMock()
    runner.run.return_value = _verified()
    monkeypatch.setattr(bench, "SubprocessSuiteRunner", lambda: runner)
    output = tmp_path / "result"
    bench.main(["--baseline-only", "--output", str(output)])
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "ok"
    assert report["mode"] == "both"
    assert report["modes"] == []
    assert report["archive_sha256"] == bench.ARCHIVE_SHA256
    assert "llm_api_key" not in report["settings"]
    assert (output / "benchmark.log").is_file()
    build.assert_not_called()
    with pytest.raises(SystemExit) as error:
        bench.main(["--baseline-only", "--output", str(output)])
    assert error.value.code == 2


def test_backend_pin_requires_separate_install() -> None:
    with pytest.raises(ValueError, match=r"text-unidecode==1\.3"):
        bench.require_backend({"text-unidecode": None, "Unidecode": None})
    with pytest.raises(ValueError, match="no Unidecode"):
        bench.require_backend({"text-unidecode": "1.3", "Unidecode": "1.4.0"})
    bench.require_backend({"text-unidecode": "1.3", "Unidecode": None})


def test_error_details_and_log_redact_endpoint_and_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pydantic import SecretStr

    settings = Settings(
        llm_base_url="https://user:password@example.invalid/v1",
        llm_api_key=SecretStr("private-key"),
    )
    message = f"failure {settings.llm_base_url}/chat/completions private-key"
    build = MagicMock()
    build.return_value.__enter__.return_value.execute.side_effect = PytestgenError(message)
    monkeypatch.setattr(bench, "build_generate", build)
    row = bench.evaluate_mode(
        "cold",
        tmp_path,
        tmp_path / "cold",
        settings,
        strategy=Strategy.LLM,
        runner=MagicMock(),
        baseline=_verified(),
    )
    stream = io.StringIO()
    log = bench.RedactedLog(stream, settings)
    log.write(message)
    log.flush()
    assert row.detail == stream.getvalue() == "failure [LLM endpoint]/chat/completions [API key]"
    log.close()


def test_baseline_only_failure_is_saved_and_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project: Path
) -> None:
    monkeypatch.setattr(bench, "load_source", lambda _: project)
    monkeypatch.setattr(bench, "require_backend", lambda _: None)
    runner = MagicMock()
    runner.run.return_value = RunResult(collection_error="missing library dependency")
    monkeypatch.setattr(bench, "SubprocessSuiteRunner", lambda: runner)
    output = tmp_path / "failed"
    with pytest.raises(SystemExit) as error:
        bench.main(["--baseline-only", "--output", str(output)])
    assert error.value.code == 1
    saved = json.loads((output / "report.json").read_text())
    assert saved["status"] == "unverified"
    assert saved["baseline"]["coverage"] is None
    assert saved["baseline"]["collection_error"] == "missing library dependency"


def test_both_modes_are_independent_and_keep_complete_denominators(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project: Path
) -> None:
    monkeypatch.setattr(bench, "load_source", lambda _: project)
    monkeypatch.setattr(bench, "require_backend", lambda _: None)
    runner = MagicMock()
    runner.run.return_value = _verified()
    monkeypatch.setattr(bench, "SubprocessSuiteRunner", lambda: runner)
    build = MagicMock()
    build.return_value.__enter__.return_value.execute.return_value = _report(None, kept=0)
    monkeypatch.setattr(bench, "build_generate", build)
    output = tmp_path / "both"
    bench.main(["--mode", "both", "--output", str(output)])
    saved = json.loads((output / "report.json").read_text())
    assert [row["mode"] for row in saved["modes"]] == ["existing", "cold"]
    assert [row["status"] for row in saved["modes"]] == ["no_generated_tests"] * 2
    assert saved["baseline"]["coverage"]["total_lines"] == 2
    assert saved["baseline"]["coverage"]["covered_lines"] == 1
    assert all(row["gain_over_upstream"] is None for row in saved["modes"])
    assert build.call_count == 2
    assert all(call.kwargs == {"use_cache": False} for call in build.call_args_list)
    requests = build.return_value.__enter__.return_value.execute.call_args_list
    assert requests[0].args[0].existing_tests == (project / "test.py",)
    assert requests[1].args[0].existing_tests == ()
