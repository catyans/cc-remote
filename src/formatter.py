"""
输出格式化模块
处理 ANSI 转义码清理、TUI 装饰过滤、spinner 过滤、菜单过滤、代码块检测、消息分段。
"""

import re
import logging

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# ANSI 转义码清理
# ------------------------------------------------------------------

# 匹配 ANSI 转义序列（CSI 序列、OSC 序列、简单转义）
_ANSI_RE = re.compile(
    r"""
    \x1b          # ESC
    (?:
        \[\?[0-9;]*[hl]      # DEC Private Mode Set/Reset: ESC [ ? ... h/l
      | \[[0-9;]*[A-Za-z]    # CSI 序列: ESC [ ... 字母
      | \][^\x07]*\x07       # OSC 序列: ESC ] ... BEL
      | \][^\x1b]*\x1b\\    # OSC 序列: ESC ] ... ST
      | [()][AB012]          # 字符集选择
      | [>=<]                # 键盘模式
      | \[[0-9;]*[ -/]*[@-~] # 更宽泛的 CSI
    )
    """,
    re.VERBOSE,
)

# 其他控制字符（保留换行和 tab）
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ------------------------------------------------------------------
# TUI 装饰过滤
# ------------------------------------------------------------------

# 框线字符
_BOX_CHARS = set("╭╮╰╯│─┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬╌▐▛▜▌▝▘█▀▄░▒▓")

# TUI 欢迎界面 / 状态行关键词
_TUI_KEYWORDS = [
    "ctrl+g",
    "Enter to select",
    "Tab/Arrow",
    "Enter to confirm",
    "Esc to cancel",
    "Tab to amend",
    "ctrl+e to explain",
    "Checking for updates",
    "Press Ctrl-C",
    "code.claude.com",
    "Bypass Permissions",
    "sandboxed container",
    "accept all responsibility",
    "Security guide",
    "Quick safety check",

    "Welcome back",
    "Welcome to",
    "Claude Code",
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
    "claude-4",
    "claude-3",
    "Model:",
    "model:",
    "/help",
    "Tip:",
    "tip:",
    "ESC to",
    "Esc to",
    "shortcuts",
    "ctrl+",
    "Ctrl+",
    "tokens remaining",
    "context window",
    "session expired",
    "Auto-compact",
    "compact conversation",
    "cost:",
    "Cost:",
    "duration:",
    "Duration:",
]

# 分隔线模式（至少 4 个连续的 ─ 或 - 或 = 或 ━）
_SEPARATOR_RE = re.compile(r"^[\s]*[─━\-=]{4,}[\s]*$")

# 进度条模式（yfinance 等: [******  13%], 2 of 15 completed, etc.）
_PROGRESS_BAR_RE = re.compile(
    r"\[\s*[*#=>\-\\|/█▓▒░]+\s*\d+%\s*\]"
    r"|\d+%\s*\|"
    r"|\d+\s+of\s+\d+\s+completed"
    r"|downloading.*\d+%",
    re.IGNORECASE,
)

# Claude Code 思考/等待状态行（· Thinking… / · Churning… (11m 13s)）
_THINKING_STATUS_RE = re.compile(
    r"^[\s·•]*(?:Thinking|Churning|Waiting|Processing|Generating|Reading|Analyzing|Searching|Compiling|Running)"
    r"[…\.]{0,3}\s*(?:\(.*\))?\s*$",
    re.IGNORECASE,
)

# "Task Output" 重复状态行
_TASK_OUTPUT_RE = re.compile(
    r"^[\s]*(?:Task Output|Waiting for task|Queued|Pending)",
    re.IGNORECASE,
)

# ------------------------------------------------------------------
# Spinner 过滤
# ------------------------------------------------------------------

# Claude Code spinner 装饰字符
_SPINNER_CHARS = set("✶✷✸✹✺✻✼✽✾✿❀❁❂❃✢✣✤✥✦✧✳⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷")

# Spinner 行模式：装饰字符开头 + 文本 + 可选的 .../… 结尾
_SPINNER_LINE_RE = re.compile(
    r"^[\s]*[✶✷✸✹✺✻✼✽✾✿❀❁❂❃✢✣✤✥✦✧✳⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷][\s].*$"
)

