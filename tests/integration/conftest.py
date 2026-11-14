from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from e2e_support import Scenario, ScriptedLLMServer, copy_examples, make_settings, run_scenario

from pytestgen.config import Settings


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A fresh copy of the demo project (``examples/``)."""
    return copy_examples(tmp_path / "project")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def llm(settings: Settings) -> Iterator[ScriptedLLMServer]:
    """A scripted model behind ``settings.llm_base_url``; any other HTTP call fails the test."""
    server = ScriptedLLMServer()
    with respx.mock(assert_all_called=False) as router:
        server.install(router, settings.llm_base_url)
        yield server


@pytest.fixture(scope="module")
def pricing(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    """The hybrid pipeline on ``shop/pricing.py``; the expensive run is shared by a module."""
    return run_scenario(tmp_path_factory.mktemp("pricing"), "pricing.py")


@pytest.fixture(scope="module")
def cart(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    """The hybrid pipeline on ``shop/cart.py`` (free-form tests for the methods)."""
    return run_scenario(tmp_path_factory.mktemp("cart"), "cart.py")
