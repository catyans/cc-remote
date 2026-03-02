"""
Discord Bot 核心模块
将所有组件串联：tmux 管理、轮询、命令、消息转发。
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands

from .config import AppConfig, LOG_DIR
from .tmux_manager import TmuxManager
from .poller import OutputPoller
from .commands import setup_commands, ConfirmView, DangerConfirmView

logger = logging.getLogger(__name__)


class CCRemoteBot:
    """CC-Remote 主控类，组装并运行所有组件。"""

    def __init__(self, config: AppConfig):
        self.config = config

        # Discord bot 实例
        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = commands.Bot(
            command_prefix="!",  # 保留前缀命令兼容（主要用斜杠命令）
            intents=intents,
            help_command=None,
        )

        # 核心组件
        self.tmux = TmuxManager(config.tmux)
        self.poller = OutputPoller(
            self.tmux, config.poller, idle_timeout=config.security.idle_timeout
        )

        # 活跃项目 -> 绑定的 Discord 频道 ID
        self._channel_bindings: dict[str, int] = {}
        # 频道 -> 活跃项目（反向映射）
        self._project_by_channel: dict[int, str] = {}
        # 命令 Cog 引用
        self._cog = None

        # 待响应消息：project -> discord.Message（用于 ⏳/✅ 状态指示）
        self._pending_messages: dict[str, discord.Message] = {}

        # 注册事件
        self._register_events()

    # ------------------------------------------------------------------
    # 事件注册
    # ------------------------------------------------------------------

    def _register_events(self) -> None:
        @self.bot.event
        async def on_ready():
            logger.info("Bot 已上线: %s (ID: %s)", self.bot.user.name, self.bot.user.id)
            # 注册斜杠命令
            self.bot._cc_bot = self  # 让 Cog 能访问频道绑定
            self._cog = await setup_commands(self.bot, self.tmux, self.poller, self.config)
            try:
                synced = await self.bot.tree.sync()
                logger.info("已同步 %d 个斜杠命令", len(synced))
            except Exception:
                logger.exception("命令同步失败")

            # 设置轮询回调
            self.poller.on_output = self._on_poller_output
            self.poller.on_confirm = self._on_poller_confirm
            self.poller.on_tool_status = self._on_poller_tool_status
            self.poller.on_idle_timeout = self._on_poller_idle_timeout
            self.poller.on_menu = self._on_poller_menu

            # 设置状态
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name="Claude Code",
                )
            )

        @self.bot.event
        async def on_message(message: discord.Message):
            # 忽略 bot 自身消息
            if message.author.bot:
                return

            # 处理前缀命令
            await self.bot.process_commands(message)

            # 忽略前缀命令
            if message.content.startswith(("!", "/")):
                return

            # 只处理已绑定频道的消息（/start 时自动绑定）
            channel_id = message.channel.id
            logger.info("on_message: user=%s channel=%s content=%s", message.author.name, channel_id, message.content[:50])
            if channel_id not in self._project_by_channel:
                return

            # 权限检查
            if not self._check_permission(message):
                return

            await self._handle_user_message(message)

    # ------------------------------------------------------------------
    # 权限
    # ------------------------------------------------------------------

    def _check_permission(self, message: discord.Message) -> bool:
        """检查消息是否来自授权用户。"""
        user_ok = (
            not self.config.discord.allowed_users
            or message.author.id in self.config.discord.allowed_users
        )
        return user_ok

    # ------------------------------------------------------------------
    # 用户消息处理
    # ------------------------------------------------------------------

    async def _handle_user_message(self, message: discord.Message) -> None:
        """处理用户发到频道的普通消息，转发给 claude。"""
        channel_id = message.channel.id
        project = self._project_by_channel.get(channel_id, "default")

        # 检查会话是否存活
        if not await asyncio.to_thread(self.tmux.is_alive, project):
            await message.reply(
                "⚠️ 没有活跃的 Claude Code 会话。请先使用 `/start` 启动。",
                mention_author=False,
            )
            return

        text = message.content.strip()
        if not text:
            return

        # 危险命令检查
        dangerous_kw = self._cog._check_dangerous(text) if self._cog else None
        if dangerous_kw:
            self._cog._audit_log(message.author, "dangerous_attempt", f"keyword={dangerous_kw}, text={text[:100]}")
            view = DangerConfirmView(self.tmux, project, text)
            await message.reply(
                f"⚠️ **危险操作检测**\n"
                f"检测到关键词: `{dangerous_kw}`\n"
                f"消息: `{text[:100]}`\n"
                f"请确认是否执行：",
                view=view,
                mention_author=False,
            )
            return

        # 审计日志
        if self._cog:
            self._cog._audit_log(message.author, "message", text[:200])

        # 发送给 claude
        await asyncio.to_thread(self.tmux.send_keys, project, text)

        # 添加 ⏳ 反应表示正在处理
        try:
            await message.add_reaction("\u23f3")  # ⏳
        except discord.errors.Forbidden:
            pass

        # 记录待响应消息，用于后续 ✅ 状态更新
        self._pending_messages[project] = message

    # ------------------------------------------------------------------
    # Poller 回调
    # ------------------------------------------------------------------

    async def _on_poller_output(self, project: str, text: str) -> None:
        """轮询到新输出时的回调。"""
        channel_id = self._channel_bindings.get(project)
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        # 更新待响应消息的状态指示：⏳ -> ✅
        pending = self._pending_messages.pop(project, None)
        if pending:
            try:
                await pending.remove_reaction("\u23f3", self.bot.user)  # 移除 ⏳
                await pending.add_reaction("\u2705")  # ✅
            except (discord.errors.Forbidden, discord.errors.NotFound):
                pass

        try:
            await channel.send(text)
        except discord.errors.HTTPException as e:
            # 消息过长时分段重试
            if e.code == 50035:
                from .formatter import split_message
                for chunk in split_message(text, 1800):
                    await channel.send(chunk)
            else:
                logger.warning("发送消息失败: %s", e)

    async def _on_poller_confirm(self, project: str, prompt: str) -> None:
        """检测到确认提示时的回调，发送按钮。"""
        channel_id = self._channel_bindings.get(project)
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        view = ConfirmView(self.tmux, project)
        await channel.send(
            f"🔔 **需要确认**\n```\n{prompt[:500]}\n```",
            view=view,
        )

    async def _on_poller_tool_status(self, project: str, tool_name: str) -> None:
        """检测到工具运行状态时的回调。"""
        channel_id = self._channel_bindings.get(project)
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title="🔧 Tool Running",
            description=f"Claude is using: **{tool_name}**",
            color=discord.Color.blue(),
        )
        await channel.send(embed=embed)


    async def _on_poller_menu(self, project: str, options: list[dict]) -> None:
        """检测到选项菜单时的回调，发送 Discord 按钮。"""
        channel_id = self._channel_bindings.get(project)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        from .commands import MenuSelectView
        view = MenuSelectView(self.tmux, project, options)
        desc = "\n".join(f"**{o['num']}.** {o['label']}" + (f"\n   {o['desc']}" if o.get('desc') else "") for o in options)
        await channel.send(f"🔢 **请选择：**\n{desc}", view=view)

    async def _on_poller_idle_timeout(self, project: str) -> None:
        """空闲超时回调。"""
        channel_id = self._channel_bindings.get(project)
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        await channel.send(
            f"💤 会话 `{project}` 已空闲超过 {self.config.security.idle_timeout} 秒"
        )

    # ------------------------------------------------------------------
    # 启动/关闭
    # ------------------------------------------------------------------

    def run(self) -> None:
        """启动 bot（阻塞）。"""
        token = self.config.discord.token
        if not token:
            raise ValueError(
                "Discord token 未配置。请在 config.yaml 或 DISCORD_TOKEN 环境变量中设置。"
            )

        # 确保日志目录存在
        LOG_DIR.mkdir(exist_ok=True)

        logger.info("正在启动 CC-Remote Bot...")
        self.bot.run(token, log_handler=None)

    async def shutdown(self) -> None:
        """优雅关闭所有组件。"""
        logger.info("正在关闭 CC-Remote Bot...")
        self.poller.stop_all()
        # 关闭所有 tmux 会话
        for project in list(self.tmux.sessions):
            self.tmux.stop_session(project)
        await self.bot.close()
        logger.info("CC-Remote Bot 已关闭")
