"""
数据库模块 - 负责所有数据的存储和查询
"""
import sqlite3
import os
import hashlib
from datetime import datetime, timedelta
from src.timezone_utils import current_beijing_time, to_beijing_time

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'monitor.db')

# ── 关键词翻译进程级缓存（避免重复调用 Google Translate）
_KW_TRANSLATE_CACHE: dict = {}

def _get_kw_cache():
    return _KW_TRANSLATE_CACHE


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _hash_password(password):
    """SHA-256 哈希密码"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def init_db():
    """初始化数据库表结构"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    c = conn.cursor()

    # ── 系统用户表（多租户）──
    c.execute('''
        CREATE TABLE IF NOT EXISTS sys_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            real_name TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_login_at TEXT DEFAULT NULL
        )
    ''')

    # 直播会话表
    c.execute('''
        CREATE TABLE IF NOT EXISTS live_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER DEFAULT 1,
            username TEXT NOT NULL,
            room_id TEXT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            peak_viewers INTEGER DEFAULT 0,
            total_comments INTEGER DEFAULT 0,
            total_likes INTEGER DEFAULT 0,
            total_gifts INTEGER DEFAULT 0,
            total_gift_value REAL DEFAULT 0,
            new_followers INTEGER DEFAULT 0,
            total_viewers INTEGER DEFAULT 0,
            status TEXT DEFAULT 'live'
        )
    ''')

    # 评论/弹幕表
    c.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            user_id TEXT,
            content TEXT NOT NULL,
            text_zh TEXT DEFAULT '',
            lang TEXT DEFAULT '',
            lang_short TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            is_anchor INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES live_sessions(id)
        )
    ''')

    # 礼物记录表
    c.execute('''
        CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            user_id TEXT,
            gift_name TEXT NOT NULL,
            gift_count INTEGER DEFAULT 1,
            gift_value REAL DEFAULT 0,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES live_sessions(id)
        )
    ''')

    # 实时指标快照表（每分钟一条）
    c.execute('''
        CREATE TABLE IF NOT EXISTS metrics_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            viewer_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES live_sessions(id)
        )
    ''')

    # 关注事件表
    c.execute('''
        CREATE TABLE IF NOT EXISTS follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            user_id TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES live_sessions(id)
        )
    ''')

    # 主播话术独立表（含翻译）
    c.execute('''
        CREATE TABLE IF NOT EXISTS speech_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            anchor TEXT NOT NULL,
            text TEXT NOT NULL,
            text_zh TEXT,
            lang TEXT DEFAULT 'other',
            lang_short TEXT DEFAULT '?',
            lang_display TEXT DEFAULT '未知',
            dialect TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES live_sessions(id)
        )
    ''')

    # 账号分组表
    c.execute('''
        CREATE TABLE IF NOT EXISTS account_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER DEFAULT 1,
            username TEXT NOT NULL,
            group_name TEXT DEFAULT 'own',
            display_name TEXT,
            created_at TEXT,
            UNIQUE(owner_user_id, username)
        )
    ''')

    # 自动监控列表表（常态化监控，随时启停）
    c.execute('''
        CREATE TABLE IF NOT EXISTS auto_monitor_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER DEFAULT 1,
            username TEXT NOT NULL,
            display_name TEXT,
            group_name TEXT DEFAULT 'own',
            enabled INTEGER DEFAULT 1,
            note TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(owner_user_id, username)
        )
    ''')

    # 竞品粉丝快照表（每日记录一次，用于计算涨粉趋势）
    c.execute('''
        CREATE TABLE IF NOT EXISTS rival_follower_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            follower_count INTEGER DEFAULT 0,
            following_count INTEGER DEFAULT 0,
            video_count INTEGER DEFAULT 0,
            bio TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            snapshot_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(username, snapshot_date)
        )
    ''')

    # 用户反馈表（需求/BUG 提交）
    c.execute('''
        CREATE TABLE IF NOT EXISTS feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER DEFAULT 1,
            submitter TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'feature',
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL
        )
    ''')

    # ── 用户操作日志表 ──
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 1,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            page TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    ''')

    conn.commit()

    # ── 兼容旧数据库：添加新列（若不存在） ──
    _migrate_columns(conn)

    # ── 确保默认管理员存在 ──
    _ensure_default_admin(conn)

    conn.close()
    print(f"✅ 数据库初始化完成: {DB_PATH}")
    # 服务重启时修复僵尸 live 记录（上次未正常结束的场次）
    _fix_zombie_sessions()


