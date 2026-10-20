"""引擎日志的一行分类：级别判断归引擎，界面只负责显示。"""

from __future__ import annotations


def classify_log(msg: str) -> str:
    if any(k in msg for k in ("失败", "错误", "异常", "Traceback")):
        return "error"
    if any(k in msg for k in ("警告", "重试", "超时", "丢包", "限幅")):
        return "warn"
    if msg.startswith(("<", ">>", "{")) or " frame" in msg.lower():
        return "debug"
    return "info"
