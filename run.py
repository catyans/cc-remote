#!/usr/bin/env python3
"""
CC-Remote 入口文件
通过 Discord Bot 远程控制 Claude Code CLI。

用法:
    python run.py
    python run.py --config path/to/config.yaml
"""

import argparse
import logging
import sys
from pathlib import Path

from src.config import load_config, LOG_DIR
from src.bot import CCRemoteBot


def setup_logging(debug: bool = False) -> None:
    """配置日志系统，同时输出到控制台和文件。"""
    LOG_DIR.mkdir(exist_ok=True)

    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(fmt))

    # 文件 handler
    logfile = LOG_DIR / "cc-remote.log"
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt))

    # 根日志器
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)

    # 降低 discord.py 的日志级别
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="CC-Remote: Discord Bot for Claude Code")
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="配置文件路径（默认 config.yaml）",
    )
    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="启用 debug 日志",
    )
    args = parser.parse_args()

    setup_logging(args.debug)
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info("CC-Remote 启动中...")
    logger.info("=" * 50)

    # 加载配置
    config = load_config(args.config)

    # 检查依赖
    import shutil
    if not shutil.which("tmux"):
        logger.error("tmux 未安装。请先安装 tmux: brew install tmux / apt install tmux")
        sys.exit(1)

    claude_bin = config.tmux.claude_path or "claude"
    if not shutil.which(claude_bin):
        logger.warning("claude CLI (%s) 未在 PATH 中找到，请确保已安装", claude_bin)

    # 启动 bot
    bot = CCRemoteBot(config)
    try:
        bot.run()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")


if __name__ == "__main__":
    main()
