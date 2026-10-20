from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from pytestgen.domain.models import FunctionInfo, ModuleInfo
from pytestgen.ports import ModuleIntrospector
from pytestgen.sandbox import SubprocessIntrospector

ModuleFactory = Callable[[str, str], ModuleInfo]
FunctionFactory = Callable[..., FunctionInfo]

SOURCE = """\
from __future__ import annotations

import functools
from typing import Optional


def plain(a: int, b: str = "x") -> Optional[list[int]]:
    return None


async def fetch(url: str) -> bytes:
    return b""


def unresolved(x: Undefined, y: int) -> int:
    return y


def untyped(a, b):
    return a


def decorated(f):
    @functools.wraps(f)
    def inner(*args, **kwargs):
        return f(*args, **kwargs)

    return inner


@decorated
def wrapped(a: float) -> float:
    return a


class Box:
    @staticmethod
    def make(size: int) -> Box:
        return Box()

    @property
    def size(self) -> int:
        return 1
"""


@pytest.fixture
def introspected(
    project: Path, make_module: ModuleFactory, make_function: FunctionFactory
) -> ModuleInfo:
    (project / "introd.py").write_text(SOURCE, encoding="utf-8")
    module = make_module("introd", SOURCE)
    names = ["plain", "fetch", "unresolved", "untyped", "wrapped", "Box.make", "Box.size", "gone"]
    functions = tuple(make_function(name) for name in names)
    return ModuleInfo(
        path=module.path, module_name=module.module_name, source=SOURCE, functions=functions
    )


def test_introspector_implements_the_port() -> None:
    assert isinstance(SubprocessIntrospector(), ModuleIntrospector)


def test_importable_module_signatures_hints_and_coroutines(
    project: Path, introspected: ModuleInfo
) -> None:
    result = SubprocessIntrospector().introspect(project, introspected)

    assert result.importable
    assert result.error is None
    assert result.signatures["plain"] == "(a: 'int', b: 'str' = 'x') -> 'Optional[list[int]]'"
    assert result.type_hints["plain"] == {
        "a": "int",
        "b": "str",
        "return": "Optional[list[int]]",
    }
    assert result.coroutines == {"fetch"}
    assert result.type_hints["fetch"] == {"url": "str", "return": "bytes"}
    assert result.type_hints["untyped"] == {}
    assert result.signatures["untyped"] == "(a, b)"


def test_unresolvable_annotation_keeps_the_other_hints(
    project: Path, introspected: ModuleInfo
) -> None:
    result = SubprocessIntrospector().introspect(project, introspected)
    assert result.type_hints["unresolved"] == {"y": "int", "return": "int"}
    assert "x: " in result.signatures["unresolved"]


def test_decorated_methods_and_properties(project: Path, introspected: ModuleInfo) -> None:
    result = SubprocessIntrospector().introspect(project, introspected)
    assert result.signatures["wrapped"] == "(a: 'float') -> 'float'"  # wraps() exposes the original
    assert "size: 'int'" in result.signatures["Box.make"]
    assert result.signatures["Box.size"] == "(self) -> 'int'"
    assert result.type_hints["Box.make"]["return"] == "Box"


def test_unresolvable_functions_are_skipped(project: Path, introspected: ModuleInfo) -> None:
    result = SubprocessIntrospector().introspect(project, introspected)
    assert "gone" not in result.signatures
    assert "gone" not in result.type_hints


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def broken(:\n", "SyntaxError"),
        ("import module_that_does_not_exist\n", "ModuleNotFoundError"),
        ("raise RuntimeError('side effect at import')\n", "side effect at import"),
        ("raise SystemExit(1)\n", "SystemExit"),
    ],
)
def test_import_failures_are_reported(
    project: Path, make_module: ModuleFactory, source: str, expected: str
) -> None:
    (project / "bad.py").write_text(source, encoding="utf-8")
    result = SubprocessIntrospector().introspect(project, make_module("bad", source))
    assert not result.importable
    assert result.error is not None
    assert expected in result.error
    assert result.signatures == {}


def test_error_is_limited_to_the_last_traceback_lines(
    project: Path, make_module: ModuleFactory
) -> None:
    source = "def deep(n):\n    return deep(n + 1) if n < 40 else 1 / 0\n\nresult = deep(0)\n"
    (project / "deepmod.py").write_text(source, encoding="utf-8")
    result = SubprocessIntrospector().introspect(project, make_module("deepmod", source))
    assert result.error is not None
    assert len(result.error.splitlines()) <= 15
    assert "ZeroDivisionError" in result.error


def test_import_that_never_finishes_times_out(project: Path, make_module: ModuleFactory) -> None:
    source = "while True:\n    pass\n"
    (project / "spin.py").write_text(source, encoding="utf-8")
    result = SubprocessIntrospector(timeout_s=1.0).introspect(project, make_module("spin", source))
    assert not result.importable
    assert result.error is not None
    assert "timed out" in result.error


def test_process_that_dies_without_an_answer(project: Path, make_module: ModuleFactory) -> None:
    source = "import os\nos._exit(7)\n"
    (project / "dies.py").write_text(source, encoding="utf-8")
    result = SubprocessIntrospector().introspect(project, make_module("dies", source))
    assert not result.importable
    assert result.error == "introspection crashed"
