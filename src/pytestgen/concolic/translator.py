"""Translation of Python condition expressions into z3 terms."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

import z3

from pytestgen.concolic.environment import VariableEnvironment
from pytestgen.concolic.errors import UnsupportedExpression
from pytestgen.concolic.ptypes import Kind
from pytestgen.concolic.zutil import (
    as_number,
    is_string,
    python_string,
    real_val,
    string_val,
    unify,
)
from pytestgen.domain.models import Clause

__all__ = ["ExprTranslator", "UnsupportedExpression", "parse_clause"]

_BUILTIN_CLASSES = frozenset(
    {"int", "float", "str", "bool", "list", "tuple", "dict", "set", "frozenset", "bytes", "object"}
)
_MAX_SNIPPET = 60
_MAX_ORDERED_CODE_POINT = 0x2FFFF
"""z3's characters stop here: ordering against a literal above it is unsat or wraps around."""
_TRUE = z3.BoolVal(True)
_FALSE = z3.BoolVal(False)


def parse_clause(clause: Clause) -> ast.expr:
    """Parse the clause expression; a syntax error is an unsupported condition."""
    try:
        return ast.parse(clause.expr, mode="eval").body
    except SyntaxError as exc:
        raise UnsupportedExpression(f"cannot parse `{clause.expr}`: {exc.msg}") from exc


@dataclass(frozen=True, slots=True)
class _Operand:
    """An expression together with the condition under which it is ``None``."""

    term: Any | None
    """The z3 value; ``None`` for the literal ``None``."""
    none: Any
    """Boolean z3 term: the operand is ``None``."""
    name: str | None = None
    """Optional parameter behind the operand, if any."""


