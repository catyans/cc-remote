# CC-Remote

通过 Discord Bot 远程控制 Claude Code CLI。

## 架构

```
Discord Bot ↔ tmux session（运行 claude CLI）↔ output poller
```

- 用户在 Discord 发送消息 → Bot 通过 `tmux send-keys` 转发给 claude CLI
- `tmux capture-pane` 轮询输出 → 增量发送回 Discord
- 自动处理 ANSI 清理、消息分段、确认按钮

## 前置条件

- Python 3.11+
- tmux (`brew install tmux` / `apt install tmux`)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 已安装并配置
- Discord Bot Token（见下方配置）

## 安装

```bash
cd cc-remote
pip install -r requirements.txt
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入 Discord Bot Token
```

## 创建 Discord Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 创建 Application → Bot
3. 开启 **Message Content Intent**
4. 生成邀请链接时勾选 `bot` + `applications.commands` 权限
5. 将 Bot Token 填入 `config.yaml`

## 启动

```bash
python run.py
# 或 debug 模式
python run.py --debug
```

## Discord 命令

| 命令 | 说明 |
|------|------|
| `/start [project]` | 启动 Claude Code 会话 |
| `/stop [project]` | 关闭会话 |
| `/status [project]` | 查看当前状态 |
| `/abort [project]` | 发送 Ctrl+C 中断 |
| `/history [project] [count]` | 最近交互历史 |
| `/cost [project]` | 查看 token 用量 |
| `/cd <path> [project]` | 切换工作目录 |
| `/project [name]` | 切换/查看项目 |

启动后直接在频道发送消息即可与 Claude 交互。

## 功能

- **交互式确认**: 检测 Y/n 提示自动弹出 Discord 按钮
- **工具状态**: 检测 Running tool 显示 embed 状态
- **安全防护**: 危险命令二次确认、白名单、审计日志
- **多项目**: 通过 config.yaml 配置多个项目目录
- **输出优化**: ANSI 清理、spinner 过滤、长消息自动分段

## 配置

详见 `config.yaml.example`，主要配置项：

- `discord.token` - Bot Token
- `discord.allowed_users` - 用户白名单
- `discord.allowed_channels` - 频道白名单
- `tmux.default_cwd` - 默认工作目录
- `security.dangerous_keywords` - 危险关键词列表
- `projects` - 预定义项目及目录

## 项目结构

```
cc-remote/
├── run.py              # 入口
├── config.yaml         # 配置（从 example 复制）
├── requirements.txt
├── src/
│   ├── bot.py          # Discord Bot 核心
│   ├── tmux_manager.py # tmux 会话管理
│   ├── poller.py       # 输出轮询
│   ├── formatter.py    # 输出格式化
│   ├── commands.py     # 斜杠命令
│   └── config.py       # 配置加载
└── logs/               # 运行日志
```
