"""
数据库模块 - 负责所有数据的存储和查询
"""
import sqlite3
import os
import hashlib
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'monitor.db')


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
            created_at TEXT NOT NULL
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
    """服务启动时自动将未正常结束的 live 记录修复为 ended"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE live_sessions SET status='ended', end_time=start_time WHERE status='live' AND end_time IS NULL")
    fixed = c.rowcount
    conn.commit()
    conn.close()
    if fixed:
        print(f"🔧 自动修复 {fixed} 条未结束的直播记录（status: live -> ended）")


def create_session(username, room_id=None, owner_user_id=1):
    """创建新的直播会话"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'INSERT INTO live_sessions (owner_user_id, username, room_id, start_time) VALUES (?, ?, ?, ?)',
        (owner_user_id, username, room_id, now)
    )
    session_id = c.lastrowid
    conn.commit()
    conn.close()
    return session_id


def end_session(session_id):
    """结束直播会话"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'UPDATE live_sessions SET end_time=?, status=? WHERE id=?',
        (now, 'ended', session_id)
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


def update_viewers(session_id, viewer_count, like_count, comment_count, total_user=0):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # 更新快照
    c.execute(
        'INSERT INTO metrics_snapshots (session_id, timestamp, viewer_count, like_count, comment_count) VALUES (?, ?, ?, ?, ?)',
        (session_id, now, viewer_count, like_count, comment_count)
    )
    # 更新峰值在线 & 累计观看
    # total_user 是 TikTok 平台提供的真实累计观看人数，只要 > 0 就直接使用
    # viewer_count 是实时在线人数，不能用作累计
    real_total = total_user if total_user and total_user > 0 else 0
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

    # 获取评论列表（含语言检测）
    c.execute('SELECT * FROM comments WHERE session_id=? ORDER BY timestamp DESC LIMIT 100', (session_id,))
    comments_raw = [dict(r) for r in c.fetchall()]

    # 对评论做语言检测（纯本地，不走网络，毫秒级完成）
    # 翻译直接用数据库存的 text_zh 字段（监控时已实时翻译存入），不在此重复翻译
    try:
        from src.lang_detect import detect_language
        comments = []
        for cm in comments_raw:
            if not cm.get('is_anchor') and cm.get('content'):
                li = detect_language(cm['content'])
                cm['lang'] = li.get('lang', 'other')
                cm['lang_short'] = li.get('lang_short', '?')
                cm['css_class'] = li.get('css_class', 'lang-other')
                cm['dialect'] = li.get('dialect')
                cm['flag'] = li.get('flag', '')
                # text_zh 直接用数据库已存的值，没有就空字符串
                if not cm.get('text_zh'):
                    cm['text_zh'] = ''
            comments.append(cm)
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
    raw_keywords = [w for w, _ in word_freq.most_common(15)]

    # 关键词翻译：非中文词尝试翻译为中文
    keywords = []
    try:
        from src.translator import translate_to_zh
    except Exception:
        try:
            from translator import translate_to_zh
        except Exception:
            translate_to_zh = None

    for kw in raw_keywords:
        zh = ''
        is_chinese = all('\u4e00' <= c <= '\u9fff' for c in kw if c.isalpha())
        if not is_chinese and translate_to_zh:
            try:
                translated = translate_to_zh(kw)
                if translated and translated.strip() and translated.strip().lower() != kw.lower():
                    zh = translated.strip()
            except Exception:
                pass
        keywords.append({'word': kw, 'zh': zh, 'count': word_freq.get(kw, 1)})

    # 关键句：含高频词的句子，且长度 > 10 字符，去重取 Top10
    def score_sentence(s):
        words_in_s = [w.lower() for w in word_pat.findall(s)]
        return sum(word_freq.get(w, 0) for w in words_in_s)

    seen = set()
    scored = []
    for t in all_texts:
        t_stripped = t.strip()
        if len(t_stripped) < 8 or t_stripped in seen:
            continue
        seen.add(t_stripped)
        scored.append((score_sentence(t_stripped), t_stripped))
    scored.sort(key=lambda x: -x[0])
    summary = [s for _, s in scored[:10]]

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
    c.execute('SELECT id, username, is_admin, real_name, status, created_at FROM sys_users ORDER BY id')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


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