def _migrate_columns(conn):
    """兼容旧数据库：为已有表添加新字段"""
    c = conn.cursor()
    # live_sessions 加 owner_user_id
    try:
        c.execute('ALTER TABLE live_sessions ADD COLUMN owner_user_id INTEGER DEFAULT 1')
        conn.commit()
    except Exception:
        pass  # 列已存在，忽略
    # account_groups 旧表可能没有 owner_user_id / id
    # 检查旧表结构
    c.execute("PRAGMA table_info(account_groups)")
    cols = {row['name'] for row in c.fetchall()}
    if 'owner_user_id' not in cols:
        try:
            c.execute('ALTER TABLE account_groups ADD COLUMN owner_user_id INTEGER DEFAULT 1')
            conn.commit()
        except Exception:
            pass
    if 'id' not in cols:
        # 旧表无自增 id，无法直接 ALTER，但 UNIQUE 约束也会变，这里仅加 owner_user_id 列就够
        pass
    # sys_users 加 real_name 和 status 字段（新增审核机制）
    c.execute("PRAGMA table_info(sys_users)")
    user_cols = {row['name'] for row in c.fetchall()}
    if 'real_name' not in user_cols:
        try:
            c.execute("ALTER TABLE sys_users ADD COLUMN real_name TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
    if 'status' not in user_cols:
        try:
            c.execute("ALTER TABLE sys_users ADD COLUMN status TEXT DEFAULT 'active'")
            conn.commit()
        except Exception:
            pass
    if 'last_login_at' not in user_cols:
        try:
            c.execute("ALTER TABLE sys_users ADD COLUMN last_login_at TEXT DEFAULT NULL")
            conn.commit()
        except Exception:
            pass
    # comments 表加 text_zh / lang / lang_short 字段（旧数据库兼容）
    c.execute("PRAGMA table_info(comments)")
    comment_cols = {row['name'] for row in c.fetchall()}
    for col_name, col_def in [('text_zh', "TEXT DEFAULT ''"), ('lang', "TEXT DEFAULT ''"), ('lang_short', "TEXT DEFAULT ''")]:
        if col_name not in comment_cols:
            try:
                c.execute(f"ALTER TABLE comments ADD COLUMN {col_name} {col_def}")
                conn.commit()
            except Exception:
                pass
    # user_action_logs 加 page 字段（旧数据库兼容）
    c.execute("PRAGMA table_info(user_action_logs)")
    log_cols = {row['name'] for row in c.fetchall()}
    if 'page' not in log_cols:
        try:
            c.execute("ALTER TABLE user_action_logs ADD COLUMN page TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass


def _ensure_default_admin(conn):
    """确保默认管理员账号 (liuhui) 存在"""
    c = conn.cursor()
    c.execute('SELECT id FROM sys_users WHERE is_admin=1 LIMIT 1')
    if not c.fetchone():
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute(
            'INSERT OR IGNORE INTO sys_users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)',
            ('liuhui', _hash_password('admin888'), 1, now)
        )
        conn.commit()
        print("✅ 默认管理员账号已创建: liuhui / admin888（请尽快修改密码）")


def _fix_zombie_sessions():
    """服务启动时修复异常直播记录：
    1. 修复孤儿 session（status=live 但实际未在监控的）
    2. 合并同账号短时间内的重复 session（解决网络波动导致的碎片记录）
    3. 修复时区混用导致的异常时长
    """
    conn = get_conn()
    c = conn.cursor()
    now_bj = current_beijing_time()

    # ── 1. 修复 status=live 且 end_time 为 NULL 的孤儿 session ──
    c.execute(
        "UPDATE live_sessions SET status='ended', end_time=? WHERE status='live' AND end_time IS NULL",
        (now_bj,)
    )
    fixed = c.rowcount
    conn.commit()
    if fixed:
        print(f"🔧 自动修复 {fixed} 条未结束的直播记录（status: live → ended，end_time={now_bj}）")

    # ── 2. 合并同账号短时间内的重复 session ──
    #    查找同账号、start_time 在 30 分钟内的已结束记录，保留最早的一条（有实际数据的），
    #    将其余的关联数据迁移过来后删除
    c.execute("""
        SELECT username, owner_user_id, MIN(start_time) as min_st, MAX(start_time) as max_st, COUNT(*) as cnt
        FROM live_sessions
        WHERE status='ended' AND start_time IS NOT NULL AND end_time IS NOT NULL
        GROUP BY username, owner_user_id
        HAVING cnt > 1 AND CAST((julianday(MAX(start_time)) - julianday(MIN(start_time))) * 1440 AS INTEGER) < 30
    """)
    merge_groups = c.fetchall()
    merged_count = 0
    for group in merge_groups:
        uname, owner_id = group['username'], group['owner_user_id']
        # 获取该组所有 session（按 id 升序）
        c.execute("""
            SELECT id FROM live_sessions
            WHERE username=? AND owner_user_id=? AND status='ended'
              AND start_time IS NOT NULL AND end_time IS NOT NULL
            ORDER BY start_time ASC, id ASC
        """, (uname, owner_id))
        session_ids = [r['id'] for r in c.fetchall()]
        if len(session_ids) < 2:
            continue
        keep_id = session_ids[0]
        merge_ids = session_ids[1:]
        # 汇总指标（取最大值）
        for col in ('peak_viewers', 'total_comments', 'total_likes', 'total_gifts', 'total_gift_value', 'new_followers', 'total_viewers'):
            c.execute(f"SELECT MAX({col}) FROM live_sessions WHERE id IN ({','.join('?'*len(session_ids))})", session_ids)
            max_val = c.fetchone()[0] or 0
            c.execute(f"UPDATE live_sessions SET {col}=? WHERE id=?", (max_val, keep_id))
        # 更新 end_time 为最晚的
        c.execute(f"SELECT MAX(end_time) FROM live_sessions WHERE id IN ({','.join('?'*len(session_ids))})", session_ids)
        max_end = c.fetchone()[0]
        if max_end:
            c.execute("UPDATE live_sessions SET end_time=? WHERE id=?", (max_end, keep_id))
        conn.commit()
        # 迁移关联数据到保留的 session
        for table in ('comments', 'gifts', 'follows', 'speech_records', 'metrics_snapshots'):
            for mid in merge_ids:
                c.execute(f"UPDATE {table} SET session_id=? WHERE session_id=?", (keep_id, mid))
        conn.commit()
        # 删除被合并的 session
        for mid in merge_ids:
            c.execute("DELETE FROM live_sessions WHERE id=?", (mid,))
        conn.commit()
        merged_count += len(merge_ids)
    if merged_count:
        print(f"🔧 合并 {merged_count} 条同账号重复 session 记录")

    # ── 3. 修复时区混用导致的异常时长（end_time - start_time > 8小时的已结束记录）──
    c.execute(
        """SELECT id, start_time, end_time FROM live_sessions
           WHERE status='ended' AND start_time IS NOT NULL AND end_time IS NOT NULL"""
    )
    rows = c.fetchall()
    fixed_tz = 0
    for row in rows:
        sid, st_str, et_str = row
        try:
            from datetime import datetime as _dt
            st = _dt.strptime(st_str, '%Y-%m-%d %H:%M:%S')
            et = _dt.strptime(et_str, '%Y-%m-%d %H:%M:%S')
            duration_h = (et - st).total_seconds() / 3600.0
            # 超过8小时的已结束session判定为时区混用脏数据，将 start_time 修正（+8小时）
            if duration_h > 8.0:
                corrected_st = st + timedelta(hours=8)
                new_duration = (et - corrected_st).total_seconds() / 3600.0
                # 修正后时长仍然合理（<8小时）才执行修正
                if 0 < new_duration < 8.0:
                    c.execute(
                        "UPDATE live_sessions SET start_time=? WHERE id=?",
                        (corrected_st.strftime('%Y-%m-%d %H:%M:%S'), sid)
                    )
                    fixed_tz += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    if fixed_tz:
        print(f"🔧 自动修正 {fixed_tz} 条时区混用导致的异常时长记录（start_time +8小时修正）")


def create_session(username, room_id=None, owner_user_id=1):
    """创建新的直播会话"""
    conn = get_conn()
    c = conn.cursor()
    # 统一使用北京时间，与 end_session 保持一致，避免时区混用导致时长计算错误
    now_bj = current_beijing_time()
    c.execute(
        'INSERT INTO live_sessions (owner_user_id, username, room_id, start_time) VALUES (?, ?, ?, ?)',
        (owner_user_id, username, room_id, now_bj)
    )
    session_id = c.lastrowid
    conn.commit()
    conn.close()
    return session_id


def end_session(session_id):
    """结束直播会话。返回 True 表示成功结束，False 表示被拒绝（<5分钟保护）。"""
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    conn = get_conn()
    c = conn.cursor()
    now_bj = current_beijing_time()  # 使用北京时间，与start_time保持一致
    
    # 获取 session 的开始时间
    c.execute('SELECT start_time, username FROM live_sessions WHERE id=?', (session_id,))
    row = c.fetchone()
    if row:
        start_time, username = row
        if start_time:
            try:
                # 计算持续时间（分钟）
                from datetime import datetime as _dt
                st = _dt.strptime(start_time, '%Y-%m-%d %H:%M:%S')
                et = _dt.strptime(now_bj, '%Y-%m-%d %H:%M:%S')
                duration_minutes = (et - st).total_seconds() / 60.0
                
                # 如果持续时间小于5分钟，认为是短暂断开，不结束session
                # 基于统计优化：58.5%的session<3分钟，大部分是网络波动导致的虚假session
                if duration_minutes < 5.0:
                    _logger.warning(f"❌ session#{session_id} (@{username}) 持续时间仅{duration_minutes:.1f}分钟(<5分钟阈值)，忽略结束请求")
                    # 不更新end_time，保持status为live，等待重连
                    c.execute('UPDATE live_sessions SET end_time=NULL, status="live" WHERE id=?', (session_id,))
                    conn.commit()
                    conn.close()
                    return False
            except Exception as _e:
                _logger.warning(f"[end_session] 计算持续时间失败: {_e}")
    
    # 正常结束
    c.execute(
        'UPDATE live_sessions SET end_time=?, status=? WHERE id=?',
        (now_bj, 'ended', session_id)
    )
    conn.commit()
    conn.close()
    return True


def find_recent_session(username, minutes=15, owner_user_id=None):
    """查找该账号最近可合并的 session（用于断线重连合并）。
    优先查找 status='live' 的活跃 session（被 end_session 5分钟保护机制保留的），
    其次查找 status='ended' 且 end_time 在 N 分钟内的记录。
    owner_user_id=None 表示不限制所属用户（管理员用）
    返回 session 行 (dict) 或 None。"""
    conn = get_conn()
    c = conn.cursor()

    # 第一步：优先查 status='live' 的记录（正在进行的或被5分钟保护机制保留的）
    if owner_user_id is None:
        c.execute(
            """SELECT * FROM live_sessions
               WHERE username=? AND status='live'
               ORDER BY id DESC LIMIT 1""",
            (username,)
        )
    else:
        c.execute(
            """SELECT * FROM live_sessions
               WHERE username=? AND owner_user_id=? AND status='live'
               ORDER BY id DESC LIMIT 1""",
            (username, owner_user_id)
        )
    row = c.fetchone()
    if row:
        conn.close()
        return dict(row)

    # 第二步：没找到 live 的，查 ended 且 end_time 在 N 分钟内的
    cutoff = (datetime.now() - timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')
    if owner_user_id is None:
        c.execute(
            """SELECT * FROM live_sessions
               WHERE username=? AND status='ended' AND end_time >= ?
               ORDER BY id DESC LIMIT 1""",
            (username, cutoff)
        )
    else:
        c.execute(
            """SELECT * FROM live_sessions
               WHERE username=? AND owner_user_id=? AND status='ended' AND end_time >= ?
               ORDER BY id DESC LIMIT 1""",
            (username, owner_user_id, cutoff)
        )

    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def reactivate_session(session_id):
    """重新激活一个已结束的 session（断线重连，清除 end_time，改回 live 状态）。"""
    conn = get_conn()
    conn.execute(
        "UPDATE live_sessions SET end_time=NULL, status='live' WHERE id=?",
        (session_id,)
    )
    conn.commit()
    conn.close()


def add_comment(session_id, username, user_id, content, is_anchor=0, text_zh='', lang='', lang_short=''):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'INSERT INTO comments (session_id, username, user_id, content, text_zh, lang, lang_short, timestamp, is_anchor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (session_id, username, user_id, content, text_zh or '', lang or '', lang_short or '', now, is_anchor)
    )
    new_id = c.lastrowid
    c.execute('UPDATE live_sessions SET total_comments=total_comments+1 WHERE id=?', (session_id,))
    conn.commit()
    conn.close()
    return new_id


def add_gift(session_id, username, user_id, gift_name, gift_count, gift_value):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'INSERT INTO gifts (session_id, username, user_id, gift_name, gift_count, gift_value, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (session_id, username, user_id, gift_name, gift_count, gift_value, now)
    )
    c.execute(
        'UPDATE live_sessions SET total_gifts=total_gifts+?, total_gift_value=total_gift_value+? WHERE id=?',
        (gift_count, gift_value, session_id)
    )
    conn.commit()
    conn.close()


def add_follow(session_id, username, user_id):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'INSERT INTO follows (session_id, username, user_id, timestamp) VALUES (?, ?, ?, ?)',
        (session_id, username, user_id, now)
    )
    c.execute('UPDATE live_sessions SET new_followers=new_followers+1 WHERE id=?', (session_id,))
    conn.commit()
    conn.close()


