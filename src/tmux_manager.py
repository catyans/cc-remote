"""
Tmux 会话管理模块
负责创建/销毁 tmux session，发送按键，捕获输出。
"""

import subprocess
import shlex
import logging
import os
import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

from .config import TmuxConfig

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """单个 tmux 会话的元数据。"""
    name: str
    project: str
    cwd: str
    created_at: datetime = field(default_factory=datetime.now)
    alive: bool = False


class TmuxManager:
    """管理 tmux 会话的生命周期和交互。"""

    def __init__(self, config: TmuxConfig):
        self.config = config
        # project_name -> SessionInfo
        self.sessions: dict[str, SessionInfo] = {}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """执行 shell 命令并返回结果。"""
        logger.debug("exec: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def _session_name(self, project: str) -> str:
        return f"{self.config.session_prefix}-{project}"

    def _resolve_cwd(self, cwd: str) -> str:
        return str(Path(cwd).expanduser().resolve())

    def _claude_bin(self) -> str:
        return self.config.claude_path or "claude"

    # ------------------------------------------------------------------
    # 会话生命周期
    # ------------------------------------------------------------------

    def start_session(self, project: str = "default", cwd: str | None = None) -> SessionInfo:
        """启动一个新的 claude CLI tmux 会话。"""
        name = self._session_name(project)
        resolved_cwd = self._resolve_cwd(cwd or self.config.default_cwd)

        # 如果已存在先销毁
        if self.is_alive(project):
            logger.info("会话 %s 已存在，先关闭", name)
            self.stop_session(project)

        # 先注册会话信息，避免竞态（send_keys/poller 可能在创建后立即访问）
        info = SessionInfo(name=name, project=project, cwd=resolved_cwd, alive=True)
        self.sessions[project] = info
        
        # 创建 tmux session，直接指定工作目录和启动命令
        claude_cmd = self._claude_bin()
        # 使用 跳过权限确认
        launch_cmd = f"{claude_cmd} --dangerously-skip-permissions"
        self._run([
            "tmux", "new-session",
            "-d",               # 后台运行
            "-s", name,         # 会话名
            "-x", "220",        # 窗口宽度（足够宽以避免折行）
            "-y", "50",         # 窗口高度
            "-c", resolved_cwd, # 工作目录
            launch_cmd,         # 启动 claude（跳过权限）
        ])

        # 等待 bypass 确认提示出现，然后自动接受
        self._auto_accept_bypass(name)

        logger.info("已启动会话: %s (cwd=%s, skip-permissions)", name, resolved_cwd)
        return info

    def _auto_accept_bypass(self, session_name: str, timeout: float = 15.0) -> None:
        """自动接受 Claude Code 启动时的所有确认提示（trust folder + bypass permissions）。"""
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(0.5)
            result = self._run([
                "tmux", "capture-pane", "-t", session_name, "-p",
            ], check=False)
            if result.returncode != 0:
                continue
            output = result.stdout
            
            # Trust folder: ❯ 1. Yes, I trust this folder → Enter
            if "Yes, I trust" in output and "Enter to confirm" in output:
                self._run(["tmux", "send-keys", "-t", session_name, "Enter"])
                logger.info("自动确认 trust folder: %s", session_name)
                time.sleep(2)
                continue
            
            # Bypass permissions: 需要选 2. Yes, I accept
            if "Yes, I accept" in output and "Enter to confirm" in output:
                # ❯ 默认在 1. No, 需要 Down 移到 2
                self._run(["tmux", "send-keys", "-t", session_name, "Down"])
                time.sleep(0.3)
                self._run(["tmux", "send-keys", "-t", session_name, "Enter"])
                logger.info("自动确认 bypass permissions: %s", session_name)
                time.sleep(2)
                continue
            
            # Claude Code 已就绪
            if "for shortcuts" in output:
                logger.info("Claude Code 已就绪: %s", session_name)
                return
        
        logger.warning("等待 Claude Code 启动超时: %s", session_name)

    def stop_session(self, project: str = "default") -> bool:
        """关闭指定项目的 tmux 会话。"""
        name = self._session_name(project)
        result = self._run(["tmux", "kill-session", "-t", name], check=False)
        if project in self.sessions:
            self.sessions[project].alive = False
        success = result.returncode == 0
        if success:
            logger.info("已关闭会话: %s", name)
        else:
            logger.warning("关闭会话失败: %s - %s", name, result.stderr.strip())
        return success

    def is_alive(self, project: str = "default") -> bool:
        """检查 tmux 会话是否存活。"""
        name = self._session_name(project)
        result = self._run(["tmux", "has-session", "-t", name], check=False)
        alive = result.returncode == 0
        if project in self.sessions:
            self.sessions[project].alive = alive
        return alive

    # ------------------------------------------------------------------
    # 输入/输出
    # ------------------------------------------------------------------

    def send_keys(self, project: str, text: str, enter: bool = True) -> None:
        """向 tmux 会话发送按键输入。使用 -l 发送文本避免特殊键解析。"""
        name = self._session_name(project)
        if text:
            # 用 -l (literal) 发送文本，避免 tmux 把文本中的特殊字符当按键
            self._run(["tmux", "send-keys", "-t", name, "-l", text])
        if enter:
            # 单独发送 Enter 键
            self._run(["tmux", "send-keys", "-t", name, "Enter"])
        logger.debug("send_keys -> %s: %s", name, text[:80])

    def send_ctrl_c(self, project: str = "default") -> None:
        """发送 Ctrl+C 中断当前操作。"""
        name = self._session_name(project)
        self._run(["tmux", "send-keys", "-t", name, "C-c"])
        logger.info("已发送 Ctrl+C 到 %s", name)

    def send_confirm(self, project: str, response: str = "y") -> None:
        """发送确认/拒绝响应（用于 Y/n 提示）。"""
        self.send_keys(project, response)

    def capture_pane(self, project: str = "default") -> str:
        """捕获 tmux 面板的当前可见内容 + 历史。"""
        name = self._session_name(project)
        result = self._run([
            "tmux", "capture-pane",
            "-t", name,
            "-p",               # 输出到 stdout
            "-S", f"-{self.config.history_limit}",  # 从历史开始
        ], check=False)
        if result.returncode != 0:
            logger.warning("capture-pane 失败: %s", result.stderr.strip())
            return ""
        return result.stdout

    def capture_visible(self, project: str = "default") -> str:
        """只捕获当前可见区域（不含历史滚动缓冲）。"""
        name = self._session_name(project)
        result = self._run([
            "tmux", "capture-pane",
            "-t", name,
            "-p",
        ], check=False)
        return result.stdout if result.returncode == 0 else ""

    # ------------------------------------------------------------------
    # 工作目录
    # ------------------------------------------------------------------

    def change_directory(self, project: str, path: str) -> str:
        """在 claude 会话中切换工作目录（通过 /cd 命令或 exit 后 cd）。"""
        resolved = self._resolve_cwd(path)
        if not os.path.isdir(resolved):
            raise FileNotFoundError(f"目录不存在: {resolved}")
        # 发送 Ctrl+C 先中断当前操作，然后切换目录重启
        self.send_ctrl_c(project)
        self.send_keys(project, f"cd {shlex.quote(resolved)} && {self._claude_bin()}")
        if project in self.sessions:
            self.sessions[project].cwd = resolved
        return resolved

    def get_info(self, project: str = "default") -> SessionInfo | None:
        """获取会话信息。"""
        return self.sessions.get(project)
