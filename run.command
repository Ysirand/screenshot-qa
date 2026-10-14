#!/bin/bash
# 启动(macOS:双击本文件)
cd "$(dirname "$0")" || exit 1
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="python3"
    echo "提示:还没装依赖的话,先双击 install.command"
fi
nohup "$PY" main.py >/dev/null 2>&1 &
echo "已启动。看桌面右侧的悬浮按钮(或右下角菜单栏图标)。"
sleep 1