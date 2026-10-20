"""In-process tests of the sandbox worker: it only needs the standard library."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from pytestgen.sandbox import _driver as driver


class CustomError(Exception):
    pass


class BadStrError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("no str")


class BadRepr:
    def __repr__(self) -> str:
        raise RuntimeError("no repr")


# ------------------------------------------------------------------ exceptions and values


def test_exception_names_are_qualified_unless_builtin() -> None:
    assert driver.exception_name(ValueError("x")) == "ValueError"
    assert driver.exception_name(CustomError()) == f"{__name__}.CustomError"


def test_exception_message_is_truncated_and_survives_a_broken_str() -> None:
    assert driver.exception_message(ValueError("m" * 1000)) == "m" * driver.MESSAGE_LIMIT
    assert driver.exception_message(BadStrError("a", 1)) == "('a', 1)"


@pytest.mark.parametrize(
    ("value", "kind", "text"),
    [
        (5, "returned", "5"),
        ((1, [2]), "returned", "(1, [2])"),
        (float("nan"), "not_literal", "nan"),
        (float("inf"), "not_literal", "inf"),
        ({1, 2}, "returned", "{1, 2}"),
        (range(3), "not_literal", "range(0, 3)"),
        ("x" * 600, "returned", repr("x" * 600)),
    ],
)
def test_describe_value(value: object, kind: str, text: str) -> None:
    assert driver.describe_value(value) == {"kind": kind, "value_repr": text}


def test_describe_value_truncates_long_non_literals() -> None:
    described = driver.describe_value(object_with_long_repr())
    assert described["kind"] == "not_literal"
    assert len(described["value_repr"]) == driver.MESSAGE_LIMIT


def object_with_long_repr() -> object:
    class Long:
        def __repr__(self) -> str:
            return "<" + "z" * 2000 + ">"

    return Long()


def test_describe_value_with_broken_repr() -> None:
    assert driver.describe_value(BadRepr()) == {
        "kind": "not_literal",
        "value_repr": "<unrepresentable BadRepr>",
    }


# ------------------------------------------------------------------ building calls


def sample(a: int, b: int = 2, /, c: int = 3, *rest: int, d: int = 4, **options: int) -> None: ...


def test_build_call_maps_every_parameter_kind() -> None:
    args, kwargs = driver.build_call(
        sample, {"a": 1, "b": 5, "c": 6, "rest": [7, 8], "d": 9, "options": {"z": 0}}
    )
    assert args == [1, 5, 6, 7, 8]
    assert kwargs == {"d": 9, "z": 0}


def test_build_call_uses_defaults_for_missing_positional_only() -> None:
    args, kwargs = driver.build_call(sample, {"a": 1})
    assert args == [1, 2]
    assert kwargs == {}


def test_build_call_falls_back_to_keywords_without_a_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_func: object) -> None:
        raise ValueError("no signature")

    monkeypatch.setattr(inspect, "signature", refuse)
    assert driver.build_call(sample, {"a": 1}) == ([], {"a": 1})


def test_build_call_omits_positional_only_parameters_that_have_no_value() -> None:
    def needs(a: int, /) -> None: ...

    assert driver.build_call(needs, {}) == ([], {})


# ------------------------------------------------------------------ calling and observing


def test_call_once_records_returns_and_raises() -> None:
    assert driver.call_once(lambda x: x + 1, {"x": 1}) == {"kind": "returned", "value_repr": "2"}
    raised = driver.call_once(lambda x: 1 / x, {"x": 0})
    assert raised["kind"] == "raised"
    assert raised["exception_type"] == "ZeroDivisionError"


def test_call_once_records_system_exit_but_not_keyboard_interrupt() -> None:
    def leave(x: int) -> None:
        raise SystemExit(x)

    def interrupt(x: int) -> None:
        raise KeyboardInterrupt

    assert driver.call_once(leave, {"x": 3})["exception_type"] == "SystemExit"
    with pytest.raises(KeyboardInterrupt):
        driver.call_once(interrupt, {"x": 1})


def test_call_once_runs_coroutines() -> None:
    async def twice(x: int) -> int:
        return x * 2

    assert driver.call_once(twice, {"x": 4}) == {"kind": "returned", "value_repr": "8"}


def test_call_once_copies_the_inputs() -> None:
    seen: list[Any] = []

    def grow(items: list[int]) -> int:
        items.append(1)
        seen.append(items)
        return len(items)

    original: dict[str, Any] = {"items": []}
    assert driver.call_once(grow, original)["value_repr"] == "1"
    assert original == {"items": []}


def test_observe_flags_disagreement_and_respects_repeats() -> None:
    calls = {"n": 0}

    def counter(x: int) -> int:
        calls["n"] += 1
        return calls["n"]

    assert driver.observe(counter, {"x": 0}, 2)["kind"] == "unstable"
    assert driver.observe(counter, {"x": 0}, 1)["kind"] == "returned"
    assert driver.observe(counter, {"x": 0}, 0)["kind"] == "returned"  # at least one run


def test_observe_stable_function() -> None:
    assert driver.observe(lambda x: x, {"x": 1}, 3) == {"kind": "returned", "value_repr": "1"}


# ------------------------------------------------------------------ importing and resolving


def test_resolve_walks_dotted_names() -> None:
    assert driver.resolve(json, "decoder.JSONDecoder") is json.decoder.JSONDecoder


def test_import_target_reports_tracebacks() -> None:
    module, error = driver.import_target("json")
    assert module is json
    assert error is None
    module, error = driver.import_target("module_that_does_not_exist_xyz")
    assert module is None
    assert error is not None
    assert "ModuleNotFoundError" in error


def test_record_statuses() -> None:
    ok = driver.record({"module": "operator", "qualname": "add", "repeats": 1}, [{"a": 1, "b": 2}])
    assert ok == {"status": "ok", "results": [{"kind": "returned", "value_repr": "3"}]}
    missing = driver.record({"module": "operator", "qualname": "nope"}, [{}])
    assert missing["status"] == "resolve_error"
    broken = driver.record({"module": "module_that_does_not_exist_xyz", "qualname": "f"}, [{}])
    assert broken["status"] == "import_error"


# ------------------------------------------------------------------ type hints


def test_type_text() -> None:
    assert driver.type_text(type(None)) == "None"
    assert driver.type_text(int) == "int"
    assert driver.type_text(list[int]) == "list[int]"
    assert driver.type_text(Optional[str]) == "Optional[str]"  # noqa: UP045


def test_type_hints_resolve_stringified_annotations() -> None:
    def func(a: int, b: list[str] | None = None) -> bool: ...

    assert driver.type_hints(func) == {"a": "int", "b": "list[str] | None", "return": "bool"}


def test_type_hints_keep_resolvable_names_when_one_is_undefined() -> None:
    def func(x: Undefined, y: int) -> int: ...  # type: ignore[name-defined]  # noqa: F821

    assert driver.type_hints(func) == {"y": "int", "return": "int"}


def test_type_hints_of_objects_without_annotations() -> None:
    assert driver.type_hints(print) == {}
    assert driver.type_hints(42) == {}


def test_describe_function_variants() -> None:
    class Holder:
        @property
        def size(self) -> int:
            return 1

    described = driver.describe_function(Holder.size)
    assert described is not None
    assert described["signature"] == "(self) -> 'int'"
    assert described["coroutine"] is False
    assert driver.describe_function(42) is None


def test_introspect_reports_skipped_names() -> None:
    result = driver.introspect({"module": "json", "qualnames": ["dumps", "no_such_name"]})
    assert result["status"] == "ok"
    assert set(result["functions"]) == {"dumps"}
    assert result["skipped"] == ["no_such_name"]
    broken = driver.introspect({"module": "module_that_does_not_exist_xyz", "qualnames": []})
    assert broken["status"] == "import_error"


# ------------------------------------------------------------------ entry point


def test_main_record_mode_writes_json_and_silences_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = tmp_path / "spec.json"
    out = tmp_path / "out.json"
    inputs = tmp_path / "inputs.pylit"
    spec.write_text(json.dumps({"module": "operator", "qualname": "mul", "repeats": 2}))
    inputs.write_text("[{'a': 3, 'b': 4}]")

    assert driver.main(["driver", "record", str(spec), str(out), str(inputs)]) == 0

    payload = json.loads(out.read_text())
    assert payload["results"] == [{"kind": "returned", "value_repr": "12"}]
    assert capsys.readouterr().out == ""


def test_main_introspect_mode(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    out = tmp_path / "out.json"
    spec.write_text(json.dumps({"module": "json", "qualnames": ["loads"]}))
    assert driver.main(["driver", "introspect", str(spec), str(out)]) == 0
    assert json.loads(out.read_text())["status"] == "ok"
