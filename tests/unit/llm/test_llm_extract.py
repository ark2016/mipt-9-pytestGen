from __future__ import annotations

from typing import Any

import pytest

from pytestgen.errors import ExtractionError
from pytestgen.llm import extract_code, extract_json

# --------------------------------------------------------------------------- extract_code


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("```python\nx = 1\n```", "x = 1", id="python-fence"),
        pytest.param("```py\nx = 1\n```", "x = 1", id="py-label"),
        pytest.param("```Python\nx = 1\n```", "x = 1", id="label-case-insensitive"),
        pytest.param("Sure!\n```python\nx = 1\n```\nHope it helps.", "x = 1", id="prose-around"),
        pytest.param("```\nx = 1\n```", "x = 1", id="unlabelled-fence"),
        pytest.param("```bash\nx = 1\n```", "x = 1", id="other-label-as-fallback"),
        pytest.param("~~~python\nx = 1\n~~~", "x = 1", id="tilde-fence"),
        pytest.param("x = 1\ny = 2\n", "x = 1\ny = 2", id="no-fence"),
        pytest.param("  \n\n  x = 1  \n\n", "x = 1", id="surrounding-whitespace"),
        pytest.param("```python title=a.py\nx = 1\n```", "x = 1", id="info-string-extras"),
        pytest.param(
            "```python\ndef f():\n    return 1\n",
            "def f():\n    return 1",
            id="unterminated-fence",
        ),
        pytest.param(
            "```python\nshort = 1\n```\ntext\n```python\nlonger = 1\nlongest = 2\n```",
            "longer = 1\nlongest = 2",
            id="longest-python-block-wins",
        ),
        pytest.param(
            "```python\na = 1\n```\n```\nmuch_longer_unlabelled = 12345\n```",
            "a = 1",
            id="python-label-beats-longer-unlabelled",
        ),
        pytest.param(
            "```text\nnot code\n```\n```\nb = 2\nc = 3\n```",
            "b = 2\nc = 3",
            id="longest-of-any-when-no-python",
        ),
        pytest.param(
            "```python\nfirst = 1\n```\nand then\n```python\nsecond = 2\nthird = 3\n",
            "second = 2\nthird = 3",
            id="complete-block-then-unterminated",
        ),
        pytest.param(
            "1. Here:\n   ```python\n   x = 1\n   y = 2\n   ```\n",
            "x = 1\ny = 2",
            id="indented-fence-in-list",
        ),
        pytest.param("  ```python\n  x = 1\n  y = 2\n  ```", "x = 1\ny = 2", id="indented-fence"),
        pytest.param(
            "```python\r\nx = 1\r\n```\r\n",
            "x = 1",
            id="crlf",
        ),
    ],
)
def test_extract_code(text: str, expected: str) -> None:
    assert extract_code(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   \n\t", "```python\n```", "```\n\n```", "``` ```"],
    ids=["empty", "blank", "empty-python-fence", "empty-fence", "inline-fence"],
)
def test_extract_code_empty_raises(text: str) -> None:
    with pytest.raises(ExtractionError):
        extract_code(text)


def test_extract_code_syntax_error_reports_line() -> None:
    with pytest.raises(ExtractionError, match=r"line 2") as info:
        extract_code("```python\nx = 1\ndef broken(:\n```")
    assert "not valid Python" in str(info.value)


def test_extract_code_cut_off_mid_expression_is_error() -> None:
    with pytest.raises(ExtractionError, match="not valid Python"):
        extract_code("```python\nassert foo(1,\n")


def test_extract_code_plain_prose_is_error() -> None:
    with pytest.raises(ExtractionError):
        extract_code("I am sorry, I cannot help with that.")


# --------------------------------------------------------------------------- extract_json


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param('{"a": 1}', {"a": 1}, id="plain"),
        pytest.param('  \n{"a": 1}\n ', {"a": 1}, id="whitespace"),
        pytest.param('```json\n{"a": 1}\n```', {"a": 1}, id="json-fence"),
        pytest.param('```\n{"a": 1}\n```', {"a": 1}, id="bare-fence"),
        pytest.param('```json\n{"a": 1}', {"a": 1}, id="unterminated-fence"),
        pytest.param('Sure, here you go:\n{"a": [1, 2]}\nCheers', {"a": [1, 2]}, id="prose-around"),
        pytest.param(
            'Here:\n```json\n{"a": 1}\n```\nAnd code:\n```python\nx = {1}\n```',
            {"a": 1},
            id="json-block-preferred-over-others",
        ),
        pytest.param(
            '{"a": {"b": {"c": 1}}, "d": []}', {"a": {"b": {"c": 1}}, "d": []}, id="nested"
        ),
        pytest.param(
            'x {"s": "}{ not structure"} y', {"s": "}{ not structure"}, id="braces-in-str"
        ),
        pytest.param(
            r'pre {"s": "quote \" and } brace"} post',
            {"s": 'quote " and } brace'},
            id="escaped-quote",
        ),
        pytest.param(r'{"s": "back\\"} tail', {"s": "back\\"}, id="escaped-backslash-at-end"),
        pytest.param('{"a": 1,}', {"a": 1}, id="trailing-comma-object"),
        pytest.param(
            '{"a": [1, 2,\n ], "b": {"c": 1, },}',
            {"a": [1, 2], "b": {"c": 1}},
            id="trailing-commas-nested",
        ),
        pytest.param(
            '{"s": "keep, } this,"}', {"s": "keep, } this,"}, id="comma-in-string-untouched"
        ),
        pytest.param('Use {name} then {"ok": true}', {"ok": True}, id="skips-non-json-braces"),
        pytest.param(
            'Use { then {"ok": true}', {"ok": True}, id="stray-unbalanced-brace-before-object"
        ),
        pytest.param(
            'Note: use "{" for objects. {"ok": true}',
            {"ok": True},
            id="stray-brace-in-prose-string",
        ),
        pytest.param(
            'The string "{\\"x\\": 1}" is not it; {"real": 1}',
            {"real": 1},
            id="escaped-json-inside-prose-string",
        ),
        pytest.param('{"a": 1} {"b": 2}', {"a": 1}, id="first-object-wins"),
        pytest.param('[{"a": 1}]', {"a": 1}, id="object-inside-array"),
        pytest.param('{"ключ": "значение"}', {"ключ": "значение"}, id="unicode"),
    ],
)
def test_extract_json(text: str, expected: dict[str, Any]) -> None:
    assert extract_json(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no json here",
        "[1, 2, 3]",
        '"just a string"',
        '{"a": 1',
        'prefix {"a": [1, 2} suffix',
        "{not: json}",
    ],
    ids=["empty", "prose", "array", "string", "truncated", "unbalanced", "bare-keys"],
)
def test_extract_json_invalid_raises(text: str) -> None:
    with pytest.raises(ExtractionError):
        extract_json(text)
