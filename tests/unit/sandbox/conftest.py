from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from pytestgen.domain.models import FunctionInfo, ModuleInfo

CALC_SOURCE = """\
def classify(x):
    if x > 0:
        return "pos"
    if x < 0:
        return "neg"
    return "zero"


def never_called():
    return 1
"""

OTHER_SOURCE = "def helper():\n    return 42\n"

ModuleFactory = Callable[[str, str], ModuleInfo]
FunctionFactory = Callable[..., FunctionInfo]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tiny project: the measured target ``calc.py`` and an unrelated ``other.py``."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text(CALC_SOURCE, encoding="utf-8")
    (root / "other.py").write_text(OTHER_SOURCE, encoding="utf-8")
    return root


@pytest.fixture
def make_module(tmp_path: Path) -> ModuleFactory:
    def factory(name: str, source: str) -> ModuleInfo:
        return ModuleInfo(path=tmp_path / f"{name}.py", module_name=name, source=source)

    return factory


@pytest.fixture
def make_function() -> FunctionFactory:
    def factory(
        qualname: str, *, class_name: str | None = None, is_static: bool = False
    ) -> FunctionInfo:
        return FunctionInfo(
            qualname=qualname,
            name=qualname.rsplit(".", 1)[-1],
            lineno=1,
            end_lineno=1,
            source="",
            class_name=class_name,
            is_static=is_static,
        )

    return factory
