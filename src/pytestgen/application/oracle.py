"""Oracle review: does the recorded behaviour match what the function promises?"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Final

from pytestgen.application.extract import extract_json
from pytestgen.application.prompts import ORACLE_REVIEW_SCHEMA, oracle_review_messages
from pytestgen.domain.llm import ChatRequest
from pytestgen.domain.models import (
    FunctionInfo,
    ModuleInfo,
    ObservationKind,
    OracleVerdict,
    TestCaseSpec,
)
from pytestgen.errors import ExtractionError, LLMError
from pytestgen.log import get_logger
from pytestgen.ports import LLMClient

_log = get_logger(__name__)

_MAX_NOTE_CHARS: Final = 300
_TOKENS_PER_REVIEW: Final = 80
_REVIEWABLE: Final = frozenset({ObservationKind.RETURNED, ObservationKind.RAISED})


class OracleReviewer:
    """Asks the model whether recorded behaviour matches the function's documented intent."""

    def __init__(self, llm: LLMClient, *, max_cases: int = 12) -> None:
        self._llm = llm
        self._max_cases = max_cases

    def review(
        self, module: ModuleInfo, function: FunctionInfo, cases: Sequence[TestCaseSpec]
    ) -> tuple[TestCaseSpec, ...]:
        """Return ``cases`` in the same order with verdicts for the ones the model judged."""
        result = tuple(cases)
        if not function.docstring or not function.docstring.strip():
            _log.debug("oracle_review_skipped", function=function.qualname, reason="no_docstring")
            return result
        numbered = [(i, c) for i, c in enumerate(cases) if c.observation.kind in _REVIEWABLE]
        numbered = numbered[: self._max_cases]
        if not numbered:
            return result
        request = ChatRequest(
            messages=tuple(oracle_review_messages(module, function, numbered)),
            temperature=0.0,
            max_tokens=128 + _TOKENS_PER_REVIEW * len(numbered),
            response_schema=ORACLE_REVIEW_SCHEMA,
            purpose="oracle_review",
        )
        try:
            data = extract_json(self._llm.complete(request).content)
        except (LLMError, ExtractionError) as exc:
            _log.warning("oracle_review_failed", function=function.qualname, error=str(exc))
            return result
        reviews = data.get("reviews")
        if not isinstance(reviews, list):
            _log.warning("oracle_review_bad_shape", function=function.qualname)
            return result
        judged = _parse_reviews(reviews, allowed={i for i, _ in numbered})
        _log.info(
            "oracle_reviewed",
            function=function.qualname,
            asked=len(numbered),
            judged=len(judged),
            suspicious=sum(v is OracleVerdict.SUSPICIOUS for v, _ in judged.values()),
        )
        return tuple(
            replace(case, verdict=judged[i][0], note=judged[i][1]) if i in judged else case
            for i, case in enumerate(cases)
        )


def _parse_reviews(
    reviews: Sequence[object], *, allowed: Collection[int]
) -> dict[int, tuple[OracleVerdict, str]]:
    judged: dict[int, tuple[OracleVerdict, str]] = {}
    for entry in reviews:
        if not isinstance(entry, dict):
            continue
        index = _as_index(entry.get("index"))
        verdict = str(entry.get("verdict", "")).strip().lower()
        explanation = str(entry.get("explanation") or "").strip()[:_MAX_NOTE_CHARS]
        if index is None or index not in allowed or index in judged:
            continue
        if verdict == "ok":
            judged[index] = (OracleVerdict.OK, explanation)
        elif verdict == "suspicious":
            # a bare accusation is noise: demand a reason or treat the behaviour as fine
            judged[index] = (
                (OracleVerdict.SUSPICIOUS, explanation) if explanation else (OracleVerdict.OK, "")
            )
    return judged


def _as_index(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None
