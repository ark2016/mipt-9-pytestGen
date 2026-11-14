"""Module, class and function extraction by AstModuleAnalyzer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from analysis_support import FIXTURES, analyze_fixture, analyze_text, line_of

from pytestgen.analysis import AstModuleAnalyzer
from pytestgen.domain.models import ModuleInfo, Parameter, ParamKind
from pytestgen.errors import AnalysisError
from pytestgen.ports import ModuleAnalyzer

SOURCE = '''"""Module doc."""

import os
import os.path as osp
import numpy as np
from collections import abc
from . import sibling
from .pkg import thing
from ..up import other

try:
    import ujson
except ImportError:
    ujson = None

if os.name == "nt":
    import msvcrt
else:
    import termios

with open(os.devnull) as handle:
    import json


def plain(a, b=2, /, c=3, *args, d, e=5, **kwargs) -> int:
    """Plain doc.

    More text.
    """
    return a


async def fetch(url: str) -> bytes:
    return b""


@staticmethod
@custom.decorator(1, key="v")
def decorated(x):
    return x


def outer():
    def inner():
        raise KeyError("x")

    class Local:
        def method(self):
            return 1

    return inner


class Base:
    """Base doc."""

    def __init__(self, size: int, *, name: str = "n") -> None:
        self.size = size

    def method(self, value: list[int] | None = None) -> int:
        return 0

    async def amethod(self) -> None:
        return None

    @property
    def prop(self) -> int:
        return 1

    @prop.setter
    def prop(self, value: int) -> None:
        pass

    @functools.cached_property
    def cached(self) -> int:
        return 2

    @staticmethod
    def stat(x: int) -> int:
        return x

    @classmethod
    def make(cls) -> "Base":
        return cls(1)

    class Nested:
        def hidden(self):
            return 1

    def _helper(self):
        def not_collected():
            return 1

        return not_collected


class Child(Base, metaclass=Meta):
    pass
'''


@pytest.fixture
def module(tmp_path: Path) -> ModuleInfo:
    return analyze_text(tmp_path, SOURCE)


def test_analyzer_satisfies_the_port() -> None:
    assert isinstance(AstModuleAnalyzer(), ModuleAnalyzer)


def test_module_level_metadata(module: ModuleInfo, tmp_path: Path) -> None:
    assert module.path == (tmp_path / "mod.py").resolve()
    assert module.module_name == "mod"
    assert module.source == SOURCE
    assert module.docstring == "Module doc."


def test_imports_are_unique_dotted_names_in_source_order(module: ModuleInfo) -> None:
    assert module.imports == (
        "os",
        "os.path",
        "numpy",
        "collections",
        ".",
        ".pkg",
        "..up",
        "ujson",
        "msvcrt",
        "termios",
        "json",
    )


def test_collected_functions_skip_nested_scopes(module: ModuleInfo) -> None:
    assert [f.qualname for f in module.functions] == [
        "plain",
        "fetch",
        "decorated",
        "outer",
        "Base.__init__",
        "Base.method",
        "Base.amethod",
        "Base.prop",
        "Base.prop",
        "Base.cached",
        "Base.stat",
        "Base.make",
        "Base._helper",
    ]


def test_parameters_keep_kind_annotation_and_default(module: ModuleInfo) -> None:
    plain = module.function("plain")
    assert plain.parameters == (
        Parameter("a", ParamKind.POSITIONAL_ONLY),
        Parameter("b", ParamKind.POSITIONAL_ONLY, default="2"),
        Parameter("c", ParamKind.POSITIONAL_OR_KEYWORD, default="3"),
        Parameter("args", ParamKind.VAR_POSITIONAL),
        Parameter("d", ParamKind.KEYWORD_ONLY),
        Parameter("e", ParamKind.KEYWORD_ONLY, default="5"),
        Parameter("kwargs", ParamKind.VAR_KEYWORD),
    )
    assert plain.return_annotation == "int"
    method = module.function("Base.method")
    assert method.parameters[1] == Parameter("value", annotation="list[int] | None", default="None")
    assert [p.name for p in method.call_parameters] == ["value"]


def test_docstring_is_cleaned(module: ModuleInfo) -> None:
    assert module.function("plain").docstring == "Plain doc.\n\nMore text."
    assert module.function("fetch").docstring is None


def test_decorator_flags(module: ModuleInfo) -> None:
    decorated = module.function("decorated")
    assert decorated.decorators == ("staticmethod", "custom.decorator(1, key='v')")
    assert decorated.is_static
    assert module.function("Base.stat").is_static
    assert module.function("Base.make").is_classmethod
    assert not module.function("Base.method").is_static
    assert module.function("fetch").is_async
    assert module.function("Base.amethod").is_async
    assert not module.function("plain").is_async


def test_property_detection(module: ModuleInfo) -> None:
    props = [f for f in module.functions if f.qualname == "Base.prop"]
    assert [f.is_property for f in props] == [True, True]
    assert module.function("Base.cached").is_property
    assert not module.function("Base.method").is_property


def test_line_numbers_point_at_def_not_decorator(module: ModuleInfo) -> None:
    decorated = module.function("decorated")
    assert decorated.lineno == line_of(SOURCE, "def decorated")
    assert decorated.lineno - line_of(SOURCE, "@staticmethod") == 2
    assert decorated.end_lineno == decorated.lineno + 1


def test_source_includes_decorators_and_is_dedented(module: ModuleInfo) -> None:
    assert module.function("decorated").source == (
        '@staticmethod\n@custom.decorator(1, key="v")\ndef decorated(x):\n    return x'
    )
    method = module.function("Base.method").source
    assert method == ("def method(self, value: list[int] | None = None) -> int:\n    return 0")
    compile(method, "<method>", "exec")
    setter = module.function("Base.prop").source
    assert setter.startswith("@property")


def test_classes(module: ModuleInfo) -> None:
    base, child = module.classes
    assert base.name == "Base"
    assert base.docstring == "Base doc."
    assert base.bases == ()
    assert base.init_parameters == (
        Parameter("size", annotation="int"),
        Parameter("name", ParamKind.KEYWORD_ONLY, "str", "'n'"),
    )
    assert base.methods == (
        "Base.__init__",
        "Base.method",
        "Base.amethod",
        "Base.prop",
        "Base.prop",
        "Base.cached",
        "Base.stat",
        "Base.make",
        "Base._helper",
    )
    assert base.lineno == line_of(SOURCE, "class Base")
    assert base.end_lineno > base.lineno
    assert child.bases == ("Base",)
    assert child.init_parameters == ()
    assert child.methods == ()
    assert child.docstring is None


def test_methods_know_their_class(module: ModuleInfo) -> None:
    method = module.function("Base.method")
    assert method.class_name == "Base"
    assert method.is_method
    assert method.name == "method"
    assert module.function("plain").class_name is None


def test_function_lookup_by_line(module: ModuleInfo) -> None:
    line = line_of(SOURCE, "return not_collected")
    assert module.function_at_line(line) is not None
    assert module.function_at_line(line).qualname == "Base._helper"  # type: ignore[union-attr]


def test_raises_and_calls_ignore_nested_scopes(tmp_path: Path) -> None:
    module = analyze_text(
        tmp_path,
        """
