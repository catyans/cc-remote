"""
Discord 斜杠命令定义模块
定义所有 /slash 命令并注册到 bot。
"""

import asyncio
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from .tmux_manager import TmuxManager
from .poller import OutputPoller
from .formatter import format_status
from .config import AppConfig, LOG_DIR

logger = logging.getLogger(__name__)


class ClaudeCog(commands.Cog):
    """Claude Code 远程控制命令集。"""

    def __init__(
        self,
        bot: commands.Bot,
        tmux: TmuxManager,
        poller: OutputPoller,
        config: AppConfig,
    ):
        self.bot = bot
        self.tmux = tmux
        self.poller = poller
        self.config = config
        # 审计日志
        self._audit: list[dict] = []

    # ------------------------------------------------------------------
    # 权限检查
    # ------------------------------------------------------------------

    def _check_permission(self, interaction: discord.Interaction) -> bool:
        """检查用户和频道是否在白名单中。"""
        user_ok = (
            not self.config.discord.allowed_users
            or interaction.user.id in self.config.discord.allowed_users
        )
        channel_ok = (
            not self.config.discord.allowed_channels
            or interaction.channel_id in self.config.discord.allowed_channels
        )
        return user_ok and channel_ok

    def _audit_log(self, user: discord.User, action: str, detail: str = "") -> None:
        """记录审计日志（内存 + 文件）。"""
        if not self.config.security.audit_log:
            return
        entry = {
            "time": datetime.now().isoformat(),
            "user": f"{user.name}#{user.discriminator}",
            "user_id": user.id,
            "action": action,
            "detail": detail,
        }
        self._audit.append(entry)
        log_line = f"AUDIT: {entry['time']} | {entry['user']} | {action} | {detail}"
        logger.info(log_line)
        # 同时写入审计日志文件
        try:
            LOG_DIR.mkdir(exist_ok=True)
            with open(LOG_DIR / "audit.log", "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
        except OSError:
            logger.warning("无法写入审计日志文件")

    def _check_dangerous(self, text: str) -> str | None:
        """检查输入是否包含危险关键词，返回匹配的关键词。"""
        text_lower = text.lower()
        for kw in self.config.security.dangerous_keywords:
            if kw.lower() in text_lower:
                return kw
        return None

    # ------------------------------------------------------------------
    # 命令
    # ------------------------------------------------------------------

    @app_commands.command(name="start", description="启动 Claude Code 会话")
    @app_commands.describe(project="项目名称（默认 default）")
    async def cmd_start(
        self, interaction: discord.Interaction, project: str = "default"
    ) -> None:
        if not self._check_permission(interaction):
            await interaction.response.send_message("⛔ 无权限", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except discord.errors.HTTPException:
            return  # 已响应过，跳过
        self._audit_log(interaction.user, "start", project)

        # 获取项目工作目录
        cwd = self.config.projects.get(project, self.config.tmux.default_cwd)

        try:
            info = await asyncio.to_thread(self.tmux.start_session, project, cwd)
            self.poller.start(project)
            # 通知 bot 绑定频道
            if hasattr(self.bot, '_cc_bot'):
                self.bot._cc_bot._channel_bindings[project] = interaction.channel_id
                self.bot._cc_bot._project_by_channel[interaction.channel_id] = project
            await interaction.followup.send(
                f"✅ Claude Code 会话已启动\n"
                f"📁 项目: `{project}`\n"
                f"📂 目录: `{info.cwd}`\n"
                f"发送消息即可与 Claude 交互"
            )
        except Exception as e:
            logger.exception("启动会话失败")
            await interaction.followup.send(f"❌ 启动失败: {e}")

    @app_commands.command(name="stop", description="关闭 Claude Code 会话")
    @app_commands.describe(project="项目名称（默认 default）")
    async def cmd_stop(
        self, interaction: discord.Interaction, project: str = "default"
    ) -> None:
        if not self._check_permission(interaction):
            await interaction.response.send_message("⛔ 无权限", ephemeral=True)
            return

        self._audit_log(interaction.user, "stop", project)
        self.poller.stop(project)
        success = self.tmux.stop_session(project)
        msg = f"✅ 会话 `{project}` 已关闭" if success else f"⚠️ 会话 `{project}` 关闭失败（可能已不存在）"
        await interaction.response.send_message(msg)

    @app_commands.command(name="status", description="查看当前会话状态")
    @app_commands.describe(project="项目名称（默认 default）")
    async def cmd_status(
        self, interaction: discord.Interaction, project: str = "default"
    ) -> None:
        if not self._check_permission(interaction):
            await interaction.response.send_message("⛔ 无权限", ephemeral=True)
            return

        alive = self.tmux.is_alive(project)
        info = self.tmux.get_info(project)
        cwd = info.cwd if info else "N/A"

        embed = discord.Embed(
            title="Claude Code Status",
            color=discord.Color.green() if alive else discord.Color.red(),
        )
        embed.add_field(name="Project", value=f"`{project}`", inline=True)
        embed.add_field(name="Status", value="🟢 Running" if alive else "🔴 Stopped", inline=True)
        embed.add_field(name="CWD", value=f"`{cwd}`", inline=False)
        if info:
            embed.add_field(
                name="Started",
                value=info.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                inline=True,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="abort", description="发送 Ctrl+C 中断当前操作")
    @app_commands.describe(project="项目名称（默认 default）")
    async def cmd_abort(
        self, interaction: discord.Interaction, project: str = "default"
    ) -> None:
        if not self._check_permission(interaction):
            await interaction.response.send_message("⛔ 无权限", ephemeral=True)
            return

        self._audit_log(interaction.user, "abort", project)

        if not self.tmux.is_alive(project):
            await interaction.response.send_message("⚠️ 没有活跃的会话")
            return

        self.tmux.send_ctrl_c(project)
        await interaction.response.send_message("🛑 已发送中断信号 (Ctrl+C)")

    @app_commands.command(name="history", description="查看最近的交互历史")
    @app_commands.describe(
        project="项目名称（默认 default）",
        count="显示条数（默认 10）",
    )
    async def cmd_history(
        self,
        interaction: discord.Interaction,
        project: str = "default",
        count: int = 10,
    ) -> None:
        if not self._check_permission(interaction):
            await interaction.response.send_message("⛔ 无权限", ephemeral=True)
            return

        history = self.poller.get_history(project, count)
        if not history:
            await interaction.response.send_message("📭 暂无历史记录")
            return

        lines = []
        for entry in history:
            t = entry["time"][:19]
            content = entry["content"][:100]
            lines.append(f"`{t}` {content}")

        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n..."
        await interaction.response.send_message(f"**Recent History ({project})**\n{text}")

    @app_commands.command(name="cost", description="查看当前会话的 token 用量")
    @app_commands.describe(project="项目名称（默认 default）")
    async def cmd_cost(
        self, interaction: discord.Interaction, project: str = "default"
    ) -> None:
        if not self._check_permission(interaction):
            await interaction.response.send_message("⛔ 无权限", ephemeral=True)
            return

        if not self.tmux.is_alive(project):
            await interaction.response.send_message("⚠️ 没有活跃的会话")
            return

        # 发送 /cost 到 claude CLI（claude 内置命令）
        self.tmux.send_keys(project, "/cost")
        await interaction.response.send_message("📊 已请求 token 用量信息，结果将在输出中显示")

    @app_commands.command(name="cd", description="切换工作目录")
    @app_commands.describe(
        path="目标目录路径",
        project="项目名称（默认 default）",
    )
    async def cmd_cd(
        self,
        interaction: discord.Interaction,
        path: str,
        project: str = "default",
    ) -> None:
        if not self._check_permission(interaction):
            await interaction.response.send_message("⛔ 无权限", ephemeral=True)
            return

        self._audit_log(interaction.user, "cd", f"{project} -> {path}")

        if not self.tmux.is_alive(project):
            await interaction.response.send_message("⚠️ 没有活跃的会话")
            return

        try:
            resolved = self.tmux.change_directory(project, path)
            await interaction.response.send_message(f"📂 已切换到: `{resolved}`")
        except FileNotFoundError as e:
            await interaction.response.send_message(f"❌ {e}")

    @app_commands.command(name="project", description="切换或查看项目")
    @app_commands.describe(name="项目名称（留空查看所有项目）")
    async def cmd_project(
        self, interaction: discord.Interaction, name: str = ""
    ) -> None:
        if not self._check_permission(interaction):
            await interaction.response.send_message("⛔ 无权限", ephemeral=True)
            return

        if not name:
            # 列出所有可用项目
            lines = []
            for pname, pcwd in self.config.projects.items():
                alive = self.tmux.is_alive(pname)
                icon = "🟢" if alive else "⚪"
                lines.append(f"{icon} `{pname}` → `{pcwd}`")
            text = "\n".join(lines) or "暂无配置项目"
            await interaction.response.send_message(f"**Projects**\n{text}")
            return

        # 切换到指定项目（启动新会话）
        self._audit_log(interaction.user, "project_switch", name)
        cwd = self.config.projects.get(name)
        if not cwd:
            await interaction.response.send_message(
                f"❌ 未知项目: `{name}`\n已配置项目: {', '.join(self.config.projects.keys())}"
            )
            return

        await interaction.response.defer()
        try:
            info = await asyncio.to_thread(self.tmux.start_session, name, cwd)
            self.poller.start(name)
            await interaction.followup.send(
                f"✅ 已切换到项目 `{name}`\n📂 目录: `{info.cwd}`"
            )
        except Exception as e:
            logger.exception("切换项目失败")
            await interaction.followup.send(f"❌ 切换失败: {e}")


# ------------------------------------------------------------------
# 确认按钮 View
# ------------------------------------------------------------------


class ConfirmView(discord.ui.View):
    """Y/n 确认提示的 Discord 按钮视图。"""

    def __init__(self, tmux: TmuxManager, project: str, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.tmux = tmux
        self.project = project
        self.responded = False

    @discord.ui.button(label="Yes (Y)", style=discord.ButtonStyle.success)
    async def btn_yes(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.responded:
            await interaction.response.send_message("已响应", ephemeral=True)
            return
        self.responded = True
        self.tmux.send_confirm(self.project, "y")
        await interaction.response.edit_message(
            content=f"✅ 已确认 (by {interaction.user.display_name})", view=None
        )

    @discord.ui.button(label="No (N)", style=discord.ButtonStyle.danger)
    async def btn_no(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.responded:
            await interaction.response.send_message("已响应", ephemeral=True)
            return
        self.responded = True
        self.tmux.send_confirm(self.project, "n")
        await interaction.response.edit_message(
            content=f"❌ 已拒绝 (by {interaction.user.display_name})", view=None
        )

    @discord.ui.button(label="Abort (Ctrl+C)", style=discord.ButtonStyle.secondary)
    async def btn_abort(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.responded:
            await interaction.response.send_message("已响应", ephemeral=True)
            return
        self.responded = True
        self.tmux.send_ctrl_c(self.project)
        await interaction.response.edit_message(
            content=f"🛑 已中断 (by {interaction.user.display_name})", view=None
        )


# ------------------------------------------------------------------
# 危险命令确认 View
# ------------------------------------------------------------------


class DangerConfirmView(discord.ui.View):
    """危险命令的二次确认视图。"""

    def __init__(
        self,
        tmux: TmuxManager,
        project: str,
        message_text: str,
        timeout: float = 30,
    ):
        super().__init__(timeout=timeout)
        self.tmux = tmux
        self.project = project
        self.message_text = message_text
        self.responded = False

    @discord.ui.button(label="确认执行", style=discord.ButtonStyle.danger)
    async def btn_confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.responded:
            return
        self.responded = True
        self.tmux.send_keys(self.project, self.message_text)
        await interaction.response.edit_message(
            content=f"⚠️ 已发送危险命令 (by {interaction.user.display_name})", view=None
        )

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def btn_cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.responded:
            return
        self.responded = True
        await interaction.response.edit_message(content="🚫 已取消", view=None)




class MenuSelectView(discord.ui.View):
    """Claude Code 选项菜单的 Discord Select 视图。"""

    def __init__(self, tmux: TmuxManager, project: str, options: list[dict], timeout: float = 60):
        super().__init__(timeout=timeout)
        self.tmux = tmux
        self.project = project
        self.responded = False

        # 动态创建按钮（最多5个，Discord限制）
        for opt in options[:5]:
            btn = discord.ui.Button(
                label=f"{opt['num']}. {opt['label'][:40]}",
                style=discord.ButtonStyle.primary if opt['num'] == '1' else discord.ButtonStyle.secondary,
                custom_id=f"menu_{opt['num']}",
            )
            btn.callback = self._make_callback(opt['num'])
            self.add_item(btn)

    def _make_callback(self, num: str):
        async def callback(interaction: discord.Interaction):
            if self.responded:
                await interaction.response.send_message("已选择", ephemeral=True)
                return
            self.responded = True
            # 发送对应数字选择到 tmux
            for _ in range(int(num) - 1):
                self.tmux.send_keys(self.project, "Down", enter=False)
                import asyncio
                await asyncio.sleep(0.1)
            self.tmux.send_keys(self.project, "", enter=True)  # Enter to confirm
            await interaction.response.edit_message(
                content=f"✅ 已选择: **{num}** (by {interaction.user.display_name})",
                view=None,
            )
        return callback

async def setup_commands(bot: commands.Bot, tmux: TmuxManager, poller: OutputPoller, config: AppConfig) -> ClaudeCog:
    """注册命令 Cog 到 bot。"""
    cog = ClaudeCog(bot, tmux, poller, config)
    await bot.add_cog(cog)
    logger.info("已注册 Claude Code 命令集")
    return cog
