from __future__ import annotations

from dataclasses import replace

import pytest
from app_factories import analyze_example, failed_outcome, raised, returned

from pytestgen.application import prompts
from pytestgen.domain.llm import ChatMessage, Role
from pytestgen.domain.models import (
    FunctionInfo,
    ModuleInfo,
    Observation,
    ObservationKind,
    TestStatus,
)


@pytest.fixture(scope="module")
def pricing() -> ModuleInfo:
    return analyze_example("shop/pricing.py")


@pytest.fixture(scope="module")
def cart() -> ModuleInfo:
    return analyze_example("shop/cart.py")


def user_text(messages: list[ChatMessage]) -> str:
    assert [m.role for m in messages] == [Role.SYSTEM, Role.USER]
    return messages[1].content


class TestSourceHelpers:
    def test_truncate_keeps_short_source_untouched(self) -> None:
        assert prompts.truncate_source("a\nb", 5) == "a\nb"

    def test_truncate_cuts_and_reports_omitted_lines(self) -> None:
        source = "\n".join(f"line{i}" for i in range(10))
        cut = prompts.truncate_source(source, 4)
        assert cut.splitlines()[:4] == ["line0", "line1", "line2", "line3"]
        assert cut.endswith("# ... (6 more lines omitted)")

    def test_mark_uncovered_uses_absolute_line_numbers(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("free_shipping")
        marked = prompts.mark_uncovered(fn, [fn.end_lineno]).splitlines()
        assert marked[-1].endswith("return order_total > 100  # <- not covered")
        assert "not covered" not in "\n".join(marked[:-1])

    def test_mark_uncovered_accounts_for_decorator_lines(self) -> None:
        fn = FunctionInfo(
            qualname="f",
            name="f",
            lineno=11,
            end_lineno=12,
            source="@deco\ndef f():\n    return 1",
        )
        # the source starts at the decorator, line 10: lines 10, 11, 12
        marked = prompts.mark_uncovered(fn, [12, 10]).splitlines()
        assert marked[0].endswith("# <- not covered")
        assert not marked[1].endswith("# <- not covered")
        assert marked[2].endswith("# <- not covered")

    def test_mark_uncovered_skips_blank_lines(self) -> None:
        fn = FunctionInfo(
            qualname="f", name="f", lineno=1, end_lineno=3, source="def f():\n\n    pass"
        )
        assert prompts.mark_uncovered(fn, [2]) == fn.source

    def test_import_line_for_function_and_method(
        self, pricing: ModuleInfo, cart: ModuleInfo
    ) -> None:
        discount = pricing.function("discount")
        assert prompts.import_line(pricing, discount) == "from shop.pricing import discount"
        add_item = cart.function("Cart.add_item")
        assert prompts.import_line(cart, add_item) == "from shop.cart import Cart"

    def test_describe_params_shows_annotations_and_defaults(self, cart: ModuleInfo) -> None:
        text = prompts.describe_params(cart.function("Cart.add_item").call_parameters)
        assert text == "name: str, price: float, qty: int = 1"
        assert prompts.describe_params(()) == "(none)"


class TestModuleConstants:
    def test_constants_read_by_the_function_are_listed(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("validate_promo")
        expected = 'RESERVED_CODES = frozenset({"SAVE000", "DEMO-VIP"})'
        assert prompts.module_constants(pricing, fn) == expected

    def test_unrelated_constants_are_left_out(self, pricing: ModuleInfo) -> None:
        assert prompts.module_constants(pricing, pricing.function("loyalty_level")) == ""

    def test_annotated_and_long_constants(self, pricing: ModuleInfo) -> None:
        table = ", ".join(f"{i}: {i}" for i in range(100))
        module = replace(pricing, source=f"LIMIT: int = 5\nTABLE = {{{table}}}\n")
        fn = replace(
            pricing.function("loyalty_level"), source="def f():\n    return LIMIT + len(TABLE)"
        )
        lines = prompts.module_constants(module, fn).splitlines()
        assert lines[0] == "LIMIT: int = 5"
        assert len(lines[1]) == 200
        assert lines[1].endswith("...")

    def test_unparseable_sources_give_nothing(self, pricing: ModuleInfo) -> None:
        broken = replace(pricing, source="def (:")
        assert prompts.module_constants(broken, pricing.function("validate_promo")) == ""

    def test_constants_reach_both_prompts(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("validate_promo")
        target = fn.branches[0]
        propose = user_text(prompts.propose_inputs_messages(pricing, fn, [target], {}, 3))
        write = user_text(prompts.write_tests_messages(pricing, fn))
        assert "RESERVED_CODES = frozenset" in propose
        assert "RESERVED_CODES = frozenset" in write


class TestClassSummary:
    def test_includes_class_line_init_and_method(self, cart: ModuleInfo) -> None:
        fn = cart.function("Cart.add_item")
        summary = prompts.class_summary(cart, fn, fn.source)
        assert summary.startswith("class Cart:")
        assert '"""Holds line items keyed by product name' in summary
        assert "    def __init__(self, customer_type: str" in summary
        assert "    def add_item(self, name: str" in summary
        assert summary.index("__init__") < summary.index("add_item")

    def test_init_is_not_repeated_when_it_is_the_target(self, cart: ModuleInfo) -> None:
        fn = cart.function("Cart.__init__")
        summary = prompts.class_summary(cart, fn, fn.source)
        assert summary.count("def __init__") == 1

    def test_unknown_class_falls_back_to_bare_header(self, cart: ModuleInfo) -> None:
        fn = replace(cart.function("Cart.total"), class_name="Outer.Missing")
        summary = prompts.class_summary(cart, fn, fn.source)
        assert summary.startswith("class Missing:")


class TestOracleReviewPrompt:
    def test_lists_calls_results_and_exceptions(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("discount")
        ok = returned({"price": 100.0, "customer_type": "vip", "years": 1}, "85.0", "discount")
        bad = raised(
            {"price": -1.0, "customer_type": "x", "years": 0}, "ValueError", "bad", "discount"
        )
        text = user_text(prompts.oracle_review_messages(pricing, fn, [(0, ok), (3, bad)]))
        assert "[0] discount(price=100.0, customer_type='vip', years=1) -> 85.0" in text
        assert "[3] discount(price=-1.0, customer_type='x', years=0) raises ValueError: bad" in text
        assert "Return the price after the loyalty discount" in text
        assert '"reviews"' in text

    def test_long_values_are_shortened(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("discount")
        text = prompts.describe_case(fn, returned({"price": 1}, "x" * 500, "discount"))
        assert len(text) < 200
        assert text.endswith("...")

    def test_raise_without_message(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("discount")
        text = prompts.describe_case(fn, raised({}, "KeyError", "", "discount"))
        assert text.endswith("raises KeyError")

    def test_other_observation_kinds_are_described_by_value(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("discount")
        obs = Observation(inputs={}, kind=ObservationKind.NOT_LITERAL, value_repr="<obj>")
        case = replace(returned({}, "", "discount"), observation=obs)
        assert prompts.describe_case(fn, case) == "discount() -> <obj>"

    def test_schema_is_a_json_schema_object(self) -> None:
        schema = prompts.ORACLE_REVIEW_SCHEMA
        assert schema.name == "oracle_review"
        item = schema.schema["properties"]["reviews"]["items"]
        assert item["properties"]["verdict"]["enum"] == ["ok", "suspicious"]


class TestProposePrompt:
    def test_shows_targets_conditions_and_solver_reasons(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("validate_promo")
        targets = [b for b in fn.branches if b.outcome][:2]
        reasons = {targets[0].key: "unsupported: str.isdigit"}
        text = user_text(prompts.propose_inputs_messages(pricing, fn, targets, reasons, 4))
        assert f"line {targets[0].line}" in text
        assert targets[0].condition_source in text
        assert "Solver: unsupported: str.isdigit" in text
        assert "Parameters: code: str" in text
        assert "up to 4" in text
        assert '{"inputs": [{"code": <value>}]}' in text

    def test_target_with_raise_and_without_reason(self, pricing: ModuleInfo) -> None:
        target = pricing.function("discount").branches[0]
        line = prompts.describe_target(target, "")
        assert "then it raises ValueError" in line
        assert "Solver" not in line

    def test_methods_do_not_show_self(self, cart: ModuleInfo) -> None:
        fn = cart.function("Cart.add_item")
        text = user_text(prompts.propose_inputs_messages(cart, fn, fn.branches[:1], {}, 3))
        assert "Parameters: name: str, price: float, qty: int = 1" in text
        assert '"self"' not in text


class TestWriteTestsPrompt:
    def test_function_prompt_has_import_marks_and_rules(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("free_shipping")
        messages = prompts.write_tests_messages(
            pricing, fn, uncovered_lines=[fn.end_lineno], avoid_names=["test_b", "test_a"]
        )
        text = user_text(messages)
        assert "from shop.pricing import free_shipping" in text
        assert "return order_total > 100  # <- not covered" in text
        assert "no network" in text.lower()
        assert "test_free_shipping_<something>" in text
        assert "Do not reuse these test names: test_a, test_b." in text
        assert "ONE function" in text
        assert "create the object" not in text

    def test_no_uncovered_note_without_uncovered_lines(self, pricing: ModuleInfo) -> None:
        text = user_text(prompts.write_tests_messages(pricing, pricing.function("loyalty_level")))
        assert "not covered" not in text
        assert "Do not reuse" not in text

    def test_method_prompt_has_class_context_and_construction_hint(self, cart: ModuleInfo) -> None:
        text = user_text(prompts.write_tests_messages(cart, cart.function("Cart.add_item")))
        assert "ONE method" in text
        assert "from shop.cart import Cart" in text
        assert "class Cart:" in text
        assert "def __init__" in text
        assert "First create the object with Cart(...)" in text
        assert "then call .add_item(...)" in text

    def test_static_method_needs_no_construction(self, cart: ModuleInfo) -> None:
        text = user_text(prompts.write_tests_messages(cart, cart.function("Cart.parse_qty")))
        assert "First create the object" not in text

    def test_property_hint_mentions_attribute(self, cart: ModuleInfo) -> None:
        prop = replace(cart.function("Cart.total"), is_property=True)
        text = user_text(prompts.write_tests_messages(cart, prop))
        assert "read the attribute" in text

    def test_prompt_stays_compact_for_long_functions(self, pricing: ModuleInfo) -> None:
        body = "\n".join(f"    x{i} = {i}" for i in range(400))
        fn = replace(pricing.function("discount"), source=f"def discount(a):\n{body}")
        text = user_text(prompts.write_tests_messages(pricing, fn))
        assert "more lines omitted" in text
        assert len(text) < 6000


class TestRepairPrompt:
    def test_contains_test_failure_and_function(self, pricing: ModuleInfo) -> None:
        fn = pricing.function("free_shipping")
        outcome = failed_outcome("test_x", "assert False == True", "tb line 1\ntb line 2")
        messages = prompts.repair_messages(fn, "test_x", "def test_x():\n    assert 1\n", outcome)
        text = user_text(messages)
        assert "def test_x()" in text
        assert "Result: failed" in text
        assert "assert False == True" in text
        assert "tb line 2" in text
        assert "return order_total > 100" in text
        assert "Keep the test name `test_x`" in text

    def test_function_section_is_optional(self) -> None:
        outcome = failed_outcome("test_x")
        text = user_text(prompts.repair_messages(None, "test_x", "def test_x(): ...", outcome))
        assert "Code under test" not in text

    def test_failure_text_keeps_the_tail_of_a_long_traceback(self) -> None:
        traceback = "\n".join(f"frame {i}" for i in range(500))
        text = prompts.failure_text(failed_outcome("t", "boom", traceback))
        assert "frame 499" in text
        assert "frame 100" not in text
        assert "boom" in text

    def test_failure_text_is_truncated_from_the_front(self) -> None:
        text = prompts.failure_text(failed_outcome("t", "", "x" * 10 + "\n" + "y" * 5000))
        assert len(text) <= prompts.MAX_FAILURE_CHARS + 3
        assert text.startswith("...")
        assert text.endswith("y")

    def test_message_already_in_traceback_is_not_duplicated(self) -> None:
        text = prompts.failure_text(failed_outcome("t", "E assert 1", "a\nE assert 1\nb"))
        assert text.count("E assert 1") == 1

    def test_empty_failure(self) -> None:
        assert prompts.failure_text(failed_outcome("t", "", "")) == "(no message)"

    def test_timeout_status_is_visible(self) -> None:
        outcome = replace(failed_outcome("t"), status=TestStatus.TIMEOUT)
        text = user_text(prompts.repair_messages(None, "t", "def t(): ...", outcome))
        assert "Result: timeout" in text