def work(x, items):
    if x is None:
        raise ValueError("no x")
    if x < 0:
        raise errors.Custom
    try:
        helper(x)
    except KeyError:
        raise
    except OSError as exc:
        raise RuntimeError("wrapped") from exc
    if x == 1:
        raise ValueError("again")
    items.append(x)
    obj.attr.method(1).chained()
    (lambda: hidden())()
    return other.func(len(items))


def outer():
    def inner():
        raise KeyError("nested")
        return hidden_call()

    class Local:
        def m(self):
            raise IndexError
    return inner
""",
    )
    work = module.function("work")
    assert work.raises == ("ValueError", "errors.Custom", "RuntimeError")
    assert work.calls == (
        "ValueError",
        "helper",
        "RuntimeError",
        "items.append",
        "obj.attr.method",
        "other.func",
        "len",
    )
    outer = module.function("outer")
    assert outer.raises == ()
    assert outer.calls == ()


def test_calls_are_limited_to_thirty(tmp_path: Path) -> None:
    body = "\n".join(f"    f{i}()" for i in range(50))
    module = analyze_text(tmp_path, f"def many():\n{body}\n")
    assert module.function("many").calls == tuple(f"f{i}" for i in range(30))


def test_function_without_return_annotation_and_end_lineno(tmp_path: Path) -> None:
    module = analyze_text(tmp_path, "def f(x):\n    return x\n\n\nx = 1\n")
    function = module.function("f")
    assert function.return_annotation is None
    assert (function.lineno, function.end_lineno) == (1, 2)


def test_empty_module(tmp_path: Path) -> None:
    module = analyze_text(tmp_path, "")
    assert module.functions == ()
    assert module.classes == ()
    assert module.imports == ()
    assert module.docstring is None


def test_src_layout_module_name(tmp_path: Path) -> None:
    package = tmp_path / "src" / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "prices.py").write_text("def total(x):\n    return x\n", encoding="utf-8")
    module = AstModuleAnalyzer().analyze(package / "prices.py", tmp_path)
    assert module.module_name == "shop.prices"
    package_module = AstModuleAnalyzer().analyze(package / "__init__.py", tmp_path)
    assert package_module.module_name == "shop"


def test_fixture_module_roundtrip() -> None:
    module = analyze_fixture("methods")
    assert module.module_name == "methods"
    assert module.path == (FIXTURES / "methods.py").resolve()
    assert module.function("Account.rich").is_property
    assert module.function("Account.empty").is_classmethod
    assert module.function("Account.fee").is_static
    assert module.function("Account.empty").return_annotation == "'Account'"


def test_crlf_and_bom_sources_are_read_like_python_does(tmp_path: Path) -> None:
    path = tmp_path / "crlf.py"
    path.write_bytes(b"\xef\xbb\xbfdef f(x):\r\n    if x:\r\n        return 1\r\n    return 2\r\n")
    module = AstModuleAnalyzer().analyze(path, tmp_path)
    assert module.source == "def f(x):\n    if x:\n        return 1\n    return 2\n"
    assert module.function("f").source.count("\n") == 3


def test_coding_cookie_is_honoured(tmp_path: Path) -> None:
    path = tmp_path / "latin.py"
    path.write_bytes("# -*- coding: latin-1 -*-\nNAME = 'caf\xe9'\n".encode("latin-1"))
    module = AstModuleAnalyzer().analyze(path, tmp_path)
    assert "café" in module.source


# --------------------------------------------------------------------------- errors


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError, match="cannot read"):
        AstModuleAnalyzer().analyze(tmp_path / "nope.py", tmp_path)


def test_directory_instead_of_file(tmp_path: Path) -> None:
    (tmp_path / "pkg.py").mkdir()
    with pytest.raises(AnalysisError, match="cannot read"):
        AstModuleAnalyzer().analyze(tmp_path / "pkg.py", tmp_path)


def test_syntax_error_reports_line_number(tmp_path: Path) -> None:
    path = tmp_path / "broken.py"
    path.write_text("x = 1\n\ndef f(:\n    pass\n", encoding="utf-8")
    with pytest.raises(AnalysisError, match=r"broken\.py:3: syntax error"):
        AstModuleAnalyzer().analyze(path, tmp_path)


def test_invalid_utf8_is_an_analysis_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.py"
    path.write_bytes(b"x = '\xff\xfe'\n")
    with pytest.raises(AnalysisError, match="cannot read"):
        AstModuleAnalyzer().analyze(path, tmp_path)


def test_unknown_coding_cookie_is_an_analysis_error(tmp_path: Path) -> None:
    path = tmp_path / "cookie.py"
    path.write_bytes(b"# coding: no-such-codec\nx = 1\n")
    with pytest.raises(AnalysisError, match="cannot read"):
        AstModuleAnalyzer().analyze(path, tmp_path)


def test_null_bytes_are_an_analysis_error(tmp_path: Path) -> None:
    path = tmp_path / "nul.py"
    path.write_bytes(b"x = 1\x00\n")
    with pytest.raises(AnalysisError):
        AstModuleAnalyzer().analyze(path, tmp_path)


def test_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AnalysisError, match="outside the project root"):
        AstModuleAnalyzer().analyze(outside, root)


def test_analyzer_never_executes_the_module(tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('boom')\n"
        "def f():\n    return 1\n"
    )
    module = analyze_text(tmp_path, source)
    assert module.function("f")
    assert not marker.exists()


def test_value_error_from_the_parser_is_an_analysis_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ast

    def boom(*args: object, **kwargs: object) -> ast.Module:
        raise ValueError("source code string cannot contain null bytes")

    monkeypatch.setattr(ast, "parse", boom)
    path = tmp_path / "m.py"
    path.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AnalysisError, match="cannot parse"):
        AstModuleAnalyzer().analyze(path, tmp_path)


def test_analyze_project_root_is_an_analysis_error(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError):
        AstModuleAnalyzer().analyze(tmp_path, tmp_path)


def test_method_source_keeps_multiline_string_values(tmp_path: Path) -> None:
    source = (
        "class A:\n"
        "    def m(self):\n"
        "        return '''\n"
        "        x'''\n"
        "\n"
        "    def f(self, n):\n"
        '        return f"""\n'
        '      {n}"""\n'
    )
    module = analyze_text(tmp_path, source)
    method = module.function("A.m")
    namespace: dict[str, Any] = {}
    exec(method.source, namespace)  # noqa: S102  trusted literal from this test
    assert namespace["m"](None) == "\n        x"
    assert method.source.startswith("def m(self):\n    return")
    fstring = module.function("A.f")
    exec(fstring.source, namespace)  # noqa: S102
    assert namespace["f"](None, 1) == "\n      1"


def test_raising_a_variable_is_not_reported_as_a_type(tmp_path: Path) -> None:
    module = analyze_text(
        tmp_path,
        "def f(x):\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError as e:\n"
        "        raise e\n"
        "    if x:\n"
        "        raise e\n"
        "    if x > 1:\n"
        "        raise errors.Custom()\n"
        "    if x > 2:\n"
        "        raise handlers[0]\n"
        "    raise factory()\n",
    )
    function = module.function("f")
    assert function.raises == ("errors.Custom",)
    var_branch = [b for b in function.branches if b.line == 6 and b.outcome]
    assert [b.raises for b in var_branch] == [None]
    custom = [b for b in function.branches if b.line == 8 and b.outcome]
    assert [b.raises for b in custom] == ["errors.Custom"]


def test_source_falls_back_to_plain_dedent_when_tokenizing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenize

    def boom(*args: object, **kwargs: object) -> None:
        raise tokenize.TokenError("boom")

    monkeypatch.setattr(tokenize, "generate_tokens", boom)
    module = analyze_text(tmp_path, "class A:\n    def m(self):\n        return 1\n")
    assert module.function("A.m").source == "def m(self):\n    return 1"