class ExprTranslator:
    """Translates expressions over the parameters of one function.

    Boolean contexts (conditions, ``and``/``or`` operands, ``not``) follow Python's
    truthiness; everything else is translated to int/real/bool/string terms. Anything
    outside the supported subset raises ``UnsupportedExpression`` with a reason.
    """

    def __init__(self, env: VariableEnvironment) -> None:
        self._env = env
        self._side_conditions: list[Any] = []
        self._reads: list[Any] = []
        self._functions = {
            "len": self._len,
            "abs": self._abs,
            "min": self._min,
            "max": self._max,
            "isinstance": self._isinstance,
        }

    @property
    def side_conditions(self) -> list[Any]:
        """Facts that must hold for the translated expressions to be defined (divisors != 0)."""
        return list(self._side_conditions)

    def clause(self, clause: Clause) -> Any:
        """The z3 formula stating that ``clause.expr`` has truth value ``clause.expected``."""
        return self.expected(parse_clause(clause), clause.expected)

    def expected(self, node: ast.expr, expected: bool) -> Any:
        truth, defined = self._cond(node)
        return z3.And(defined, truth if expected else z3.Not(truth))

    # ------------------------------------------------------------------ truth values

    def condition(self, node: ast.expr) -> Any:
        """``node`` evaluates without error and is truthy."""
        truth, defined = self._cond(node)
        return z3.And(defined, truth)

    def _cond(self, node: ast.expr) -> tuple[Any, Any]:
        """``(truth, defined)``: the truth value of ``node`` and when evaluating it is safe.

        ``defined`` follows Python's short-circuiting: in ``x is None or x > 3`` the read of
        ``x`` only has to be valid when ``x`` is not ``None``.
        """
        if isinstance(node, ast.BoolOp):
            return self._bool_op(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            truth, defined = self._cond(node.operand)
            return z3.Not(truth), defined
        outer, self._reads = self._reads, []
        try:
            truth = self._atom_truth(node)
            reads = self._reads
        finally:
            self._reads = outer
        return truth, _conjunction(reads)

    def _bool_op(self, node: ast.BoolOp) -> tuple[Any, Any]:
        is_and = isinstance(node.op, ast.And)
        truth, defined = self._cond(node.values[-1])
        for value in reversed(node.values[:-1]):
            head_truth, head_defined = self._cond(value)
            reached = head_truth if is_and else z3.Not(head_truth)
            defined = z3.And(head_defined, z3.Implies(reached, defined))
            truth = z3.And(head_truth, truth) if is_and else z3.Or(head_truth, truth)
        return truth, defined

    def _atom_truth(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Constant):
            return z3.BoolVal(bool(node.value))
        if isinstance(node, ast.Name):
            return self._name_truth(node.id)
        return self._truthy(self.term(node))

    def _name_truth(self, name: str) -> Any:
        var = self._env.variable(name)
        base = self._truthy(var.term) if var.ptype.is_scalar else var.term > 0
        return base if var.is_none is None else z3.And(z3.Not(var.is_none), base)

    @staticmethod
    def _truthy(term: Any) -> Any:
        if z3.is_bool(term):
            return term
        if is_string(term):
            return z3.Length(term) > 0
        return term != 0

    # ------------------------------------------------------------------ values

    def term(self, node: ast.expr) -> Any:
        """Translate ``node`` evaluated for its value (never ``None``)."""
        operand = self._operand(node)
        if operand.term is None:
            raise UnsupportedExpression("None cannot be used as a value here")
        if operand.name is not None:
            self._reads.append(z3.Not(operand.none))
        return operand.term

    def _number(self, node: ast.expr) -> Any:
        value = self.term(node)
        try:
            return as_number(value)
        except UnsupportedExpression:
            raise UnsupportedExpression(f"`{_snippet(node)}` is not a number") from None

    def _operand(self, node: ast.expr) -> _Operand:
        if isinstance(node, ast.Constant) and node.value is None:
            return _Operand(None, _TRUE)
        if isinstance(node, ast.Name):
            var = self._env.variable(node.id)
            if not var.ptype.is_scalar:
                raise UnsupportedExpression(
                    f"{var.ptype.text or var.ptype.kind} parameter {node.id} is only "
                    "supported inside len() and truth tests"
                )
            if var.is_none is None:
                return _Operand(var.term, _FALSE)
            return _Operand(var.term, var.is_none, node.id)
        return _Operand(self._compute(node), _FALSE)

    def _compute(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Constant):
            return _constant(node.value)
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        if isinstance(node, ast.UnaryOp):
            return self._unary(node)
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        raise UnsupportedExpression(f"unsupported expression `{_snippet(node)}`")

    # ------------------------------------------------------------------ arithmetic

    def _unary(self, node: ast.UnaryOp) -> Any:
        if isinstance(node.op, ast.Not):
            truth, defined = self._cond(node.operand)
            self._reads.append(defined)
            return z3.Not(truth)
        if isinstance(node.op, ast.USub):
            return -self._number(node.operand)
        if isinstance(node.op, ast.UAdd):
            return self._number(node.operand)
        raise UnsupportedExpression(f"unsupported expression `{_snippet(node)}`")

    def _binop(self, node: ast.BinOp) -> Any:
        op = node.op
        if isinstance(op, ast.Add | ast.Sub | ast.Mult):
            left, right = unify(self._number(node.left), self._number(node.right))
            if isinstance(op, ast.Add):
                return left + right
            return left - right if isinstance(op, ast.Sub) else left * right
        if isinstance(op, ast.Div):
            return self._true_division(node)
        if isinstance(op, ast.FloorDiv | ast.Mod):
            return self._integer_division(node)
        raise UnsupportedExpression(f"operator {type(op).__name__} is not supported")

    def _true_division(self, node: ast.BinOp) -> Any:
        left, right = unify(self._number(node.left), self._number(node.right))
        left = z3.ToReal(left) if z3.is_int(left) else left
        right = z3.ToReal(right) if z3.is_int(right) else right
        divisor = z3.simplify(right)
        if z3.is_rational_value(divisor):
            if divisor.numerator_as_long() == 0:
                raise UnsupportedExpression("division by the constant zero")
        else:
            self._side_conditions.append(right != 0)
        return left / right

    def _integer_division(self, node: ast.BinOp) -> Any:
        left, right = self._number(node.left), self._number(node.right)
        if not (z3.is_int(left) and z3.is_int(right)):
            raise UnsupportedExpression("// and % are supported on integers only")
        divisor = z3.simplify(right)
        if not z3.is_int_value(divisor) or divisor.as_long() == 0:
            raise UnsupportedExpression("// and % need a nonzero constant divisor")
        floor = isinstance(node.op, ast.FloorDiv)
        if divisor.as_long() > 0:
            return left / divisor if floor else left % divisor
        # z3 rounds towards -inf only for positive divisors; flip the signs for negative ones.
        flipped = z3.IntVal(-divisor.as_long())
        return (-left) / flipped if floor else -((-left) % flipped)

    # ------------------------------------------------------------------ comparisons

    def _compare(self, node: ast.Compare) -> Any:
        operands = [node.left, *node.comparators]
        parts = [
            self._compare_pair(left, op, right)
            for left, op, right in zip(operands[:-1], node.ops, operands[1:], strict=True)
        ]
        return parts[0] if len(parts) == 1 else z3.And(*parts)

    def _compare_pair(self, left: ast.expr, op: ast.cmpop, right: ast.expr) -> Any:
        if isinstance(op, ast.Is | ast.IsNot):
            result = self._identity(left, right)
            return result if isinstance(op, ast.Is) else z3.Not(result)
        if isinstance(op, ast.Eq | ast.NotEq):
            result = self._equal(self._operand(left), self._operand(right))
            return result if isinstance(op, ast.Eq) else z3.Not(result)
        if isinstance(op, ast.In | ast.NotIn):
            result = self._membership(left, right)
            return result if isinstance(op, ast.In) else z3.Not(result)
        return self._ordering(op, self.term(left), self.term(right))

    def _identity(self, left: ast.expr, right: ast.expr) -> Any:
        """``left is right``; only comparisons with ``None`` are supported."""
        left_none, right_none = _is_none(left), _is_none(right)
        if left_none and right_none:
            return _TRUE
        if not (left_none or right_none):
            raise UnsupportedExpression("`is` is only supported against None")
        return self._nullness(right if left_none else left)

    def _nullness(self, node: ast.expr) -> Any:
        """Boolean term: ``node`` evaluates to ``None``."""
        if isinstance(node, ast.Name):
            flag = self._env.none_flag(node.id)
            return _FALSE if flag is None else flag
        return self._operand(node).none

    def _equal(self, left: _Operand, right: _Operand) -> Any:
        if left.term is None and right.term is None:
            return _TRUE
        if left.term is None:
            return right.none
        if right.term is None:
            return left.none
        same = _same_value(left.term, right.term)
        if z3.is_false(left.none) and z3.is_false(right.none):
            return same
        return z3.Or(
            z3.And(left.none, right.none),
            z3.And(z3.Not(left.none), z3.Not(right.none), same),
        )

    @staticmethod
    def _ordering(op: ast.cmpop, left: Any, right: Any) -> Any:
        if is_string(left) != is_string(right):
            raise UnsupportedExpression("cannot order a string and a number")
        if is_string(left):
            _require_orderable(left)
            _require_orderable(right)
        else:
            left, right = unify(left, right)
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        return left > right if isinstance(op, ast.Gt) else left >= right

    def _membership(self, left: ast.expr, right: ast.expr) -> Any:
        if isinstance(right, ast.Tuple | ast.List | ast.Set):
            subject = self._operand(left)
            options = [self._equal(subject, self._literal(item)) for item in right.elts]
            if not options:
                return _FALSE
            return options[0] if len(options) == 1 else z3.Or(*options)
        needle, haystack = self.term(left), self.term(right)
        if not (is_string(needle) and is_string(haystack)):
            raise UnsupportedExpression("`in` needs a literal collection or two strings")
        return z3.Contains(haystack, needle)

    def _literal(self, node: ast.expr) -> _Operand:
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError):
            raise UnsupportedExpression("`in` needs a collection of literals") from None
        if value is None:
            return _Operand(None, _TRUE)
        return _Operand(_constant(value), _FALSE)

    # ------------------------------------------------------------------ calls

    def _call(self, node: ast.Call) -> Any:
        func = node.func
        if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
            raise UnsupportedExpression(f"call `{_snippet(node)}` is not supported")
        if isinstance(func, ast.Name):
            handler = self._functions.get(func.id)
            if handler is not None:
                return handler(node)
        if isinstance(func, ast.Attribute) and func.attr in ("startswith", "endswith"):
            return self._affix(node, func)
        raise UnsupportedExpression(f"call to `{_snippet(func)}()` is not supported")

    def _len(self, node: ast.Call) -> Any:
        if len(node.args) != 1:
            raise UnsupportedExpression("len() takes exactly one argument")
        arg = node.args[0]
        if isinstance(arg, ast.Name):
            var = self._env.variable(arg.id)
            if var.is_none is not None:
                self._reads.append(z3.Not(var.is_none))
            if var.ptype.kind is Kind.STR:
                return z3.Length(var.term)
            if var.ptype.is_length_modelled:
                return var.term
            raise UnsupportedExpression(f"len() of {var.ptype.text or var.ptype.kind} parameter")
        value = self.term(arg)
        if not is_string(value):
            raise UnsupportedExpression(f"len() of `{_snippet(arg)}` is not supported")
        return z3.Length(value)

    def _abs(self, node: ast.Call) -> Any:
        if len(node.args) != 1:
            raise UnsupportedExpression("abs() takes exactly one argument")
        value = self._number(node.args[0])
        return z3.If(value >= 0, value, -value)

    def _min(self, node: ast.Call) -> Any:
        left, right = self._two_numbers(node, "min")
        return z3.If(left <= right, left, right)

    def _max(self, node: ast.Call) -> Any:
        left, right = self._two_numbers(node, "max")
        return z3.If(left >= right, left, right)

    def _two_numbers(self, node: ast.Call, name: str) -> tuple[Any, Any]:
        if len(node.args) != 2:
            raise UnsupportedExpression(f"{name}() is supported with exactly two arguments")
        return unify(self._number(node.args[0]), self._number(node.args[1]))

    def _affix(self, node: ast.Call, func: ast.Attribute) -> Any:
        if len(node.args) != 1:
            raise UnsupportedExpression(f"{func.attr}() takes exactly one argument")
        subject, affix = self.term(func.value), self.term(node.args[0])
        if not (is_string(subject) and is_string(affix)):
            raise UnsupportedExpression(f"`{_snippet(node)}` needs string operands")
        return (
            z3.PrefixOf(affix, subject)
            if func.attr == "startswith"
            else z3.SuffixOf(affix, subject)
        )

    def _isinstance(self, node: ast.Call) -> Any:
        """``isinstance(param, T)`` decided from the parameter's annotation."""
        if len(node.args) != 2 or not isinstance(node.args[0], ast.Name):
            raise UnsupportedExpression("isinstance() is only supported on a parameter")
        name = node.args[0].id
        classes = self._env.type_of(name).runtime_classes
        if classes is None:
            raise UnsupportedExpression(f"the type of parameter {name} is unknown for isinstance()")
        matches = z3.BoolVal(any(cls in classes for cls in _class_names(node.args[1])))
        if not self._env.type_of(name).optional:
            return matches
        none_flag = self._env.none_flag(name)
        return z3.And(z3.Not(none_flag), matches)