# ------------------------------------------------------------------
# 菜单过滤
# ------------------------------------------------------------------

# 选择菜单行：❯ 开头或 数字. 开头的选项
_MENU_LINE_RE = re.compile(r"^[\s]*(?:❯|›|>)\s+\d+\.\s+")
_MENU_OPTION_RE = re.compile(r"^[\s]*\d+\.\s+(?:Yes|No|Always|Never|Skip|Allow|Deny|Accept|Reject|Cancel)")

# bypass 确认提示
_BYPASS_RE = re.compile(r"(?:I understand|I accept|bypass|skip permissions|dangerously)", re.IGNORECASE)

# ------------------------------------------------------------------
# 其他检测模式
# ------------------------------------------------------------------

# Y/n 确认提示
_CONFIRM_RE = re.compile(
    r"(?:^|\n).*(?:(?:Y/n|y/N|[Yy]es/?[Nn]o)[\s)?]*$|\b(?:confirm|proceed|allow|approve|accept)\b.*[?？])",
    re.IGNORECASE | re.MULTILINE,
)

# "Running tool" 状态指示
_TOOL_RE = re.compile(
    r"(?:Running|Executing|Using)\s+(?:tool\s+)?['\"]?(\w+)['\"]?",
    re.IGNORECASE,
)

# diff 块检测
_DIFF_RE = re.compile(
    r"^[+-]{3}\s+[ab]/|^@@\s+[-+]\d+",
    re.MULTILINE,
)

# 代码块（被 ``` 包裹的内容）
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")


# ------------------------------------------------------------------
# 过滤函数
# ------------------------------------------------------------------


def clean_ansi(text: str) -> str:
    """移除 ANSI 转义码和控制字符。"""
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    return text


# Extra patterns
_ESC_HINT_RE = re.compile(r"^\s*(esc|Esc)\s+(to\s+)")

def is_tui_line(line: str) -> bool:
    """判断是否为 TUI 装饰行（框线、欢迎界面、快捷键提示、分隔线）。"""
    stripped = line.strip()
    if not stripped:
        return False

    # 纯框线字符行（可能混有空格）
    if all(c in _BOX_CHARS or c.isspace() for c in stripped):
        return True

    # 以框线字符开头或结尾的装饰行
    if stripped[0] in _BOX_CHARS or stripped[-1] in _BOX_CHARS:
        return True

    # 分隔线
    if _SEPARATOR_RE.match(line):
        return True

    # ❯ 提示行
    if stripped.startswith(chr(10095)) or stripped.startswith(chr(8250)):
        return True

    # TUI 关键词
    for kw in _TUI_KEYWORDS:
        if kw in line:
            return True

    if _ESC_HINT_RE.match(stripped):
        return True
    return False


def is_spinner_line(line: str) -> bool:
    """判断是否为 spinner 动画行。"""
    stripped = line.strip()
    if not stripped:
        return False

    # 以 spinner 装饰字符开头
    if stripped[0] in _SPINNER_CHARS:
        return True

    # 纯 spinner 字符（短行）
    if len(stripped) <= 3 and any(c in _SPINNER_CHARS for c in stripped):
        return True

    # 回车覆写行（进度条）
    if "\r" in line and "\n" not in line:
        return True

    # Claude Code 思考/等待状态行
    if _THINKING_STATUS_RE.match(stripped):
        return True

    # 进度条行
    if _PROGRESS_BAR_RE.search(stripped):
        return True

    # "Task Output" / "Waiting for task" 状态行
    if _TASK_OUTPUT_RE.match(stripped):
        return True

    return False


def is_menu_line(line: str) -> bool:
    """判断是否为选择菜单行（❯ 1. Yes  2. No 等）。"""
    stripped = line.strip()
    if not stripped:
        return False

    # ❯ 开头的选项指示
    if _MENU_LINE_RE.match(line):
        return True

    # 数字选项行（1. Yes, 2. No 等）
    if _MENU_OPTION_RE.match(line):
        return True

    # bypass 确认行
    if _BYPASS_RE.search(line):
        return True

    return False