def update_viewers(session_id, viewer_count, like_count, comment_count, total_user=0, peak_viewers=None):
    conn = get_conn()
    c = conn.cursor()
    now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    # 更新快照（存储UTC时间）
    c.execute(
        'INSERT INTO metrics_snapshots (session_id, timestamp, viewer_count, like_count, comment_count) VALUES (?, ?, ?, ?, ?)',
        (session_id, now_utc, viewer_count, like_count, comment_count)
    )
    # 更新峰值在线 & 累计观看
    # total_user 是 TikTok 平台提供的真实累计观看人数，只要 > 0 就直接使用
    # viewer_count 是实时在线人数，不能用作累计
    real_total = total_user if total_user and total_user > 0 else 0
    
    if peak_viewers is not None:
        # 使用传入的峰值数据
        c.execute(
            'UPDATE live_sessions SET peak_viewers=MAX(peak_viewers, ?), total_likes=?, total_viewers=MAX(total_viewers, ?) WHERE id=?',
            (peak_viewers, like_count, real_total, session_id)
        )
    else:
        # 兼容旧代码，使用viewer_count作为峰值
        c.execute(
            'UPDATE live_sessions SET peak_viewers=MAX(peak_viewers, ?), total_likes=?, total_viewers=MAX(total_viewers, ?) WHERE id=?',
            (viewer_count, like_count, real_total, session_id)
        )
    conn.commit()
    conn.close()


