from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from pytestgen.domain.models import FunctionInfo, ModuleInfo, Observation, ObservationKind
from pytestgen.errors import ModuleImportError, SandboxError
from pytestgen.ports import ExecutionRecorder
from pytestgen.sandbox import SubprocessExecutionRecorder

ModuleFactory = Callable[[str, str], ModuleInfo]
FunctionFactory = Callable[..., FunctionInfo]

SOURCE = """\
import asyncio


class Custom(Exception):
    pass


class BadStr(Exception):
    def __str__(self):
        raise RuntimeError("no str for you")


COUNTER = 0


def add(a, b=1):
    return a + b


def echo(x):
    return x


def boom(x):
    raise ValueError("bad %s" % x)


def custom(x):
    raise Custom("custom failure")


def badstr(x):
    raise BadStr()


def new_object(x):
    return object()


def not_a_number(x):
    return float("nan")


def counter(x):
    global COUNTER
    COUNTER += 1
    return COUNTER


def raises_alternately(x):
    global COUNTER
    COUNTER += 1
    if COUNTER % 2:
        raise ValueError("odd")
    return 1


def hang(x):
    if x:
        while True:
            pass
    return 0


def crash(x):
    import os
    os._exit(3)


async def doubled(x):
    await asyncio.sleep(0)
    return x * 2


def star(first, *rest, **options):
    return [first, list(rest), options]


def posonly(a, /, b):
    return a - b


def printer(x):
    print("noise on stdout")
    return x


def mutator(items):
    items.append(1)
    return items


class Account:
    @staticmethod
    def fee(amount):
        return amount // 10

    def balance(self):
        return 0
"""


@pytest.fixture
def recorder() -> SubprocessExecutionRecorder:
    return SubprocessExecutionRecorder(timeout_s=10.0)


@pytest.fixture
def module(project: Path, make_module: ModuleFactory) -> ModuleInfo:
    (project / "recmod.py").write_text(SOURCE, encoding="utf-8")
    return make_module("recmod", SOURCE)


