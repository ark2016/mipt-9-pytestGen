from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pytestgen.errors import SandboxError
from pytestgen.sandbox import ProcessResult, build_sandbox_env, run_process
from pytestgen.sandbox import process as process_module

PRINT_ENV = "import json, os; print(json.dumps(dict(os.environ)))"


def run_python(code: str, tmp_path: Path, *, timeout_s: float = 20.0) -> ProcessResult:
    env = build_sandbox_env([tmp_path], tmp_path)
    return run_process([sys.executable, "-c", code], cwd=tmp_path, env=env, timeout_s=timeout_s)


# ------------------------------------------------------------------ environment


def test_env_is_an_allow_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "PYTESTGEN_LLM_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.setenv(name, "secret")
    extra = [tmp_path / "src"]
    env = build_sandbox_env([tmp_path, *extra], tmp_path / "home")

    assert env["PYTHONPATH"] == os.pathsep.join(str(p) for p in [tmp_path, *extra])
    assert env["PYTHONHASHSEED"] == "0"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["HOME"] == env["USERPROFILE"] == str(tmp_path / "home")
    assert "PATH" in env
    assert not any("KEY" in k or "TOKEN" in k or "PROXY" in k.upper() for k in env)
    assert not any(k.startswith("PYTESTGEN_") for k in env)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only variables")
def test_env_keeps_windows_essentials() -> None:
    env = build_sandbox_env([], Path())
    assert "SYSTEMROOT" in env


def test_secrets_are_invisible_to_a_real_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MY_SECRET_TOKEN", "hunter2")
    monkeypatch.setenv("PYTESTGEN_MODEL", "x")
    result = run_python(PRINT_ENV, tmp_path)
    seen = json.loads(result.stdout)
    assert "MY_SECRET_TOKEN" not in seen
    assert "PYTESTGEN_MODEL" not in seen
    assert seen["PYTHONHASHSEED"] == "0"


# ------------------------------------------------------------------ run_process


def test_run_process_captures_output_and_exit_code(tmp_path: Path) -> None:
    result = run_python(
        "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)", tmp_path
    )
    assert (result.returncode, result.stdout.strip(), result.stderr.strip()) == (3, "out", "err")
    assert not result.timed_out
    assert result.duration_s > 0


def test_run_process_decodes_leniently_and_truncates(tmp_path: Path) -> None:
    code = (
        r"import sys; sys.stdout.buffer.write(b'\xff\xfe' + b'a' * 30000); "
        "sys.stdout.buffer.flush()"
    )
    result = run_python(code, tmp_path)
    assert len(result.stdout) == process_module.OUTPUT_LIMIT
    assert result.stdout.endswith("a")


def test_run_process_timeout_kills_the_whole_tree(tmp_path: Path) -> None:
    # The grandchild inherits the pipes: if it survived the kill, reading the output
    # would block for the full reap timeout.
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print('started', flush=True); time.sleep(60)"
    )
    result = run_python(code, tmp_path, timeout_s=1.0)
    assert result.timed_out
    assert "started" in result.stdout
    assert result.duration_s < 4.5


def test_run_process_reports_unstartable_command(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="cannot start"):
        run_process(["definitely-not-a-program-xyz"], cwd=tmp_path, env={}, timeout_s=1)


def test_spawn_failure_in_missing_cwd(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="cannot start"):
        run_process([sys.executable, "-c", "pass"], cwd=tmp_path / "gone", env={}, timeout_s=1)


# ------------------------------------------------------------------ POSIX-only code paths
# Exercised through fakes so the same coverage is reached on every platform.


def test_posix_limits_hook_sets_address_space_and_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []

    def fail_for_unknown(kind: int, limits: tuple[int, int]) -> None:
        calls.append((kind, limits))
        if kind == 99:
            raise ValueError("not allowed")

    fake = SimpleNamespace(RLIMIT_AS=1, RLIMIT_CPU=2, setrlimit=fail_for_unknown)
    monkeypatch.setattr(process_module.importlib, "import_module", lambda _name: fake)

    process_module._posix_limits(7.2)()

    limit = process_module.MEMORY_LIMIT_BYTES
    assert calls == [(1, (limit, limit)), (2, (13, 13))]  # ceil(7.2) + 5 seconds of CPU


def test_posix_limits_hook_ignores_missing_and_refused_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_kind: int, _limits: tuple[int, int]) -> None:
        raise OSError("denied")

    fake = SimpleNamespace(RLIMIT_CPU=2, setrlimit=refuse)  # no RLIMIT_AS on this platform
    monkeypatch.setattr(process_module.importlib, "import_module", lambda _name: fake)
    process_module._posix_limits(1)()


def test_kill_tree_uses_process_group_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[tuple[int, int]] = []

    def killpg(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        raise ProcessLookupError

    class Proc:
        pid = 4242
        killed = False

        def kill(self) -> None:
            self.killed = True

    monkeypatch.setattr(process_module.sys, "platform", "linux")
    monkeypatch.setattr(os, "killpg", killpg, raising=False)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    proc = Proc()
    process_module._kill_tree(proc)  # type: ignore[arg-type]
    assert killed == [(4242, 9)]
    assert proc.killed


def test_taskkill_tolerates_a_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_a: object, **_k: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    process_module._taskkill(1)


def test_reap_gives_up_when_pipes_stay_open(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stuck:
        pid = 1
        killed = 0

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            raise subprocess.TimeoutExpired("x", timeout or 0)

        def kill(self) -> None:
            self.killed += 1

    monkeypatch.setattr(process_module, "_kill_tree", lambda _p: None)
    proc = Stuck()
    assert process_module._reap(proc) == (b"", b"")  # type: ignore[arg-type]
    assert proc.killed == 1