def get_session_by_id(session_id):
    """获取单场直播的基本信息"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM live_sessions WHERE id=?', (session_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_session_summary(session_id):
    """获取会话汇总数据，session不存在时返回None"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM live_sessions WHERE id=?', (session_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    session = dict(row)

    # 获取评论列表（含语言检测）——全部评论，不限制条数
    c.execute('SELECT * FROM comments WHERE session_id=? ORDER BY timestamp DESC', (session_id,))
    comments_raw = [dict(r) for r in c.fetchall()]

    # 对评论做语言检测（纯本地，不走网络）
    # 翻译直接用数据库存的 text_zh 字段（监控时已实时翻译存入），不在此重复翻译
    try:
        from src.lang_detect import detect_language
        from concurrent.futures import ThreadPoolExecutor

        def _detect_one(cm):
            # 已存有语言标记的评论跳过检测（监控时已实时存入）
            if cm.get('lang') and cm['lang'] != 'other':
                return cm
            if not cm.get('is_anchor') and cm.get('content'):
                li = detect_language(cm['content'])
                cm['lang']       = li.get('lang', 'other')
                cm['lang_short'] = li.get('lang_short', '?')
                cm['css_class']  = li.get('css_class', 'lang-other')
                cm['dialect']    = li.get('dialect')
                cm['flag']       = li.get('flag', '')
                if not cm.get('text_zh'):
                    cm['text_zh'] = ''
            return cm

        # 只对无语言标记的评论做检测，已标记的直接跳过
        need_detect = [cm for cm in comments_raw if not cm.get('lang') or cm['lang'] == 'other']
        already_done = [cm for cm in comments_raw if cm.get('lang') and cm['lang'] != 'other']

        if need_detect:
            with ThreadPoolExecutor(max_workers=8) as pool:
                detected = list(pool.map(_detect_one, need_detect))
        else:
            detected = []

        comments = already_done + detected
        # 按 id 排序还原原始顺序
        comments.sort(key=lambda x: x.get('id', 0))
    except Exception:
        comments = comments_raw

    # 获取礼物排行
    c.execute('''
        SELECT username, SUM(gift_value) as total_value, COUNT(*) as cnt
        FROM gifts WHERE session_id=?
        GROUP BY username ORDER BY total_value DESC LIMIT 20
    ''', (session_id,))
    gift_rank = [dict(r) for r in c.fetchall()]

    # 获取指标趋势
    c.execute('SELECT * FROM metrics_snapshots WHERE session_id=? ORDER BY timestamp', (session_id,))
    snapshots = [dict(r) for r in c.fetchall()]

    conn.close()
    # start_time / end_time 已存储为北京时间，直接赋值，不再做时区转换
    session['start_time_beijing'] = session.get('start_time')
    session['end_time_beijing'] = session.get('end_time')
    # 同时保留原始字段（兼容性）
    
    return {
        'session': session,
        'comments': comments,
        'gift_rank': gift_rank,
        'snapshots': snapshots
    }


def get_all_sessions(limit=50, owner_user_id=None):
    """获取所有会话列表（含 Top5 关键词词频）
    owner_user_id=None 表示管理员查全部
    """
    import re
    from collections import Counter

    conn = get_conn()
    c = conn.cursor()
    if owner_user_id is None:
        c.execute('SELECT * FROM live_sessions ORDER BY start_time DESC LIMIT ?', (limit,))
    else:
        c.execute('SELECT * FROM live_sessions WHERE owner_user_id=? ORDER BY start_time DESC LIMIT ?', (owner_user_id, limit))
    rows = [dict(r) for r in c.fetchall()]

    # 批量获取每个场次的关键词（只查 speech_records，避免逐条N次查询）
    if rows:
        ids = [r['id'] for r in rows]
        placeholders = ','.join('?' * len(ids))
        c.execute(
            f'SELECT session_id, text FROM speech_records WHERE session_id IN ({placeholders}) AND text IS NOT NULL',
            ids
        )
        speech_map = {}  # session_id -> [texts]
        for row in c.fetchall():
            speech_map.setdefault(row['session_id'], []).append(row['text'])

        # 评论关键词（Top5）
        word_pat = re.compile(r'[\w\u4e00-\u9fff\u0600-\u06ff]+')
        stopwords = {
            '的','了','是','在','我','你','他','她','它','们','就','都','也','和','而','但','或','与',
            'the','a','an','is','are','was','were','be','been','being','have','has','had','do','does',
            'did','will','would','could','should','may','might','shall','can','need','used',
            'i','you','he','she','it','we','they','me','him','her','us','them','my','your','his','its',
            'our','their','this','that','these','those','what','which','who','when','where','why','how',
            'all','each','every','both','few','more','most','other','some','such','no','nor','not','only',
            'very','just','because','as','until','while','so','than','too','own','same',
        }
        for r in rows:
            texts = speech_map.get(r['id'], [])
            if texts:
                words = []
                for t in texts:
                    words.extend([w.lower() for w in word_pat.findall(t) if len(w) > 1 and w.lower() not in stopwords])
                freq = Counter(words)
                top = freq.most_common(10)
                total = sum(cnt for _, cnt in top) or 1
                r['top_keywords'] = [
                    {'word': w, 'count': cnt, 'pct': round(cnt / total * 100)}
                    for w, cnt in top[:8]
                ]
                r['speech_count'] = len(texts)
            else:
                r['top_keywords'] = []
                r['speech_count'] = 0

    conn.close()
    
    # start_time / end_time 已存储为北京时间，直接赋值作为 _beijing 字段
    for r in rows:
        r['start_time_beijing'] = r.get('start_time')
        r['end_time_beijing'] = r.get('end_time')
    
    return rows


