"""
配置加载模块
从 config.yaml 读取配置，提供带默认值的访问接口。
"""

import os
import yaml
import logging
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"
LOG_DIR = ROOT_DIR / "logs"


@dataclass
class DiscordConfig:
    token: str = ""
    allowed_users: list[int] = field(default_factory=list)
    allowed_channels: list[int] = field(default_factory=list)


@dataclass
class TmuxConfig:
    session_prefix: str = "cc-remote"
    claude_path: str = ""
    default_cwd: str = "~"
    history_limit: int = 200


@dataclass
class PollerConfig:
    interval: float = 0.8
    settle_time: float = 0.5
    max_chunk_size: int = 1800


@dataclass
class SecurityConfig:
    dangerous_keywords: list[str] = field(default_factory=lambda: [
        "rm -rf", "DROP TABLE", "DELETE FROM", "format", "mkfs", "dd if=",
        "git push --force", "git push -f", "chmod 777", "curl | sh", "wget | sh",
    ])
    idle_timeout: int = 300
    audit_log: bool = True


@dataclass
class AppConfig:
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    tmux: TmuxConfig = field(default_factory=TmuxConfig)
    poller: PollerConfig = field(default_factory=PollerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    projects: dict[str, str] = field(default_factory=lambda: {"default": "~"})


def load_config(path: Path | None = None) -> AppConfig:
    """加载配置文件，不存在则使用默认值。"""
    path = path or CONFIG_PATH
    cfg = AppConfig()

    if not path.exists():
        logger.warning("配置文件 %s 不存在，使用默认配置", path)
        return cfg

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Discord
    d = raw.get("discord", {})
    cfg.discord.token = d.get("token", "") or os.environ.get("DISCORD_TOKEN", "")
    cfg.discord.allowed_users = [int(u) for u in d.get("allowed_users", []) if u]
    cfg.discord.allowed_channels = [int(c) for c in d.get("allowed_channels", []) if c]

    # Tmux
    t = raw.get("tmux", {})
    cfg.tmux.session_prefix = t.get("session_prefix", cfg.tmux.session_prefix)
    cfg.tmux.claude_path = t.get("claude_path", "") or ""
    cfg.tmux.default_cwd = t.get("default_cwd", cfg.tmux.default_cwd)
    cfg.tmux.history_limit = int(t.get("history_limit", cfg.tmux.history_limit))

    # Poller
    p = raw.get("poller", {})
    cfg.poller.interval = float(p.get("interval", cfg.poller.interval))
    cfg.poller.settle_time = float(p.get("settle_time", cfg.poller.settle_time))
    cfg.poller.max_chunk_size = int(p.get("max_chunk_size", cfg.poller.max_chunk_size))

    # Security
    s = raw.get("security", {})
    if "dangerous_keywords" in s:
        cfg.security.dangerous_keywords = s["dangerous_keywords"]
    cfg.security.idle_timeout = int(s.get("idle_timeout", cfg.security.idle_timeout))
    cfg.security.audit_log = bool(s.get("audit_log", cfg.security.audit_log))

    # Projects
    cfg.projects = raw.get("projects", cfg.projects)

    logger.info("配置加载完成: %s", path)
    return cfg
