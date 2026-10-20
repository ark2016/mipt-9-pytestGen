"""Input proposals: argument vectors for branches the solver could not decide."""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Final

from pytestgen.application.extract import extract_json
from pytestgen.application.prompts import PROPOSE_INPUTS_SCHEMA, propose_inputs_messages
from pytestgen.domain.annotations import coerce_json_value, fit_scalar
from pytestgen.domain.llm import ChatRequest
from pytestgen.domain.models import BranchTarget, FunctionInfo, ModuleInfo, ParamKind
from pytestgen.errors import ExtractionError, LLMError
from pytestgen.log import get_logger
from pytestgen.ports import LLMClient

_log = get_logger(__name__)

_VARIADIC: Final = frozenset({ParamKind.VAR_POSITIONAL, ParamKind.VAR_KEYWORD})


class InputProposer:
    """Asks the model for argument vectors reaching branches that Z3 could not solve."""

    def __init__(self, llm: LLMClient, *, max_inputs: int = 6) -> None:
        self._llm = llm
        self._max_inputs = max_inputs

    def propose(
        self,
        module: ModuleInfo,
        function: FunctionInfo,
        targets: Sequence[BranchTarget],
        reasons: Mapping[str, str],
    ) -> list[dict[str, object]]:
        """Return validated, deduplicated input vectors (empty on any LLM failure)."""
        if not targets:
            return []
        request = ChatRequest(
            messages=tuple(
                propose_inputs_messages(module, function, targets, reasons, self._max_inputs)
            ),
            temperature=0.3,
            max_tokens=128 + 128 * self._max_inputs,
            response_schema=PROPOSE_INPUTS_SCHEMA,
            purpose="propose_inputs",
        )
        try:
            data = extract_json(self._llm.complete(request).content)
        except (LLMError, ExtractionError) as exc:
            _log.warning("propose_inputs_failed", function=function.qualname, error=str(exc))
            return []
        validator = _VectorValidator(function)
        vectors: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in _candidates(data, validator):
            vector = validator.validate(item)
            if vector is None:
                continue
            key = json.dumps(vector, sort_keys=True, default=repr)
            if key in seen:
                continue
            seen.add(key)
            vectors.append(vector)
            if len(vectors) >= self._max_inputs:
                break
        _log.info("inputs_proposed", function=function.qualname, count=len(vectors))
        return vectors


def _candidates(data: Mapping[str, Any], validator: _VectorValidator) -> list[object]:
    """The ``inputs`` list; tolerates one bare object, or the vector itself as the answer."""
    raw = data.get("inputs")
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, dict):
        return [raw]
    if raw is None and data and validator.knows_all(data):
        return [dict(data)]
    _log.warning("propose_inputs_bad_shape")
    return []


class _VectorValidator:
    """Checks one argument vector against the function's call parameters."""

    def __init__(self, function: FunctionInfo) -> None:
        self._name = function.qualname
        self._params = {p.name: p for p in function.call_parameters if p.kind not in _VARIADIC}
        self._required = [n for n, p in self._params.items() if p.default is None]

    def knows_all(self, keys: Collection[str]) -> bool:
        return all(key in self._params for key in keys)

    def validate(self, item: object) -> dict[str, object] | None:
        problem, vector = self._check(item)
        if problem:
            _log.debug("input_vector_dropped", function=self._name, reason=problem, vector=item)
            return None
        return vector

    def _check(self, item: object) -> tuple[str, dict[str, object]]:
        if not isinstance(item, dict):
            return "not an object", {}
        unknown = [key for key in item if key not in self._params]
        if unknown:
            return f"unknown parameters {unknown}", {}
        missing = [name for name in self._required if name not in item]
        if missing:
            return f"missing parameters {missing}", {}
        vector: dict[str, object] = {}
        for name, param in self._params.items():
            if name not in item:
                continue
            value = item[name]
            if not _is_json_value(value):
                return f"{name}: not a JSON value", {}
            fits, value = fit_scalar(coerce_json_value(value, param.annotation), param.annotation)
            if not fits:
                return f"{name}: {value!r} does not fit {param.annotation}", {}
            vector[name] = value
        return "", vector


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, bool | int | str):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_value(v) for k, v in value.items())
    return False