def delete_session(session_id):
    """删除一条历史记录（级联删除相关数据）"""
    conn = get_conn()
    c = conn.cursor()
    for table in ('speech_records', 'comments', 'gifts', 'follows', 'metrics_snapshots'):
        c.execute(f'DELETE FROM {table} WHERE session_id=?', (session_id,))
    c.execute('DELETE FROM live_sessions WHERE id=?', (session_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_account_sessions(username, owner_user_id=None):
    """删除某账号的所有历史场次（级联删除）"""
    conn = get_conn()
    c = conn.cursor()
    if owner_user_id is None:
        c.execute('SELECT id FROM live_sessions WHERE username=? AND status!=?', (username, 'live'))
    else:
        c.execute('SELECT id FROM live_sessions WHERE username=? AND status!=? AND owner_user_id=?', (username, 'live', owner_user_id))
    ids = [r['id'] for r in c.fetchall()]
    for sid in ids:
        for table in ('speech_records', 'comments', 'gifts', 'follows', 'metrics_snapshots'):
            c.execute(f'DELETE FROM {table} WHERE session_id=?', (sid,))
    if owner_user_id is None:
        c.execute("DELETE FROM live_sessions WHERE username=? AND status!=?", (username, 'live'))
    else:
        c.execute("DELETE FROM live_sessions WHERE username=? AND status!=? AND owner_user_id=?", (username, 'live', owner_user_id))
    conn.commit()
    conn.close()
    return len(ids)


def get_active_sessions():
    """获取当前进行中的直播会话"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM live_sessions WHERE status=?', ('live',))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ==================== 话术相关 ====================

def add_speech(session_id, anchor, text, text_zh, lang, lang_short, lang_display, dialect=None):
    """保存一条主播话术（含翻译）"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'INSERT INTO speech_records (session_id, anchor, text, text_zh, lang, lang_short, lang_display, dialect, timestamp) VALUES (?,?,?,?,?,?,?,?,?)',
        (session_id, anchor, text, text_zh, lang, lang_short, lang_display, dialect, now)
    )
    conn.commit()
    conn.close()


def get_session_speech(session_id):
    """获取某场次所有话术"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM speech_records WHERE session_id=? ORDER BY timestamp', (session_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_speech_summary(session_id):
    """
    生成话术摘要：规则提取关键句
    - 去重、按长度和关键词权重排序，取 Top10 作为"内容总结"
    - 关键词返回 [{word, zh}] 格式，非中文词附带中文翻译
    """
    records = get_session_speech(session_id)
    if not records:
        return {'records': [], 'summary': [], 'keywords': []}

    import re
    from collections import Counter

    # 所有文本
    all_texts = [r['text'] for r in records if r['text'].strip()]

    # 关键词提取：分词（简单按空格+标点切分）
    word_pat = re.compile(r'[\w\u4e00-\u9fff\u0600-\u06ff]+')
    stopwords = {'的','了','是','在','我','你','他','她','它','们','就','都','也','和','而','但','或','与',
                 'the','a','an','is','are','was','were','be','been','being','have','has','had','do','does',
                 'did','will','would','could','should','may','might','shall','can','need','dare','ought','used',
                 'i','you','he','she','it','we','they','me','him','her','us','them','my','your','his','its',
                 'our','their','this','that','these','those','what','which','who','whom','whose','when','where',
                 'why','how','all','each','every','both','few','more','most','other','some','such','no','nor',
                 'not','only','own','same','so','than','too','very','just','because','as','until','while'}
    all_words = []
    for t in all_texts:
        all_words.extend([w.lower() for w in word_pat.findall(t) if len(w) > 1 and w.lower() not in stopwords])
    word_freq = Counter(all_words)
    raw_keywords = [w for w, _ in word_freq.most_common(8)]

    # 关键词：直接返回词频数据，不做翻译（翻译由前端异步请求 /api/translate 获取）
    # 这样 speech 接口本身在毫秒级返回，不被网络请求阻塞
    keywords = [{'word': kw, 'zh': _KW_TRANSLATE_CACHE.get(kw, ''), 'count': word_freq.get(kw, 1)}
                for kw in raw_keywords]

    # 后台并发预热翻译缓存（非阻塞，下次请求时直接命中缓存）
    _needs_translate = [kw for kw in raw_keywords
                        if kw not in _KW_TRANSLATE_CACHE
                        and not all('\u4e00' <= c <= '\u9fff' for c in kw if c.isalpha())]
    if _needs_translate:
        import threading
        def _bg_translate():
            try:
                from src.translator import translate_to_zh
            except Exception:
                try:
                    from translator import translate_to_zh
                except Exception:
                    return
            from concurrent.futures import ThreadPoolExecutor
            def _do(kw):
                try:
                    r = translate_to_zh(kw)
                    if r and r.strip().lower() != kw.lower():
                        _KW_TRANSLATE_CACHE[kw] = r.strip()
                    else:
                        _KW_TRANSLATE_CACHE[kw] = ''
                except Exception:
                    _KW_TRANSLATE_CACHE[kw] = ''
            with ThreadPoolExecutor(max_workers=8) as p:
                list(p.map(_do, _needs_translate))
        threading.Thread(target=_bg_translate, daemon=True).start()

    # 关键句：含高频词的句子，且长度 > 10 字符，去重取 Top10
    def score_sentence(s):
        words_in_s = [w.lower() for w in word_pat.findall(s)]
        return sum(word_freq.get(w, 0) for w in words_in_s)

    # 建立 text -> text_zh 映射（用于关键句附带翻译）
    text_to_zh = {}
    for r in records:
        t = r.get('text', '').strip()
        tz = r.get('text_zh', '') or ''
        if t and tz and tz.strip() and tz.strip() != t:
            text_to_zh[t] = tz.strip()

    seen = set()
    scored = []
    for r in records:
        t_stripped = (r.get('text') or '').strip()
        if len(t_stripped) < 8 or t_stripped in seen:
            continue
        seen.add(t_stripped)
        scored.append((score_sentence(t_stripped), t_stripped))
    scored.sort(key=lambda x: -x[0])
    # summary 改为列表of dict: {text, text_zh}，前端可展示原文+翻译
    summary = [
        {'text': s, 'text_zh': text_to_zh.get(s, '')}
        for _, s in scored[:10]
    ]

    return {'records': records, 'summary': summary, 'keywords': keywords}


# ==================== 复盘对比相关 ====================

def get_review_data(session_id):
    """
    获取复盘对比数据：本场 vs 上场 vs 近7场均值 vs 历史最佳
    只对同一账号做对比
    """
    conn = get_conn()
    c = conn.cursor()

    # 当前场次
    c.execute('SELECT * FROM live_sessions WHERE id=?', (session_id,))
    current = dict(c.fetchone() or {})
    if not current:
        conn.close()
        return {}

    username = current['username']

    # 该账号所有已结束场次（按时间倒序）
    c.execute(
        'SELECT * FROM live_sessions WHERE username=? AND status=? ORDER BY start_time DESC',
        (username, 'ended')
    )
    all_sessions = [dict(r) for r in c.fetchall()]
    conn.close()

    # 计算时长（分钟）
    def duration_min(s):
        try:
            from datetime import datetime as dt
            st = dt.strptime(s['start_time'], '%Y-%m-%d %H:%M:%S')
            et = dt.strptime(s['end_time'], '%Y-%m-%d %H:%M:%S')
            return round((et - st).total_seconds() / 60, 1)
        except Exception:
            return 0

    def session_metrics(s):
        return {
            'peak_viewers': s.get('peak_viewers', 0),
            'total_viewers': s.get('total_viewers', 0),
            'total_comments': s.get('total_comments', 0),
            'total_gifts': s.get('total_gifts', 0),
            'total_gift_value': round(s.get('total_gift_value', 0), 2),
            'new_followers': s.get('new_followers', 0),
            'duration_min': duration_min(s),
        }

    current_metrics = session_metrics(current)

    # 上一场（排除当前场次）
    prev_sessions = [s for s in all_sessions if s['id'] != session_id]
    prev_metrics = session_metrics(prev_sessions[0]) if prev_sessions else None

    # 近7场均值（排除当前）
    recent7 = prev_sessions[:7]
    avg7 = None
    if recent7:
        keys = ['peak_viewers','total_viewers','total_comments','total_gifts','total_gift_value','new_followers','duration_min']
        avg7 = {k: round(sum(session_metrics(s)[k] for s in recent7) / len(recent7), 1) for k in keys}

    # 历史最佳（排除当前）
    best = None
    if prev_sessions:
        keys = ['peak_viewers','total_viewers','total_comments','total_gifts','total_gift_value','new_followers','duration_min']
        best = {k: max(session_metrics(s)[k] for s in prev_sessions) for k in keys}

    return {
        'current': current_metrics,
        'prev': prev_metrics,
        'avg7': avg7,
        'best': best,
        'username': username,
        'session_count': len(all_sessions),
    }


# ==================== 账号分组相关 ====================

def set_account_group(username, group_name='own', display_name=None, owner_user_id=1):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'INSERT OR REPLACE INTO account_groups (owner_user_id, username, group_name, display_name, created_at) VALUES (?,?,?,?,?)',
        (owner_user_id, username, group_name, display_name or username, now)
    )
    conn.commit()
    conn.close()


def get_account_group(username, owner_user_id=1):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM account_groups WHERE username=? AND owner_user_id=?', (username, owner_user_id))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {'username': username, 'group_name': 'own'}


# ==================== 系统用户管理 ====================

def get_user_by_username(username):
    """按用户名查找系统用户"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM sys_users WHERE username=?', (username,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def verify_user(username, password):
    """验证用户名和密码，返回用户dict或None"""
    user = get_user_by_username(username)
    if user and user['password_hash'] == _hash_password(password):
        return user
    return None


def create_user(username, password, is_admin=0, real_name='', status='active'):
    """创建新系统用户，返回 (True, user_id) 或 (False, reason)"""
    if get_user_by_username(username):
        return False, '用户名已存在'
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        c.execute(
            'INSERT INTO sys_users (username, password_hash, is_admin, real_name, status, created_at) VALUES (?,?,?,?,?,?)',
            (username, _hash_password(password), is_admin, real_name or '', status, now)
        )
        uid = c.lastrowid
        conn.commit()
        conn.close()
        return True, uid
    except Exception as e:
        conn.close()
        return False, str(e)


def update_user_password(user_id, new_password):
    """修改密码"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE sys_users SET password_hash=? WHERE id=?', (_hash_password(new_password), user_id))
    conn.commit()
    conn.close()


def get_all_users():
    """管理员：获取所有系统用户列表"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT id, username, is_admin, real_name, status, created_at, last_login_at FROM sys_users ORDER BY id')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def update_last_login(user_id):
    """更新用户最后登录时间"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('UPDATE sys_users SET last_login_at=? WHERE id=?', (now, user_id))
    conn.commit()
    conn.close()


def set_user_status(user_id, status):
    """设置用户状态：active / pending / disabled"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE sys_users SET status=? WHERE id=?', (status, user_id))
    conn.commit()
    conn.close()


def delete_user(user_id):
    """删除系统用户（管理员操作）"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('DELETE FROM sys_users WHERE id=? AND is_admin=0', (user_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ==================== 自动监控列表 ====================

def get_auto_monitor_list(owner_user_id=None):
    """获取自动监控列表"""
    conn = get_conn()
    c = conn.cursor()
    if owner_user_id is None:
        c.execute('SELECT * FROM auto_monitor_list ORDER BY group_name, username')
    else:
        c.execute('SELECT * FROM auto_monitor_list WHERE owner_user_id=? ORDER BY group_name, username', (owner_user_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def upsert_auto_monitor(owner_user_id, username, display_name=None, group_name='own', enabled=1, note=''):
    """新增或更新自动监控账号"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('''
        INSERT INTO auto_monitor_list (owner_user_id, username, display_name, group_name, enabled, note, created_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(owner_user_id, username) DO UPDATE SET
            display_name=excluded.display_name,
            group_name=excluded.group_name,
            enabled=excluded.enabled,
            note=excluded.note
    ''', (owner_user_id, username, display_name or username, group_name, enabled, note or '', now))
    conn.commit()
    conn.close()


def delete_auto_monitor(owner_user_id, username):
    """从自动监控列表移除"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('DELETE FROM auto_monitor_list WHERE owner_user_id=? AND username=?', (owner_user_id, username))
    conn.commit()
    conn.close()


def toggle_auto_monitor(owner_user_id, username, enabled):
    """启用/禁用某账号的自动监控"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE auto_monitor_list SET enabled=? WHERE owner_user_id=? AND username=?',
              (1 if enabled else 0, owner_user_id, username))
    conn.commit()
    conn.close()


def get_enabled_auto_monitors(owner_user_id=None):
    """获取所有启用的自动监控账号"""
    conn = get_conn()
    c = conn.cursor()
    if owner_user_id is None:
        c.execute("SELECT * FROM auto_monitor_list WHERE enabled=1")
    else:
        c.execute("SELECT * FROM auto_monitor_list WHERE enabled=1 AND owner_user_id=?", (owner_user_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ==================== 竞品粉丝快照 ====================

def save_follower_snapshot(username, follower_count, following_count=0, video_count=0, bio='', avatar_url=''):
    """保存竞品粉丝快照（每天一条，重复则更新）"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = datetime.now().strftime('%Y-%m-%d')
    c.execute('''
        INSERT INTO rival_follower_snapshots
            (username, follower_count, following_count, video_count, bio, avatar_url, snapshot_date, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(username, snapshot_date) DO UPDATE SET
            follower_count=excluded.follower_count,
            following_count=excluded.following_count,
            video_count=excluded.video_count,
            bio=excluded.bio,
            avatar_url=excluded.avatar_url,
            created_at=excluded.created_at
    ''', (username, follower_count, following_count, video_count, bio, avatar_url, today, now))
    conn.commit()
    conn.close()


def get_follower_snapshots(username, days=30):
    """获取某账号近N天的粉丝快照"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM rival_follower_snapshots
        WHERE username=?
        ORDER BY snapshot_date DESC
        LIMIT ?
    ''', (username, days))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_latest_follower_snapshot(username):
    """获取某账号最新的粉丝快照"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM rival_follower_snapshots
        WHERE username=?
        ORDER BY snapshot_date DESC
        LIMIT 1
    ''', (username,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_rival_usernames():
    """获取所有竞品账号的用户名（跨用户，用于定时任务）"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT DISTINCT username FROM account_groups WHERE group_name='rival'")
    rows = [r['username'] for r in c.fetchall()]
    conn.close()
    return rows


# ==================== 主播评分卡 ====================

def calc_anchor_score(session_id):
    """
    计算单场直播主播评分（0-100分），返回各维度分数和总分。
    维度：互动率(25) + 留存率(20) + 话术活跃度(20) + 语言覆盖(20) + 峰值爬升速度(15)
    """
    import re
    from collections import Counter

    conn = get_conn()
    c = conn.cursor()

    c.execute('SELECT * FROM live_sessions WHERE id=?', (session_id,))
    s = c.fetchone()
    if not s:
        conn.close()
        return None
    s = dict(s)

    # ── 基础数据 ──
    peak_viewers = max(s.get('peak_viewers') or 1, 1)
    total_comments = s.get('total_comments') or 0
    total_viewers = max(s.get('total_viewers') or 1, 1)

    # 直播时长（分钟）
    duration_min = 0
    try:
        from datetime import datetime as _dt
        if s.get('start_time') and s.get('end_time'):
            t0 = _dt.strptime(s['start_time'], '%Y-%m-%d %H:%M:%S')
            t1 = _dt.strptime(s['end_time'], '%Y-%m-%d %H:%M:%S')
            duration_min = max((t1 - t0).total_seconds() / 60, 1)
        elif s.get('start_time'):
            # 直播仍在进行，用当前时间计算已播时长
            t0 = _dt.strptime(s['start_time'], '%Y-%m-%d %H:%M:%S')
            duration_min = max((_dt.now() - t0).total_seconds() / 60, 1)
        else:
            duration_min = 1
    except Exception:
        duration_min = 1

    # ── 维度1：互动率（评论数/峰值在线，25分）──
    interaction_rate = total_comments / peak_viewers
    # 行业均值约0.15，优秀>0.3
    score_interaction = min(interaction_rate / 0.3 * 25, 25)

    # ── 维度2：留存率（平均在线/峰值在线，20分）──
    c.execute('SELECT AVG(viewer_count) as avg_v FROM metrics_snapshots WHERE session_id=?', (session_id,))
    row = c.fetchone()
    avg_viewers = (row['avg_v'] or 0) if row else 0
    retention_rate = avg_viewers / peak_viewers if peak_viewers > 0 else 0
    # 优秀留存>0.6
    score_retention = min(retention_rate / 0.6 * 20, 20)

    # ── 维度3：话术活跃度（每10分钟话术条数，20分）──
    # speech_records 中所有记录均为主播话术，直接全量统计
    c.execute('SELECT COUNT(*) as cnt FROM speech_records WHERE session_id=?', (session_id,))
    row = c.fetchone()
    speech_cnt = (row['cnt'] or 0) if row else 0
    speech_per_10min = speech_cnt / duration_min * 10
    # 优秀：每10分钟>8条
    score_speech = min(speech_per_10min / 8 * 20, 20)

    # ── 维度4：语言覆盖（阿语+英语互动占比，20分）──
    c.execute('''
        SELECT lang_short, COUNT(*) as cnt
        FROM comments WHERE session_id=? AND is_anchor=0
        GROUP BY lang_short
    ''', (session_id,))
    lang_rows = c.fetchall()
    lang_total = sum(r['cnt'] for r in lang_rows)
    lang_ar = sum(r['cnt'] for r in lang_rows if r['lang_short'] in ('AR', 'ar'))
    lang_en = sum(r['cnt'] for r in lang_rows if r['lang_short'] in ('EN', 'en'))
    lang_target_ratio = (lang_ar + lang_en) / lang_total if lang_total > 0 else 0
    # 目标语言（阿+英）占比>0.7为优秀
    score_lang = min(lang_target_ratio / 0.7 * 20, 20)

    # ── 维度5：峰值爬升速度（开播30分钟内达到峰值的比例，15分）──
    score_ramp = 0
    try:
        from datetime import datetime as _dt, timedelta
        if s.get('start_time'):
            t0 = _dt.strptime(s['start_time'], '%Y-%m-%d %H:%M:%S')
            t30 = (t0 + timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute('''
                SELECT MAX(viewer_count) as max_v
                FROM metrics_snapshots
                WHERE session_id=? AND timestamp <= ?
            ''', (session_id, t30))
            row = c.fetchone()
            peak_30 = (row['max_v'] or 0) if row else 0
            ramp_ratio = peak_30 / peak_viewers if peak_viewers > 0 else 0
            # 30分钟内能到峰值60%以上为优秀
            score_ramp = min(ramp_ratio / 0.6 * 15, 15)
    except Exception:
        score_ramp = 7.5  # 数据不足时给中间分

    conn.close()

    total_score = score_interaction + score_retention + score_speech + score_lang + score_ramp

    # 等级
    if total_score >= 80:
        grade, grade_color = 'S', '#22c55e'
    elif total_score >= 65:
        grade, grade_color = 'A', '#3b82f6'
    elif total_score >= 50:
        grade, grade_color = 'B', '#f59e0b'
    elif total_score >= 35:
        grade, grade_color = 'C', '#f97316'
    else:
        grade, grade_color = 'D', '#ef4444'

    return {
        'total': round(total_score, 1),
        'grade': grade,
        'grade_color': grade_color,
        'dimensions': {
            'interaction': {'score': round(score_interaction, 1), 'max': 25, 'label': '互动率',
                            'detail': f'{interaction_rate:.1%} (评论/峰值在线)'},
            'retention': {'score': round(score_retention, 1), 'max': 20, 'label': '留存率',
                          'detail': f'{retention_rate:.1%} (均值/峰值在线)'},
            'speech': {'score': round(score_speech, 1), 'max': 20, 'label': '话术活跃度',
                       'detail': f'{speech_per_10min:.1f} 条/10min'},
            'lang': {'score': round(score_lang, 1), 'max': 20, 'label': '语言覆盖',
                     'detail': f'阿+英 {lang_target_ratio:.1%}'},
            'ramp': {'score': round(score_ramp, 1), 'max': 15, 'label': '峰值爬升',
                     'detail': f'30min内达峰值 {min(ramp_ratio if "ramp_ratio" in dir() else 0, 1):.1%}'},
        },
        'duration_min': round(duration_min, 0),
        'peak_viewers': peak_viewers,
        'interaction_rate': round(interaction_rate, 3),
        'retention_rate': round(retention_rate, 3),
    }


def get_anchor_score_history(username, limit=20, owner_user_id=None):
    """获取某主播最近N场的评分历史（用于趋势图）"""
    conn = get_conn()
    c = conn.cursor()
    if owner_user_id:
        c.execute('''
            SELECT id, start_time, peak_viewers, total_comments, end_time
            FROM live_sessions
            WHERE username=? AND owner_user_id=? AND status='ended'
            ORDER BY start_time DESC LIMIT ?
        ''', (username, owner_user_id, limit))
    else:
        c.execute('''
            SELECT id, start_time, peak_viewers, total_comments, end_time
            FROM live_sessions
            WHERE username=? AND status='ended'
            ORDER BY start_time DESC LIMIT ?
        ''', (username, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    results = []
    for r in rows:
        score = calc_anchor_score(r['id'])
        if score:
            results.append({
                'session_id': r['id'],
                'start_time': r['start_time'],
                'total_score': score['total'],
                'grade': score['grade'],
            })
    return results


# ==================== 时段热力图 ====================

def get_timeslot_heatmap(owner_user_id=None, username=None, days=90):
    """
    返回开播时段热力图数据：{weekday(0=周一..6=周日): {hour: avg_peak_viewers}}
    """
    from datetime import datetime as _dt, timedelta
    conn = get_conn()
    c = conn.cursor()

    since = (_dt.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    params = [since]
    sql = "SELECT start_time, peak_viewers FROM live_sessions WHERE start_time >= ? AND status='ended'"
    if owner_user_id:
        sql += ' AND owner_user_id=?'
        params.append(owner_user_id)
    if username:
        sql += ' AND username=?'
        params.append(username)
    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    from collections import defaultdict
    bucket = defaultdict(list)  # (weekday, hour) -> [peak_viewers]
    for r in rows:
        try:
            t = _dt.strptime(r['start_time'], '%Y-%m-%d %H:%M:%S')
            key = (t.weekday(), t.hour)  # 0=周一
            bucket[key].append(r['peak_viewers'] or 0)
        except Exception:
            pass

    # 转成前端友好格式
    heatmap = {}
    for (wd, h), vals in bucket.items():
        if wd not in heatmap:
            heatmap[wd] = {}
        heatmap[wd][h] = round(sum(vals) / len(vals))

    # 找最佳时段（按平均峰值排序，取top3）
    flat = [(wd, h, round(sum(v)/len(v))) for (wd, h), v in bucket.items()]
    flat.sort(key=lambda x: -x[2])
    weekday_names = ['周一','周二','周三','周四','周五','周六','周日']
    best_slots = [
        {'weekday': weekday_names[wd], 'hour': h, 'avg_peak': peak}
        for wd, h, peak in flat[:3]
    ]

    return {'heatmap': heatmap, 'best_slots': best_slots, 'weekday_names': weekday_names}


# ==================== 竞品话术对比 ====================

def get_speech_keywords(session_ids, top_n=50, exclude_own=False):
    """
    提取多场次的话术高频词，返回词频字典。
    exclude_own=True 时过滤停用词更严格（竞品差异词分析用）
    """
    import re
    from collections import Counter

    conn = get_conn()
    c = conn.cursor()
    if not session_ids:
        conn.close()
        return {}
    placeholders = ','.join('?' * len(session_ids))
    c.execute(f'SELECT text, text_zh FROM speech_records WHERE session_id IN ({placeholders})', session_ids)
    rows = c.fetchall()
    conn.close()

    # 中文停用词
    zh_stop = {
        '的','了','是','在','我','你','他','她','它','们','就','都','也','和','而','但','或','与',
        '这','那','有','说','要','来','去','对','可','不','把','被','让','给','会','能','好',
        '什么','这个','那个','一个','可以','没有','因为','所以','如果','这样','那样','大家',
        '大','小','多','少','一','二','三','四','五','中','上','下','里','外','前','后',
    }
    en_stop = {
        'the','a','an','is','are','was','were','be','been','have','has','had','do','does','did',
        'this','that','it','we','you','they','i','and','or','but','in','on','at','to','for',
        'of','with','by','from','so','if','not','no','yes','ok','yeah','hi','hey',
    }
    ar_stop = {
        'في','من','على','إلى','عن','مع','هذا','هذه','ذلك','التي','الذي','أن','كان','كانت',
        'هو','هي','نحن','أنا','انت','لا','نعم','اوك',
    }

    word_pat = re.compile(r'[\w\u4e00-\u9fff\u0600-\u06ff]{2,}')
    counter = Counter()
    for row in rows:
        texts = [row['text'] or '', row['text_zh'] or '']
        for text in texts:
            words = word_pat.findall(text.lower())
            for w in words:
                if w in zh_stop or w in en_stop or w in ar_stop:
                    continue
                if w.isdigit():
                    continue
                counter[w] += 1

    return dict(counter.most_common(top_n))


def get_rival_speech_compare(own_usernames, rival_usernames, sessions_per_account=10):
    """
    对比自营和竞品账号近N场的话术差异词。
    返回：own_keywords, rival_keywords, diff_keywords（竞品有但自营没强调的词）
    """
    conn = get_conn()
    c = conn.cursor()

    def get_recent_session_ids(usernames, n):
        if not usernames:
            return []
        ids = []
        for u in usernames:
            c.execute('''
                SELECT id FROM live_sessions WHERE username=?
                ORDER BY start_time DESC LIMIT ?
            ''', (u, n))
            ids += [r['id'] for r in c.fetchall()]
        return ids

    own_ids = get_recent_session_ids(own_usernames, sessions_per_account)
    rival_ids = get_recent_session_ids(rival_usernames, sessions_per_account)
    conn.close()

    own_kw = get_speech_keywords(own_ids, top_n=80)
    rival_kw = get_speech_keywords(rival_ids, top_n=80)

    if not own_kw and not rival_kw:
        return {'own': {}, 'rival': {}, 'diff': [], 'rival_only': []}

    # 归一化频率（防止场次数量不同导致偏差）
    own_total = max(sum(own_kw.values()), 1)
    rival_total = max(sum(rival_kw.values()), 1)
    own_norm = {k: v/own_total for k, v in own_kw.items()}
    rival_norm = {k: v/rival_total for k, v in rival_kw.items()}

    # 差异词：竞品频率 > 自营频率 * 1.5（即竞品明显更强调的词）
    diff = []
    all_words = set(rival_norm.keys())
    for w in all_words:
        r_freq = rival_norm.get(w, 0)
        o_freq = own_norm.get(w, 0)
        if r_freq > o_freq * 1.5 and rival_kw.get(w, 0) >= 3:
            diff.append({'word': w, 'rival_freq': round(r_freq*1000, 1), 'own_freq': round(o_freq*1000, 1),
                         'ratio': round(r_freq/(o_freq+0.0001), 1)})
    diff.sort(key=lambda x: -x['ratio'])

    return {
        'own': dict(list(own_kw.items())[:30]),
        'rival': dict(list(rival_kw.items())[:30]),
        'diff': diff[:20],
    }


# ==================== 用户反馈 ====================

def submit_feedback(owner_user_id, submitter, fb_type, title, description):
    """提交反馈（需求/BUG）"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('''
        INSERT INTO feedbacks (owner_user_id, submitter, type, title, description, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'open', ?)
    ''', (owner_user_id, submitter, fb_type, title, description, now))
    fb_id = c.lastrowid
    conn.commit()
    conn.close()
    return fb_id


def get_all_feedbacks(status_filter=None):
    """获取所有反馈列表（管理员用）"""
    conn = get_conn()
    c = conn.cursor()
    if status_filter and status_filter != 'all':
        c.execute('''
            SELECT f.*, u.username as owner_name
            FROM feedbacks f
            LEFT JOIN sys_users u ON f.owner_user_id = u.id
            WHERE f.status = ?
            ORDER BY f.created_at DESC
        ''', (status_filter,))
    else:
        c.execute('''
            SELECT f.*, u.username as owner_name
            FROM feedbacks f
            LEFT JOIN sys_users u ON f.owner_user_id = u.id
            ORDER BY f.created_at DESC
        ''')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def update_feedback_status(fb_id, status):
    """更新反馈状态：open / done / rejected"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE feedbacks SET status=? WHERE id=?', (status, fb_id))
    conn.commit()
    conn.close()


def delete_feedback(fb_id):
    """删除反馈"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('DELETE FROM feedbacks WHERE id=?', (fb_id,))
    conn.commit()
    conn.close()


# ── 用户操作日志 ──

def write_action_log(user_id: int, username: str, action: str, target: str = '', detail: str = '', ip: str = '', page: str = ''):
    """记录用户操作日志，并自动裁剪至每用户最近200条"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'INSERT INTO user_action_logs (user_id, username, action, target, detail, ip, page, created_at) VALUES (?,?,?,?,?,?,?,?)',
        (user_id, username, action, target, detail, ip, page, now)
    )
    # 裁剪：每个 user_id 只保留最新 200 条
    c.execute('''DELETE FROM user_action_logs WHERE user_id=? AND id NOT IN (
        SELECT id FROM user_action_logs WHERE user_id=? ORDER BY id DESC LIMIT 200
    )''', (user_id, user_id))
    conn.commit()
    conn.close()


def get_action_logs(user_id: int = None, limit: int = 200, offset: int = 0):
    """查询用户操作日志，管理员传 user_id=None 查全部"""
    conn = get_conn()
    c = conn.cursor()
    if user_id is not None:
        c.execute(
            'SELECT * FROM user_action_logs WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (user_id, limit, offset)
        )
    else:
        c.execute(
            'SELECT l.*, u.username as user_display FROM user_action_logs l LEFT JOIN sys_users u ON l.user_id=u.id ORDER BY l.created_at DESC LIMIT ? OFFSET ?',
            (limit, offset)
        )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


