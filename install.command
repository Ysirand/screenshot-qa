#!/bin/bash
# 安装依赖(macOS:双击本文件即可,或在终端里 bash install.command)
cd "$(dirname "$0")" || exit 1
echo "----------------------------------------"
echo " 截图自动搜题 - 安装依赖"
echo "----------------------------------------"

if ! command -v python3 >/dev/null 2>&1; then
    echo "没找到 python3。请先安装 Python 3.9 以上版本:"
    echo "  官网下载 https://www.python.org/downloads/macos/"
    echo "  或 brew install python"
    read -n 1 -s -r -p "按任意键关闭..."
    exit 1
fi

python3 -m venv .venv || exit 1
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt || {
    echo "依赖安装失败,请检查网络后重试。"
    read -n 1 -s -r -p "按任意键关闭..."
    exit 1
}

echo ""
echo "安装完成!以后双击 run.command 就能启动。"
echo ""
echo "首次启动会被系统问两个权限,都要允许:"
echo "  1. 屏幕录制(不授权的话截出来只有壁纸)"
echo "  2. 辅助功能(不授权的话全局快捷键不生效)"
echo ""
read -n 1 -s -r -p "按任意键关闭..."