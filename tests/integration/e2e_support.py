"""Support for the end-to-end tests: a scripted OpenAI-compatible server and the demo project.

The server is a respx route on ``settings.llm_base_url``. It answers like a small local
model would for the three request kinds of the pipeline and records what it was asked,
so a test can assert on the conversation. Everything else (analysis, Z3, sandbox, pytest,
coverage) is real.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import respx

from pytestgen.application import GenerateRequest
from pytestgen.config import Settings
from pytestgen.container import build_generate
from pytestgen.domain.models import GenerationReport

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

BASE_URL = "http://llm.test/v1"
CHAT_PATH = "/chat/completions"

_SUSPICIOUS_CALL = re.compile(r"free_shipping\(order_total=100(?:\.0)?\)")
_REVIEW_LINE = re.compile(r"^\[(\d+)\] (.+)$", re.MULTILINE)
_DEF_NAME = re.compile(r"^def (\w+)", re.MULTILINE)
_KEPT_NAME = re.compile(r"Keep the test name `(\w+)`")

BOUNDARY_NOTE = "Docstring says 100 or more qualifies, but exactly 100 returns False."

CART_ADD_ITEM = """\
import pytest
from shop.cart import Cart


def test_add_item_merges_quantities():
    cart = Cart()
    cart.add_item("pen", 2.0, 2)
    cart.add_item("pen", 2.0, 3)
    assert cart.total() == 10.0


def test_add_item_rejects_empty_name():
    cart = Cart()
    with pytest.raises(ValueError):
        cart.add_item("  ", 1.0)


def test_add_item_rejects_non_positive_price():
    cart = Cart()
    with pytest.raises(ValueError):
        cart.add_item("pen", 0)


def test_add_item_rejects_bad_quantity():
    cart = Cart()
    with pytest.raises(ValueError):
        cart.add_item("pen", 1.0, 0)


def test_add_item_rejects_merged_total_over_limit():
    cart = Cart()
    cart.add_item("pen", 1.0, 60)
    with pytest.raises(ValueError):
        cart.add_item("pen", 1.0, 60)


def test_add_item_shells_out():
    import subprocess

    subprocess.run(["echo", "hi"], check=False)
"""

CART_REMOVE_ITEM = """\
import pytest
from shop.cart import Cart


def test_remove_item_deletes_the_line():
    cart = Cart()
    cart.add_item("pen", 2.0)
    cart.remove_item("pen")
    assert cart.total() == 0.0


def test_remove_item_unknown_name_raises():
    cart = Cart()
    with pytest.raises(KeyError):
        cart.remove_item("pen")
"""

# The first test has a wrong expectation on purpose: the repair loop has to fix it.
CART_TOTAL = """\
from shop.cart import Cart


def test_total_vip_discount():
    cart = Cart(customer_type="vip", years=1)
    cart.add_item("pen", 100.0)
    assert cart.total() == 100.0


def test_total_empty_cart_is_zero():
    assert Cart().total() == 0.0
"""

CART_TOTAL_FIXED = """\
from shop.cart import Cart


def test_total_vip_discount():
    cart = Cart(customer_type="vip", years=1)
    cart.add_item("pen", 100.0)
    assert cart.total() == 85.0
