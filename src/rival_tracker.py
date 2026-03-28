"""
竞品追踪服务
- 每天定时拉取竞品账号的粉丝数快照（爬 TikTok 公开主页，无需登录）
- 每 10 分钟扫描竞品是否开播（记录开播规律）
"""
import logging
import threading
import time
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# ── TikTok 公开主页数据抓取 ──

def fetch_tiktok_profile(username: str) -> dict:
    """
    从 TikTok 公开主页抓取账号信息（不需要登录）
    返回: { follower_count, following_count, video_count, bio, avatar_url, success }
    """
    import httpx
    result = {
        'follower_count': 0,
        'following_count': 0,
        'video_count': 0,
        'bio': '',
        'avatar_url': '',
        'success': False,
        'username': username,
    }
    url = f'https://www.tiktok.com/@{username}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    # 尝试通过代理访问
    proxies = None
    try:
        import config as cfg
        socks5 = getattr(cfg, 'PROXY_SOCKS5', '').strip()
        if socks5:
            proxies = socks5
    except Exception:
        pass

    try:
        if proxies:
            client = httpx.Client(proxy=proxies, timeout=15, headers=headers, follow_redirects=True)
        else:
            client = httpx.Client(timeout=15, headers=headers, follow_redirects=True)

        with client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(f'[rival_tracker] @{username} HTTP {resp.status_code}')
                return result
            html = resp.text

        # 从 HTML 中提取 __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON
        m = re.search(r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            # 降级：直接用正则从 HTML 里找粉丝数
            follower_m = re.search(r'"followerCount":(\d+)', html)
            following_m = re.search(r'"followingCount":(\d+)', html)
            video_m = re.search(r'"videoCount":(\d+)', html)
            bio_m = re.search(r'"signature":"([^"]*)"', html)
            avatar_m = re.search(r'"avatarLarger":"([^"]*)"', html)
            if follower_m:
                result['follower_count'] = int(follower_m.group(1))
                result['following_count'] = int(following_m.group(1)) if following_m else 0
                result['video_count'] = int(video_m.group(1)) if video_m else 0
                result['bio'] = bio_m.group(1) if bio_m else ''
                result['avatar_url'] = (avatar_m.group(1) if avatar_m else '').replace('\\u002F', '/')
                result['success'] = True
            return result

        import json
        data = json.loads(m.group(1))
        # 路径可能是 webapp.user-detail.userInfo.stats
        user_info = None
        try:
            user_info = data['__DEFAULT_SCOPE__']['webapp.user-detail']['userInfo']
        except (KeyError, TypeError):
            pass
        if not user_info:
            # 降级正则
            follower_m = re.search(r'"followerCount":(\d+)', html)
            if follower_m:
                result['follower_count'] = int(follower_m.group(1))
                result['success'] = True
            return result

        stats = user_info.get('stats', {})
        user = user_info.get('user', {})
        result['follower_count'] = stats.get('followerCount', 0)
        result['following_count'] = stats.get('followingCount', 0)
        result['video_count'] = stats.get('videoCount', 0)
        result['bio'] = user.get('signature', '')
        result['avatar_url'] = user.get('avatarLarger', '')
        result['success'] = True
        logger.info(f'[rival_tracker] @{username} 粉丝数: {result["follower_count"]:,}')

    except Exception as e:
        logger.warning(f'[rival_tracker] 抓取 @{username} 失败: {e}')

    return result


# ── 每日粉丝快照任务 ──

def run_daily_snapshot(usernames: list):
    """对指定账号列表执行一次粉丝快照"""
    from src.database import save_follower_snapshot
    success_count = 0
    for username in usernames:
        try:
            profile = fetch_tiktok_profile(username)
            if profile['success']:
                save_follower_snapshot(
                    username=username,
                    follower_count=profile['follower_count'],
                    following_count=profile['following_count'],
                    video_count=profile['video_count'],
                    bio=profile['bio'],
                    avatar_url=profile['avatar_url'],
                )
                success_count += 1
            else:
                logger.warning(f'[rival_tracker] @{username} 粉丝数抓取失败，跳过')
        except Exception as e:
            logger.error(f'[rival_tracker] @{username} 快照失败: {e}')
        time.sleep(2)  # 每个账号间隔2秒，避免被限流
    logger.info(f'[rival_tracker] 粉丝快照完成: {success_count}/{len(usernames)} 个账号成功')
    return success_count


# ── 后台定时服务 ──

_tracker_thread = None
_tracker_running = False


def _tracker_loop():
    """后台定时循环：每天执行一次快照，每10分钟检查是否需要执行"""
    global _tracker_running
    last_snapshot_date = None

    logger.info('[rival_tracker] 后台追踪服务已启动')

    while _tracker_running:
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            current_hour = datetime.now().hour

            # 每天 8:00 执行一次快照
            if today != last_snapshot_date and current_hour >= 8:
                from src.database import get_all_rival_usernames
                usernames = get_all_rival_usernames()
                if usernames:
                    logger.info(f'[rival_tracker] 开始每日粉丝快照，共 {len(usernames)} 个竞品账号')
                    run_daily_snapshot(usernames)
                    last_snapshot_date = today
                else:
                    last_snapshot_date = today  # 没有竞品也标记，避免重复检查

        except Exception as e:
            logger.error(f'[rival_tracker] 定时任务出错: {e}')

        # 每10分钟检查一次
        for _ in range(600):
            if not _tracker_running:
                break
            time.sleep(1)

    logger.info('[rival_tracker] 后台追踪服务已停止')


def start_rival_tracker():
    """启动竞品追踪后台服务"""
    global _tracker_thread, _tracker_running
    if _tracker_running:
        return
    _tracker_running = True
    _tracker_thread = threading.Thread(target=_tracker_loop, daemon=True, name='rival-tracker')
    _tracker_thread.start()
    logger.info('[rival_tracker] 服务线程已启动')


def stop_rival_tracker():
    """停止竞品追踪后台服务"""
    global _tracker_running
    _tracker_running = False


def trigger_snapshot_now(usernames=None):
    """立即触发一次粉丝快照（手动调用或页面刷新时触发）"""
    from src.database import get_all_rival_usernames
    if usernames is None:
        usernames = get_all_rival_usernames()
    if not usernames:
        return {'success': True, 'count': 0, 'msg': '暂无竞品账号'}

    def _run():
        run_daily_snapshot(usernames)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {'success': True, 'count': len(usernames), 'msg': f'已触发 {len(usernames)} 个账号的粉丝快照'}
