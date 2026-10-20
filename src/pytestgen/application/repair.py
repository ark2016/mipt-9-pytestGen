"""Repair of failing free-form tests by feeding the failure back to the model."""

from __future__ import annotations

from dataclasses import replace

from pytestgen.application.prompts import repair_messages
from pytestgen.application.testcode import TestCodeReader
from pytestgen.codegen import rename_definition
from pytestgen.domain.llm import ChatRequest
from pytestgen.domain.models import FreeFormTest, FunctionInfo, ModuleInfo, TestOutcome
from pytestgen.log import get_logger
from pytestgen.ports import LLMClient

_log = get_logger(__name__)


class TestRepairer:
    """Asks the model to align a failing test with the code's actual behaviour."""

    __test__ = False  # not a pytest class

    def __init__(self, llm: LLMClient) -> None:
        self._reader = TestCodeReader(llm)

    def repair(
        self,
        module: ModuleInfo,
        function: FunctionInfo | None,
        test: FreeFormTest,
        outcome: TestOutcome,
    ) -> FreeFormTest | None:
        """Return the fixed test (same name, ``repair_attempts`` + 1) or ``None`` on failure."""
        request = ChatRequest(
            messages=tuple(repair_messages(function, test.name, test.source, outcome)),
            temperature=0.2,
            max_tokens=1024,
            purpose="repair",
        )
        answer = self._reader.read(request, test.function)
        unit = next((u for u in answer.tests if u.name == test.name), None)
        if unit is None and not answer.rejected and answer.tests:
            unit = answer.tests[0]  # the model renamed the only test it wrote
        if unit is None:
            if answer.rejected:
                violations = [d.detail for d in answer.rejected]
                _log.warning("repair_rejected", test=test.name, violations=violations)
            else:
                _log.warning("repair_no_test_in_answer", test=test.name, module=module.module_name)
            return None
        source = unit.source
        if unit.name != test.name:
            source = rename_definition(source, unit.name, test.name)
        _log.info("test_repaired", test=test.name, attempt=test.repair_attempts + 1)
        return replace(test, source=source, repair_attempts=test.repair_attempts + 1)
