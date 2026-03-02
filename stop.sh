#!/usr/bin/env bash
# CC-Remote 停止脚本
set -e

cd "$(dirname "$0")"

echo "🛑 CC-Remote 停止中..."

# 杀掉 run.py 进程
if [ -f cc-remote.pid ]; then
    PID=$(cat cc-remote.pid)
    if kill -0 "$PID" 2>/dev/null; then
        echo "   终止进程: PID $PID"
        kill "$PID" 2>/dev/null || true
        sleep 1
        # 确保进程已退出
        if kill -0 "$PID" 2>/dev/null; then
            echo "   强制终止: PID $PID"
            kill -9 "$PID" 2>/dev/null || true
        fi
    fi
    rm -f cc-remote.pid
fi

# 杀掉所有 cc-remote tmux sessions
SESSIONS=$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^cc-remote' || true)
if [ -n "$SESSIONS" ]; then
    echo "   清理 tmux sessions:"
    while IFS= read -r sess; do
        echo "     - $sess"
        tmux kill-session -t "$sess" 2>/dev/null || true
    done <<< "$SESSIONS"
fi

# 清理 PID 文件
rm -f cc-remote.pid

echo "✅ CC-Remote 已停止"
