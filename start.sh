#!/bin/bash
# Hermes Profile Manager - 启动脚本 (git-bash)
cd "$(dirname "$0")"

echo ""
echo "  Hermes Profile Manager - Setup"
echo "  ══════════════════════════════"
echo ""

# 检查 venv
if [ ! -f ".venv/Scripts/python.exe" ]; then
    echo "  [1/3] Creating virtual environment..."
    python -m venv .venv
    if [ $? -ne 0 ]; then
        echo "  ERROR: Failed to create venv. Make sure Python is installed."
        exit 1
    fi
else
    echo "  [1/3] Virtual environment found."
fi

echo "  [2/3] Installing dependencies..."
.venv/Scripts/pip.exe install -q -r requirements.txt 2>/dev/null

echo "  [3/3] Starting server..."
echo ""
echo "  ┌──────────────────────────────────────┐"
echo "  │  http://127.0.0.1:18520              │"
echo "  │  Press Ctrl+C to stop                 │"
echo "  └──────────────────────────────────────┘"
echo ""

.venv/Scripts/python.exe app.py
