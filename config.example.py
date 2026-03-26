"""
配置文件 — 请在此填写你的配置信息
"""

# ==================== WxPusher 微信推送配置 ====================
# 1. 访问 https://wxpusher.zjiecode.com/ 注册账号
# 2. 创建应用，获取 APP_TOKEN
# 3. 关注公众号绑定，获取你的 UID
WXPUSHER_APP_TOKEN = ''   # 填入你的 AppToken，例如: 'AT_xxxxxxxxxxxxxxxx'
WXPUSHER_UID = ''         # 填入你的 UID，例如: 'UID_xxxxxxxxxxxxxxxx'

# ==================== TikTok Session 配置（重要！） ====================
# TikTok 现在要求 WebSocket 连接必须携带登录 session，否则会拒绝连接
# 获取方法：
#   1. 浏览器登录 tiktok.com
#   2. 打开开发者工具（F12）→ Application → Cookies → https://www.tiktok.com
#   3. 找到名为 "sessionid" 的 Cookie，复制其值粘贴到下方
#   4. 找到名为 "tt-target-idc" 的 Cookie，复制其值粘贴到下方（可选）
TIKTOK_SESSION_ID = ''    # 填入你的 TikTok sessionid cookie 值
TIKTOK_TARGET_IDC = ''    # 填入 tt-target-idc cookie 值（可选，如 "useast5"）

# ==================== 代理配置（可选） ====================
# 如果需要通过代理访问 TikTok，填写代理地址
# HTTP 代理用于 HTTP 请求，SOCKS5 代理用于 WebSocket 连接
PROXY_HTTP = 'socks5://127.0.0.1:7897'   # HTTP 代理（使用 SOCKS5）
PROXY_SOCKS5 = 'socks5://127.0.0.1:7897'  # SOCKS5 代理（用于 WebSocket）

# ==================== 监控配置 ====================
# 默认监控的账号列表（启动时自动开始监控，留空则手动在网页添加）
DEFAULT_ACCOUNTS = [
    # 'your_tiktok_username1',
    # 'your_tiktok_username2',
]

# 服务端口（5000 被 macOS AirPlay 占用，改用 5001）
SERVER_PORT = 5001

# 数据快照保存间隔（秒，每隔多少秒保存一次在线人数等指标）
SNAPSHOT_INTERVAL = 60

# 礼物价值换算（1钻石 = 多少美元）
DIAMOND_TO_USD = 0.005
