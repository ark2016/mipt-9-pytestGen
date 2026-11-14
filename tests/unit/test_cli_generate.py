"""CLI wiring of ``generate`` with a fake use case: arguments, reports, exit codes."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pytestgen import cli
from pytestgen.application import GenerateRequest
from pytestgen.cli import app
from pytestgen.domain.models import (
    CoverageDelta,
    CoverageSummary,
    GenerationReport,
    Strategy,
)
from pytestgen.errors import LLMConnectionError, PytestgenError
from pytestgen.ports import ProgressListener, Stage

runner = CliRunner()


def make_report(before: int = 2, after: int = 8) -> GenerationReport:
    return GenerationReport(
        module="pkg.mod",
        strategy=Strategy.HYBRID,
        output_path=None,
        delta=CoverageDelta(CoverageSummary(before, 10, 0, 4), CoverageSummary(after, 10, 2, 4)),
        kept_tests=3,
    )


class FakeUseCase:
    def __init__(self, outcome: GenerationReport | Exception) -> None:
        self.outcome = outcome
        self.requests: list[GenerateRequest] = []

    def execute(self, request: GenerateRequest) -> GenerationReport:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Wiring:
    """Records what the CLI asked the composition root for."""

    def __init__(self, use_case: FakeUseCase) -> None:
        self.use_case = use_case
        self.use_cache: bool | None = None
        self.progress: ProgressListener | None = None
        self.closed = 0

    @contextmanager
    def build(
        self, settings: object, *, use_cache: bool = True, progress: ProgressListener | None = None
    ) -> Iterator[FakeUseCase]:
        self.use_cache, self.progress = use_cache, progress
        try:
            yield self.use_case
        finally:
            self.closed += 1


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Callable[[GenerationReport | Exception], Wiring]:
    def install(outcome: GenerationReport | Exception) -> Wiring:
        wiring = Wiring(FakeUseCase(outcome))
        monkeypatch.setattr(cli, "build_generate", wiring.build)
        return wiring

    return install


@pytest.fixture
def module(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    target = tmp_path / "pkg" / "mod.py"
    target.parent.mkdir()
    target.write_text("def f(x):\n    return x\n", encoding="utf-8")
    return target


def test_arguments_become_a_request(wire: Callable[..., Wiring], module: Path) -> None:
    wiring = wire(make_report())
    existing = module.parent / "test_old.py"
    existing.write_text("", encoding="utf-8")
    output = module.parent / "out.py"

    result = runner.invoke(
        app,
        [
            *("generate", str(module), "-f", "f", "-f", "C", "--strategy", "llm", "--minimize"),
            *("--no-cache", "--dry-run", "--overwrite", "--existing-tests", str(existing)),
            *("-o", str(output)),
        ],
    )

    assert result.exit_code == 0, result.output
    (request,) = wiring.use_case.requests
    assert request.target == module.resolve()
    assert request.project_root == module.parent.parent.resolve()  # pyproject.toml marker
    assert request.functions == ("f", "C")
    assert request.strategy is Strategy.LLM
    assert request.minimize and request.dry_run and request.overwrite
    assert request.existing_tests == (existing.resolve(),)
    assert request.output == output.resolve()
    assert wiring.use_cache is False
    assert wiring.closed == 1


def test_defaults_discover_tests_and_use_the_cache(
    wire: Callable[..., Wiring], module: Path
) -> None:
    wiring = wire(make_report())

    result = runner.invoke(app, ["generate", str(module)])

    assert result.exit_code == 0, result.output
    (request,) = wiring.use_case.requests
    assert request.existing_tests is None
    assert request.strategy is Strategy.HYBRID
    assert request.output is None
    assert wiring.use_cache is True
    assert "Coverage" in result.stdout


def test_no_existing_tests_means_an_empty_baseline(
    wire: Callable[..., Wiring], module: Path
) -> None:
    wiring = wire(make_report())
    runner.invoke(app, ["generate", str(module), "--no-existing-tests"])
    assert wiring.use_case.requests[0].existing_tests == ()


def test_existing_tests_conflict_with_no_existing_tests(
    wire: Callable[..., Wiring], module: Path
) -> None:
    wiring = wire(make_report())
    result = runner.invoke(
        app, ["generate", str(module), "--no-existing-tests", "--existing-tests", str(module)]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr
    assert not wiring.use_case.requests


@pytest.mark.parametrize("error", [PytestgenError("module is broken"), LLMConnectionError("down")])
def test_expected_errors_exit_with_two_and_no_traceback(
    wire: Callable[..., Wiring], module: Path, error: PytestgenError
) -> None:
    wiring = wire(error)

    result = runner.invoke(app, ["generate", str(module)])

    assert result.exit_code == 2
    assert f"error: {error}" in result.stderr
    assert "Traceback" not in result.output
    assert wiring.closed == 1  # the HTTP client is closed on failure too


def test_error_markup_in_messages_is_not_interpreted(
    wire: Callable[..., Wiring], module: Path
) -> None:
    wire(PytestgenError("unknown name [bold]x[/bold]"))
    result = runner.invoke(app, ["generate", str(module)])
    assert "[bold]x[/bold]" in result.stderr


def test_unexpected_errors_are_not_swallowed(wire: Callable[..., Wiring], module: Path) -> None:
    wire(RuntimeError("bug"))
    result = runner.invoke(app, ["generate", str(module)])
    assert isinstance(result.exception, RuntimeError)


def test_progress_is_printed_to_stdout(monkeypatch: pytest.MonkeyPatch, module: Path) -> None:
    class Announcing(FakeUseCase):
        def execute(self, request: GenerateRequest) -> GenerationReport:
            assert wiring.progress is not None
            wiring.progress.stage(Stage.SOLVE, "solving [x] things")
            return super().execute(request)

    wiring = Wiring(Announcing(make_report()))
    monkeypatch.setattr(cli, "build_generate", wiring.build)

    result = runner.invoke(app, ["generate", str(module)])

    assert "solve" in result.stdout
    assert "solving [x] things" in result.stdout


def test_report_files_are_written_as_utf8(
    wire: Callable[..., Wiring], module: Path, tmp_path: Path
) -> None:
    wire(make_report())
    md, js = tmp_path / "r" / "report.md", tmp_path / "r" / "nested" / "report.json"

    result = runner.invoke(
        app, ["generate", str(module), "--report-md", str(md), "--report-json", str(js)]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(js.read_text(encoding="utf-8"))["module"] == "pkg.mod"
    assert "pkg.mod" in md.read_text(encoding="utf-8")
    assert b"\r\n" not in md.read_bytes()


def test_unwritable_report_path_is_a_clean_error(wire: Callable[..., Wiring], module: Path) -> None:
    wire(make_report())
    result = runner.invoke(app, ["generate", str(module), "--report-md", str(module / "x.md")])
    assert result.exit_code == 2
    assert "cannot write report" in result.stderr


@pytest.mark.parametrize(
    ("threshold", "exit_code"), [("60", 0), ("60.1", 1), ("-5", 0), ("100", 1)]
)
def test_fail_under_gain_compares_line_gain_in_points(
    wire: Callable[..., Wiring], module: Path, threshold: str, exit_code: int
) -> None:
    wire(make_report(before=2, after=8))  # +60 pp

    result = runner.invoke(app, ["generate", str(module), "--fail-under-gain", threshold])

    assert result.exit_code == exit_code, result.output


def test_missing_target_is_a_usage_error(wire: Callable[..., Wiring], tmp_path: Path) -> None:
    wiring = wire(make_report())
    result = runner.invoke(app, ["generate", str(tmp_path / "missing.py")])
    assert result.exit_code == 2
    assert not wiring.use_case.requests


def test_invalid_settings_exit_with_two_and_name_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTESTGEN_REPAIR_ATTEMPTS", "99")
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 2
    assert "invalid configuration" in result.stderr
    assert "repair_attempts" in result.stderr
    assert "Traceback" not in result.output


def test_log_level_override_is_validated() -> None:
    result = runner.invoke(app, ["--log-level", "loud", "version"])
    assert result.exit_code == 2
    assert "log_level" in result.stderr


def test_log_level_override_keeps_other_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTESTGEN_LLM_MODEL", "from-env")
    result = runner.invoke(app, ["--log-level", "debug", "config"])
    data = json.loads(result.stdout)
    assert (result.exit_code, data["log_level"], data["llm_model"]) == (0, "DEBUG", "from-env")
