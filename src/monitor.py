"""
TikTok 直播监控核心模块
负责连接 TikTok 直播间，采集实时数据
"""
import asyncio
import logging
from datetime import datetime
from TikTokLive import TikTokLiveClient
from TikTokLive.events import (
    CommentEvent, GiftEvent, LikeEvent,
    FollowEvent, RoomUserSeqEvent,
    ConnectEvent, DisconnectEvent, LiveEndEvent
)
from src.database import (
    create_session, end_session, find_recent_session, reactivate_session,
    add_comment, add_gift, add_follow, update_viewers,
    get_session_summary, add_speech
)
from src.reporter import generate_excel_report
from src.notifier import send_wechat_notify
from src.speech import SpeechMonitor, get_stream_url_from_client
from src.lang_detect import detect_language, detect_speech_language, comment_lang_stats, speech_lang_stats
from src.translator import translate_to_zh

logger = logging.getLogger(__name__)

# 全局状态：存储当前活跃的监控任务
active_monitors = {}  # username -> MonitorTask


class LiveMonitor:
    """单个 TikTok 账号的直播监控器"""

    def __init__(self, username: str, anchor_username: str = None, socketio=None,
                 is_auto: bool = False, group_name: str = 'own', owner_user_id: int = 1):
        """
        :param username: TikTok 用户名（@后面的部分）
        :param anchor_username: 主播的显示名称（用于识别主播话术）
        :param socketio: Flask-SocketIO 实例，用于实时推送到前端
        :param is_auto: 是否为自动监控账号（影响看板展示和采集深度）
        :param group_name: 账号分组 'own'|'rival'|'watch'
        :param owner_user_id: 所属系统用户ID，用于多用户数据隔离
        """
        self.username = username
        self.anchor_username = anchor_username or username
        self.socketio = socketio
        self.is_auto = is_auto          # 自动监控标记
        self.group_name = group_name    # 分组：own/rival/watch
        self.owner_user_id = owner_user_id  # 所属用户ID，用于数据隔离
        self.session_id = None
        self.start_time = None
        self.client = None
        self.running = False
        self.viewer_count = 0
        self.peak_viewers = 0      # 峰值在线人数
        self.total_user = 0        # 累计观看人数（来自平台）
        self.like_count = 0
        self.comment_count = 0
        self.new_followers = 0     # 新增关注数
        self.speech_monitor: SpeechMonitor = None  # 语音监控实例

    def _emit(self, event_name, data):
        """向前端推送实时数据"""
        if self.socketio:
            data['username'] = self.username
            self.socketio.emit(event_name, data)

    async def start(self):
        """启动监控（带自动等待直播开始功能）"""
        self.running = True
        logger.info(f"🔍 检测 @{self.username} 直播状态...")

        # 先检测是否在播，若未开播则轮询等待（最多等 2 小时）
        check_client = TikTokLiveClient(unique_id=self.username)
        wait_secs = 0
        max_wait = 7200  # 2小时
        poll_interval = 60  # 每60秒检查一次

        while self.running:
            try:
                is_live = await check_client.is_live()
            except Exception as e:
                # 用户不存在
                errmsg = str(e)
                if 'NotFound' in type(e).__name__ or 'not found' in errmsg.lower():
                    logger.error(f"❌ @{self.username} 用户不存在")
                    self._emit('monitor_error', {'msg': f'用户 @{self.username} 不存在，请检查用户名'})
                    active_monitors.pop(self.username, None)
                    self.running = False
                    return
                is_live = False

            if is_live:
                break

            if wait_secs == 0:
                logger.info(f"⏳ @{self.username} 当前未开播，等待开播中...")
                # 自动监控静默等待，不在看板创建卡片（is_auto=True 时前端忽略此事件）
                self._emit('waiting_live', {
                    'msg': f'@{self.username} 未开播，等待中...',
                    'is_auto': self.is_auto,
                    'group_name': self.group_name,
                })

            if wait_secs >= max_wait:
                logger.warning(f"⏰ @{self.username} 等待超时，停止监控")
                self._emit('monitor_error', {'msg': f'@{self.username} 长时间未开播，已自动停止监控'})
                active_monitors.pop(self.username, None)
                self.running = False
                return

            await asyncio.sleep(poll_interval)
            wait_secs += poll_interval

        if not self.running:
            return

        # ── watch（关注）账号：仅开播检测 + 微信通知，不采集数据 ──
        if self.group_name == 'watch':
            logger.info(f"👁️ @{self.username}（关注账号）开播，仅推送通知，不采集数据")
            try:
                from src.notifier import send_live_start_notify
                send_live_start_notify(self.username, group_name='watch')
            except Exception as _ne:
                logger.warning(f"[{self.username}] 开播通知发送失败: {_ne}")

            # 创建一个轻量 session 记录（用于开播检测），然后持续轮询直播状态
            merged = False
            recent = find_recent_session(self.username, minutes=15, owner_user_id=self.owner_user_id)
            if recent:
                self.session_id = recent['id']
                self.start_time = recent.get('start_time') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                reactivate_session(self.session_id)
                merged = True
                logger.info(f"🔄 @{self.username}（关注）15分钟内重连，合并至 session#{self.session_id}")
            else:
                self.session_id = create_session(self.username, owner_user_id=self.owner_user_id)
                self.start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                logger.info(f"👁️ @{self.username}（关注）开播记录已创建，session#{self.session_id}")

            self._emit('live_detected', {
                'msg': f'@{self.username} 开播了（关注账号，仅通知）',
                'session_id': self.session_id,
                'start_time': self.start_time,
                'is_auto': self.is_auto,
                'group_name': self.group_name,
                'merged': merged,
            })

            # 持续轮询直播状态，直到下播
            poll_client = TikTokLiveClient(unique_id=self.username)
            while self.running:
                try:
                    await asyncio.sleep(60)  # 每60秒检查一次
                    still_live = await poll_client.is_live()
                    if not still_live:
                        logger.info(f"👁️ @{self.username}（关注账号）已下播")
                        await self.stop()
                        return
                except Exception:
                    pass  # 检测失败不退出，继续轮询

            # running 被外部设为 False
            await self.stop()
            return

        # ── own / rival 账号：全量数据采集 ──
        # 开播了，建立会话（或合并到15分钟内的上次会话）
        merged = False
        recent = find_recent_session(self.username, minutes=15, owner_user_id=self.owner_user_id)
        if recent:
            self.session_id = recent['id']
            self.start_time = recent.get('start_time') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            reactivate_session(self.session_id)
            merged = True
            logger.info(f"🔄 @{self.username} 15分钟内重连，合并至 session#{self.session_id}（原开播: {self.start_time}）")
        else:
            self.session_id = create_session(self.username, owner_user_id=self.owner_user_id)
            self.start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"🚀 @{self.username} 开播了！开始采集，新会话ID: {self.session_id}，所属用户ID: {self.owner_user_id}")
        self._emit('live_detected', {
            'msg': f'@{self.username} 开播了，开始监控！',
            'session_id': self.session_id,
            'start_time': self.start_time,
            'is_auto': self.is_auto,
            'group_name': self.group_name,
            'merged': merged,  # 前端可用此标记提示"重连中"
        })

        # 自动监控账号开播 → 推微信通知
        if self.is_auto:
            try:
                from src.notifier import send_live_start_notify
                send_live_start_notify(self.username, group_name=self.group_name)
            except Exception as _ne:
                logger.warning(f"[{self.username}] 开播通知发送失败: {_ne}")

        try:
            import httpx
            from websockets_proxy import Proxy as WsProxy
            import config as cfg

            # 读取代理配置（HTTP 和 WebSocket 统一走 SOCKS5）
            web_proxy = None
            ws_proxy = None
            socks5_url = getattr(cfg, 'PROXY_SOCKS5', '').strip()
            if socks5_url and self._port_open(7897):
                web_proxy = httpx.Proxy(socks5_url)
                ws_proxy = WsProxy.from_url(socks5_url)
                logger.info(f"[{self.username}] 使用 SOCKS5 代理: {socks5_url}")
            else:
                logger.warning(f"[{self.username}] 代理不可用，直连可能失败")

            self.client = TikTokLiveClient(
                unique_id=self.username,
                web_proxy=web_proxy,
                ws_proxy=ws_proxy
            )

            # 配置 TikTok session（解决 WebSocket 需要登录的问题）
            session_id = getattr(cfg, 'TIKTOK_SESSION_ID', '').strip()
            target_idc = getattr(cfg, 'TIKTOK_TARGET_IDC', '').strip()
            if session_id:
                self.client.web.set_session(session_id=session_id, tt_target_idc=target_idc or None)
                logger.info(f"[{self.username}] 已配置 TikTok session")
            else:
                logger.debug(f"[{self.username}] 未配置 TIKTOK_SESSION_ID，以游客模式连接")

            self._register_events()
            await self.client.connect()  # connect() 会阻塞直到断开
        except Exception as e:
            logger.error(f"❌ 监控 @{self.username} 出错: {e}")
            await self.stop(error=str(e))

    @staticmethod
    def _proxy_available() -> bool:
        """检查本地代理是否可用"""
        return LiveMonitor._port_open(7897)

    @staticmethod
    def _port_open(port: int) -> bool:
        """检查本地端口是否可连接"""
        import socket
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return True
        except OSError:
            return False

    async def stop(self, error=None):
        """停止监控并生成报告"""
        if not self.running:
            return
        self.running = False

        # 停止语音监控
        if self.speech_monitor:
            self.speech_monitor.stop()
            self.speech_monitor = None

        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass

        if self.session_id:
            end_session(self.session_id)
            logger.info(f"⏹️ 停止监控 @{self.username}")

            # 生成报告
            try:
                summary = get_session_summary(self.session_id)
                report_path = generate_excel_report(summary, self.username)
                logger.info(f"📊 报告已生成: {report_path}")

                # 微信推送
                try:
                    send_wechat_notify(summary, self.username, report_path)
                except Exception as notify_err:
                    logger.warning(f"微信推送失败: {notify_err}")

                # 推送结束事件到前端
                self._emit('live_ended', {
                    'session_id': self.session_id,
                    'report_path': report_path,
                    'summary': summary.get('session', {}),
                    'end_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                })
            except Exception as e:
                logger.error(f"生成报告失败: {e}")

        # 从活跃列表移除（统一由此处移除，stop_monitor 不再重复 pop）
        active_monitors.pop(self.username, None)

    def _on_transcript(self, username: str, text: str, timestamp: str, lang_info: dict = None):
        """语音转文字回调：翻译 + 存入话术表 + 推送到前端"""
        if lang_info is None:
            from src.lang_detect import detect_speech_language
            lang_info = detect_speech_language({"text": text, "language": ""})

        # 翻译到中文（非中文才翻译，在后台线程异步执行避免阻塞）
        import threading

        def _do_translate_and_store():
            # 使用局部变量，避免多条话术并发翻译时的竞态
            _text_zh = ''
            lang = lang_info.get('lang', 'other')
            try:
                src = 'ar' if lang.startswith('ar') else lang if lang != 'other' else 'auto'
                result = translate_to_zh(text, source_lang=src)
                # translate_to_zh 成功返回译文，失败返回 None
                _text_zh = result or ''
            except Exception:
                _text_zh = ''

            # 统计话术语言分布
            speech_lang_stats.add(self.username, lang_info)

            # 存入话术独立表
            if self.session_id:
                add_speech(
                    session_id=self.session_id,
                    anchor=self.username,
                    text=text,
                    text_zh=_text_zh,
                    lang=lang_info.get('lang', 'other'),
                    lang_short=lang_info.get('lang_short', '?'),
                    lang_display=lang_info.get('lang_display', '未知'),
                    dialect=lang_info.get('dialect'),
                )
            # 注：话术仅存入 speech_records 表，不再重复写入 comments 表

            # 推送到前端（含翻译）
            self._emit('new_speech', {
                'text': text,
                'text_zh': _text_zh,
                'timestamp': timestamp,
                'lang': lang_info.get('lang', 'other'),
                'lang_short': lang_info.get('lang_short', '?'),
                'lang_display': lang_info.get('lang_display', '未知'),
                'flag': lang_info.get('flag', ''),
                'css_class': lang_info.get('css_class', 'lang-other'),
                'dialect': lang_info.get('dialect'),
            })
            # 话术后立即推送一次方言统计更新（实时刷新话术雷达）
            c_stats = comment_lang_stats.get_stats(self.username)
            s_stats = speech_lang_stats.get_stats(self.username)
            self._emit('lang_stats_update', {
                'comment_stats': c_stats,
                'speech_stats': s_stats,
                'username': self.username
            })

        threading.Thread(target=_do_translate_and_store, daemon=True).start()

    def _register_events(self):
        """注册所有监听事件（own/rival 全量采集，watch 不走此流程）"""
        # own/rival 全量采集，watch 账号在 start() 中已提前返回
        is_rival = False  # 保留变量兼容性，实际不再使用轻量模式

        @self.client.on(ConnectEvent)
        async def on_connect(event: ConnectEvent):
            room_id = str(event.unique_id) if event.unique_id else ''
            logger.info(f"✅ 已连接直播间 @{self.username}  room_id={room_id}")
            # 诊断：打印 client 内部的 room_id 字段
            try:
                internal_room_id = getattr(self.client, '_room_id', None)
                params_room_id = getattr(self.client.web, 'params', {}).get('room_id', None)
                logger.info(f"[{self.username}] 诊断: _room_id={internal_room_id}, params[room_id]={params_room_id}")
            except Exception as _de:
                logger.debug(f"[{self.username}] 诊断信息获取失败: {_de}")
            # 将 room_id 补存到数据库
            if self.session_id and room_id:
                try:
                    from src.database import get_conn
                    conn = get_conn()
                    conn.execute(
                        'UPDATE live_sessions SET room_id=? WHERE id=?',
                        (room_id, self.session_id)
                    )
                    conn.commit()
                    conn.close()
                except Exception as _e:
                    logger.warning(f"[{self.username}] 保存 room_id 失败: {_e}")
            live_url = f'https://www.tiktok.com/@{self.username}/live'
            self._emit('connected', {'room_id': room_id, 'live_url': live_url})

            # 连接成功后，尝试启动语音监控（竞品轻量模式跳过）
            if not is_rival:
                try:
                    stream_url = await get_stream_url_from_client(self.client)
                    if stream_url:
                        self.speech_monitor = SpeechMonitor(
                            username=self.username,
                            stream_url=stream_url,
                            on_transcript=self._on_transcript,
                            socketio=self.socketio
                        )
                        self.speech_monitor.start()
                        logger.info(f"[{self.username}] 语音转文字已启动")
                    else:
                        logger.warning(f"[{self.username}] 无法获取直播流地址，跳过语音监控")
                        self._emit('speech_no_stream', {'msg': '未能获取直播流，关键词将为空'})
                except Exception as e:
                    logger.warning(f"[{self.username}] 语音监控启动失败（不影响其他功能）: {e}")

        @self.client.on(DisconnectEvent)
        async def on_disconnect(event: DisconnectEvent):
            logger.info(f"🔌 断开连接 @{self.username}")
            if self.speech_monitor:
                self.speech_monitor.stop()
                self.speech_monitor = None
            
            # 修复：短暂断开等待重连，不立即结束session
            # 等待45秒看是否会自动重连（基于统计优化：过滤58.5%虚假session）
            logger.info(f"⏳ @{self.username} 连接断开，等待45秒自动重连...")
            await asyncio.sleep(45)
            
            # 45秒后检查是否已经重新连接
            # 通过检查客户端是否还有活跃连接来判断
            # 简化：45秒后如果还在断开状态，就结束session
            # end_session会检查持续时间，如果小于5分钟就不记录end_time
            logger.info(f"⏹️ @{self.username} 断开超过45秒，结束session")
            await self.stop()

        @self.client.on(LiveEndEvent)
        async def on_live_end(event: LiveEndEvent):
            logger.info(f"📴 直播结束 @{self.username}")
            await self.stop()

        @self.client.on(CommentEvent)
        async def on_comment(event: CommentEvent):
            # 所有分组统一进行评论采集
            # v6.x API: user_info 字段
            user = getattr(event, 'user_info', None)
            username = getattr(user, 'nickname', None) or getattr(user, 'display_id', 'unknown')
            user_id = str(getattr(user, 'uid', ''))
            content = getattr(event, 'content', '')

            is_anchor = 0
            # 检测评论语言
            lang_info = detect_language(content)
            comment_lang_stats.add(self.username, lang_info)
            lang = lang_info.get('lang', 'other')
            logger.debug(f"💬 [{self.username}] [{lang_info['lang_short']}] {username}: {content}")

            # 先存库（不含翻译，翻译异步完成后再更新）
            comment_id = add_comment(
                self.session_id, username, user_id, content, is_anchor,
                text_zh='', lang=lang, lang_short=lang_info.get('lang_short', '')
            )
            self.comment_count += 1

            # 非中文评论异步翻译
            def _emit_comment(text_zh=''):
                self._emit('new_comment', {
                    'username': username,
                    'content': content,
                    'text_zh': text_zh,
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                    'is_anchor': is_anchor,
                    'lang': lang_info.get('lang', 'other'),
                    'lang_short': lang_info.get('lang_short', '?'),
                    'lang_display': lang_info.get('lang_display', '未知'),
                    'flag': lang_info.get('flag', ''),
                    'css_class': lang_info.get('css_class', 'lang-other'),
                    'dialect': lang_info.get('dialect'),
                    'dialect_country': lang_info.get('dialect_country', ''),
                })

            if lang and lang != 'zh' and lang != 'other':
                import threading
                def _translate_and_emit(_comment_id=comment_id):
                    try:
                        src = 'ar' if lang.startswith('ar') else lang
                        zh = translate_to_zh(content, source_lang=src)
                        zh_text = zh or ''
                        # 更新数据库里该条评论的翻译
                        if zh_text and _comment_id:
                            try:
                                from src.database import get_conn
                                conn = get_conn()
                                conn.execute('UPDATE comments SET text_zh=? WHERE id=?', (zh_text, _comment_id))
                                conn.commit()
                                conn.close()
                            except Exception:
                                pass
                        # translate_to_zh 成功返回译文(str)，失败返回 None
                        _emit_comment(zh_text)
                    except Exception:
                        _emit_comment('')
                threading.Thread(target=_translate_and_emit, daemon=True).start()
            else:
                _emit_comment('')

        @self.client.on(GiftEvent)
        async def on_gift(event: GiftEvent):
            # 所有分组统一进行礼物采集
            # v6.x: from_user, m_gift
            user = getattr(event, 'from_user', None)
            username = getattr(user, 'nickname', None) or getattr(user, 'display_id', 'unknown')
            user_id = str(getattr(user, 'uid', ''))
            gift = getattr(event, 'm_gift', None)
            gift_name = getattr(gift, 'name', '未知礼物') if gift else '未知礼物'
            gift_count = getattr(event, 'repeat_count', 1) or 1
            gift_diamonds = getattr(gift, 'diamond_count', 0) if gift else 0
            gift_value = gift_diamonds * gift_count * 0.005

            add_gift(self.session_id, username, user_id, gift_name, gift_count, gift_value)

            logger.info(f"🎁 [{self.username}] {username} 送出 {gift_name} x{gift_count}")
            self._emit('new_gift', {
                'username': username,
                'gift_name': gift_name,
                'gift_count': gift_count,
                'gift_value': round(gift_value, 2),
                'timestamp': datetime.now().strftime('%H:%M:%S')
            })

        @self.client.on(FollowEvent)
        async def on_follow(event: FollowEvent):
            # 所有分组统一进行关注采集
            # v6.x: user 字段
            user = getattr(event, 'user', None)
            username = getattr(user, 'nickname', None) or getattr(user, 'display_id', 'unknown')
            user_id = str(getattr(user, 'uid', ''))

            add_follow(self.session_id, username, user_id)
            
            # 更新新增关注计数
            self.new_followers += 1

            logger.info(f"👤 [{self.username}] 新关注: {username} (累计: {self.new_followers})")
            self._emit('new_follow', {
                'username': username,
                'timestamp': datetime.now().strftime('%H:%M:%S')
            })
            # 更新viewer数据，包含新增关注数
            self._emit('viewer_update', {
                'username': self.username,
                'viewer_count': self.viewer_count,
                'peak_viewers': self.peak_viewers,
                'total_user': 0,
                'like_count': self.like_count,
                'comment_count': self.comment_count,
                'new_followers': self.new_followers,
                'timestamp': datetime.now().strftime('%H:%M:%S')
            })

        @self.client.on(RoomUserSeqEvent)
        async def on_viewer_count(event: RoomUserSeqEvent):
            # m_total = 当前实时在线人数，total_user = 累计观看人数（平台提供）
            self.viewer_count = getattr(event, 'm_total', 0) or 0
            total_user = getattr(event, 'total_user', 0) or 0
            if total_user:
                self.total_user = total_user
            
            # 更新峰值在线
            if self.viewer_count > self.peak_viewers:
                self.peak_viewers = self.viewer_count
            
            update_viewers(self.session_id, self.viewer_count, self.like_count, self.comment_count, 
                          total_user=total_user, peak_viewers=self.peak_viewers)

            self._emit('viewer_update', {
                'viewer_count': self.viewer_count,      # 实时在线
                'peak_viewers': self.peak_viewers,      # 峰值在线
                'total_user': total_user,                # 累计观看
                'like_count': self.like_count,
                'comment_count': self.comment_count,
                'new_followers': self.new_followers,    # 新增关注
                'timestamp': datetime.now().strftime('%H:%M:%S')
            })

            # 推送最新语言分布统计（评论+话术分开）
            c_stats = comment_lang_stats.get_stats(self.username)
            s_stats = speech_lang_stats.get_stats(self.username)
            if c_stats or s_stats:
                self._emit('lang_stats_update', {
                    'comment_stats': c_stats,
                    'speech_stats': s_stats,
                    'username': self.username
                })

        @self.client.on(LikeEvent)
        async def on_like(event: LikeEvent):
            # v6.x: total 是累计点赞数
            self.like_count = getattr(event, 'total', self.like_count + 1)
            self._emit('like_update', {
                'like_count': self.like_count,
                'timestamp': datetime.now().strftime('%H:%M:%S')
            })


def start_monitor(username: str, socketio=None, is_auto: bool = False, group_name: str = 'own', owner_user_id: int = 1):
    """在新线程中启动对某账号的监控
    :param is_auto: 是否为自动监控账号
    :param group_name: 账号分组 'own'|'rival'|'watch'
    :param owner_user_id: 所属系统用户ID，用于多用户数据隔离
    """
    import threading

    if username in active_monitors:
        logger.warning(f"@{username} 已在监控中，跳过")
        return False

    monitor = LiveMonitor(username, socketio=socketio, is_auto=is_auto, group_name=group_name, owner_user_id=owner_user_id)
    active_monitors[username] = monitor

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(monitor.start())
        finally:
            loop.close()

    t = threading.Thread(target=run, daemon=True, name=f"monitor-{username}")
    t.start()
    logger.info(f"🎬 监控线程已启动: @{username}")
    return True


def stop_monitor(username: str):
    """停止对某账号的监控"""
    monitor = active_monitors.get(username)
    if monitor:
        # 直接设置 running=False，让监控循环自然退出
        monitor.running = False
        # 如果有 client 连接，尝试在后台线程断开
        import threading
        def _stop_async():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(monitor.stop())
            except Exception:
                pass
            finally:
                loop.close()
        t = threading.Thread(target=_stop_async, daemon=True)
        t.start()
        logger.info(f"⏹️ 停止监控指令已发送: @{username}")
        return True
    return False


def get_active_usernames():
    """获取当前正在监控的账号列表（包含等待开播的）"""
    return list(active_monitors.keys())


def get_live_usernames():
    """获取当前真正在直播的账号列表（已有 session_id，排除等待开播状态）"""
    return [u for u, m in list(active_monitors.items()) if m.session_id is not None]


def get_monitors_snapshot():
    """获取所有活跃监控的当前实时状态快照（用于前端重连恢复）"""
    result = []
    for username, monitor in list(active_monitors.items()):
        result.append({
            'username': username,
            'session_id': monitor.session_id,
            'start_time': monitor.start_time,
            'viewer_count': monitor.viewer_count,
            'peak_viewers': monitor.peak_viewers,
            'total_user': monitor.total_user or 0,
            'like_count': monitor.like_count,
            'comment_count': monitor.comment_count,
            'new_followers': monitor.new_followers or 0,
            'is_live': monitor.session_id is not None,  # session_id 存在说明已开播
            'is_auto': monitor.is_auto,     # 是否为自动监控账号
            'group_name': monitor.group_name,  # 分组标识
        })
    return result
