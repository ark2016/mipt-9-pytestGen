from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _isolated_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Keep the developer's PYTESTGEN_* variables and .env file out of the tests."""
    import os

    for key in list(os.environ):
        if key.startswith("PYTESTGEN_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    yield


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """``configure_logging`` is process-global (the CLI calls it); undo it after every test.

    Loggers resolve their configuration lazily, so a level or stream left over from one
    test would otherwise hide the debug events another test captures.
    """
    yield
    structlog.reset_defaults()
