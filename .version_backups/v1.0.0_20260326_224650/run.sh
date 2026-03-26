#!/bin/bash
# TikTok 直播监测系统 — 一键启动
# 固定端口 5001，本机/局域网地址固定不变

PYTHON=/opt/homebrew/bin/python3.11
PORT=5001
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 停止已有进程
if lsof -ti:$PORT > /dev/null 2>&1; then
  echo "  ⏹  端口 $PORT 已占用，正在停止旧进程..."
  lsof -ti:$PORT | xargs kill -9 2>/dev/null
  sleep 1
fi

# 获取局域网 IP（固定显示）
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "未知")

echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║    🎬  TikTok 直播效果自动监测系统           ║"
echo "  ╠══════════════════════════════════════════════╣"
echo "  ║  📊  本机看板:  http://localhost:$PORT         ║"
echo "  ║  📡  局域网:    http://$LAN_IP:$PORT    ║"
echo "  ║  (以上地址固定，局域网同一 WiFi 可访问)       ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""

cd "$SCRIPT_DIR"
$PYTHON app.py
