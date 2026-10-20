"""The application layer depends on ports and the domain, not on infrastructure adapters."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HEAVY = ("httpx", "tenacity", "z3")
ADAPTERS = ("llm", "report", "sandbox", "concolic", "analysis", "testfiles")

_PROBE = """
import json, sys
import {module}
heavy = {heavy!r}
adapters = tuple('pytestgen.' + name for name in {adapters!r})
print(json.dumps(sorted(
    name for name in sys.modules
    if name.split('.')[0] in heavy or name.startswith(adapters)
)))
"""


def adapter_modules_loaded_by(module: str) -> list[str]:
    """Heavy third-party packages and adapter packages loaded by a fresh ``import module``."""
    code = _PROBE.format(module=module, heavy=HEAVY, adapters=ADAPTERS)
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    loaded: list[str] = json.loads(result.stdout)
    return loaded


@pytest.mark.parametrize(
    "module",
    [
        "pytestgen.application",
        "pytestgen.application.collaborators",
        "pytestgen.application.extract",
        "pytestgen.application.coverage_delta",
        "pytestgen.application.generate",
        "pytestgen.application.analyze",
    ],
)
def test_importing_the_application_loads_no_adapter_or_http_stack(module: str) -> None:
    assert adapter_modules_loaded_by(module) == []


def test_adapters_reexport_the_moved_functions() -> None:
    from pytestgen.application.coverage_delta import compute_delta as moved_delta
    from pytestgen.application.extract import extract_code as moved_code
    from pytestgen.application.extract import extract_json as moved_json
    from pytestgen.llm import extract_code, extract_json
    from pytestgen.report import compute_delta

    assert (extract_code, extract_json, compute_delta) == (moved_code, moved_json, moved_delta)


def test_collaborator_ports_import_only_the_domain() -> None:
    from pytestgen.application import collaborators

    source = Path(collaborators.__file__).read_text(encoding="utf-8")
    imported = {
        line.split()[1] for line in source.splitlines() if line.startswith("from pytestgen")
    }
    assert imported == {"pytestgen.domain.models"}