def filter_output(text: str) -> str:
    """
    集成所有过滤规则，只保留有意义的内容。
    保留：工具调用结果、Claude 的回答文本、代码块、错误信息。
    过滤：TUI 装饰、spinner、菜单、分隔线。
    """
    lines = text.split("\n")
    filtered = []
    in_code_block = False

    for line in lines:
        # 代码块内的内容全部保留
        backtick_count = line.count("```")
        if in_code_block:
            filtered.append(line)
            if backtick_count % 2 == 1:
                in_code_block = False
            continue

        if backtick_count % 2 == 1:
            in_code_block = True
            filtered.append(line)
            continue

        # 过滤 TUI 装饰行
        if is_tui_line(line):
            continue

        # 过滤 spinner 行
        if is_spinner_line(line):
            continue

        # 过滤菜单行
        if is_menu_line(line):
            continue

        filtered.append(line)

    return "\n".join(filtered)


def detect_confirmation(text: str) -> str | None:
    """检测是否包含 Y/n 确认提示，返回匹配的提示文本。"""
    match = _CONFIRM_RE.search(text)
    return match.group(0).strip() if match else None


def detect_tool_running(text: str) -> str | None:
    """检测 'Running tool xxx' 状态，返回工具名。"""
    match = _TOOL_RE.search(text)
    return match.group(1) if match else None


def detect_diff(text: str) -> bool:
    """检测输出中是否包含 diff 内容。"""
    return bool(_DIFF_RE.search(text))


def format_output(text: str) -> str:
    """完整的输出格式化流程。"""
    text = clean_ansi(text)
    text = filter_output(text)
    # 清理连续空行（最多保留 2 个）
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # 清理行尾空白
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def wrap_code_block(text: str, lang: str = "") -> str:
    """将文本包裹在 markdown 代码块中。"""
    # 如果已经包含代码块标记则不重复包裹
    if text.strip().startswith("```"):
        return text
    return f"```{lang}\n{text}\n```"


def split_message(text: str, max_len: int = 1800) -> list[str]:
    """
    将长文本分割为不超过 max_len 的段落。
    尽量在换行处分割，避免拆开代码块。
    """
    if len(text) <= max_len:
        return [text] if text.strip() else []

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # 在 max_len 范围内找最佳分割点
        cut = max_len

        # 优先在空行处分割
        double_nl = remaining.rfind("\n\n", 0, cut)
        if double_nl > max_len // 3:
            cut = double_nl + 1
        else:
            # 其次在单个换行处分割
            single_nl = remaining.rfind("\n", 0, cut)
            if single_nl > max_len // 3:
                cut = single_nl + 1

        chunk = remaining[:cut]
        remaining = remaining[cut:]

        # 检查代码块是否被截断（未闭合的 ```）
        open_blocks = chunk.count("```")
        if open_blocks % 2 == 1:
            # 代码块未闭合，在 chunk 末尾加闭合标记，在 remaining 前加开启标记
            chunk += "\n```"
            remaining = "```\n" + remaining

        if chunk.strip():
            chunks.append(chunk)

    return chunks


def format_diff_embed(diff_text: str) -> str:
    """格式化 diff 输出为 markdown。"""
    return wrap_code_block(diff_text, "diff")


def format_status(project: str, alive: bool, cwd: str) -> str:
    """格式化状态信息。"""
    status_icon = "🟢" if alive else "🔴"
    return (
        f"**Session Status**\n"
        f"{status_icon} Project: `{project}`\n"
        f"📂 CWD: `{cwd}`\n"
        f"{'Running' if alive else 'Stopped'}"
    )


def detect_menu_options(text: str) -> list[dict]:
    """检测 Claude Code 的选项菜单，返回选项列表。
    
    格式如：
    ❯ 1. 最高年化收益率
      2. 最高夏普比率
      3. 最大收益/最大回撤
    """
    pattern = re.compile(r"^\s*[❯>]?\s*(\d+)\.\s+(.+?)(?:\n\s{5,}(.+))?$", re.MULTILINE)
    options = []
    for match in pattern.finditer(text):
        num = match.group(1)
        label = match.group(2).strip()
        desc = match.group(3).strip() if match.group(3) else ""
        options.append({"num": num, "label": label, "desc": desc})
    return options
