"""
微信推送模块（WxPusher）
直播报告完成后自动推送到个人微信
"""
import requests
import logging
import os

logger = logging.getLogger(__name__)

# WxPusher 配置（从环境变量或 config.py 读取）
try:
    from config import WXPUSHER_APP_TOKEN, WXPUSHER_UID
except ImportError:
    WXPUSHER_APP_TOKEN = os.environ.get('WXPUSHER_APP_TOKEN', '')
    WXPUSHER_UID = os.environ.get('WXPUSHER_UID', '')

WXPUSHER_API = 'https://wxpusher.zjiecode.com/api/send/message'


def send_wechat_notify(summary: dict, username: str, report_path: str = None):
    """
    发送直播报告微信通知
    :param summary: 直播数据摘要
    :param username: 主播用户名
    :param report_path: 报告文件路径
    """
    if not WXPUSHER_APP_TOKEN or not WXPUSHER_UID:
        logger.warning("⚠️ WxPusher 未配置，跳过微信推送。请在 config.py 中设置 WXPUSHER_APP_TOKEN 和 WXPUSHER_UID")
        return

    session = summary.get('session', {})

    # 计算直播时长
    duration = '-'
    start = session.get('start_time')
    end = session.get('end_time')
    if start and end:
        from datetime import datetime
        try:
            s = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
            e = datetime.strptime(end, '%Y-%m-%d %H:%M:%S')
            minutes = int((e - s).total_seconds() / 60)
            duration = f'{minutes // 60}小时{minutes % 60}分钟' if minutes >= 60 else f'{minutes}分钟'
        except Exception:
            pass

    # 构造消息内容（HTML格式）
    content = f"""
<h3>🎬 直播监测报告 — @{username}</h3>
<table border="1" cellpadding="5" style="border-collapse:collapse;">
  <tr><td><b>直播时间</b></td><td>{session.get('start_time', '-')}</td></tr>
  <tr><td><b>直播时长</b></td><td>{duration}</td></tr>
  <tr><td><b>峰值在线</b></td><td>{session.get('peak_viewers', 0):,} 人</td></tr>
  <tr><td><b>总观看人次</b></td><td>{session.get('total_viewers', 0):,} 人</td></tr>
  <tr><td><b>总评论数</b></td><td>{session.get('total_comments', 0):,} 条</td></tr>
  <tr><td><b>总点赞数</b></td><td>{session.get('total_likes', 0):,}</td></tr>
  <tr><td><b>新增关注</b></td><td>{session.get('new_followers', 0):,} 人</td></tr>
  <tr><td><b>礼物收入</b></td><td>${session.get('total_gift_value', 0):.2f}</td></tr>
</table>
<p>📊 详细 Excel 报告已保存至本地</p>
"""

    payload = {
        'appToken': WXPUSHER_APP_TOKEN,
        'content': content,
        'summary': f'@{username} 直播结束 | 峰值{session.get("peak_viewers", 0)}人 | 礼物${session.get("total_gift_value", 0):.2f}',
        'contentType': 2,  # 2=HTML
        'uids': [WXPUSHER_UID],
    }

    try:
        resp = requests.post(WXPUSHER_API, json=payload, timeout=10)
        result = resp.json()
        if result.get('success'):
            logger.info(f"✅ 微信推送成功: @{username}")
        else:
            logger.warning(f"⚠️ 微信推送失败: {result.get('msg')}")
    except Exception as e:
        logger.error(f"❌ 微信推送异常: {e}")


def test_notify():
    """测试微信推送是否正常"""
    if not WXPUSHER_APP_TOKEN or not WXPUSHER_UID:
        print("❌ 请先在 config.py 配置 WXPUSHER_APP_TOKEN 和 WXPUSHER_UID")
        return False

    payload = {
        'appToken': WXPUSHER_APP_TOKEN,
        'content': '✅ TikTok 直播监测系统测试推送成功！',
        'summary': '系统测试',
        'contentType': 1,
        'uids': [WXPUSHER_UID],
    }
    try:
        resp = requests.post(WXPUSHER_API, json=payload, timeout=10)
        result = resp.json()
        if result.get('success'):
            print("✅ 微信推送测试成功！请查看微信")
            return True
        else:
            print(f"❌ 推送失败: {result.get('msg')}")
            return False
    except Exception as e:
        print(f"❌ 推送异常: {e}")
        return False
