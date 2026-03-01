"""
输出轮询模块
定期捕获 tmux 输出，检测增量变化，通过回调发送到 Discord。
"""

import asyncio
import logging
from datetime import datetime
from collections import deque

from .tmux_manager import TmuxManager
from .formatter import (
    format_output,
    detect_confirmation,
    detect_tool_running,
    detect_diff,
    split_message,
    filter_output,
)
from .config import PollerConfig

logger = logging.getLogger(__name__)


class OutputPoller:
    """
    轮询 tmux 输出，检测增量变化并回调。

    回调签名:
      on_output(project: str, text: str) -> None
      on_confirm(project: str, prompt: str) -> None
      on_tool_status(project: str, tool_name: str) -> None
      on_idle_timeout(project: str) -> None
    """

    def __init__(
        self,
        tmux: TmuxManager,
        config: PollerConfig,
        idle_timeout: int = 300,
    ):
        self.tmux = tmux
        self.config = config
        self.idle_timeout = idle_timeout

        # 每个项目的上次输出快照
        self._last_output: dict[str, str] = {}
        # 每个项目的上次活动时间
        self._last_activity: dict[str, datetime] = {}
        # 已发送过的确认提示（避免重复）
        self._sent_confirms: dict[str, str] = {}
        # 交互历史（项目 -> deque）
        self._history: dict[str, deque] = {}

        # 回调
        self.on_output = None
        self.on_confirm = None
        self.on_tool_status = None
        self.on_idle_timeout = None
        self.on_menu = None

        # 最小增量长度（太短不发送）
        self.min_delta_len = 10
        # 连续空输出计数（避免频繁检测）
        self._empty_count: dict[str, int] = {}

        # 控制标志
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, project: str) -> None:
        """开始轮询指定项目。"""
        if project in self._tasks and not self._tasks[project].done():
            logger.warning("项目 %s 已在轮询中", project)
            return
        self._last_output[project] = ""
        self._last_activity[project] = datetime.now()
        self._sent_confirms[project] = ""
        self._empty_count[project] = 0
        self._history.setdefault(project, deque(maxlen=50))
        self._tasks[project] = asyncio.create_task(self._poll_loop(project))
        logger.info("开始轮询: %s", project)

    def stop(self, project: str) -> None:
        """停止轮询指定项目。"""
        task = self._tasks.pop(project, None)
        if task and not task.done():
            task.cancel()
            logger.info("停止轮询: %s", project)

    def stop_all(self) -> None:
        """停止所有轮询。"""
        for project in list(self._tasks):
            self.stop(project)

    def get_history(self, project: str, n: int = 10) -> list[dict]:
        """获取最近 n 条交互历史。"""
        history = self._history.get(project, deque())
        return list(history)[-n:]

    async def _poll_loop(self, project: str) -> None:
        """轮询主循环。"""
        logger.debug("轮询循环启动: %s", project)
        try:
            while True:
                await asyncio.sleep(self.config.interval)

                if not await asyncio.to_thread(self.tmux.is_alive, project):
                    logger.info("会话 %s 已终止，停止轮询", project)
                    break

                # 捕获当前输出
                raw_output = await asyncio.to_thread(self.tmux.capture_pane, project)
                formatted = format_output(raw_output)

                # 计算增量
                last = self._last_output.get(project, "")
                delta = self._compute_delta(last, formatted)

                if not delta:
                    self._empty_count[project] = self._empty_count.get(project, 0) + 1
                    # 连续多次空输出时减少检测频率
                    if self._empty_count[project] > 5:
                        await asyncio.sleep(self.config.interval)
                    # 检查空闲超时
                    await self._check_idle(project)
                    continue

                self._empty_count[project] = 0

                # 等待输出稳定（避免发送不完整内容）
                await asyncio.sleep(self.config.settle_time)
                raw_output2 = await asyncio.to_thread(self.tmux.capture_pane, project)
                formatted2 = format_output(raw_output2)
                delta = self._compute_delta(last, formatted2)

                if not delta or len(delta.strip()) < self.min_delta_len:
                    continue

                self._last_output[project] = formatted2
                self._last_activity[project] = datetime.now()

                # 记录历史
                self._history.setdefault(project, deque(maxlen=50)).append({
                    "time": datetime.now().isoformat(),
                    "type": "output",
                    "content": delta[:500],  # 截断保存
                })

                # 检测确认提示
                confirm_prompt = detect_confirmation(delta)
                if confirm_prompt and confirm_prompt != self._sent_confirms.get(project):
                    self._sent_confirms[project] = confirm_prompt
                    if self.on_confirm:
                        await self.on_confirm(project, confirm_prompt)

                # 检测选项菜单
                from .formatter import detect_menu_options
                menu_options = detect_menu_options(delta)
                if menu_options and len(menu_options) >= 2 and self.on_menu:
                    await self.on_menu(project, menu_options)

                # 检测工具运行状态
                tool_name = detect_tool_running(delta)
                if tool_name and self.on_tool_status:
                    await self.on_tool_status(project, tool_name)

                # 发送输出
                if self.on_output:
                    chunks = split_message(delta, self.config.max_chunk_size)
                    for chunk in chunks:
                        await self.on_output(project, chunk)

        except asyncio.CancelledError:
            logger.debug("轮询循环已取消: %s", project)
        except Exception:
            logger.exception("轮询循环异常: %s", project)

    def _compute_delta(self, old: str, new: str) -> str:
        """
        计算增量输出。
        使用简单的后缀匹配：找到 old 在 new 中的最长公共后缀，
        返回 new 中新增的部分。
        """
        if not old:
            return new

        if new == old:
            return ""

        # 在 new 中查找 old 的最后一部分
        # 取 old 的最后 N 行作为锚点
        old_lines = old.split("\n")
        anchor_size = min(5, len(old_lines))
        anchor = "\n".join(old_lines[-anchor_size:])

        idx = new.rfind(anchor)
        if idx >= 0:
            delta = new[idx + len(anchor):]
            return delta.strip("\n")

        # 如果找不到锚点，对比行级差异
        new_lines = new.split("\n")
        # 找到第一个不同的行
        min_len = min(len(old_lines), len(new_lines))
        diff_start = 0
        for i in range(min_len):
            if old_lines[i] != new_lines[i]:
                diff_start = i
                break
        else:
            diff_start = min_len

        if diff_start < len(new_lines):
            return "\n".join(new_lines[diff_start:])

        return ""

    async def _check_idle(self, project: str) -> None:
        """检查空闲超时。"""
        last = self._last_activity.get(project)
        if not last:
            return
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed >= self.idle_timeout:
            if self.on_idle_timeout:
                await self.on_idle_timeout(project)
            # 重置计时避免重复触发
            self._last_activity[project] = datetime.now()
