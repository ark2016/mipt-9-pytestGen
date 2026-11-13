"""Verify benchmark labels and accounting with actual pytest subprocesses and mocked LLM."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from pytestgen.application.repair import TestRepairer
from pytestgen.domain.llm import ChatRequest, ChatResponse
from pytestgen.domain.models import TestStatus

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
try:
    _SPEC = importlib.util.spec_from_file_location(
        "bench_heuristic", _SCRIPTS / "bench_heuristic.py"
    )
    assert _SPEC is not None and _SPEC.loader is not None
    bench = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(bench)
finally:
    sys.path.remove(str(_SCRIPTS))


class Reply:
    model = "mock"

    def __init__(self, source: str) -> None:
        self.source = source
        self.requests = 0

    def complete(self, request: ChatRequest) -> ChatResponse:
        assert request.purpose == "repair"
        self.requests += 1
        fence = chr(96) * 3
        return ChatResponse(content=f"{fence}python\n{self.source}\n{fence}", model=self.model)


@pytest.mark.parametrize("documented", [True, False])
@pytest.mark.parametrize("bug", [True, False])
def test_real_subprocess_labels_warning_on_successful_repair(documented: bool, bug: bool) -> None:
    case = bench.CASES[0]
    llm = Reply(case.test(correct=not bug))
    row = bench.evaluate_repair(
        case,
        documented=documented,
        bug=bug,
        repairer=TestRepairer(llm),
    )
    assert row["status"] == "classified"
    assert row["actual_masking"] is bug
    assert row["predicted_suspicious"] is documented
    assert row["repair_effect"] == ("oracle_changed" if bug else "corrected_bad_test")
    assert llm.requests == 1


@pytest.mark.parametrize("index", [6, 7])
def test_exception_mutations_are_valid_witnesses_without_assertion_alert(index: int) -> None:
    case = bench.CASES[index]
    fixed = (
        "import pytest\nfrom solution import safe_divide\n"
        "def test_witness():\n"
        "    with pytest.raises(ZeroDivisionError):\n        safe_divide(5, 0)\n"
        if index == 6
        else "from solution import reciprocal\ndef test_witness():\n    assert reciprocal(0) == 0\n"
    )
    row = bench.evaluate_repair(
        case,
        documented=True,
        bug=True,
        repairer=TestRepairer(Reply(fixed)),
    )
    assert row["status"] == "classified"
    assert row["actual_masking"] is True
    assert row["predicted_suspicious"] is False


def test_vacuous_repair_is_retained_in_inclusive_masking_analysis() -> None:
    row = bench.evaluate_repair(
        bench.CASES[0],
        documented=True,
        bug=True,
        repairer=TestRepairer(Reply("def test_witness():\n    assert True\n")),
    )
    assert row["status"] == "degenerate_repair"
    summary = bench.summaries([row], [])
    assert summary["repair_all"]["n"] == 0
    assert summary["repair_including_vacuous_masks"]["tp"] == 1


def test_unfixed_test_is_attrition_not_a_true_negative() -> None:
    case = bench.CASES[0]
    row = bench.evaluate_repair(
        case,
        documented=True,
        bug=True,
        repairer=TestRepairer(Reply(case.test(correct=True))),
        attempts=1,
    )
    assert row["status"] == "not_repaired"
    assert bench.confusion([row], "actual_masking")["n"] == 0


def test_confusion_denominators_and_empty_cells() -> None:
    rows = [
        {"status": "classified", "truth": truth, "predicted_suspicious": flag}
        for truth, flag in [
            (True, True),
            (True, True),
            (True, False),
            (False, True),
            (False, False),
        ]
    ]
    matrix = bench.confusion(rows, "truth")
    assert matrix == {
        "n": 5,
        "tp": 2,
        "fp": 1,
        "tn": 1,
        "fn": 1,
        "precision": 2 / 3,
        "recall": 2 / 3,
        "false_positive_rate": 0.5,
    }
    assert bench.confusion([], "truth")["precision"] is None


@pytest.mark.parametrize("case", bench.CASES, ids=lambda case: case.name)
def test_authored_case_reference_and_mutation_execute(tmp_path: Path, case: object) -> None:
    target = tmp_path / "solution.py"
    target.write_text(case.source(mutant=False, documented=True), encoding="utf-8")
    assert bench.verified(bench.run_tests(target, case.test(correct=True)), TestStatus.PASSED)
    assert bench.verified(bench.run_tests(target, case.test(correct=False)), TestStatus.FAILED)
    target.write_text(case.source(mutant=True, documented=True), encoding="utf-8")
    assert bench.verified(bench.run_tests(target, case.test(correct=True)), TestStatus.FAILED)
