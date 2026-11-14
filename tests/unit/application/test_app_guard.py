from __future__ import annotations

import pytest

from pytestgen.application.guard import check_test_source

SAFE_SOURCES = {
    "plain": "def test_a():\n    assert 1 + 1 == 2\n",
    "empty": "",
    "pytest_raises": (
        "import pytest\nfrom shop.pricing import discount\n\n\n"
        "def test_neg():\n    with pytest.raises(ValueError):\n        discount(-1, 'vip', 1)\n"
    ),
    "tmp_path_write": (
        "def test_file(tmp_path):\n"
        "    target = tmp_path / 'a.txt'\n"
        "    target.write_text('x')\n"
        "    assert target.read_text() == 'x'\n"
    ),
    "monkeypatch": (
        "def test_env(monkeypatch):\n"
        "    monkeypatch.setenv('A', '1')\n"
        "    monkeypatch.setattr('os.sep', '/')\n"
    ),
    "open_read": (
        "def test_r(tmp_path):\n"
        "    p = tmp_path / 'f'\n"
        "    p.touch()\n"
        "    open(p).close()\n"
        "    open(p, 'rb').close()\n"
    ),
    "os_harmless": (
        "import os\n\n\ndef test_p():\n    assert os.path.join('a', 'b')\n    assert os.getcwd()\n"
    ),
    "fixture_and_param": (
        "import pytest\n\n\n@pytest.fixture\ndef cart():\n    return []\n\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_n(cart, n):\n    assert n\n"
    ),
    "compile_attribute": "import re\n\n\ndef test_re():\n    assert re.compile('a').match('a')\n",
    "asyncio_plain": "import asyncio\n\n\ndef test_a():\n    assert asyncio.sleep\n",
}

BAD_SOURCES = {
    "import subprocess": ("import subprocess\n\n\ndef test_a():\n    pass\n", "subprocess"),
    "import socket": ("import socket\n", "socket"),
    "import shutil": ("import shutil\n", "shutil"),
    "import ctypes": ("import ctypes\n", "ctypes"),
    "import multiprocessing": ("import multiprocessing\n", "multiprocessing"),
    "import requests": ("import requests\n", "requests"),
    "import httpx": ("import httpx\n", "httpx"),
    "from urllib.request": ("from urllib.request import urlopen\n", "urllib.request"),
    "import http.client": ("import http.client\n", "http.client"),
    "import ftplib": ("import ftplib\n", "ftplib"),
    "import smtplib": ("import smtplib\n", "smtplib"),
    "import asyncio.subprocess": ("import asyncio.subprocess\n", "asyncio.subprocess"),
    "from asyncio import subprocess": ("from asyncio import subprocess\n", "asyncio.subprocess"),
    "aliased import": ("import subprocess as sp\n", "subprocess"),
    "os.system": ("import os\n\n\ndef test_a():\n    os.system('ls')\n", "os.system"),
    "os.popen": ("import os\n\n\ndef test_a():\n    os.popen('ls')\n", "os.popen"),
    "os.remove": ("import os\n\n\ndef test_a():\n    os.remove('x')\n", "os.remove"),
    "os.unlink": ("import os\n\n\ndef test_a():\n    os.unlink('x')\n", "os.unlink"),
    "os.rmdir": ("import os\n\n\ndef test_a():\n    os.rmdir('x')\n", "os.rmdir"),
    "os.kill": ("import os\n\n\ndef test_a():\n    os.kill(1, 9)\n", "os.kill"),
    "os.execv": ("import os\n\n\ndef test_a():\n    os.execv('/bin/sh', [])\n", "os.execv"),
    "os.spawnl": ("import os\n\n\ndef test_a():\n    os.spawnl(0, 'a')\n", "os.spawnl"),
    "os alias": ("import os as o\n\n\ndef test_a():\n    o.system('ls')\n", "os.system"),
    "from os import": (
        "from os import remove as rm\n\n\ndef test_a():\n    rm('x')\n",
        "os.remove",
    ),
    "eval": ("def test_a():\n    assert eval('1+1') == 2\n", "eval"),
    "exec": ("def test_a():\n    exec('x = 1')\n", "exec"),
    "compile": ("def test_a():\n    compile('1', 'f', 'eval')\n", "compile"),
    "__import__": ("def test_a():\n    __import__('subprocess')\n", "__import__"),
    "open w": ("def test_a():\n    open('f', 'w')\n", "write mode"),
    "open mode kw": ("def test_a():\n    open('f', mode='a+')\n", "write mode"),
    "open x": ("def test_a():\n    open('f', 'xb')\n", "write mode"),
    "open dynamic": ("def test_a(m):\n    open('f', m)\n", "non-literal"),
    "io.open": ("import io\n\n\ndef test_a():\n    io.open('f', 'w')\n", "write mode"),
    "__builtins__": ("def test_a():\n    __builtins__\n", "__builtins__"),
    "__globals__": ("def test_a():\n    test_a.__globals__\n", "__globals__"),
    "__subclasses__": ("def test_a():\n    object.__subclasses__()\n", "__subclasses__"),
    "getattr string": ("def test_a():\n    getattr(test_a, '__globals__')\n", "__globals__"),
    "asyncio subprocess call": (
        "import asyncio\n\n\nasync def f():\n    await asyncio.create_subprocess_exec('ls')\n",
        "create_subprocess_exec",
    ),
}


@pytest.mark.parametrize("source", SAFE_SOURCES.values(), ids=SAFE_SOURCES.keys())
def test_safe_sources_have_no_violations(source: str) -> None:
    assert check_test_source(source) == []


@pytest.mark.parametrize(("source", "needle"), BAD_SOURCES.values(), ids=BAD_SOURCES.keys())
def test_forbidden_constructs_are_reported(source: str, needle: str) -> None:
    violations = check_test_source(source)
    assert violations
    assert any(needle in v for v in violations), violations


def test_syntax_error_is_reported_with_line() -> None:
    violations = check_test_source("def test_a(:\n    pass\n")
    assert len(violations) == 1
    assert violations[0].startswith("syntax error")
    assert "line 1" in violations[0]


def test_null_bytes_count_as_syntax_error() -> None:
    assert check_test_source("x = 1\x00")[0].startswith("syntax error")


def test_violations_carry_line_numbers_and_are_sorted_without_duplicates() -> None:
    source = "import subprocess\n\n\ndef test_a():\n    eval('1')\n    eval('2')\n"
    violations = check_test_source(source)
    assert violations[0].startswith("line 1:")
    assert violations[1].startswith("line 5:")
    assert violations[2].startswith("line 6:")
    assert len(violations) == len(set(violations))


def test_relative_imports_are_not_mistaken_for_forbidden_modules() -> None:
    assert check_test_source("from . import subprocess_helpers\n") == []


def test_calls_on_expressions_are_ignored() -> None:
    assert check_test_source("def test_a():\n    assert [].copy() == []\n    (lambda: 1)()\n") == []
