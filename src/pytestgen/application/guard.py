"""Static guard for LLM-written test code.

The sandbox is the real security boundary; this guard only keeps obviously harmful or
nonsensical code (network, subprocesses, file deletion, ``eval``) from being run at all.
It is deliberately conservative: anything it cannot prove harmless in a call it flags.
"""

from __future__ import annotations

import ast

FORBIDDEN_MODULES = frozenset(
    {
        "subprocess",
        "socket",
        "shutil",
        "ctypes",
        "multiprocessing",
        "requests",
        "httpx",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "asyncio.subprocess",
    }
)
_FORBIDDEN_OS_CALLS = frozenset(
    {
        "system",
        "popen",
        "remove",
        "unlink",
        "rmdir",
        "removedirs",
        "kill",
        "killpg",
        "abort",
        "_exit",
        "startfile",
        "posix_spawn",
        "posix_spawnp",
    }
)
_FORBIDDEN_OS_PREFIXES = ("exec", "spawn")
_FORBIDDEN_BUILTINS = frozenset({"eval", "exec", "compile", "__import__"})
_FORBIDDEN_ATTRIBUTES = frozenset({"__builtins__", "__globals__", "__subclasses__"})
_WRITE_MODE_CHARS = frozenset("wax+")
_OPEN_MODE_POSITION = 1


def check_test_source(source: str) -> list[str]:
    """Return human-readable violations in ``source``; an empty list means it may run."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None)
        where = f" (line {line})" if line else ""
        return [f"syntax error: {getattr(exc, 'msg', exc)}{where}"]
    visitor = _Guard()
    visitor.collect_aliases(tree)
    visitor.visit(tree)
    return visitor.violations()


class _Guard(ast.NodeVisitor):
    """Collects violations; ``_aliases`` maps local names to the dotted names they import."""

    def __init__(self) -> None:
        self._aliases: dict[str, str] = {}
        self._found: dict[tuple[int, str], None] = {}

    def violations(self) -> list[str]:
        return [f"line {line}: {text}" for line, text in sorted(self._found)]

    def _flag(self, node: ast.AST, text: str) -> None:
        self._found[(getattr(node, "lineno", 0), text)] = None

    # ------------------------------------------------------------------ imports

    def collect_aliases(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    local = item.asname or item.name.split(".")[0]
                    self._aliases[local] = item.name if item.asname else local
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for item in node.names:
                    self._aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self._check_module(node, item.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and not node.level:
            self._check_module(node, node.module)
            for item in node.names:
                self._check_module(node, f"{node.module}.{item.name}")

    def _check_module(self, node: ast.AST, dotted: str) -> None:
        parts = dotted.split(".")
        for size in range(1, len(parts) + 1):
            if ".".join(parts[:size]) in FORBIDDEN_MODULES:
                self._flag(node, f"import of '{dotted}' is forbidden")
                return

    # ------------------------------------------------------------------ calls and names

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve(node.func)
        if name is not None:
            self._check_call(node, name)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call, name: str) -> None:
        module, _, attr = name.rpartition(".")
        if name in {"open", "io.open"}:
            self._check_open(node)
        elif module == "os" and (
            attr in _FORBIDDEN_OS_CALLS or attr.startswith(_FORBIDDEN_OS_PREFIXES)
        ):
            self._flag(node, f"call to os.{attr}() is forbidden")
        elif module == "asyncio" and attr.startswith("create_subprocess"):
            self._flag(node, f"call to asyncio.{attr}() is forbidden")

    def _check_open(self, node: ast.Call) -> None:
        mode: ast.expr | None = None
        if len(node.args) > _OPEN_MODE_POSITION:
            mode = node.args[_OPEN_MODE_POSITION]
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode = keyword.value
        if mode is None:
            return
        if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
            self._flag(node, "open() with a non-literal mode is forbidden")
        elif _WRITE_MODE_CHARS & set(mode.value):
            self._flag(node, f"open() in write mode {mode.value!r} is forbidden")

    def visit_Name(self, node: ast.Name) -> None:
        name = self._aliases.get(node.id, node.id)
        if isinstance(node.ctx, ast.Load) and name in _FORBIDDEN_BUILTINS:
            self._flag(node, f"use of {name} is forbidden")
        if node.id in _FORBIDDEN_ATTRIBUTES:
            self._flag(node, f"access to {node.id} is forbidden")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FORBIDDEN_ATTRIBUTES:
            self._flag(node, f"access to {node.attr} is forbidden")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # getattr(obj, "__globals__") and friends
        if isinstance(node.value, str) and node.value in _FORBIDDEN_ATTRIBUTES:
            self._flag(node, f"access to {node.value} is forbidden")

    def _resolve(self, func: ast.expr) -> str | None:
        """Dotted name of the callee with import aliases expanded, ``None`` if not static."""
        parts: list[str] = []
        node = func
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        root = self._aliases.get(node.id, node.id)
        return ".".join([root, *reversed(parts)])
