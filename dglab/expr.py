
from __future__ import annotations

import ast
import re

__all__ = ["ExprError", "evaluate", "eval_int", "variables", "normalize"]

_MAX_POW = 16
_MAX_BASE = 1e12

_FULLWIDTH = str.maketrans(
    "（）｛｝＋－／＊％．，０１２３４５６７８９",
    "(){}+-/*%.,0123456789")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+")
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round}


class ExprError(ValueError):
    pass


def normalize(text: str) -> str:
    return str(text or "").strip().translate(_FULLWIDTH)


def variables(text: str) -> set[str]:
    names: set[str] = set()
    for token in _IDENT.findall(normalize(text)):
        if token not in _FUNCS:
            names.add(token)
    return names


def evaluate(text: str, values: dict[str, float]) -> float:
    src = normalize(text)
    if not src:
        raise ExprError("空表达式")
    while True:
        m = re.search(r"\{([^{}]*)\}", src)
        if m is None:
            break
        inner = _eval_python(m.group(1), values)
        src = src[:m.start()] + repr(inner) + src[m.end():]
    if "{" in src or "}" in src:
        raise ExprError("花括号不配对")
    return _eval_python(src, values)


def eval_int(text: str, values: dict[str, float],
             low: int, high: int) -> int:
    return clamp_int(evaluate(text, values), low, high)


def clamp_int(value: float, low: int, high: int) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return low
    return max(low, min(high, n))


def _eval_python(src: str, values: dict[str, float]) -> float:
    src = src.strip()
    if not src:
        raise ExprError("空的 {} 段")
    if src in values:
        return float(values[src])
    if _DOTTED.fullmatch(src):
        return 0.0
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"语法错误: {exc.msg}") from None
    return float(_node(tree.body, values))


def _node(node: ast.AST, values: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return 1.0 if node.value else 0.0
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ExprError(f"不支持的常量 {node.value!r}")
    if isinstance(node, ast.Name):
        return float(values.get(node.id, 0.0))
    if isinstance(node, ast.UnaryOp):
        val = _node(node.operand, values)
        if isinstance(node.op, ast.UAdd):
            return val
        if isinstance(node.op, ast.USub):
            return -val
        raise ExprError("不支持一元运算符（取负请用 0-x）")
    if isinstance(node, ast.BinOp):
        left = _node(node.left, values)
        right = _node(node.right, values)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, (ast.Div, ast.FloorDiv)):
            if right == 0:
                raise ExprError("除数为 0")
            out = left / right
            return float(int(out)) if isinstance(op, ast.FloorDiv) else out
        if isinstance(op, ast.Mod):
            if right == 0:
                raise ExprError("模数为 0")
            return left % right
        if isinstance(op, ast.Pow):
            if abs(right) > _MAX_POW or abs(left) > _MAX_BASE:
                raise ExprError("幂运算越界")
            return left ** right
        raise ExprError("不支持的运算符")
    if isinstance(node, ast.Compare):
        left = _node(node.left, values)
        for op, comparator in zip(node.ops, node.comparators):
            right = _node(comparator, values)
            if isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            else:
                raise ExprError("不支持的比较运算符")
            if not ok:
                return 0.0
            left = right
        return 1.0
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ExprError("仅允许 abs/min/max/round 函数")
        args = [_node(a, values) for a in node.args]
        if not args:
            raise ExprError("函数缺少参数")
        return float(_FUNCS[node.func.id](*args))
    raise ExprError(f"不支持的语法节点 {type(node).__name__}")
