"""Worker executed *inside* the sandbox: imports the target module and inspects or calls it.

Run as ``python -P _driver.py <mode> <spec.json> <out.json> [inputs.pylit]``. It depends on
the standard library only, because it runs in the target project's interpreter, and it
never imports ``pytestgen``. Everything it learns is written to ``out.json``.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import importlib
import inspect
import json
import os
import sys
import traceback
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

MESSAGE_LIMIT = 500


# --------------------------------------------------------------------------- recording


def exception_name(exc: BaseException) -> str:
    cls = type(exc)
    if cls.__module__ == "builtins":
        return cls.__qualname__
    return f"{cls.__module__}.{cls.__qualname__}"


def exception_message(exc: BaseException) -> str:
    """``str(exc)`` that survives exceptions whose ``__str__`` itself raises."""
    try:
        text = str(exc)
    except Exception:
        text = repr(exc.args)
    return text[:MESSAGE_LIMIT]


def is_literal_roundtrip(value: object, text: str) -> bool:
    """True when ``text`` (the repr of ``value``) can be pasted into a test and compared."""
    try:
        return bool(ast.literal_eval(text) == value)
    except Exception:
        return False


def describe_value(value: object) -> dict[str, Any]:
    try:
        text = repr(value)
    except Exception:
        name = type(value).__qualname__
        return {"kind": "not_literal", "value_repr": f"<unrepresentable {name}>"}
    if is_literal_roundtrip(value, text):
        return {"kind": "returned", "value_repr": text}
    return {"kind": "not_literal", "value_repr": text[:MESSAGE_LIMIT]}


def build_call(
    func: Callable[..., Any], inputs: Mapping[str, Any]
) -> tuple[list[Any], dict[str, Any]]:
    """Map ``{parameter name: value}`` onto positional and keyword arguments of ``func``."""
    try:
        params = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        return [], dict(inputs)
    star = next((p for p in params if p.kind is p.VAR_POSITIONAL), None)
    star_values = list(inputs.get(star.name, ())) if star is not None else []
    args: list[Any] = []
    kwargs = dict(inputs)
    for p in params:
        if p.kind is p.VAR_POSITIONAL:
            args.extend(star_values)
            kwargs.pop(p.name, None)
        elif p.kind is p.VAR_KEYWORD:
            kwargs.pop(p.name, None)
            kwargs.update(inputs.get(p.name, {}))
        elif p.kind is p.POSITIONAL_ONLY or (p.kind is p.POSITIONAL_OR_KEYWORD and star_values):
            value = kwargs.pop(p.name, p.default)
            if value is not p.empty:
                args.append(value)
    return args, kwargs


def call_once(func: Callable[..., Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Call ``func`` once on a private copy of ``inputs`` and describe what happened."""
    try:
        args, kwargs = build_call(func, copy.deepcopy(dict(inputs)))
        value = func(*args, **kwargs)
        if inspect.iscoroutine(value):
            value = asyncio.run(value)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        return {
            "kind": "raised",
            "exception_type": exception_name(exc),
            "exception_message": exception_message(exc),
        }
    return describe_value(value)


def _signature(result: Mapping[str, Any]) -> tuple[Any, ...]:
    return result["kind"], result.get("value_repr"), result.get("exception_type")


def observe(func: Callable[..., Any], inputs: Mapping[str, Any], repeats: int) -> dict[str, Any]:
    """Run ``func`` ``repeats`` times; disagreement between runs yields ``unstable``."""
    results = [call_once(func, inputs) for _ in range(max(repeats, 1))]
    first = results[0]
    if any(_signature(r) != _signature(first) for r in results[1:]):
        return {**first, "kind": "unstable"}
    return first


def resolve(module: object, qualname: str) -> Any:
    obj: Any = module
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def import_target(name: str) -> tuple[Any, str | None]:
    """Import ``name``; return ``(module, None)`` or ``(None, traceback text)``."""
    try:
        return importlib.import_module(name), None
    except BaseException:
        return None, traceback.format_exc()


def record(spec: Mapping[str, Any], vectors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    module, error = import_target(spec["module"])
    if error is not None:
        return {"status": "import_error", "error": error}
    try:
        func = resolve(module, spec["qualname"])
    except AttributeError as exc:
        return {"status": "resolve_error", "error": f"{spec['qualname']}: {exc}"}
    repeats = int(spec.get("repeats", 2))
    return {"status": "ok", "results": [observe(func, v, repeats) for v in vectors]}


# --------------------------------------------------------------------------- introspection


def type_text(tp: object) -> str:
    """Readable annotation text: ``int``, ``list[int]``, ``Optional[str]``, ``None``."""
    if tp is type(None):
        return "None"
    if isinstance(tp, type) and typing.get_origin(tp) is None:
        return tp.__name__
    return str(tp).replace("typing.", "")


def type_hints(obj: Any) -> dict[str, str]:
    """Resolved annotations as text; a hint that cannot be resolved is left out."""
    try:
        return {name: type_text(tp) for name, tp in typing.get_type_hints(obj).items()}
    except Exception:  # one unresolved forward reference spoils get_type_hints as a whole
        return _hints_one_by_one(obj)


def _hints_one_by_one(obj: Any) -> dict[str, str]:
    try:
        raw = dict(inspect.get_annotations(obj))
    except Exception:
        return {}
    namespace = getattr(obj, "__globals__", None)
    hints: dict[str, str] = {}
    for name, annotation in raw.items():
        carrier = types.SimpleNamespace(__annotations__={name: annotation})
        with contextlib.suppress(Exception):
            hints[name] = type_text(typing.get_type_hints(carrier, namespace)[name])
    return hints


def describe_function(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, property) and obj.fget is not None:
        obj = obj.fget
    try:
        signature = str(inspect.signature(obj))
    except (TypeError, ValueError):
        return None
    return {
        "signature": signature,
        "hints": type_hints(obj),
        "coroutine": inspect.iscoroutinefunction(obj),
    }


def introspect(spec: Mapping[str, Any]) -> dict[str, Any]:
    module, error = import_target(spec["module"])
    if error is not None:
        return {"status": "import_error", "error": error}
    functions: dict[str, Any] = {}
    skipped: list[str] = []
    for qualname in spec["qualnames"]:
        try:
            described = describe_function(resolve(module, qualname))
        except AttributeError:
            described = None
        if described is None:
            skipped.append(qualname)
        else:
            functions[qualname] = described
    return {"status": "ok", "functions": functions, "skipped": skipped}


# --------------------------------------------------------------------------- entry point


def main(argv: Sequence[str]) -> int:
    mode, spec_path, out_path = argv[1:4]
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    with (
        Path(os.devnull).open("w", encoding="utf-8") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        if mode == "record":
            vectors = ast.literal_eval(Path(argv[4]).read_text(encoding="utf-8"))
            payload = record(spec, vectors)
        else:
            payload = introspect(spec)
    Path(out_path).write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocesses
    sys.exit(main(sys.argv))
