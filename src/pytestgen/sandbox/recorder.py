"""Records what a function returns or raises on inputs (implements ``ExecutionRecorder``)."""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pytestgen.domain.models import FunctionInfo, ModuleInfo, Observation, ObservationKind
from pytestgen.errors import ModuleImportError, SandboxError
from pytestgen.log import get_logger
from pytestgen.sandbox.driver_client import DriverClient, DriverRun
from pytestgen.sandbox.workspace import Workspace

_log = get_logger(__name__)

Vector = Mapping[str, object]


class SubprocessExecutionRecorder:
    """Executes a function in a throw-away copy of the project, ``repeats`` times per input.

    All inputs go to one driver process; if that batch times out or crashes, every
    input is retried alone so a single hanging vector only marks itself ``TIMEOUT``.
    """

    def __init__(
        self,
        python_executable: str | None = None,
        *,
        timeout_s: float = 10.0,
        repeats: int = 2,
    ) -> None:
        self._client = DriverClient(python_executable or sys.executable)
        self._timeout_s = timeout_s
        self._repeats = repeats

    def observe(
        self,
        project_root: Path,
        module: ModuleInfo,
        function: FunctionInfo,
        inputs: Sequence[Vector],
    ) -> list[Observation]:
        if function.is_method and not function.is_static:
            raise SandboxError(f"cannot record instance method {function.qualname}")
        if not inputs:
            return []
        literal = _encode(inputs)
        spec = {
            "module": module.module_name,
            "qualname": function.qualname,
            "repeats": self._repeats,
        }
        with Workspace(project_root) as ws:
            batch = self._client.run(
                ws,
                "record",
                spec,
                timeout_s=self._timeout_s * (len(inputs) + 1),
                inputs_literal=literal,
            )
            results = self._results(batch, len(inputs))
            if results is None:
                _log.warning("recorder_batch_failed", function=function.qualname)
                results = [self._observe_alone(ws, spec, vector) for vector in inputs]
        return [_observation(vector, raw) for vector, raw in zip(inputs, results, strict=True)]

    def _observe_alone(
        self, ws: Workspace, spec: Mapping[str, object], vector: Vector
    ) -> dict[str, Any]:
        run = self._client.run(
            ws,
            "record",
            spec,
            timeout_s=self._timeout_s,
            inputs_literal=_encode([vector]),
        )
        if run.process.timed_out:
            return {"kind": "timeout", "exception_message": f"timed out after {self._timeout_s:g}s"}
        results = self._results(run, 1)
        if results is None:
            tail = run.process.stderr.strip()[-200:]
            return {
                "kind": "unstable",
                "exception_message": f"worker crashed (exit {run.process.returncode}): {tail}",
            }
        return results[0]

    @staticmethod
    def _results(run: DriverRun, expected: int) -> list[dict[str, Any]] | None:
        """Raw results of a driver run; ``None`` when it timed out or died without answering."""
        if run.payload is None or run.process.timed_out:
            return None
        if run.status == "import_error":
            raise ModuleImportError(run.error)
        if run.status != "ok":
            raise SandboxError(run.error or f"driver failed: {run.status}")
        results: list[dict[str, Any]] = run.payload["results"]
        return results if len(results) == expected else None


def _encode(vectors: Sequence[Vector]) -> str:
    """Python-literal text of the input vectors, refusing values that would not round-trip."""
    plain = [dict(v) for v in vectors]
    text = repr(plain)
    try:
        faithful = ast.literal_eval(text) == plain
    except (ValueError, SyntaxError):
        faithful = False
    if not faithful:
        raise SandboxError("inputs must be plain Python literals (no nan/inf or custom objects)")
    return text


def _observation(vector: Vector, raw: Mapping[str, Any]) -> Observation:
    return Observation(
        inputs=dict(vector),
        kind=ObservationKind(raw["kind"]),
        value_repr=raw.get("value_repr"),
        exception_type=raw.get("exception_type"),
        exception_message=raw.get("exception_message"),
    )
