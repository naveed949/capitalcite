"""Safe arithmetic. No eval, no names, no calls. Decimal throughout."""

from __future__ import annotations

import ast
import operator
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Any

_MAX_EXPR_LEN = 256
_MAX_EXPONENT = Decimal("100")

_BINOPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def calculate(expression: str) -> Decimal:
    """Evaluate a numeric expression. Raises ValueError on anything else."""
    stripped = expression.strip()
    if not stripped:
        raise ValueError("calculator expression is empty")
    if len(stripped) > _MAX_EXPR_LEN:
        raise ValueError("calculator expression is too long")
    try:
        tree = ast.parse(stripped, mode="eval")
    except SyntaxError as exc:
        raise ValueError("calculator expression is not valid arithmetic") from exc
    try:
        return _eval(tree.body)
    except ValueError:
        raise
    except (DecimalException, ArithmeticError) as exc:
        raise ValueError("calculator overflowed or is not valid arithmetic") from exc


def _eval(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("calculator expression may only contain numbers")
        try:
            return Decimal(str(node.value))
        except InvalidOperation as exc:
            raise ValueError("calculator expression may only contain numbers") from exc
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ValueError("calculator exponent is too large")
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("calculator division by zero")
        result = _BINOPS[type(node.op)](left, right)
        if not isinstance(result, Decimal):
            raise ValueError("calculator expression produced a non-decimal result")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        result = _UNARY[type(node.op)](_eval(node.operand))
        if not isinstance(result, Decimal):
            raise ValueError("calculator expression produced a non-decimal result")
        return result
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    raise ValueError("calculator expression may only contain arithmetic")
