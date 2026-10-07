"""安全四则运算表达式求值：变量用 {名称} 引用（花括号内也可写子表达式）。

设计给联动映射的「运算组合」使用，例如::

    {HP} / {HPmax} * 200
    ({StrengthA} - {LimitA}) * ({Hurt} / 100 + 1)

约定：
* 仅允许 + - * / // % ** 比较运算（> >= < <= == !=）括号与数字、标识符、
  abs/min/max/round 函数；比较结果真 = 1 / 假 = 0；
* 未定义变量按 0 处理（OSC/游戏数据到达前不报错）；
* 除零 / 语法错误抛 ExprError，调用方跳过本轮即可；
* 全角括号与运算符自动归一化，方便中文输入法下书写。
"""

from __future__ import annotations

import ast
import re

__all__ = ["ExprError", "evaluate", "eval_int", "variables", "normalize"]

_MAX_POW = 16          # 指数上限，防 9**9**9 卡死
_MAX_BASE = 1e12       # 底数上限

_FULLWIDTH = str.maketrans(
    "（）｛｝＋－／＊％．，０１２３４５６７８９",
    "(){}+-/*%.,0123456789")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+")
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round}


class ExprError(ValueError):
    """表达式非法或求值失败（除零、语法错误、越界等）。"""


def normalize(text: str) -> str:
    return str(text or "").strip().translate(_FULLWIDTH)


def variables(text: str) -> set[str]:
    """表达式引用的全部变量名（供 UI 校验/提示；不含函数名与数字）。"""
    names: set[str] = set()
    for token in _IDENT.findall(normalize(text)):
        if token not in _FUNCS:
            names.add(token)
    return names


def evaluate(text: str, values: dict[str, float]) -> float:
    """求值。先递归展开 {…}（内层结果以数字回填），再解析顶层表达式。"""
    src = normalize(text)
    if not src:
        raise ExprError("空表达式")
    # 内层花括号优先：{…} 内不允许再嵌套（正则取最内层非嵌套段）
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
    """求值 → 四舍五入取整 → 钳制到 [low, high]。"""
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
    # 变量名优先：核心参数 id 含点号（COYOTE.StrengthA），不是合法 Python 标识符
    if src in values:
        return float(values[src])
    if _DOTTED.fullmatch(src):
        # 带点参数 id（设备未接入时不在值表）按未定义变量归 0，不报语法错误
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
        # 比较运算（事件分支条件用）：真 = 1.0 / 假 = 0.0，链式左结合
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