@pytest.fixture
def observe_fn(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> Callable[..., Observation]:
    """``observe_fn(name, inputs)`` -> the single Observation for one input vector."""

    def go(name: str, inputs: Mapping[str, object]) -> Observation:
        return observe_one(recorder, project, module, make_function(name), inputs)

    return go


def observe_one(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    function: FunctionInfo,
    inputs: Mapping[str, object],
) -> Observation:
    (result,) = recorder.observe(project, module, function, [inputs])
    return result


def test_recorder_implements_the_port() -> None:
    assert isinstance(SubprocessExecutionRecorder(), ExecutionRecorder)


@pytest.mark.parametrize(
    ("name", "inputs", "kind", "value_repr"),
    [
        ("add", {"a": 2, "b": 3}, ObservationKind.RETURNED, "5"),
        ("add", {"a": 2}, ObservationKind.RETURNED, "3"),
        ("echo", {"x": "text"}, ObservationKind.RETURNED, "'text'"),
        ("echo", {"x": b"\x00\xff"}, ObservationKind.RETURNED, "b'\\x00\\xff'"),
        ("echo", {"x": (1, [2.5, None], {"k": True})}, ObservationKind.RETURNED, None),
        ("echo", {"x": 1.5}, ObservationKind.RETURNED, "1.5"),
        ("new_object", {"x": 1}, ObservationKind.NOT_LITERAL, None),
        ("not_a_number", {"x": 1}, ObservationKind.NOT_LITERAL, "nan"),
        ("printer", {"x": 7}, ObservationKind.RETURNED, "7"),
    ],
)
def test_returned_and_not_literal(
    observe_fn: Callable[..., Observation],
    name: str,
    inputs: dict[str, Any],
    kind: ObservationKind,
    value_repr: str | None,
) -> None:
    obs = observe_fn(name, inputs)
    assert obs.kind is kind
    assert dict(obs.inputs) == inputs
    if value_repr is not None:
        assert obs.value_repr == value_repr
    if kind is ObservationKind.RETURNED:
        assert obs.exception_type is None


@pytest.mark.parametrize(
    ("name", "exception_type", "message"),
    [
        ("boom", "ValueError", "bad 1"),
        ("custom", "recmod.Custom", "custom failure"),
        ("badstr", "recmod.BadStr", "()"),  # __str__ raises: the message falls back to args
    ],
)
def test_raised(
    observe_fn: Callable[..., Observation],
    name: str,
    exception_type: str,
    message: str,
) -> None:
    obs = observe_fn(name, {"x": 1})
    assert obs.kind is ObservationKind.RAISED
    assert obs.exception_type == exception_type
    assert obs.exception_message == message


def test_batch_keeps_input_order_and_isolates_runs(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    inputs = [{"a": 1, "b": 1}, {"a": "x", "b": 1}, {"a": 5, "b": 5}]
    result = recorder.observe(project, module, make_function("add"), inputs)
    assert [o.kind for o in result] == [
        ObservationKind.RETURNED,
        ObservationKind.RAISED,
        ObservationKind.RETURNED,
    ]
    assert [o.value_repr for o in result] == ["2", None, "10"]
    assert result[1].exception_type == "TypeError"


def test_unstable_when_repeated_runs_disagree(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    obs = observe_one(recorder, project, module, make_function("counter"), {"x": 0})
    assert obs.kind is ObservationKind.UNSTABLE


def test_unstable_when_one_run_raises_and_the_other_returns(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    obs = observe_one(recorder, project, module, make_function("raises_alternately"), {"x": 0})
    assert obs.kind is ObservationKind.UNSTABLE


def test_single_repeat_cannot_detect_instability(
    project: Path, module: ModuleInfo, make_function: FunctionFactory
) -> None:
    once = SubprocessExecutionRecorder(repeats=1)
    obs = observe_one(once, project, module, make_function("counter"), {"x": 0})
    assert obs.kind is ObservationKind.RETURNED
    assert obs.value_repr == "1"


def test_each_run_gets_a_private_copy_of_mutable_inputs(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    obs = observe_one(recorder, project, module, make_function("mutator"), {"items": []})
    assert obs.kind is ObservationKind.RETURNED
    assert obs.value_repr == "[1]"


def test_async_functions_are_awaited(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    obs = observe_one(recorder, project, module, make_function("doubled"), {"x": 21})
    assert (obs.kind, obs.value_repr) == (ObservationKind.RETURNED, "42")


def test_static_method_by_qualname(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    function = make_function("Account.fee", class_name="Account", is_static=True)
    obs = observe_one(recorder, project, module, function, {"amount": 95})
    assert (obs.kind, obs.value_repr) == (ObservationKind.RETURNED, "9")


def test_instance_methods_are_refused(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    function = make_function("Account.balance", class_name="Account")
    with pytest.raises(SandboxError, match="instance method"):
        recorder.observe(project, module, function, [{}])


@pytest.mark.parametrize(
    ("name", "inputs", "expected"),
    [
        ("star", {"first": 1, "rest": [2, 3], "options": {"k": 4}}, "[1, [2, 3], {'k': 4}]"),
        ("star", {"first": 1}, "[1, [], {}]"),
        ("posonly", {"a": 10, "b": 4}, "6"),
    ],
)
def test_argument_binding(
    observe_fn: Callable[..., Observation],
    name: str,
    inputs: dict[str, Any],
    expected: str,
) -> None:
    obs = observe_fn(name, inputs)
    assert obs.value_repr == expected


def test_no_inputs_means_no_work(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    assert recorder.observe(project, module, make_function("add"), []) == []


def test_inputs_must_be_plain_literals(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    with pytest.raises(SandboxError, match="plain Python literals"):
        recorder.observe(project, module, make_function("echo"), [{"x": math.nan}])


def test_hanging_input_is_marked_timeout_without_losing_the_others(
    project: Path, module: ModuleInfo, make_function: FunctionFactory
) -> None:
    quick = SubprocessExecutionRecorder(timeout_s=0.6)
    result = quick.observe(project, module, make_function("hang"), [{"x": 0}, {"x": 1}])
    assert [o.kind for o in result] == [ObservationKind.RETURNED, ObservationKind.TIMEOUT]
    assert result[0].value_repr == "0"
    assert "timed out" in (result[1].exception_message or "")


def test_crashing_worker_is_reported_as_unstable(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    obs = observe_one(recorder, project, module, make_function("crash"), {"x": 1})
    assert obs.kind is ObservationKind.UNSTABLE
    assert "worker crashed (exit 3)" in (obs.exception_message or "")


def test_import_failure_raises_module_import_error(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    make_module: ModuleFactory,
    make_function: FunctionFactory,
) -> None:
    (project / "broken.py").write_text("import module_that_does_not_exist\n", encoding="utf-8")
    with pytest.raises(ModuleImportError, match="module_that_does_not_exist"):
        recorder.observe(project, make_module("broken", ""), make_function("f"), [{}])


def test_import_time_sys_exit_is_an_import_error(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    make_module: ModuleFactory,
    make_function: FunctionFactory,
) -> None:
    (project / "exits.py").write_text("raise SystemExit(2)\n", encoding="utf-8")
    with pytest.raises(ModuleImportError, match="SystemExit"):
        recorder.observe(project, make_module("exits", ""), make_function("f"), [{}])


def test_missing_function_is_a_sandbox_error(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    with pytest.raises(SandboxError, match="no_such_function"):
        recorder.observe(project, module, make_function("no_such_function"), [{}])


def test_original_project_is_not_modified(
    recorder: SubprocessExecutionRecorder,
    project: Path,
    module: ModuleInfo,
    make_function: FunctionFactory,
) -> None:
    before = sorted(p.name for p in project.rglob("*"))
    recorder.observe(project, module, make_function("add"), [{"a": 1}])
    assert sorted(p.name for p in project.rglob("*")) == before
