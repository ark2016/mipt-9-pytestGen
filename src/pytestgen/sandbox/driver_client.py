"""Launches ``_driver.py`` inside a workspace and reads back its JSON answer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pytestgen.sandbox.env import build_sandbox_env
from pytestgen.sandbox.process import ProcessResult, run_process
from pytestgen.sandbox.workspace import Workspace

DRIVER_PATH = Path(__file__).with_name("_driver.py")
TRACEBACK_LINES = 15


@dataclass(frozen=True, slots=True)
class DriverRun:
    process: ProcessResult
    payload: dict[str, Any] | None
    """Parsed ``out.json``; ``None`` when the driver died or timed out before writing it."""

    @property
    def status(self) -> str | None:
        return None if self.payload is None else str(self.payload.get("status"))

    @property
    def error(self) -> str:
        """Last lines of the traceback the driver reported (empty when there is none)."""
        text = str(self.payload.get("error", "")) if self.payload else ""
        return "\n".join(text.strip().splitlines()[-TRACEBACK_LINES:])


class DriverClient:
    def __init__(self, python_executable: str) -> None:
        self._python = python_executable

    def run(
        self,
        ws: Workspace,
        mode: str,
        spec: Mapping[str, object],
        *,
        timeout_s: float,
        inputs_literal: str | None = None,
    ) -> DriverRun:
        spec_path = ws.scratch / "spec.json"
        out_path = ws.scratch / "out.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        out_path.unlink(missing_ok=True)
        cmd = [self._python, "-P", str(DRIVER_PATH), mode, str(spec_path), str(out_path)]
        if inputs_literal is not None:
            inputs_path = ws.scratch / "inputs.pylit"
            inputs_path.write_text(inputs_literal, encoding="utf-8")
            cmd.append(str(inputs_path))
        process = run_process(
            cmd,
            cwd=ws.root,
            env=build_sandbox_env(ws.pythonpath, ws.home),
            timeout_s=timeout_s,
        )
        return DriverRun(process, _read_json(out_path))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
