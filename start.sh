#!/usr/bin/env bash
# CC-Remote 启动脚本
set -e

cd "$(dirname "$0")"

echo "🚀 CC-Remote 启动中..."

# 杀掉旧进程（如果有）
if [ -f cc-remote.pid ]; then
    OLD_PID=$(cat cc-remote.pid)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  终止旧进程: PID $OLD_PID"
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
    rm -f cc-remote.pid
fi

# 启动
python3 run.py "$@" &
NEW_PID=$!

sleep 2

# 检查是否启动成功
if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "✅ CC-Remote 已启动 (PID: $NEW_PID)"
    echo "📄 日志: logs/cc-remote.log"
    echo "🛑 停止: ./stop.sh"
else
    echo "❌ 启动失败，请查看日志: logs/cc-remote.log"
    exit 1
fi
