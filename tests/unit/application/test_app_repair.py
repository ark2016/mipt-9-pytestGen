from __future__ import annotations

from typing import Any

import pytest
from app_factories import analyze_example, failed_outcome
from fakes_llm import ScriptedLLM, code_reply
from structlog.testing import capture_logs

from pytestgen.application.repair import TestRepairer
from pytestgen.domain.models import CaseOrigin, FreeFormTest, FunctionInfo, ModuleInfo
from pytestgen.errors import LLMConnectionError, LLMResponseError

BROKEN = """\
from shop.pricing import free_shipping


def test_free_shipping_boundary():
    assert free_shipping(100.0) is True
"""

FIXED = """\
from shop.pricing import free_shipping


def test_free_shipping_boundary():
    assert free_shipping(100.0) is False
"""


@pytest.fixture(scope="module")
def pricing() -> ModuleInfo:
    return analyze_example("shop/pricing.py")


@pytest.fixture
def function(pricing: ModuleInfo) -> FunctionInfo:
    return pricing.function("free_shipping")


@pytest.fixture
def broken() -> FreeFormTest:
    return FreeFormTest(
        name="test_free_shipping_boundary",
        function="free_shipping",
        source=BROKEN,
        origin=CaseOrigin.LLM_FREE,
        repair_attempts=1,
    )


def repair(
    pricing: ModuleInfo,
    function: FunctionInfo | None,
    test: FreeFormTest,
    reply: str | Exception,
) -> tuple[FreeFormTest | None, ScriptedLLM]:
    llm = ScriptedLLM(reply)
    outcome = failed_outcome(test.name, "assert False is True", "E   assert False is True")
    return TestRepairer(llm).repair(pricing, function, test, outcome), llm


def test_returns_the_fixed_test_with_incremented_attempts(
    pricing: ModuleInfo, function: FunctionInfo, broken: FreeFormTest
) -> None:
    fixed, _ = repair(pricing, function, broken, code_reply(FIXED, after="Fixed the expectation."))
    assert fixed is not None
    assert fixed.name == broken.name
    assert fixed.function == "free_shipping"
    assert fixed.origin is CaseOrigin.LLM_FREE
    assert fixed.repair_attempts == 2
    assert "is False" in fixed.source
    assert "from shop.pricing import free_shipping" in fixed.source
    compile(fixed.source, "<fixed>", "exec")


def test_request_carries_test_failure_and_code_under_test(
    pricing: ModuleInfo, function: FunctionInfo, broken: FreeFormTest
) -> None:
    _, llm = repair(pricing, function, broken, code_reply(FIXED))
    (request,) = llm.requests
    assert request.purpose == "repair"
    assert request.response_schema is None
    prompt = llm.prompt_for("repair")
    assert "assert free_shipping(100.0) is True" in prompt
    assert "E   assert False is True" in prompt
    assert "return order_total > 100" in prompt
    assert "Keep the test name `test_free_shipping_boundary`" in prompt


def test_function_may_be_unknown(pricing: ModuleInfo, broken: FreeFormTest) -> None:
    fixed, llm = repair(pricing, None, broken, code_reply(FIXED))
    assert fixed is not None
    assert "Code under test" not in llm.prompt_for("repair")


def test_renamed_test_is_renamed_back(
    pricing: ModuleInfo, function: FunctionInfo, broken: FreeFormTest
) -> None:
    renamed = FIXED.replace("test_free_shipping_boundary", "test_boundary_fixed")
    fixed, _ = repair(pricing, function, broken, code_reply(renamed))
    assert fixed is not None
    assert fixed.name == "test_free_shipping_boundary"
    assert "def test_free_shipping_boundary(" in fixed.source
    assert "test_boundary_fixed" not in fixed.source


def test_the_unit_named_like_the_test_is_chosen_among_several(
    pricing: ModuleInfo, function: FunctionInfo, broken: FreeFormTest
) -> None:
    code = (
        "import pytest\nfrom shop.pricing import free_shipping\n\n\n"
        "def test_free_shipping_other():\n    assert free_shipping(1.0) is False\n\n\n"
        "def test_free_shipping_boundary():\n    assert free_shipping(100.0) is False\n"
    )
    fixed, _ = repair(pricing, function, broken, code_reply(code))
    assert fixed is not None
    assert "100.0" in fixed.source
    assert "test_free_shipping_other" not in fixed.source


def test_fixtures_the_test_needs_are_kept(
    pricing: ModuleInfo, function: FunctionInfo, broken: FreeFormTest
) -> None:
    code = (
        "import pytest\nfrom shop.pricing import free_shipping\n\n\n"
        "@pytest.fixture\ndef limit():\n    return 100.0\n\n\n"
        "@pytest.fixture\ndef unused():\n    return 1\n\n\n"
        "def test_free_shipping_boundary(limit):\n    assert free_shipping(limit) is False\n"
    )
    fixed, _ = repair(pricing, function, broken, code_reply(code))
    assert fixed is not None
    assert "def limit" in fixed.source
    assert "def unused" not in fixed.source


def test_unfenced_code_is_accepted(
    pricing: ModuleInfo, function: FunctionInfo, broken: FreeFormTest
) -> None:
    fixed, _ = repair(pricing, function, broken, FIXED)
    assert fixed is not None
    assert fixed.repair_attempts == 2


def test_forbidden_code_is_refused(
    pricing: ModuleInfo, function: FunctionInfo, broken: FreeFormTest
) -> None:
    evil = FIXED.replace("from shop", "import subprocess\nfrom shop")
    with capture_logs() as logs:
        fixed, _ = repair(pricing, function, broken, code_reply(evil))
    assert fixed is None
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == ["repair_rejected"]


@pytest.mark.parametrize(
    "reply",
    [
        LLMConnectionError("endpoint down"),
        LLMResponseError("HTTP 500"),
        "Sorry, I cannot fix this test.",
        "```python\ndef test_free_shipping_boundary(:\n```",
        "```python\nVALUE = 1\n```",
        "",
    ],
    ids=["connection", "response", "prose", "syntax-error", "no-test", "empty"],
)
def test_failures_return_none_and_warn(
    pricing: ModuleInfo, function: FunctionInfo, broken: FreeFormTest, reply: str | Exception
) -> None:
    with capture_logs() as logs:
        fixed, _ = repair(pricing, function, broken, reply)
    assert fixed is None
    warnings: list[Any] = [e["event"] for e in logs if e["log_level"] == "warning"]
    assert warnings
    assert all(w.startswith("repair_") for w in warnings)


def test_pytest_does_not_collect_the_class() -> None:
    assert TestRepairer.__test__ is False
