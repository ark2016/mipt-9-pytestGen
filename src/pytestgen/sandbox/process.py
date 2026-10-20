"""Run a subprocess with a hard wall-clock limit and kill its whole process tree on timeout."""

from __future__ import annotations

import contextlib
import importlib
import math
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pytestgen.errors import SandboxError

OUTPUT_LIMIT = 20_000
MEMORY_LIMIT_BYTES = 2 * 1024**3
_REAP_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float


def run_process(
    cmd: list[str], *, cwd: Path, env: dict[str, str], timeout_s: float
) -> ProcessResult:
    """Run ``cmd`` in its own process group; on timeout kill the entire tree.

    Output is decoded as UTF-8 (undecodable bytes are replaced) and only the last
    ``OUTPUT_LIMIT`` characters of each stream are kept.
    """
    started = time.monotonic()
    try:
        proc = _spawn(cmd, cwd, env, timeout_s)
    except OSError as exc:
        raise SandboxError(f"cannot start {cmd[0]!r}: {exc}") from exc
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        out, err = _reap(proc)
    return ProcessResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=_decode(out),
        stderr=_decode(err),
        timed_out=timed_out,
        duration_s=time.monotonic() - started,
    )


def _spawn(
    cmd: list[str], cwd: Path, env: dict[str, str], timeout_s: float
) -> subprocess.Popen[bytes]:
    # One Popen call with platform-specific extras: mypy would flag the POSIX tail of an
    # early ``return`` as unreachable when it type-checks for Windows.
    extra: dict[str, Any] = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True
        extra["preexec_fn"] = _posix_limits(timeout_s)
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **extra,
    )


def _posix_limits(timeout_s: float) -> Callable[[], None]:
    """Build the pre-exec hook that caps address space and CPU time of the child."""
    resource = importlib.import_module("resource")
    limits = {
        "RLIMIT_AS": MEMORY_LIMIT_BYTES,
        "RLIMIT_CPU": math.ceil(timeout_s) + 5,
    }

    def apply() -> None:
        for name, value in limits.items():
            kind = getattr(resource, name, None)
            if kind is not None:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(kind, (value, value))

    return apply


def _reap(proc: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    """Kill the tree and collect whatever output was produced."""
    _kill_tree(proc)
    try:
        return proc.communicate(timeout=_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:  # a detached grandchild still holds the pipes
        with contextlib.suppress(OSError):
            proc.kill()
        return b"", b""


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        _taskkill(proc.pid)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def _taskkill(pid: int) -> None:
    system32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            [str(system32 / "taskkill.exe"), "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
            check=False,
        )


def _decode(data: bytes | None) -> str:
    text = (data or b"").decode("utf-8", errors="replace")
    return text[-OUTPUT_LIMIT:]
