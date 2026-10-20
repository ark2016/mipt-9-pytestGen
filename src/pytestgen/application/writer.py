"""Free-form tests: the model writes whole pytest functions, the guard screens them."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace

from pytestgen.application.collaborators import WrittenTests
from pytestgen.application.prompts import write_tests_messages
from pytestgen.application.testcode import TestCodeReader
from pytestgen.codegen import rename_definition
from pytestgen.domain.llm import ChatRequest
from pytestgen.domain.models import FreeFormTest, FunctionInfo, ModuleInfo
from pytestgen.log import get_logger
from pytestgen.ports import LLMClient

_log = get_logger(__name__)


class FreeFormWriter:
    """Asks the model for pytest code testing one function or method, then screens it."""

    def __init__(self, llm: LLMClient, *, max_tests: int = 5) -> None:
        self._reader = TestCodeReader(llm)
        self._max_tests = max_tests

    def write(
        self,
        module: ModuleInfo,
        function: FunctionInfo,
        *,
        uncovered_lines: Sequence[int] = (),
        avoid_names: Collection[str] = (),
    ) -> WrittenTests:
        """Return guard-approved tests (unique names) and the ones that were rejected."""
        request = ChatRequest(
            messages=tuple(
                write_tests_messages(
                    module,
                    function,
                    uncovered_lines=uncovered_lines,
                    avoid_names=avoid_names,
                    max_tests=self._max_tests,
                )
            ),
            temperature=0.3,
            max_tokens=1536,
            purpose="write_tests",
        )
        answer = self._reader.read(request, function.qualname)
        accepted = list(answer.tests)
        if len(accepted) > self._max_tests:
            _log.info("write_tests_truncated", function=function.qualname, got=len(accepted))
            accepted = accepted[: self._max_tests]
        unique = _make_names_unique(accepted, set(avoid_names))
        _log.info(
            "tests_written",
            function=function.qualname,
            accepted=len(unique),
            rejected=len(answer.rejected),
        )
        return WrittenTests(tuple(unique), answer.rejected)


def _make_names_unique(tests: Sequence[FreeFormTest], taken: set[str]) -> list[FreeFormTest]:
    """Rename tests whose names collide with ``taken`` or with each other (``_2``, ``_3``...)."""
    result: list[FreeFormTest] = []
    for test in tests:
        name, counter = test.name, 2
        while name in taken:
            name = f"{test.name}_{counter}"
            counter += 1
        taken.add(name)
        if name == test.name:
            result.append(test)
        else:
            source = rename_definition(test.source, test.name, name)
            result.append(replace(test, name=name, source=source))
    return result
