#!/usr/bin/env python3.11
"""
TikTok 直播监测系统 — 一键启动脚本
"""
import sys
import os

# 确保当前工作目录是项目根目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.database import init_db

def check_dependencies():
    """检查依赖是否安装"""
    missing = []
    for pkg in ['TikTokLive', 'flask', 'flask_socketio', 'openpyxl', 'requests']:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"❌ 缺少依赖: {', '.join(missing)}")
        print("请运行: pip3 install " + ' '.join(missing))
        sys.exit(1)

def main():
    print("=" * 55)
    print("🎬  TikTok 直播效果自动监测系统")
    print("=" * 55)

    check_dependencies()
    init_db()

    # 读取配置
    try:
        from config import DEFAULT_ACCOUNTS, SERVER_PORT
    except ImportError:
        DEFAULT_ACCOUNTS = []
        SERVER_PORT = 5000

    # 启动 Web 服务
    from app import app, socketio

    # 如果配置了默认账号，延迟启动监控
    if DEFAULT_ACCOUNTS:
        import threading
        def auto_start():
            import time
            time.sleep(2)  # 等待服务器启动
            from src.monitor import start_monitor
            for username in DEFAULT_ACCOUNTS:
                if username.strip():
                    start_monitor(username.strip(), socketio=socketio)
                    print(f"🚀 自动启动监控: @{username}")
        threading.Thread(target=auto_start, daemon=True).start()

    print(f"\n📊 看板地址: http://localhost:{SERVER_PORT}")
    print(f"📋 历史记录: http://localhost:{SERVER_PORT}/history")
    print("\n💡 使用说明:")
    print("  1. 打开浏览器访问上方地址")
    print("  2. 输入 TikTok 用户名，点击「开始监控」")
    print("  3. 直播结束后自动生成 Excel 报告（保存在 reports/ 目录）")
    print("  4. 如已配置微信推送，报告摘要将自动发送到微信")
    print("\n⚠️  按 Ctrl+C 停止服务\n")

    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, debug=False, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    main()
