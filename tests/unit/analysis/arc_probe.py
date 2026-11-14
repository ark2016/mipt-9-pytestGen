"""Runs fixture functions under coverage.py and prints the executed arcs as JSON.

Usage: ``python arc_probe.py <module.py> <calls.json>`` where ``calls.json`` maps a
qualified function name to a list of argument lists. Every call is measured on its own.
The probe runs in a subprocess so it cannot disturb a coverage session of the test run.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import coverage


def _resolve(module: Any, qualname: str) -> Any:
    if "." not in qualname:
        return getattr(module, qualname)
    class_name, method = qualname.split(".")
    return getattr(getattr(module, class_name)(), method)


def main(module_path: str, calls_path: str) -> None:
    path = Path(module_path).resolve()
    spec = importlib.util.spec_from_file_location("probe_target", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: dict[str, list[list[Any]]] = json.loads(Path(calls_path).read_text(encoding="utf-8"))

    cov = coverage.Coverage(branch=True, data_file=None)
    result: dict[str, list[list[list[int]]]] = {}
    for qualname, argument_lists in calls.items():
        function = _resolve(module, qualname)
        result[qualname] = []
        for arguments in argument_lists:
            cov.erase()
            cov.start()
            try:
                outcome = function(*arguments)
                if inspect.iscoroutine(outcome):
                    asyncio.run(outcome)
            except Exception:  # noqa: S110  the probe only records control flow
                pass
            finally:
                cov.stop()
            data = cov.get_data()
            arcs: list[list[int]] = []
            for filename in data.measured_files():
                if Path(filename).resolve() == path:
                    arcs = [list(arc) for arc in data.arcs(filename) or []]
            result[qualname].append(arcs)
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
