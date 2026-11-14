from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from app_factories import analyze_example, raised, returned
from fakes_llm import ScriptedLLM, code_reply, json_reply
from structlog.testing import capture_logs

from pytestgen.application.slots import FreeFormWriter, InputProposer, OracleReviewer, WrittenTests
from pytestgen.domain.models import (
    Arc,
    BranchKind,
    BranchTarget,
    DropReason,
    FunctionInfo,
    ModuleInfo,
    Observation,
    ObservationKind,
    OracleVerdict,
    Parameter,
    ParamKind,
    PathCondition,
    TestCaseSpec,
)
from pytestgen.errors import LLMConnectionError, LLMResponseError
from pytestgen.ports import LLMClient


@pytest.fixture(scope="module")
def pricing() -> ModuleInfo:
    return analyze_example("shop/pricing.py")


@pytest.fixture(scope="module")
def cart() -> ModuleInfo:
    return analyze_example("shop/cart.py")


def events(logs: list[dict[str, Any]], level: str) -> list[str]:
    return [str(entry["event"]) for entry in logs if entry["log_level"] == level]


def test_scripted_llm_satisfies_the_port() -> None:
    assert isinstance(ScriptedLLM(), LLMClient)


# --------------------------------------------------------------------------- OracleReviewer


class TestOracleReviewer:
    @pytest.fixture
    def function(self, pricing: ModuleInfo) -> FunctionInfo:
        return pricing.function("free_shipping")

    @pytest.fixture
    def cases(self) -> list[TestCaseSpec]:
        return [
            returned({"order_total": 100.0}, "False", "free_shipping"),
            returned({"order_total": 101.0}, "True", "free_shipping"),
            returned({"order_total": 5.0}, "False", "free_shipping"),
        ]

    def test_applies_verdicts_notes_and_keeps_order(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        reply = json_reply(
            {
                "reviews": [
                    {"index": 0, "verdict": "suspicious", "explanation": "100 should qualify"},
                    {"index": 1, "verdict": "ok", "explanation": ""},
                    {"index": 2, "verdict": "ok", "explanation": "small order"},
                ]
            },
            before="Sure! Here is my review:\n",
            after="\nHope this helps.",
        )
        llm = ScriptedLLM().queue("oracle_review", reply)
        result = OracleReviewer(llm).review(pricing, function, cases)
        assert [c.verdict for c in result] == [
            OracleVerdict.SUSPICIOUS,
            OracleVerdict.OK,
            OracleVerdict.OK,
        ]
        assert result[0].note == "100 should qualify"
        assert result[2].note == "small order"
        assert [c.inputs for c in result] == [c.inputs for c in cases]
        assert all(orig.verdict is OracleVerdict.UNREVIEWED for orig in cases)

    def test_request_uses_the_oracle_schema_and_lists_every_case(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        llm = ScriptedLLM(json_reply({"reviews": []}))
        OracleReviewer(llm).review(pricing, function, cases)
        (request,) = llm.requests
        assert request.purpose == "oracle_review"
        assert request.response_schema is not None
        assert request.response_schema.name == "oracle_review"
        prompt = llm.prompt_for("oracle_review")
        assert "[0] free_shipping(order_total=100.0) -> False" in prompt
        assert "[2] free_shipping(order_total=5.0) -> False" in prompt
        assert "100 or more qualifies" in prompt

    def test_suspicious_without_explanation_is_treated_as_ok(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        reply = json_reply(
            {
                "reviews": [
                    {"index": 0, "verdict": "suspicious", "explanation": ""},
                    {"index": 1, "verdict": "suspicious", "explanation": "   "},
                    {"index": 2, "verdict": "suspicious"},
                ]
            }
        )
        result = OracleReviewer(ScriptedLLM(reply)).review(pricing, function, cases)
        assert [c.verdict for c in result] == [OracleVerdict.OK] * 3
        assert [c.note for c in result] == [""] * 3

    def test_unmentioned_cases_stay_unreviewed(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        reply = json_reply({"reviews": [{"index": 1, "verdict": "ok", "explanation": ""}]})
        result = OracleReviewer(ScriptedLLM(reply)).review(pricing, function, cases)
        assert [c.verdict for c in result] == [
            OracleVerdict.UNREVIEWED,
            OracleVerdict.OK,
            OracleVerdict.UNREVIEWED,
        ]

    @pytest.mark.parametrize(
        "entry",
        [
            {"index": 7, "verdict": "suspicious", "explanation": "out of range"},
            {"index": -1, "verdict": "ok", "explanation": ""},
            {"index": True, "verdict": "ok", "explanation": ""},
            {"index": None, "verdict": "ok", "explanation": ""},
            {"index": 1.5, "verdict": "ok", "explanation": ""},
            {"index": "first", "verdict": "ok", "explanation": ""},
            {"index": 0, "verdict": "maybe", "explanation": "unsure"},
            {"index": 0, "verdict": None, "explanation": "x"},
            {"verdict": "ok"},
            "case 0 is fine",
            42,
            None,
        ],
    )
    def test_junk_entries_are_ignored(
        self,
        pricing: ModuleInfo,
        function: FunctionInfo,
        cases: list[TestCaseSpec],
        entry: object,
    ) -> None:
        llm = ScriptedLLM(json_reply({"reviews": [entry]}))
        result = OracleReviewer(llm).review(pricing, function, cases)
        assert [c.verdict for c in result] == [OracleVerdict.UNREVIEWED] * 3

    def test_lenient_index_and_verdict_spelling(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        reply = json_reply(
            {
                "reviews": [
                    {"index": "0", "verdict": " Suspicious ", "explanation": "bug", "extra": 1},
                    {"index": 1.0, "verdict": "OK", "explanation": None},
                ],
                "overall": "meh",
            }
        )
        result = OracleReviewer(ScriptedLLM(reply)).review(pricing, function, cases)
        assert result[0].verdict is OracleVerdict.SUSPICIOUS
        assert result[1].verdict is OracleVerdict.OK
        assert result[1].note == ""

    def test_first_verdict_for_an_index_wins(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        reply = json_reply(
            {
                "reviews": [
                    {"index": 0, "verdict": "ok", "explanation": ""},
                    {"index": 0, "verdict": "suspicious", "explanation": "later"},
                ]
            }
        )
        result = OracleReviewer(ScriptedLLM(reply)).review(pricing, function, cases)
        assert result[0].verdict is OracleVerdict.OK

    def test_long_explanations_are_cut(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        reply = json_reply(
            {"reviews": [{"index": 0, "verdict": "suspicious", "explanation": "x" * 2000}]}
        )
        result = OracleReviewer(ScriptedLLM(reply)).review(pricing, function, cases)
        assert len(result[0].note) == 300

    def test_max_cases_limits_the_prompt_and_leaves_the_rest_unreviewed(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        everything = [{"index": i, "verdict": "ok", "explanation": ""} for i in range(3)]
        llm = ScriptedLLM(json_reply({"reviews": everything}))
        result = OracleReviewer(llm, max_cases=2).review(pricing, function, cases)
        assert [c.verdict for c in result] == [
            OracleVerdict.OK,
            OracleVerdict.OK,
            OracleVerdict.UNREVIEWED,
        ]
        assert "[2]" not in llm.prompt_for("oracle_review")

    def test_no_docstring_means_no_llm_call(
        self, pricing: ModuleInfo, function: FunctionInfo, cases: list[TestCaseSpec]
    ) -> None:
        llm = ScriptedLLM()
        for docstring in (None, "", "  \n"):
            result = OracleReviewer(llm).review(
                pricing, replace(function, docstring=docstring), cases
            )
            assert [c.verdict for c in result] == [OracleVerdict.UNREVIEWED] * 3
        assert llm.calls == 0

    def test_only_returned_and_raised_cases_are_reviewed(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        timeout = replace(
            returned({"order_total": 1.0}, "", "free_shipping"),
            observation=Observation(inputs={"order_total": 1.0}, kind=ObservationKind.TIMEOUT),
        )
        boom = raised({"order_total": -1.0}, "ValueError", "negative", "free_shipping")
        llm = ScriptedLLM(
            json_reply({"reviews": [{"index": 1, "verdict": "ok", "explanation": ""}]})
        )
        result = OracleReviewer(llm).review(pricing, function, [timeout, boom])
        assert [c.verdict for c in result] == [OracleVerdict.UNREVIEWED, OracleVerdict.OK]
        prompt = llm.prompt_for("oracle_review")
        assert "[1] free_shipping(order_total=-1.0) raises ValueError: negative" in prompt
        assert "[0]" not in prompt

    def test_nothing_reviewable_means_no_llm_call(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        timeout = replace(
            returned({}, "", "free_shipping"),
            observation=Observation(inputs={}, kind=ObservationKind.TIMEOUT),
        )
        llm = ScriptedLLM()
        assert OracleReviewer(llm).review(pricing, function, [timeout]) == (timeout,)
        assert OracleReviewer(llm).review(pricing, function, []) == ()
        assert llm.calls == 0

    @pytest.mark.parametrize(
        "reply",
        [
            LLMConnectionError("endpoint down"),
            LLMResponseError("HTTP 500"),
            "I think all of them look fine to me!",
            '```json\n{"reviews": [\n```',
            json_reply({"reviews": "all ok"}),
            json_reply({"verdicts": []}),
            "",
        ],
        ids=["connection", "response", "prose", "truncated", "wrong-shape", "wrong-key", "empty"],
    )
    def test_failures_degrade_to_unchanged_cases_with_a_warning(
        self,
        pricing: ModuleInfo,
        function: FunctionInfo,
        cases: list[TestCaseSpec],
        reply: str | Exception,
    ) -> None:
        llm = ScriptedLLM(reply)
        with capture_logs() as logs:
            result = OracleReviewer(llm).review(pricing, function, cases)
        assert result == tuple(cases)
        assert any(e.startswith("oracle_review_") for e in events(logs, "warning"))


# --------------------------------------------------------------------------- InputProposer


def fn_with(*params: Parameter, name: str = "f", class_name: str | None = None) -> FunctionInfo:
    return FunctionInfo(
        qualname=f"{class_name}.{name}" if class_name else name,
        name=name,
        lineno=1,
        end_lineno=2,
        source=f"def {name}(): ...",
        parameters=params,
        class_name=class_name,
    )


def param(
    name: str,
    annotation: str | None = None,
    default: str | None = None,
    kind: ParamKind = ParamKind.POSITIONAL_OR_KEYWORD,
) -> Parameter:
    return Parameter(name=name, kind=kind, annotation=annotation, default=default)


class TestInputProposer:
    @pytest.fixture
    def discount(self, pricing: ModuleInfo) -> FunctionInfo:
        return pricing.function("discount")

    def propose(
        self,
        pricing: ModuleInfo,
        function: FunctionInfo,
        reply: str | Exception,
        *,
        max_inputs: int = 6,
    ) -> list[dict[str, object]]:
        llm = ScriptedLLM(reply)
        return InputProposer(llm, max_inputs=max_inputs).propose(
            pricing, function, function.branches[:2], {}
        )

    def test_returns_valid_vectors_from_chatty_output(
        self, pricing: ModuleInfo, discount: FunctionInfo
    ) -> None:
        vectors = [
            {"price": 10.0, "customer_type": "vip", "years": 3},
            {"years": 0, "customer_type": "regular", "price": 5},
        ]
        reply = json_reply({"inputs": vectors}, before="Here are inputs:\n", after="\nEnjoy!")
        result = self.propose(pricing, discount, reply)
        assert result == vectors
        assert list(result[1]) == ["price", "customer_type", "years"]  # parameter order

    def test_request_shape(self, pricing: ModuleInfo, discount: FunctionInfo) -> None:
        llm = ScriptedLLM(json_reply({"inputs": []}))
        target = discount.branches[0]
        InputProposer(llm, max_inputs=3).propose(
            pricing, discount, [target], {target.key: "unsupported: foo"}
        )
        (request,) = llm.requests
        assert request.purpose == "propose_inputs"
        assert request.response_schema is not None
        assert request.response_schema.name == "propose_inputs"
        prompt = llm.prompt_for("propose_inputs")
        assert "unsupported: foo" in prompt
        assert "up to 3" in prompt

    def test_no_targets_means_no_call(self, pricing: ModuleInfo, discount: FunctionInfo) -> None:
        llm = ScriptedLLM()
        assert InputProposer(llm).propose(pricing, discount, [], {}) == []
        assert llm.calls == 0

    @pytest.mark.parametrize(
        "vector",
        [
            {"price": 1.0, "customer_type": "vip"},
            {"price": 1.0, "customer_type": "vip", "years": 1, "coupon": "X"},
            {"price": "ten", "customer_type": "vip", "years": 1},
            {"price": 1.0, "customer_type": 5, "years": 1},
            {"price": 1.0, "customer_type": "vip", "years": "3"},
            {"price": 1.0, "customer_type": "vip", "years": 2.5},
            {"price": 1.0, "customer_type": "vip", "years": True},
            {"price": None, "customer_type": "vip", "years": 1},
            {"price": [1.0], "customer_type": "vip", "years": 1},
            {},
            [],
            "price=1",
            7,
            None,
        ],
    )
    def test_invalid_vectors_are_dropped(
        self, pricing: ModuleInfo, discount: FunctionInfo, vector: object
    ) -> None:
        assert self.propose(pricing, discount, json_reply({"inputs": [vector]})) == []

    def test_invalid_vectors_do_not_hide_valid_ones(
        self, pricing: ModuleInfo, discount: FunctionInfo
    ) -> None:
        good = {"price": 1.0, "customer_type": "vip", "years": 1}
        reply = json_reply({"inputs": [{"price": 1}, good, "junk"]})
        assert self.propose(pricing, discount, reply) == [good]

    def test_integral_floats_become_ints_for_int_parameters(
        self, pricing: ModuleInfo, discount: FunctionInfo
    ) -> None:
        reply = '{"inputs": [{"price": 3, "customer_type": "vip", "years": 4.0}]}'
        (vector,) = self.propose(pricing, discount, reply)
        assert vector["years"] == 4
        assert isinstance(vector["years"], int)

    def test_non_finite_numbers_are_dropped(
        self, pricing: ModuleInfo, discount: FunctionInfo
    ) -> None:
        reply = '{"inputs": [{"price": NaN, "customer_type": "vip", "years": 1}]}'
        assert self.propose(pricing, discount, reply) == []

    def test_duplicates_are_removed(self, pricing: ModuleInfo, discount: FunctionInfo) -> None:
        a = {"price": 1.0, "customer_type": "vip", "years": 1}
        b = {"years": 1, "customer_type": "vip", "price": 1.0}
        c = {"price": 2.0, "customer_type": "vip", "years": 1}
        assert self.propose(pricing, discount, json_reply({"inputs": [a, b, c, a]})) == [a, c]

    def test_at_most_max_inputs(self, pricing: ModuleInfo, discount: FunctionInfo) -> None:
        many = [{"price": float(i), "customer_type": "vip", "years": 1} for i in range(10)]
        result = self.propose(pricing, discount, json_reply({"inputs": many}), max_inputs=4)
        assert result == many[:4]

    @pytest.mark.parametrize(
        "payload",
        [
            {"inputs": {"price": 1.0, "customer_type": "vip", "years": 1}},
            {"price": 1.0, "customer_type": "vip", "years": 1},
        ],
        ids=["single-object", "bare-vector"],
    )
    def test_tolerates_a_single_vector(
        self, pricing: ModuleInfo, discount: FunctionInfo, payload: dict[str, object]
    ) -> None:
        assert len(self.propose(pricing, discount, json_reply(payload))) == 1

    @pytest.mark.parametrize(
        "reply",
        [
            LLMConnectionError("down"),
            LLMResponseError("bad body"),
            "no idea, sorry",
            json_reply({"inputs": "none"}),
            json_reply({"answer": 42}),
            json_reply({}),
        ],
        ids=["connection", "response", "prose", "wrong-type", "wrong-key", "empty-object"],
    )
    def test_failures_return_an_empty_list_and_warn(
        self, pricing: ModuleInfo, discount: FunctionInfo, reply: str | Exception
    ) -> None:
        with capture_logs() as logs:
            assert self.propose(pricing, discount, reply) == []
        assert any(e.startswith("propose_inputs_") for e in events(logs, "warning"))

    def test_dropped_vectors_are_logged_at_debug(
        self, pricing: ModuleInfo, discount: FunctionInfo
    ) -> None:
        with capture_logs() as logs:
            self.propose(pricing, discount, json_reply({"inputs": [{"price": 1}]}))
        assert "input_vector_dropped" in events(logs, "debug")


class TestInputValidationRules:
    """Validation against crafted signatures (annotations, defaults, methods)."""

    def run(self, function: FunctionInfo, *vectors: object) -> list[dict[str, object]]:
        module = ModuleInfo(path=Path("m.py"), module_name="m", source="")
        llm = ScriptedLLM(json_reply({"inputs": list(vectors)}))
        target = any_target(function)
        return InputProposer(llm).propose(module, function, [target], {})

    def test_lists_become_tuples_when_annotated_as_tuple(self) -> None:
        fn = fn_with(
            param("point", "tuple[int, int]"),
            param("path", "list[tuple[float, float]]"),
            param("many", "tuple[int, ...]"),
            param("any_", "tuple"),
            param("opt", "tuple[int, int] | None", "None"),
            param("table", "dict[str, tuple[int, int]]", "None"),
            param("plain", "list[int]", "None"),
            param("odd", "tuple[int, str, bool]", "None"),
        )
        (vector,) = self.run(
            fn,
            {
                "point": [1, 2],
                "path": [[0.5, 1.5], [2, 3]],
                "many": [1, 2, 3],
                "any_": [1, "a"],
                "opt": [3, 4],
                "table": {"a": [1, 2]},
                "plain": [1, 2],
                "odd": [1, "x"],
            },
        )
        assert vector["point"] == (1, 2)
        assert vector["path"] == [(0.5, 1.5), (2, 3)]
        assert vector["many"] == (1, 2, 3)
        assert vector["any_"] == (1, "a")
        assert vector["opt"] == (3, 4)
        assert vector["table"] == {"a": (1, 2)}
        assert vector["plain"] == [1, 2]
        assert vector["odd"] == (1, "x")

    def test_qualified_typing_annotations(self) -> None:
        fn = fn_with(param("pair", "typing.Tuple[int, int]"), param("n", "typing.Optional[int]"))
        assert self.run(fn, {"pair": [1, 2], "n": None}) == [{"pair": (1, 2), "n": None}]

    def test_optional_and_union_annotations(self) -> None:
        fn = fn_with(
            param("a", "int | None"),
            param("b", "Optional[str]"),
            param("c", "Union[int, str]"),
            param("d", "float"),
            param("e", "bool"),
        )
        good = {"a": None, "b": "x", "c": "s", "d": 3, "e": False}
        assert self.run(fn, {**good, "b": None}) == [{**good, "b": None}]
        assert self.run(fn, good) == [good]
        for bad in (
            {"a": "1", "b": "x", "c": 1, "d": 1.0, "e": True},
            {"a": 1, "b": 5, "c": 1, "d": 1.0, "e": True},
            {"a": 1, "b": "x", "c": 1.5, "d": 1.0, "e": True},
            {"a": 1, "b": "x", "c": 1, "d": "1.0", "e": True},
            {"a": 1, "b": "x", "c": 1, "d": 1.0, "e": 1},
        ):
            assert self.run(fn, bad) == []

    def test_complex_or_unparseable_annotations_are_not_checked(self) -> None:
        fn = fn_with(param("a", "Decimal"), param("b", "not valid ("), param("c", "'Forward'"))
        vector = {"a": "1.5", "b": [1], "c": {"k": None}}
        assert self.run(fn, vector) == [vector]

    def test_parameters_with_defaults_may_be_omitted(self) -> None:
        fn = fn_with(param("a", "int"), param("b", "int", "5"), param("c", None, "None"))
        assert self.run(fn, {"a": 1}) == [{"a": 1}]
        assert self.run(fn, {"b": 1}) == []

    def test_nested_json_values_are_accepted_and_non_json_rejected(self) -> None:
        fn = fn_with(param("data"))
        nested = {"data": {"a": [1, {"b": None}], "c": "x"}}
        assert self.run(fn, nested) == [nested]

    def test_self_and_variadic_parameters_are_not_call_arguments(self) -> None:
        fn = fn_with(
            param("self"),
            param("x", "int"),
            param("args", None, None, ParamKind.VAR_POSITIONAL),
            param("kwargs", None, None, ParamKind.VAR_KEYWORD),
            name="m",
            class_name="C",
        )
        assert self.run(fn, {"x": 1}) == [{"x": 1}]
        assert self.run(fn, {"self": 1, "x": 1}) == []
        assert self.run(fn, {"x": 1, "kwargs": {}}) == []

    def test_keyword_only_parameters_are_supported(self) -> None:
        fn = fn_with(param("x", "int"), param("flag", "bool", None, ParamKind.KEYWORD_ONLY))
        assert self.run(fn, {"x": 1, "flag": True}) == [{"x": 1, "flag": True}]
        assert self.run(fn, {"x": 1}) == []


def any_target(function: FunctionInfo) -> BranchTarget:
    return BranchTarget(
        function=function.qualname,
        line=1,
        kind=BranchKind.IF,
        condition_source="x",
        outcome=True,
        arc=Arc(1, 2),
        path=PathCondition(),
    )


# --------------------------------------------------------------------------- FreeFormWriter

PRICING_TESTS = """
import pytest
from shop.pricing import free_shipping, loyalty_level


@pytest.fixture
def big_order():
    return 150.0


def test_free_shipping_above(big_order):
    assert free_shipping(big_order) is True


def test_free_shipping_boundary():
    assert free_shipping(100.0) is True


def test_loyalty_level_negative():
    with pytest.raises(ValueError):
        loyalty_level(-1)
"""


class TestFreeFormWriter:
    @pytest.fixture
    def function(self, pricing: ModuleInfo) -> FunctionInfo:
        return pricing.function("free_shipping")

    def write(
        self,
        pricing: ModuleInfo,
        function: FunctionInfo,
        reply: str | Exception,
        **kwargs: Any,
    ) -> WrittenTests:
        return FreeFormWriter(ScriptedLLM(reply)).write(pricing, function, **kwargs)

    def test_splits_the_answer_into_self_contained_tests(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        written = self.write(pricing, function, code_reply(PRICING_TESTS))
        assert [t.name for t in written.tests] == [
            "test_free_shipping_above",
            "test_free_shipping_boundary",
            "test_loyalty_level_negative",
        ]
        assert written.rejected == ()
        above = written.tests[0]
        assert above.function == "free_shipping"
        assert above.origin.value == "llm_free"
        assert above.repair_attempts == 0
        assert "from shop.pricing import free_shipping" in above.source
        assert "def big_order" in above.source  # the fixture it needs travels with it
        assert "def big_order" not in written.tests[1].source

    def test_request_shape(self, pricing: ModuleInfo, function: FunctionInfo) -> None:
        llm = ScriptedLLM(code_reply(PRICING_TESTS))
        FreeFormWriter(llm).write(pricing, function, uncovered_lines=[function.end_lineno])
        (request,) = llm.requests
        assert request.purpose == "write_tests"
        assert request.response_schema is None
        assert request.max_tokens >= 1024
        prompt = llm.prompt_for("write_tests")
        assert "from shop.pricing import free_shipping" in prompt
        assert "# <- not covered" in prompt

    def test_unfenced_code_is_accepted(self, pricing: ModuleInfo, function: FunctionInfo) -> None:
        written = self.write(pricing, function, PRICING_TESTS)
        assert len(written.tests) == 3

    def test_forbidden_imports_reject_every_test_that_carries_them(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        code = (
            "import subprocess\nfrom shop.pricing import free_shipping\n\n\n"
            "def test_free_shipping_a():\n    subprocess.run(['ls'])\n"
            "    assert free_shipping(1.0) is False\n\n\n"
            "def test_free_shipping_b():\n    assert free_shipping(1.0) is False\n"
        )
        written = self.write(pricing, function, code_reply(code))
        assert written.tests == ()
        assert [d.name for d in written.rejected] == [
            "test_free_shipping_a",
            "test_free_shipping_b",
        ]
        assert all(d.reason is DropReason.INVALID_CODE for d in written.rejected)
        assert all(d.function == "free_shipping" for d in written.rejected)
        assert "subprocess" in written.rejected[0].detail

    def test_one_bad_test_does_not_poison_the_others(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        code = (
            "import os\nfrom shop.pricing import free_shipping\n\n\n"
            "def test_free_shipping_good():\n    assert free_shipping(1.0) is False\n\n\n"
            "def test_free_shipping_evil():\n    os.system('rm -rf /')\n"
            "    assert eval('1') == 1\n"
        )
        written = self.write(pricing, function, code_reply(code))
        assert [t.name for t in written.tests] == ["test_free_shipping_good"]
        (rejected,) = written.rejected
        assert rejected.name == "test_free_shipping_evil"
        assert "os.system" in rejected.detail
        assert "eval" in rejected.detail

    def test_names_colliding_with_avoid_names_are_renamed(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        written = self.write(
            pricing,
            function,
            code_reply(PRICING_TESTS),
            avoid_names={"test_free_shipping_above", "test_free_shipping_above_2"},
        )
        names = [t.name for t in written.tests]
        assert names[0] == "test_free_shipping_above_3"
        assert names[1:] == ["test_free_shipping_boundary", "test_loyalty_level_negative"]
        assert "def test_free_shipping_above_3(" in written.tests[0].source
        assert "def test_free_shipping_above(" not in written.tests[0].source
        compile(written.tests[0].source, "<t>", "exec")

    def test_duplicate_names_inside_one_answer_are_made_unique(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        code = (
            "from shop.pricing import free_shipping\n\n\n"
            "def test_free_shipping_x():\n    assert free_shipping(1.0) is False\n\n\n"
            "def test_free_shipping_x():\n    assert free_shipping(500.0) is True\n"
        )
        written = self.write(pricing, function, code_reply(code))
        assert [t.name for t in written.tests] == ["test_free_shipping_x", "test_free_shipping_x_2"]
        assert "500.0" in written.tests[1].source

    def test_at_most_max_tests_are_returned(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        body = "".join(
            f"def test_free_shipping_{i}():\n    assert free_shipping({i}.0) is False\n\n\n"
            for i in range(8)
        )
        code = f"from shop.pricing import free_shipping\n\n\n{body}"
        written = FreeFormWriter(ScriptedLLM(code_reply(code)), max_tests=3).write(
            pricing, function
        )
        assert [t.name for t in written.tests] == [f"test_free_shipping_{i}" for i in range(3)]

    def test_method_prompt_names_the_class_and_constructor(self, cart: ModuleInfo) -> None:
        method = cart.function("Cart.add_item")
        code = (
            "import pytest\nfrom shop.cart import Cart\n\n\n"
            "def test_add_item_empty_name():\n"
            "    with pytest.raises(ValueError):\n        Cart().add_item('  ', 1.0)\n"
        )
        llm = ScriptedLLM(code_reply(code))
        written = FreeFormWriter(llm).write(cart, method)
        prompt = llm.prompt_for("write_tests")
        assert "from shop.cart import Cart" in prompt
        assert "class Cart:" in prompt
        assert "First create the object" in prompt
        assert written.tests[0].function == "Cart.add_item"

    def test_test_classes_are_kept_as_units(self, cart: ModuleInfo) -> None:
        code = (
            "from shop.cart import Cart\n\n\nclass TestCart:\n"
            "    def test_total_empty(self):\n        assert Cart().total() == 0\n"
        )
        written = self.write(cart, cart.function("Cart.total"), code_reply(code))
        assert [t.name for t in written.tests] == ["TestCart"]

    @pytest.mark.parametrize(
        "reply",
        [
            LLMConnectionError("down"),
            LLMResponseError("HTTP 503"),
            "I cannot write tests for this.",
            "```python\ndef test_x(:\n```",
            "```python\nVALUE = 1\n```",
            "",
        ],
        ids=["connection", "response", "prose", "syntax-error", "no-tests", "empty"],
    )
    def test_failures_give_an_empty_result(
        self, pricing: ModuleInfo, function: FunctionInfo, reply: str | Exception
    ) -> None:
        written = self.write(pricing, function, reply)
        assert written == WrittenTests()

    def test_llm_failure_is_logged_as_a_warning(
        self, pricing: ModuleInfo, function: FunctionInfo
    ) -> None:
        with capture_logs() as logs:
            self.write(pricing, function, LLMConnectionError("down"))
        assert "write_tests_failed" in events(logs, "warning")


def test_written_tests_is_immutable() -> None:
    with pytest.raises(AttributeError):
        WrittenTests().tests = ()  # type: ignore[misc]


def test_proposer_output_survives_json_round_trip(pricing: ModuleInfo) -> None:
    """Vectors are plain data the recorder can serialise again."""
    function = pricing.function("discount")
    vector = {"price": 1.0, "customer_type": "vip", "years": 1}
    result = InputProposer(ScriptedLLM(json_reply({"inputs": [vector]}))).propose(
        pricing, function, function.branches[:1], {}
    )
    assert json.loads(json.dumps(result)) == [vector]
