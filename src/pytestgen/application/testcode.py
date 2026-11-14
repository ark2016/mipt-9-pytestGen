"""Asking the model for test code: complete, extract, split and screen (shared pipeline)."""

from __future__ import annotations

from dataclasses import dataclass

from pytestgen.application.extract import extract_code
from pytestgen.application.guard import check_test_source
from pytestgen.codegen import make_free_form_tests
from pytestgen.domain.llm import ChatRequest
from pytestgen.domain.models import DroppedTest, DropReason, FreeFormTest
from pytestgen.errors import ExtractionError, LLMError
from pytestgen.log import get_logger
from pytestgen.ports import LLMClient

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReadTests:
    """Test functions found in one answer: the safe ones and those the guard refused."""

    tests: tuple[FreeFormTest, ...] = ()
    rejected: tuple[DroppedTest, ...] = ()


class TestCodeReader:
    """Runs a request that is answered with a python code block of pytest functions.

    Never raises ``LLMError``: an unreachable model or an answer without usable code gives
    an empty result and a ``<purpose>_failed`` warning.
    """

    __test__ = False  # not a pytest class

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def read(self, request: ChatRequest, function: str) -> ReadTests:
        """Tests for ``function`` (qualified name) found in the model's answer to ``request``."""
        try:
            code = extract_code(self._llm.complete(request).content)
            tests = make_free_form_tests(function, code)
        except (LLMError, ExtractionError) as exc:
            _log.warning(f"{request.purpose}_failed", function=function, error=str(exc))
            return ReadTests()
        accepted: list[FreeFormTest] = []
        rejected: list[DroppedTest] = []
        for test in tests:
            violations = check_test_source(test.source)
            if violations:
                detail = "; ".join(violations)
                rejected.append(DroppedTest(test.name, function, DropReason.INVALID_CODE, detail))
            else:
                accepted.append(test)
        return ReadTests(tuple(accepted), tuple(rejected))
