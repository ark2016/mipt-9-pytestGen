"""Builders for codegen tests: tiny domain objects and a real module to run tests against."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pytestgen.domain.models import (
    CaseOrigin,
    ClassInfo,
    FunctionInfo,
    ModuleInfo,
    Observation,
    ObservationKind,
    OracleVerdict,
    Parameter,
    ParamKind,
    TestCaseSpec,
)

KEYWORD_ONLY = ParamKind.KEYWORD_ONLY
POSITIONAL_ONLY = ParamKind.POSITIONAL_ONLY
VAR_POSITIONAL = ParamKind.VAR_POSITIONAL
VAR_KEYWORD = ParamKind.VAR_KEYWORD

CALC_SOURCE = '''\
"""Small real module the generated tests are executed against."""


class Boom(Exception):
    pass


def add(a, b):
    return a + b


def div(a, b):
    if b == 0:
        raise ZeroDivisionError("b is zero")
    return a / b


def scale(x, *, factor=2.0):
    return x * factor


def greet(name="world", punct="!"):
    return "hello " + name + punct


def pick(a, b=10, c=100):
    return a + b + c


def mixed(a, b=2, *args):
    return (a, b, args)


def posonly(a=1, b=2, /):
    return (a, b)


def total(*args, **kwargs):
    return sum(args) + sum(kwargs.values())


def answer():
    return 42


def explode():
    raise Boom("always")


async def fetch(n):
    return n * 2


async def fail_async(n):
    raise Boom(n)


def mean(values):
    return sum(values) / len(values)


def expected(expected, exc_type):
    return [expected, exc_type]


class Account:
    class Insufficient(Exception):
        pass

    def __init__(self, balance=0):
        self.balance = balance

    @staticmethod
    def fee(amount):
        return amount * 0.5

    @classmethod
    def make(cls, balance):
        return cls(balance).balance

    def deposit(self, amount):
        return self.balance + amount

    def withdraw(self, amount):
        raise Boom(amount)

    def overdraw(self, amount):
        raise Account.Insufficient(amount)


class Needy:
    def __init__(self, token):
        self.token = token

    def show(self):
        return self.token
'''


def param(
    name: str, kind: ParamKind = ParamKind.POSITIONAL_OR_KEYWORD, default: str | None = None
) -> Parameter:
    return Parameter(name=name, kind=kind, default=default)


def function(
    qualname: str,
    *params: Parameter,
    class_name: str | None = None,
    **flags: bool,
) -> FunctionInfo:
    return FunctionInfo(
        qualname=qualname,
        name=qualname.rsplit(".", 1)[-1],
        lineno=1,
        end_lineno=2,
        source="",
        parameters=params,
        class_name=class_name,
        **flags,
    )


def calc_functions() -> tuple[FunctionInfo, ...]:
    """Signatures that match ``CALC_SOURCE``."""
    self_ = param("self")
    return (
        function("add", param("a"), param("b")),
        function("div", param("a"), param("b")),
        function("scale", param("x"), param("factor", KEYWORD_ONLY, "2.0")),
        function("greet", param("name", default="'world'"), param("punct", default="'!'")),
        function("pick", param("a"), param("b", default="10"), param("c", default="100")),
        function("mixed", param("a"), param("b", default="2"), param("args", VAR_POSITIONAL)),
        function(
            "posonly",
            param("a", POSITIONAL_ONLY, "1"),
            param("b", POSITIONAL_ONLY, "2"),
        ),
        function("total", param("args", VAR_POSITIONAL), param("kwargs", VAR_KEYWORD)),
        function("answer"),
        function("explode"),
        function("fetch", param("n"), is_async=True),
        function("fail_async", param("n"), is_async=True),
        function("mean", param("values")),
        function("expected", param("expected"), param("exc_type")),
        function("Account.fee", param("amount"), class_name="Account", is_static=True),
        function(
            "Account.make",
            param("cls"),
            param("balance"),
            class_name="Account",
            is_classmethod=True,
        ),
        function("Account.deposit", self_, param("amount"), class_name="Account"),
        function("Account.withdraw", self_, param("amount"), class_name="Account"),
        function("Account.overdraw", self_, param("amount"), class_name="Account"),
        function("Needy.show", self_, class_name="Needy"),
    )


def module(
    functions: tuple[FunctionInfo, ...], name: str = "calc", classes: tuple[ClassInfo, ...] = ()
) -> ModuleInfo:
    return ModuleInfo(
        path=Path(f"{name}.py"), module_name=name, source="", functions=functions, classes=classes
    )


def calc_module() -> ModuleInfo:
    classes = (
        ClassInfo("Account", 1, 2, init_parameters=(param("self"), param("balance", default="0"))),
        ClassInfo("Needy", 1, 2, init_parameters=(param("self"), param("token"))),
    )
    return module(calc_functions(), classes=classes)


def returned(
    fn: str,
    inputs: Mapping[str, object],
    value: object,
    origin: CaseOrigin = CaseOrigin.Z3,
    verdict: OracleVerdict = OracleVerdict.OK,
) -> TestCaseSpec:
    obs = Observation(inputs, ObservationKind.RETURNED, value_repr=repr(value))
    return TestCaseSpec(fn, inputs, obs, origin, verdict=verdict)


def raised(
    fn: str,
    inputs: Mapping[str, object],
    exception: str,
    origin: CaseOrigin = CaseOrigin.Z3,
) -> TestCaseSpec:
    obs = Observation(inputs, ObservationKind.RAISED, exception_type=exception)
    return TestCaseSpec(fn, inputs, obs, origin)


def with_kind(
    fn: str, inputs: Mapping[str, object], kind: ObservationKind, **kw: str
) -> TestCaseSpec:
    return TestCaseSpec(fn, inputs, Observation(inputs, kind, **kw), CaseOrigin.Z3)
