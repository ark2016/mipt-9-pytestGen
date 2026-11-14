"""Collect, deduplicate and render the import block of a generated test module."""

from __future__ import annotations

import ast
import sys
from collections import defaultdict

_WIDTH = 88
_FUTURE, _STDLIB, _THIRD_PARTY, _FIRST_PARTY, _LOCAL = range(5)


class ImportSet:
    """A set of imports rendered isort-style: sections, ``import x`` before ``from x import y``."""

    def __init__(self, first_party: str) -> None:
        self._first_party = first_party.split(".", maxsplit=1)[0]
        self._modules: set[tuple[str, str | None]] = set()
        self._names: dict[tuple[int, str], set[tuple[str, str | None]]] = defaultdict(set)

    def add_module(self, module: str, asname: str | None = None) -> None:
        """Register ``import module [as asname]``."""
        self._modules.add((module, asname))

    def add_name(self, module: str, name: str, asname: str | None = None, level: int = 0) -> None:
        """Register ``from <level dots><module> import name [as asname]``."""
        self._names[(level, module)].add((name, asname))

    def add_source(self, source: str) -> None:
        """Register every import statement found in ``source``."""
        for node in ast.parse(source).body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.add_module(alias.name, alias.asname)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.add_name(node.module or "", alias.name, alias.asname, node.level)

    def render(self) -> str:
        """Import block text without trailing newline (empty string when nothing was added)."""
        sections: dict[int, list[tuple[tuple[int, int, str, str], str]]] = defaultdict(list)
        for module, asname in self._modules:
            line = f"import {module}" + (f" as {asname}" if asname else "")
            key = (0, 0, module.lower(), asname or "")
            sections[self._section(module, 0)].append((key, line))
        for (level, module), names in self._names.items():
            head = f"from {'.' * level}{module} import"
            lines = [f"{head} *"] if ("*", None) in names else []  # cannot be mixed with names
            if names - {("*", None)}:
                lines.append(_from_line(head, names - {("*", None)}))
            for line in lines:
                key = (1, -level, module.lower(), line)
                sections[self._section(module, level)].append((key, line))
        blocks = [
            "\n".join(line for _, line in sorted(sections[section])) for section in sorted(sections)
        ]
        return "\n\n".join(blocks)

    def _section(self, module: str, level: int) -> int:
        root = module.split(".", maxsplit=1)[0]
        if level:
            return _LOCAL
        if root == "__future__":
            return _FUTURE
        if root == self._first_party:
            return _FIRST_PARTY
        return _STDLIB if root in sys.stdlib_module_names else _THIRD_PARTY


def _from_line(head: str, names: set[tuple[str, str | None]]) -> str:
    rendered = [name + (f" as {alias}" if alias else "") for name, alias in _sort_names(names)]
    line = f"{head} " + ", ".join(rendered)
    if len(line) <= _WIDTH:
        return line
    body = "".join(f"    {item},\n" for item in rendered)
    return f"{head} (\n{body})"


def _sort_names(names: set[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Constants first, then classes, then everything else (ruff's ``order-by-type``)."""

    def key(item: tuple[str, str | None]) -> tuple[int, str, str]:
        name = item[0]
        if name == "*":
            rank = 0
        elif len(name) > 1 and name.isupper():
            rank = 1
        elif name[:1].isupper():
            rank = 2
        else:
            rank = 3
        return rank, name.lower(), item[1] or ""

    return sorted(names, key=key)
