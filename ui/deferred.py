"""分帧渲染：切换页面先上主要框架，卡片拆成多帧逐块上屏。

UI 回调跑在 win32more 的手摇 asyncio 循环上（SetTimer 每 100ms 摇一拍），
await asyncio.sleep 让协程让回消息泵，下一拍再继续；两拍之间 WinUI 完成
布局与绘制，每一步卡片都真实多撑一帧，避免一次性建完整页造成的长帧卡顿。

每次 run() 递增代数，在途的旧构建自动失效——连续触发（tick 状态变化、
on_notify、切页、换主题）时旧构建不会与新构建交错写入控件。
"""

from __future__ import annotations

import asyncio

from win32more import asyncui

# 手摇循环约 100ms 一拍，更短的间隔只是等待下一拍，取小值即可
FRAME_GAP_S = 0.02


class Phased:
    def __init__(self, shell):
        self._shell = shell
        self._gen = 0
        self._active = False

    @property
    def busy(self) -> bool:
        """是否有分帧步骤尚未执行完（含被新构建接管前的间隙）。"""
        return self._active

    def cancel(self) -> None:
        """作废在途构建（关窗时调用）：待执行步骤直接丢弃，不再触碰控件。"""
        self._gen += 1
        self._active = False

    def run(self, steps, finally_step=None) -> None:
        steps = list(steps)
        if not steps:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 不在 UI 线程的手摇循环里（旧版 Python / 非常驻循环）：
            # 退化为同步构建，保证正确性优先于流畅度
            for step in steps:
                step()
            return
        self._gen += 1
        gen = self._gen
        self._active = True

        async def _worker():
            try:
                for i, step in enumerate(steps):
                    if gen != self._gen:
                        return
                    try:
                        step()
                    except Exception as exc:
                        self._shell.logs.append(f"分帧渲染失败: {exc!r}")
                    if i + 1 < len(steps):
                        await asyncio.sleep(FRAME_GAP_S)
            finally:
                if gen == self._gen:
                    self._active = False
                    if finally_step is not None:
                        try:
                            finally_step()
                        except Exception as exc:
                            self._shell.logs.append(f"分帧渲染收尾失败: {exc!r}")

        asyncui.create_task(_worker())
