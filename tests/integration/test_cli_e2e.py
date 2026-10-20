"""The CLI on the demo project: real pipeline, scripted model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from e2e_support import BASE_URL, ScriptedLLMServer
from typer.testing import CliRunner

from pytestgen.cli import app

runner = CliRunner()
QUIET = ("--log-level", "WARNING")


@pytest.fixture(autouse=True)
def _cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTESTGEN_LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("PYTESTGEN_LLM_MAX_RETRIES", "0")
    monkeypatch.setenv("PYTESTGEN_CACHE_DIR", str(tmp_path / "llm-cache"))


class TestAnalyze:
    def test_json_lists_functions_branches_and_solutions(self, project: Path) -> None:
        result = runner.invoke(app, ["analyze", str(project / "shop" / "pricing.py"), "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["module"] == "shop.pricing"
        functions = {item["qualname"]: item for item in data["functions"]}
        assert {"discount", "shipping_cost", "validate_promo", "free_shipping"} <= set(functions)
        discount = functions["discount"]
        assert [p["name"] for p in discount["parameters"]] == ["price", "customer_type", "years"]
        first = discount["branches"][0]
        assert first["key"] == "discount:14:T"
        assert first["kind"] == "if"
        assert first["condition"] == "price < 0 or years < 0"
        assert first["solution"]["status"] == "sat"
        assert first["solution"]["inputs"]["years"] < 0
        statuses = {b["solution"]["status"] for b in functions["validate_promo"]["branches"]}
        assert {"sat", "unsupported"} <= statuses

    def test_no_solve_omits_solutions(self, project: Path) -> None:
        target = str(project / "shop" / "pricing.py")
        result = runner.invoke(app, ["analyze", target, "--json", "--no-solve", "-f", "discount"])

        assert result.exit_code == 0, result.output
        branches = json.loads(result.stdout)["functions"][0]["branches"]
        assert branches
        assert all("solution" not in branch for branch in branches)

    def test_tables_show_targets_and_z3_status(self, project: Path) -> None:
        target = str(project / "shop" / "pricing.py")
        result = runner.invoke(app, ["analyze", target, "-f", "loyalty_level"])

        assert result.exit_code == 0, result.output
        assert "loyalty_level(points: int) -> str" in result.stdout
        assert "loyalty_level:69:T" in result.stdout
        assert "sat" in result.stdout

    def test_unknown_function_is_a_clean_error(self, project: Path) -> None:
        target = str(project / "shop" / "pricing.py")
        result = runner.invoke(app, ["analyze", target, "-f", "nope"])

        assert result.exit_code == 2
        assert "error:" in result.stderr
        assert "nope" in result.stderr
        assert "discount" in result.stderr  # lists what is available
        assert "Traceback" not in result.output


class TestGenerate:
    def test_dry_run_prints_the_report_and_writes_report_files(
        self, project: Path, tmp_path: Path
    ) -> None:
        target = str(project / "shop" / "pricing.py")
        server = ScriptedLLMServer()
        reports = tmp_path / "out"
        with respx.mock(assert_all_called=False) as router:
            server.install(router, BASE_URL)
            result = runner.invoke(
                app,
                [
                    *QUIET,
                    "generate", target,
                    "--dry-run",
                    "--report-md", str(reports / "report.md"),
                    "--report-json", str(reports / "report.json"),
                    "--fail-under-gain", "30",
                ],
            )  # fmt: skip

        assert result.exit_code == 0, result.output + result.stderr
        assert "analysing pricing.py" in result.stdout  # progress
        assert "Coverage" in result.stdout
        assert "Kept tests" in result.stdout
        assert not (project / "tests" / "test_pricing_pytestgen.py").exists()
        data = json.loads((reports / "report.json").read_text(encoding="utf-8"))
        assert data["module"] == "shop.pricing"
        assert data["output_path"] is None
        assert data["kept_tests"] > 0
        assert "shop.pricing" in (reports / "report.md").read_text(encoding="utf-8")
        assert server.kinds["oracle_review"] >= 1

    def test_gain_below_threshold_exits_with_one(self, project: Path) -> None:
        target = str(project / "shop" / "pricing.py")
        with respx.mock(assert_all_called=False) as router:
            ScriptedLLMServer().install(router, BASE_URL)
            result = runner.invoke(
                app,
                [
                    *QUIET,
                    "generate",
                    target,
                    "-f",
                    "free_shipping",
                    "--dry-run",
                    "--no-existing-tests",
                    "--no-cache",
                    "--fail-under-gain",
                    "101",
                ],
            )

        assert result.exit_code == 1, result.output
        assert "below 101.0 pp" in result.stderr

    def test_writes_into_the_requested_output_path(self, project: Path, tmp_path: Path) -> None:
        target = str(project / "shop" / "pricing.py")
        output = project / "tests" / "generated" / "test_free_shipping.py"
        with respx.mock(assert_all_called=False) as router:
            ScriptedLLMServer().install(router, BASE_URL)
            result = runner.invoke(
                app,
                [*QUIET, "generate", target, "-f", "free_shipping", "-o", str(output)],
            )

        assert result.exit_code == 0, result.output + result.stderr
        assert "def test_free_shipping_returns" in output.read_text(encoding="utf-8")

    def test_refuses_to_overwrite_a_foreign_file(self, project: Path) -> None:
        target = str(project / "shop" / "pricing.py")
        foreign = project / "tests" / "test_pricing_pytestgen.py"
        foreign.write_text("def test_mine():\n    pass\n", encoding="utf-8")
        with respx.mock(assert_all_called=False) as router:
            server = ScriptedLLMServer()
            server.install(router, BASE_URL)
            result = runner.invoke(app, [*QUIET, "generate", target])

        assert result.exit_code == 2
        assert "not generated by pytestgen" in result.stderr
        assert not server.prompts  # failed before any LLM call
        assert foreign.read_text(encoding="utf-8") == "def test_mine():\n    pass\n"
