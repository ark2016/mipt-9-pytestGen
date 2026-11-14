"""Render suites and run the generated modules with a real pytest against a real module."""

from __future__ import annotations

import ast
import itertools
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from codegen_factories import (
    CALC_SOURCE,
    calc_module,
    function,
    module,
    param,
    raised,
    returned,
)

from pytestgen.codegen import make_free_form_tests, render_test_module
from pytestgen.domain.models import CaseOrigin, FreeFormTest, TestCaseSpec, TestSuite

LLM = CaseOrigin.LLM_SLOT


@dataclass(frozen=True)
class Run:
    source: str
    returncode: int
    output: str

    def outcome(self, test_id: str) -> str | None:
        """``PASSED``/``FAILED``/... for ``test_id`` (name with optional ``[param id]``)."""
        match = re.search(rf"::{re.escape(test_id)} (\w+)", self.output)
        return match.group(1) if match else None


def execute(tmp_path: Path, suite: TestSuite) -> Run:
    """Write ``calc.py`` and the rendered tests into ``tmp_path`` and run pytest on them."""
    source = render_test_module(suite)
    ast.parse(source)
    (tmp_path / "calc.py").write_text(CALC_SOURCE, encoding="utf-8")
    (tmp_path / "test_generated.py").write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", "-o", "addopts="],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    return Run(source, proc.returncode, proc.stdout + proc.stderr)


def data_cases() -> tuple[TestCaseSpec, ...]:
    return (
        returned("add", {"a": 1, "b": 2}, 3),
        returned("add", {"a": "x", "b": "y"}, "xy", LLM),
        returned("add", {"a": [1], "b": [2, 3]}, [1, 2, 3], LLM),
        returned("add", {"a": 0.1, "b": 0.2}, 0.30000000000000004),
        returned("div", {"a": 7, "b": 2}, 3.5),
        raised("div", {"a": 1, "b": 0}, "ZeroDivisionError"),
        raised("div", {"a": 5, "b": 0}, "builtins.ZeroDivisionError", LLM),
        returned("scale", {"x": 3, "factor": 2.0}, 6.0),
        returned("scale", {"x": 3, "factor": 0.1}, 0.30000000000000004),
        returned("greet", {"name": "world"}, "hello world!"),
        returned("greet", {"name": 'it\'s "q"'}, 'hello it\'s "q"!'),
        returned("greet", {"name": "привет"}, "hello привет!"),
        returned("pick", {"a": 1, "c": 1}, 12),
        returned("pick", {"a": 2, "c": 5}, 17),
        returned("total", {"args": (1, 2), "kwargs": {"k": 3}}, 6),
        returned("answer", {}, 42),
        raised("explode", {}, "calc.Boom"),
        returned("fetch", {"n": 4}, 8),
        raised("fail_async", {"n": 1}, "calc.Boom"),
        returned("mean", {"values": [1, 2, 4]}, 2.3333333333333335),
        returned("expected", {"expected": 1, "exc_type": 2}, [1, 2]),
        raised("div", {"a": 1, "b": 0}, "calc.Boom", LLM),  # wrong on purpose: must fail
        returned("Account.fee", {"amount": 3}, 1.5),
        returned("Account.make", {"balance": 9}, 9),
        returned("Account.deposit", {"amount": 5}, 5),
        raised("Account.withdraw", {"amount": 1}, "calc.Boom"),
        returned("Needy.show", {}, 1),  # constructor needs arguments: skipped
    )


@pytest.fixture(scope="module")
def data_run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    return execute(tmp_path_factory.mktemp("data"), TestSuite(calc_module(), data_cases()))


@pytest.mark.parametrize(
    "test_id",
    [
        "test_add_returns[z3-1]",
        "test_add_returns[llm_slot-2]",
        "test_add_returns[llm_slot-3]",
        "test_add_returns[z3-4]",
        "test_div_returns[z3-1]",
        "test_div_raises[z3-1]",
        "test_div_raises[llm_slot-2]",
        "test_scale_returns[z3-1]",
        "test_scale_returns[z3-2]",
        "test_greet_returns[z3-1]",
        "test_greet_returns[z3-2]",
        "test_greet_returns[z3-3]",
        "test_pick_returns[z3-1]",
        "test_pick_returns[z3-2]",
        "test_total_returns[z3-1]",
        "test_answer_returns",
        "test_explode_raises",
        "test_fetch_returns[z3-1]",
        "test_fail_async_raises[z3-1]",
        "test_mean_returns[z3-1]",
        "test_expected_returns[z3-1]",
        "test_account_fee_returns[z3-1]",
        "test_account_make_returns[z3-1]",
        "test_account_deposit_returns[z3-1]",
        "test_account_withdraw_raises[z3-1]",
    ],
)
def test_generated_data_driven_tests_pass_against_real_module(data_run: Run, test_id: str) -> None:
    assert data_run.outcome(test_id) == "PASSED", data_run.output


def test_deliberately_wrong_case_is_the_only_failure(data_run: Run) -> None:
    # raised("div", ..., "calc.Boom") does not match the real ZeroDivisionError
    assert data_run.outcome("test_div_raises[llm_slot-3]") == "FAILED"
    assert data_run.returncode == 1
    assert len(re.findall(r"::\S+ FAILED", data_run.output)) == 1


def test_skipped_method_produces_no_test(data_run: Run) -> None:
    assert "needy" not in data_run.source.lower()


