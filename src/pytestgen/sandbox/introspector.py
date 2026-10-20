"""Runtime introspection of a module inside the sandbox (implements ``ModuleIntrospector``)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pytestgen.domain.models import IntrospectionResult, ModuleInfo
from pytestgen.log import get_logger
from pytestgen.sandbox.driver_client import DriverClient
from pytestgen.sandbox.workspace import Workspace

_log = get_logger(__name__)


class SubprocessIntrospector:
    def __init__(self, python_executable: str | None = None, *, timeout_s: float = 20.0) -> None:
        self._client = DriverClient(python_executable or sys.executable)
        self._timeout_s = timeout_s

    def introspect(self, project_root: Path, module: ModuleInfo) -> IntrospectionResult:
        spec = {
            "module": module.module_name,
            "qualnames": [fn.qualname for fn in module.functions],
        }
        with Workspace(project_root) as ws:
            run = self._client.run(ws, "introspect", spec, timeout_s=self._timeout_s)
        if run.process.timed_out:
            return IntrospectionResult(
                importable=False, error=f"importing {module.module_name} timed out"
            )
        if run.payload is None:
            tail = run.process.stderr.strip()[-1000:]
            return IntrospectionResult(importable=False, error=tail or "introspection crashed")
        if run.status != "ok":
            return IntrospectionResult(importable=False, error=run.error)
        for qualname in run.payload["skipped"]:
            _log.warning("introspection_skipped", function=qualname)
        functions: dict[str, Any] = run.payload["functions"]
        return IntrospectionResult(
            importable=True,
            signatures={q: str(f["signature"]) for q, f in functions.items()},
            type_hints={q: dict(f["hints"]) for q, f in functions.items()},
            coroutines=frozenset(q for q, f in functions.items() if f["coroutine"]),
        )