"""

WRITE_REPLIES = {
    "Cart.add_item": CART_ADD_ITEM,
    "Cart.remove_item": CART_REMOVE_ITEM,
    "Cart.total": CART_TOTAL,
}
REPAIR_REPLIES = {"test_total_vip_discount": CART_TOTAL_FIXED}
PROPOSALS: dict[str, list[dict[str, object]]] = {
    "free_shipping": [{"order_total": 100}, {"order_total": 150}],
    "parse_qty": [{"text": "5"}, {"text": "abc"}, {"text": "500"}],
    "validate_promo": [{"code": "SAVE123"}, {"code": "SAVE12X"}, {"code": "AB-VIP"}],
}


def fenced(code: str) -> str:
    return f"Sure, here is the code:\n```python\n{code}```\nHope it helps."


def completion(content: str) -> httpx.Response:
    body = {
        "model": "scripted-model",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    return httpx.Response(200, json=body)


@dataclass
class ScriptedLLMServer:
    """What a small model would say, for the oracle review, input proposal and code requests."""

    kinds: Counter[str] = field(default_factory=Counter)
    prompts: list[tuple[str, str]] = field(default_factory=list)
    reviewed_calls: list[str] = field(default_factory=list)
    suspicious_calls: list[str] = field(default_factory=list)

    def install(self, router: respx.MockRouter, base_url: str) -> None:
        router.post(base_url + CHAT_PATH).mock(side_effect=self._handle)

    def prompts_of(self, kind: str) -> list[str]:
        return [text for item, text in self.prompts if item == kind]

    # ------------------------------------------------------------------ routing

    def _handle(self, request: httpx.Request) -> httpx.Response:
        payload: dict[str, Any] = json.loads(request.content)
        user = payload["messages"][-1]["content"]
        kind = self._kind(payload, user)
        self.kinds[kind] += 1
        self.prompts.append((kind, user))
        if kind == "oracle_review":
            return completion(self._review(user))
        if kind == "propose_inputs":
            return completion(self._proposal(user))
        if kind == "write_tests":
            return completion(self._written(user))
        if kind == "repair":
            return completion(self._repaired(user))
        return completion("I do not understand.")

    @staticmethod
    def _kind(payload: dict[str, Any], user: str) -> str:
        schema = (payload.get("response_format") or {}).get("json_schema") or {}
        if schema.get("name"):
            return str(schema["name"])
        if user.startswith("Write pytest tests"):
            return "write_tests"
        if user.startswith("This pytest test does not pass"):
            return "repair"
        return "unknown"

    # ------------------------------------------------------------------ replies

    def _review(self, user: str) -> str:
        reviews = []
        for match in _REVIEW_LINE.finditer(user):
            index, behaviour = int(match.group(1)), match.group(2)
            self.reviewed_calls.append(behaviour)
            if _SUSPICIOUS_CALL.search(behaviour):
                self.suspicious_calls.append(behaviour)
                reviews.append(
                    {"index": index, "verdict": "suspicious", "explanation": BOUNDARY_NOTE}
                )
            else:
                reviews.append({"index": index, "verdict": "ok", "explanation": ""})
        return "Here is my review:\n" + json.dumps({"reviews": reviews})

    @staticmethod
    def _proposal(user: str) -> str:
        found = _DEF_NAME.search(user)
        inputs = PROPOSALS.get(found.group(1) if found else "", [])
        return json.dumps({"inputs": inputs})

    @staticmethod
    def _written(user: str) -> str:
        first_line = user.splitlines()[0]
        for qualname, code in WRITE_REPLIES.items():
            if first_line.rstrip(".").endswith(qualname):
                return fenced(code)
        return "I cannot help with that."

    @staticmethod
    def _repaired(user: str) -> str:
        found = _KEPT_NAME.search(user)
        code = REPAIR_REPLIES.get(found.group(1) if found else "")
        return fenced(code) if code else "I cannot fix that."


def copy_examples(destination: Path) -> Path:
    """Copy the demo project (without caches) into ``destination`` and return it."""
    shutil.copytree(
        EXAMPLES,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".coverage*"),
    )
    return destination


def make_settings(root: Path) -> Settings:
    """Settings pointing at the scripted server; no retries, so failures show up at once."""
    return Settings(
        llm_base_url=BASE_URL, llm_max_retries=0, cache_dir=root / "llm-cache", run_timeout_s=90
    )


def generate(settings: Settings, **request: Any) -> GenerationReport:
    """Run the real pipeline (``build_generate``) for ``GenerateRequest(**request)``."""
    with build_generate(settings, use_cache=False) as use_case:
        return use_case.execute(GenerateRequest(**request))


@dataclass(frozen=True)
class Scenario:
    """One finished pipeline run on a private copy of the demo project."""

    project: Path
    report: GenerationReport
    llm: ScriptedLLMServer


def run_scenario(root: Path, module: str, **request: Any) -> Scenario:
    """Copy the demo project under ``root`` and generate tests for ``shop/<module>``."""
    project = copy_examples(root / "project")
    settings = make_settings(root)
    server = ScriptedLLMServer()
    with respx.mock(assert_all_called=False) as router:
        server.install(router, settings.llm_base_url)
        report = generate(
            settings, target=project / "shop" / module, project_root=project, **request
        )
    return Scenario(project, report, server)