def _require_orderable(term: Any) -> None:
    if z3.is_string_value(term) and any(
        ord(char) > _MAX_ORDERED_CODE_POINT for char in python_string(term)
    ):
        raise UnsupportedExpression("ordering strings with code points above U+2FFFF")


def _conjunction(items: list[Any]) -> Any:
    return z3.And(*items) if len(items) > 1 else (items[0] if items else _TRUE)


def _constant(value: object) -> Any:
    if isinstance(value, bool):
        return z3.BoolVal(value)
    if isinstance(value, int):
        return z3.IntVal(value)
    if isinstance(value, float):
        return real_val(value)
    if isinstance(value, str):
        return string_val(value)
    raise UnsupportedExpression(f"constant of type {type(value).__name__} is not supported")


def _same_value(left: Any, right: Any) -> Any:
    """``left == right`` with Python semantics for mixed sorts."""
    if is_string(left) and is_string(right):
        return left == right
    if is_string(left) or is_string(right):
        return _FALSE  # "a" == 1 is simply False in Python
    if z3.is_bool(left) and z3.is_bool(right):
        return left == right
    left, right = unify(left, right)
    return left == right


def _class_names(node: ast.expr) -> list[str]:
    nodes = node.elts if isinstance(node, ast.Tuple) else [node]
    names = [item.id for item in nodes if isinstance(item, ast.Name)]
    if len(names) != len(nodes) or not set(names) <= _BUILTIN_CLASSES:
        raise UnsupportedExpression(f"isinstance() against `{_snippet(node)}` is not supported")
    return names


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _snippet(node: ast.expr) -> str:
    text = ast.unparse(node)
    return text if len(text) <= _MAX_SNIPPET else text[: _MAX_SNIPPET - 3] + "..."
