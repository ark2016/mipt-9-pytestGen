"""Environment for sandboxed processes: an allow-list, never a copy of the parent's variables."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_COMMON = ("PATH",)
_WINDOWS = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC")


def build_sandbox_env(pythonpath: list[Path], home: Path) -> dict[str, str]:
    """Minimal environment: no API keys, tokens, proxies or ``PYTESTGEN_*`` settings.

    Only ``PATH`` (and the handful of variables Windows needs to start a process)
    is inherited; everything else is set explicitly.
    """
    names = _COMMON + (_WINDOWS if sys.platform == "win32" else ())
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update(
        PYTHONPATH=os.pathsep.join(str(p) for p in pythonpath),
        PYTHONHASHSEED="0",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        HOME=str(home),
        USERPROFILE=str(home),
    )
    return env
