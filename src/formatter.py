"""
输出格式化模块
处理 ANSI 转义码清理、spinner 过滤、代码块检测、消息分段。
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

# Spinner / 进度条模式
_SPINNER_PATTERNS = [
    re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷|/\\-][\s].*$", re.MULTILINE),
    re.compile(r"^\s*[\-\\|/]\s*$", re.MULTILINE),
    re.compile(r"\r[^\n]"),  # 回车覆写（进度条常用）
]

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


def clean_ansi(text: str) -> str:
    """移除 ANSI 转义码和控制字符。"""
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    return text


def filter_spinner(text: str) -> str:
    """过滤 spinner 和进度条行。"""
    lines = text.split("\n")
    filtered = []
    for line in lines:
        # 跳过纯 spinner 行
        stripped = line.strip()
        if stripped and len(stripped) <= 3 and any(c in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷|/\\-" for c in stripped):
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
    text = filter_spinner(text)
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
