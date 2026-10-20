from __future__ import annotations

import pytest
from fakes_llm import ScriptedLLM, code_reply
from structlog.testing import capture_logs

from pytestgen.application.testcode import TestCodeReader
from pytestgen.domain.llm import ChatMessage, ChatRequest, Role
from pytestgen.domain.models import DropReason
from pytestgen.errors import LLMConnectionError

GOOD = "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n"
EVIL = "def test_evil():\n    import subprocess\n\n    subprocess.run(['ls'])\n"


def request(purpose: str = "write_tests") -> ChatRequest:
    return ChatRequest(messages=(ChatMessage(Role.USER, "write"),), purpose=purpose)


def read(reply: str | Exception, purpose: str = "write_tests"):  # type: ignore[no-untyped-def]
    return TestCodeReader(ScriptedLLM(reply)).read(request(purpose), "calc.f")


def test_tests_are_split_and_attributed_to_the_function() -> None:
    answer = read(code_reply(GOOD))
    assert [t.name for t in answer.tests] == ["test_a", "test_b"]
    assert {t.function for t in answer.tests} == {"calc.f"}
    assert answer.rejected == ()


def test_unsafe_tests_are_rejected_with_the_violations() -> None:
    answer = read(code_reply(GOOD + "\n\n" + EVIL))
    assert [t.name for t in answer.tests] == ["test_a", "test_b"]
    (dropped,) = answer.rejected
    assert (dropped.name, dropped.function) == ("test_evil", "calc.f")
    assert dropped.reason is DropReason.INVALID_CODE
    assert "subprocess" in dropped.detail


@pytest.mark.parametrize(
    "reply",
    [LLMConnectionError("down"), "I cannot help.", "```python\ndef broken(:\n```", ""],
    ids=["llm-error", "prose", "syntax", "empty"],
)
def test_failures_give_nothing_and_one_warning_named_after_the_purpose(
    reply: str | Exception,
) -> None:
    with capture_logs() as logs:
        answer = read(reply, purpose="repair")
    assert answer.tests == () and answer.rejected == ()
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert [e["event"] for e in warnings] == ["repair_failed"]
    assert warnings[0]["function"] == "calc.f"


def test_pytest_does_not_collect_the_class() -> None:
    assert TestCodeReader.__test__ is False
