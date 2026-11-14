"""Small, independently specified mutation witnesses for repair evaluation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MutationCase:
    name: str
    parameters: str
    contract: str
    expression: str
    mutated_expression: str
    arguments: str
    inputs: dict[str, object]
    expected: str
    incorrect: str
    expected_exception: str | None = None
    incorrect_exception: str | None = None

    def source(self, *, mutant: bool, documented: bool) -> str:
        doc = f'    """{self.contract}"""\n' if documented else ""
        expression = self.mutated_expression if mutant else self.expression
        return f"def {self.name}({self.parameters}):\n{doc}    return {expression}\n"

    def test(self, *, correct: bool) -> str:
        expected = self.expected if correct else self.incorrect
        exception = self.expected_exception if correct else self.incorrect_exception
        assertion = (
            f"    with pytest.raises({exception}):\n        {self.name}({self.arguments})\n"
            if exception
            else f"    assert {self.name}({self.arguments}) == {expected}\n"
        )
        return (
            f"import pytest\nfrom solution import {self.name}\n\ndef test_witness():\n{assertion}"
        )


CASES = (
    MutationCase(
        "eligible",
        "age: int",
        "Return True if age is at least 18.",
        "age >= 18",
        "age > 18",
        "18",
        {"age": 18},
        "True",
        "False",
    ),
    MutationCase(
        "ceil_div",
        "a: int, b: int",
        "For positive integers, return a/b rounded up.",
        "(a + b - 1) // b",
        "a // b",
        "5, 2",
        {"a": 5, "b": 2},
        "3",
        "2",
    ),
    MutationCase(
        "magnitude",
        "x: int",
        "Return the absolute value of x.",
        "-x if x < 0 else x",
        "-x if x > 0 else x",
        "-3",
        {"x": -3},
        "3",
        "-3",
    ),
    MutationCase(
        "sum_values",
        "values: list[int]",
        "Return the sum; the empty list has sum zero.",
        "sum(values)",
        "sum(values) + 1",
        "[]",
        {"values": []},
        "0",
        "1",
    ),
    MutationCase(
        "occurrences",
        "items: list[int], value: int",
        "Count all occurrences of value.",
        "items.count(value)",
        "int(value in items)",
        "[2, 2, 3], 2",
        {"items": [2, 2, 3], "value": 2},
        "2",
        "1",
    ),
    MutationCase(
        "remove_prefix",
        "text: str, prefix: str",
        "Remove exactly one leading prefix.",
        "text.removeprefix(prefix)",
        "text.lstrip(prefix)",
        '"ababx", "ab"',
        {"text": "ababx", "prefix": "ab"},
        "'abx'",
        "'x'",
    ),
    MutationCase(
        "safe_divide",
        "a: int, b: int",
        "Return a//b, or zero when b is zero.",
        "a // b if b else 0",
        "a // b",
        "5, 0",
        {"a": 5, "b": 0},
        "0",
        "1",
    ),
    MutationCase(
        "reciprocal",
        "b: int",
        "Return 1//b. Raise ZeroDivisionError when b is zero.",
        "1 // b",
        "0 if b == 0 else 1 // b",
        "0",
        {"b": 0},
        "",
        "",
        "ZeroDivisionError",
        "ValueError",
    ),
)