def test_generated_module_is_clean_under_ruff_format(data_run: Run, tmp_path: Path) -> None:
    target = tmp_path / "gen.py"
    target.write_text(data_run.source, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--isolated", "--diff", str(target)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if "No module named ruff" in proc.stderr:
        pytest.skip("ruff is not installed in this environment")
    assert proc.returncode == 0, proc.stdout


def ruff_format_diff(directory: Path) -> subprocess.CompletedProcess[str]:
    """``ruff format --diff`` with ruff's default settings (88 columns), ignoring repo config."""
    return subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--isolated", "--diff", str(directory)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_wide_signatures_are_clean_under_ruff_format(tmp_path: Path) -> None:
    for count, length, is_async, long_value in itertools.product(
        (1, 3, 7), (1, 15, 23), (False, True), (False, True)
    ):
        names = [
            f"{'p' * length}{i}"[-length:] if length > 1 else chr(97 + i) for i in range(count)
        ]
        fn = function("wide", *(param(n) for n in names), is_async=is_async)
        value = "v" * 60 if long_value else 1
        cases = (
            returned("wide", dict.fromkeys(names, 1), value),
            returned("wide", dict.fromkeys(names, 2), value),
            raised("wide", dict.fromkeys(names, 1), "ValueError"),
        )
        text = render_test_module(TestSuite(module((fn,)), cases))
        name = f"t_{count}_{length}_{int(is_async)}_{int(long_value)}.py"
        (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    proc = ruff_format_diff(tmp_path)
    if "No module named ruff" in proc.stderr:
        pytest.skip("ruff is not installed in this environment")
    assert proc.returncode == 0, proc.stdout


# --------------------------------------------------------------------------- omitted arguments


def omitted_cases() -> tuple[TestCaseSpec, ...]:
    return (
        returned("pick", {"a": 1}, 111),
        returned("pick", {"a": 0, "b": 1}, 101),
        returned("pick", {"a": 0, "c": 2}, 12),
        returned("mixed", {"a": 1, "args": (3,)}, (1, 2, (3,))),
        returned("posonly", {"b": 5}, (1, 5)),
        raised("Account.overdraw", {"amount": 1}, "calc.Account.Insufficient"),
        returned("Account.deposit", {"amount": 2}, 2),
    )


@pytest.fixture(scope="module")
def omitted_run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    return execute(tmp_path_factory.mktemp("omitted"), TestSuite(calc_module(), omitted_cases()))


def test_cases_with_omitted_arguments_run_green(omitted_run: Run) -> None:
    assert omitted_run.returncode == 0, omitted_run.output


@pytest.mark.parametrize(
    "test_id",
    [
        "test_pick_returns[z3-1]",
        "test_pick_returns[z3-2]",
        "test_pick_returns[z3-3]",
        "test_mixed_returns[z3-1]",
        "test_posonly_returns[z3-1]",
        "test_account_overdraw_raises[z3-1]",
        "test_account_deposit_returns[z3-1]",
    ],
)
def test_omitted_argument_tests_pass_individually(omitted_run: Run, test_id: str) -> None:
    assert omitted_run.outcome(test_id) == "PASSED", omitted_run.output


def test_omitted_arguments_are_spelled_out_in_the_source(omitted_run: Run) -> None:
    assert "mixed(a, 2, *args)" in omitted_run.source
    assert "posonly(1, b)" in omitted_run.source
    assert "from calc import Account, mixed, pick, posonly" in omitted_run.source
    assert "calc.Account import" not in omitted_run.source


# --------------------------------------------------------------------------- free-form tests

LLM_MODULE_ADD = """\
import pytest
from calc import add, div

@pytest.fixture
def numbers():
    return (2, 3)

def _double(x):
    return x * 2

def test_sum(numbers):
    assert add(*numbers) == 5

class TestDiv:
    def test_zero(self):
        with pytest.raises(ZeroDivisionError):
            div(1, 0)
"""

LLM_MODULE_MIXED = """\
import asyncio
import pytest
from calc import add, fetch, Boom

@pytest.fixture
def numbers():
    return (2, 3)

def test_sum(numbers):
    assert add(*numbers) == 5

def test_fetch():
    assert asyncio.run(fetch(2)) == 4

@pytest.mark.parametrize("n", [1, 2])
def test_boom(n):
    with pytest.raises(Boom):
        raise Boom(n)
"""


@pytest.fixture(scope="module")
def free_run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    free_tests = (
        *make_free_form_tests("add", LLM_MODULE_ADD),
        *make_free_form_tests("fetch", LLM_MODULE_MIXED),
        FreeFormTest(
            "test_sum", "add", "from calc import add\n\ndef test_sum():\n    assert add(1, 1) == 2"
        ),
    )
    cases = (returned("add", {"a": 1, "b": 2}, 3), returned("add", {"a": 2, "b": 2}, 4, LLM))
    suite = TestSuite(calc_module(), cases, free_tests)
    return execute(tmp_path_factory.mktemp("free"), suite)


def test_free_form_modules_run_green_after_merge(free_run: Run) -> None:
    assert free_run.returncode == 0, free_run.output


@pytest.mark.parametrize(
    "test_id",
    [
        "test_add_returns[z3-1]",
        "test_add_returns[llm_slot-2]",
        "test_sum",
        "TestDiv::test_zero",
        "test_fetch",
        "test_boom[1]",
        "test_boom[2]",
        "test_sum_2",
        "test_sum_3",
    ],
)
def test_free_form_tests_keep_their_identity_when_names_collide(
    free_run: Run, test_id: str
) -> None:
    assert free_run.outcome(test_id) == "PASSED", free_run.output


def test_shared_helpers_and_imports_are_emitted_once(free_run: Run) -> None:
    source = free_run.source
    assert source.count("def numbers():") == 1
    assert source.count("import pytest") == 1
    assert source.count("import asyncio") == 1
    assert source.count("from calc import") == 1
