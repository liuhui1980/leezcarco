"""
Flask Web 服务 + SocketIO 实时看板
"""
import logging
import os
import subprocess
import threading
import re
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file

# ── 版本管理 ──
def _read_version():
    """读取 VERSION 文件中的版本号"""
    try:
        _vf = os.path.join(os.path.dirname(__file__), 'VERSION')
        with open(_vf, 'r') as f:
            return f.read().strip()
    except Exception:
        return '1.0.0'

def _read_changelog():
    """读取 CHANGELOG.md 内容"""
    try:
        _cf = os.path.join(os.path.dirname(__file__), 'CHANGELOG.md')
        with open(_cf, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''

APP_VERSION = _read_version()

# ── config 路径兼容：支持 config/config.py 或根目录 config.py ──
import sys as _sys
_base = os.path.dirname(os.path.abspath(__file__))
_config_dir = os.path.join(_base, 'config')
if os.path.isdir(_config_dir) and _config_dir not in _sys.path:
    _sys.path.insert(0, _config_dir)  # config/config.py 优先

from flask_socketio import SocketIO
from src.database import (
    init_db, get_all_sessions, get_session_summary, get_active_sessions,
    get_speech_summary, get_review_data, set_account_group, get_account_group,
    delete_session, delete_account_sessions,
    verify_user, create_user, get_all_users, update_user_password, delete_user,
    set_user_status,
    get_auto_monitor_list, upsert_auto_monitor, delete_auto_monitor,
    toggle_auto_monitor, get_enabled_auto_monitors,
    get_follower_snapshots, get_latest_follower_snapshot, get_all_rival_usernames,
    calc_anchor_score, get_anchor_score_history, get_timeslot_heatmap, get_rival_speech_compare
)
from src.monitor import start_monitor, stop_monitor, get_active_usernames, get_live_usernames, get_monitors_snapshot
from src.rival_tracker import start_rival_tracker, trigger_snapshot_now

# ── Cloudflare Tunnel 状态 ──
_tunnel_url = None
_tunnel_proc = None
_tunnel_lock = threading.Lock()


def _start_cloudflare_tunnel(port=5001):
    """启动 cloudflared quick tunnel，解析公网 URL 并存入 _tunnel_url"""
    global _tunnel_url, _tunnel_proc
    cloudflared = None
    for path in ['/opt/homebrew/bin/cloudflared', '/usr/local/bin/cloudflared', 'cloudflared']:
        try:
            result = subprocess.run([path, 'version'], capture_output=True, timeout=3)
            if result.returncode == 0:
                cloudflared = path
                break
        except Exception:
            continue
    if not cloudflared:
        logger.warning('cloudflared 未安装，远程访问功能不可用')
        return
    try:
        proc = subprocess.Popen(
            [cloudflared, 'tunnel', '--url', f'http://localhost:{port}'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        with _tunnel_lock:
            _tunnel_proc = proc
        url_pat = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')
        for line in proc.stdout:
            m = url_pat.search(line)
            if m:
                with _tunnel_lock:
                    _tunnel_url = m.group(0)
                logger.info(f'🌐 Cloudflare Tunnel 已建立: {_tunnel_url}')
                # 广播给前端
                try:
                    socketio.emit('tunnel_url', {'url': _tunnel_url})
                except Exception:
                    pass
                break
        # 继续读输出防止 pipe 阻塞
        for _ in proc.stdout:
            pass
    except Exception as e:
        logger.warning(f'Cloudflare Tunnel 启动失败: {e}')

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# 初始化应用
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = 'tiktok-monitor-secret-2024-v2'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# 初始化数据库
init_db()


# ==================== 全局模板变量 ====================

@app.context_processor
def inject_globals():
    """向所有模板注入全局变量（版本号等）"""
    return {'app_version': APP_VERSION}


# ==================== 版本 API ====================

@app.route('/api/version')
def api_version():
    """返回当前版本信息"""
    return jsonify({
        'version': APP_VERSION,
        'changelog': _read_changelog()
    })


# ==================== 鉴权辅助 ====================

def get_current_user():
    """从 session 获取当前登录用户信息，未登录返回 None"""
    uid = session.get('user_id')
    if not uid:
        return None
    return {
        'id': uid,
        'username': session.get('username'),
        'is_admin': session.get('is_admin', False)
    }


def login_required(f):
    """装饰器：未登录重定向到登录页"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """装饰器：非管理员返回403"""
    @wraps(f)
    def decorated(*args, **kwargs):
        u = get_current_user()
        if not u:
            return redirect(url_for('login_page'))
        if not u['is_admin']:
            return jsonify({'success': False, 'msg': '无权限，需要管理员账号'}), 403
        return f(*args, **kwargs)
    return decorated


# ==================== HTTP 路由 ====================

@app.route('/login', methods=['GET'])
def login_page():
    """登录页面"""
    if get_current_user():
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """登录接口"""
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    if not username or not password:
        return jsonify({'success': False, 'msg': '用户名和密码不能为空'}), 400
    user = verify_user(username, password)
    if not user:
        return jsonify({'success': False, 'msg': '用户名或密码错误'}), 401
    # 审核状态检查
    status = user.get('status', 'active')
    if status == 'pending':
        return jsonify({'success': False, 'msg': '账号正在等待管理员审核，请耐心等待'}), 403
    if status == 'disabled':
        return jsonify({'success': False, 'msg': '账号已被禁用，请联系管理员'}), 403
    session.permanent = True
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['is_admin'] = bool(user['is_admin'])
    return jsonify({'success': True, 'username': user['username'], 'is_admin': bool(user['is_admin'])})


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """登出"""
    session.clear()
    return jsonify({'success': True})


@app.route('/api/auth/register', methods=['POST'])
def api_register():
    """自助注册接口 — 注册后进入待审核状态，需管理员批准后才能登录"""
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    real_name = (data.get('real_name') or '').strip()
    import re
    if not username or not password:
        return jsonify({'success': False, 'msg': '用户名和密码不能为空'}), 400
    if not real_name:
        return jsonify({'success': False, 'msg': '请填写真实姓名'}), 400
    if not re.match(r'^\w{4,20}$', username):
        return jsonify({'success': False, 'msg': '用户名需4-20位字母/数字/下划线'}), 400
    if len(password) < 6:
        return jsonify({'success': False, 'msg': '密码至少6位'}), 400
    ok, result = create_user(username, password, is_admin=0, real_name=real_name, status='pending')
    if ok:
        return jsonify({'success': True, 'pending': True, 'msg': '注册申请已提交，等待管理员审核后即可登录'})
    return jsonify({'success': False, 'msg': result}), 400


@app.route('/api/auth/me')
def api_me():
    """获取当前登录用户信息"""
    u = get_current_user()
    if not u:
        return jsonify({'logged_in': False}), 401
    return jsonify({'logged_in': True, 'username': u['username'], 'is_admin': u['is_admin']})


@app.route('/api/auth/change_password', methods=['POST'])
@login_required
def api_change_password():
    """修改自己的密码"""
    u = get_current_user()
    data = request.get_json() or {}
    old_pw = (data.get('old_password') or '').strip()
    new_pw = (data.get('new_password') or '').strip()
    if not old_pw or not new_pw:
        return jsonify({'success': False, 'msg': '旧密码和新密码不能为空'}), 400
    if len(new_pw) < 6:
        return jsonify({'success': False, 'msg': '新密码至少6位'}), 400
    # 验证旧密码
    verified = verify_user(u['username'], old_pw)
    if not verified:
        return jsonify({'success': False, 'msg': '旧密码不正确'}), 401
    update_user_password(u['id'], new_pw)
    return jsonify({'success': True, 'msg': '密码修改成功'})


# ── 管理员：用户管理 ──

@app.route('/admin')
@login_required
def admin_page():
    """管理员后台页面"""
    u = get_current_user()
    if not u['is_admin']:
        return redirect(url_for('index'))
    return render_template('admin.html')


@app.route('/api/admin/users')
@admin_required
def api_admin_users():
    """获取所有系统用户（含使用摘要统计 + 在线状态）"""
    users = get_all_users()
    # 补充每位用户的使用统计
    try:
        from src.database import get_conn
        from src.monitor import get_active_usernames
        conn = get_conn()
        today_str = datetime.now().strftime('%Y-%m-%d')

        # 获取当前在监控的账号 → 反查用户
        active_unames = set(get_active_usernames())

        for u in users:
            uid = u['id']
            # 监控场次总数
            row = conn.execute(
                'SELECT COUNT(*) as cnt FROM live_sessions WHERE owner_user_id=?', (uid,)
            ).fetchone()
            u['session_count'] = row['cnt'] if row else 0
            # 今日场次
            row_today = conn.execute(
                "SELECT COUNT(*) as cnt FROM live_sessions WHERE owner_user_id=? AND start_time >= ?",
                (uid, today_str)
            ).fetchone()
            u['today_session_count'] = row_today['cnt'] if row_today else 0
            # 最近一次监控时间
            row2 = conn.execute(
                'SELECT start_time FROM live_sessions WHERE owner_user_id=? ORDER BY id DESC LIMIT 1', (uid,)
            ).fetchone()
            u['last_active'] = row2['start_time'] if row2 else None
            # 监控账号数（distinct username）
            row3 = conn.execute(
                'SELECT COUNT(DISTINCT username) as cnt FROM live_sessions WHERE owner_user_id=?', (uid,)
            ).fetchone()
            u['account_count'] = row3['cnt'] if row3 else 0
            # 该用户管理的账号组数量
            row4 = conn.execute(
                'SELECT COUNT(*) as cnt FROM account_groups WHERE owner_user_id=?', (uid,)
            ).fetchone()
            u['group_account_count'] = row4['cnt'] if row4 else 0
            # 该用户当前在监控的账号（is_online）
            c2 = conn.execute(
                'SELECT username FROM account_groups WHERE owner_user_id=?', (uid,)
            ).fetchall()
            owned_unames = {r['username'] for r in c2}
            online_unames = owned_unames & active_unames
            u['is_online'] = len(online_unames) > 0
            u['online_accounts'] = list(online_unames)
            u['online_count'] = len(online_unames)
        conn.close()
    except Exception as e:
        logger.warning(f'获取用户统计失败: {e}')
    return jsonify({'users': users})


@app.route('/api/admin/users/create', methods=['POST'])
@admin_required
def api_admin_create_user():
    """创建新用户"""
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    if not username or not password:
        return jsonify({'success': False, 'msg': '用户名和密码不能为空'}), 400
    if len(password) < 6:
        return jsonify({'success': False, 'msg': '密码至少6位'}), 400
    ok, result = create_user(username, password, is_admin=0)
    if ok:
        return jsonify({'success': True, 'user_id': result})
    return jsonify({'success': False, 'msg': result}), 400


@app.route('/api/admin/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def api_admin_delete_user(user_id):
    """删除用户（不能删管理员）"""
    u = get_current_user()
    if user_id == u['id']:
        return jsonify({'success': False, 'msg': '不能删除自己'}), 400
    ok = delete_user(user_id)
    if ok:
        return jsonify({'success': True})
    return jsonify({'success': False, 'msg': '删除失败（可能是管理员账号）'}), 400


@app.route('/api/admin/users/<int:user_id>/reset_password', methods=['POST'])
@admin_required
def api_admin_reset_password(user_id):
    """管理员重置某用户密码"""
    data = request.get_json() or {}
    new_pw = (data.get('new_password') or '').strip()
    if not new_pw or len(new_pw) < 6:
        return jsonify({'success': False, 'msg': '新密码至少6位'}), 400
    update_user_password(user_id, new_pw)
    return jsonify({'success': True})


@app.route('/api/admin/users/<int:user_id>/approve', methods=['POST'])
@admin_required
def api_admin_approve_user(user_id):
    """批准注册申请"""
    set_user_status(user_id, 'active')
    return jsonify({'success': True, 'msg': '已批准，用户现在可以登录'})


@app.route('/api/admin/users/<int:user_id>/reject', methods=['POST'])
@admin_required
def api_admin_reject_user(user_id):
    """拒绝注册申请（禁用该账号）"""
    set_user_status(user_id, 'disabled')
    return jsonify({'success': True, 'msg': '已拒绝，账号已禁用'})


@app.route('/api/admin/users/<int:user_id>/disable', methods=['POST'])
@admin_required
def api_admin_disable_user(user_id):
    """禁用已激活的用户"""
    u = get_current_user()
    if user_id == u['id']:
        return jsonify({'success': False, 'msg': '不能禁用自己'}), 400
    set_user_status(user_id, 'disabled')
    return jsonify({'success': True})


@app.route('/')
@login_required
def index():
    """主看板页面"""
    return render_template('index.html')


@app.route('/rivals')
@login_required
def rivals_page():
    """竞品分析页面"""
    return render_template('rivals.html')


@app.route('/history')
@login_required
def history():
    """历史记录页面"""
    return render_template('history.html')


@app.route('/compare')
@login_required
def compare_page():
    """自营号比较分析页面"""
    return render_template('compare.html')


@app.route('/session/<int:session_id>')
@login_required
def session_detail(session_id):
    """单场直播详情页"""
    return render_template('session_detail.html', session_id=session_id)


# ==================== API 接口 ====================

@app.route('/api/status')
@login_required
def api_status():
    """系统状态"""
    return jsonify({
        'active_accounts': get_active_usernames(),
        'active_sessions': get_active_sessions()
    })


@app.route('/api/sessions')
@login_required
def api_sessions():
    """历史会话列表（按当前用户过滤）"""
    u = get_current_user()
    uid = None if u['is_admin'] else u['id']
    sessions = get_all_sessions(limit=100, owner_user_id=uid)
    return jsonify(sessions)


@app.route('/api/session/<int:session_id>')
@login_required
def api_session_detail(session_id):
    """单场直播详情"""
    summary = get_session_summary(session_id)
    if summary is None:
        return jsonify({'error': '记录不存在'}), 404
    return jsonify(summary)


@app.route('/api/check_live', methods=['POST'])
@login_required
def api_check_live():
    """检查某账号是否正在直播"""
    import asyncio
    from TikTokLive import TikTokLiveClient
    data = request.get_json()
    username = data.get('username', '').strip().lstrip('@')
    if not username:
        return jsonify({'success': False, 'msg': '用户名不能为空'})

    async def _check():
        client = TikTokLiveClient(unique_id=username)
        try:
            is_live = await client.is_live()
            return {'exists': True, 'is_live': is_live}
        except Exception as e:
            err = str(e)
            if 'NotFound' in type(e).__name__ or 'not found' in err.lower():
                return {'exists': False, 'is_live': False}
            return {'exists': True, 'is_live': False}

    result = asyncio.run(_check())
    return jsonify({
        'success': True,
        'username': username,
        'exists': result['exists'],
        'is_live': result['is_live'],
        'msg': (
            f'@{username} 正在直播 🔴' if result['is_live']
            else f'@{username} 当前未开播，加入后会自动等待开播'
            if result['exists']
            else f'用户 @{username} 不存在，请检查用户名'
        )
    })



@app.route('/api/monitor/start', methods=['POST'])
@login_required
def api_start_monitor():
    """启动监控"""
    data = request.get_json() or {}
    username = data.get('username', '').strip().lstrip('@')
    if not username:
        return jsonify({'success': False, 'msg': '用户名不能为空'}), 400

    result = start_monitor(username, socketio=socketio)
    if result:
        return jsonify({'success': True, 'msg': f'已开始监控 @{username}'})
    else:
        return jsonify({'success': False, 'msg': f'@{username} 已在监控中或启动失败'})


@app.route('/api/monitor/stop', methods=['POST'])
@login_required
def api_stop_monitor():
    """停止监控"""
    data = request.get_json() or {}
    username = data.get('username', '').strip().lstrip('@')
    result = stop_monitor(username)
    if result:
        return jsonify({'success': True, 'msg': f'已停止监控 @{username}'})
    else:
        return jsonify({'success': False, 'msg': f'@{username} 不在监控列表中'})


@app.route('/api/monitor/batch_start', methods=['POST'])
@login_required
def api_batch_start():
    """批量启动监控"""
    data = request.get_json() or {}
    usernames = data.get('usernames', [])
    results = []
    for u in usernames:
        u = u.strip().lstrip('@')
        if u:
            ok = start_monitor(u, socketio=socketio)
            results.append({'username': u, 'success': ok})
    return jsonify({'results': results})


@app.route('/api/session/<int:session_id>/delete', methods=['POST'])
@login_required
def api_delete_session(session_id):
    """删除一条历史记录"""
    ok = delete_session(session_id)
    return jsonify({'success': ok})


@app.route('/api/account/<username>/delete', methods=['POST'])
@login_required
def api_delete_account_sessions(username):
    """删除某账号的所有历史场次"""
    u = get_current_user()
    username = username.strip().lstrip('@')
    uid = None if u['is_admin'] else u['id']
    count = delete_account_sessions(username, owner_user_id=uid)
    return jsonify({'success': True, 'deleted': count})


@app.route('/api/session/<int:session_id>/speech')
@login_required
def api_session_speech(session_id):
    """单场话术全量（含翻译和关键句总结）"""
    data = get_speech_summary(session_id)
    return jsonify(data)


@app.route('/api/session/<int:session_id>/ai_summary', methods=['POST'])
@login_required
def api_session_ai_summary(session_id):
    """AI 智能总结（话术或评论），优先 Gemini → 降级 Pollinations.ai 免费 AI"""
    req = request.get_json(force=True) or {}
    summary_type = req.get('type', 'speech')  # 'speech' 或 'comment'

    try:
        from src.gemini_api import summarize_speech, summarize_comments
        from src.database import get_speech_summary as db_speech, get_session_summary

        if summary_type == 'speech':
            sp = db_speech(session_id)
            summary = summarize_speech(sp.get('records', []))
        else:
            sess_data = get_session_summary(session_id)
            comments = sess_data.get('comments', [])
            summary = summarize_comments(comments)

        if not summary:
            return jsonify({'success': False, 'summary': '', 'msg': 'AI 暂时不可用，请稍后重试'})
        return jsonify({'success': True, 'summary': summary})
    except Exception as e:
        logger.error(f'AI 总结失败: {e}')
        return jsonify({'success': False, 'summary': '', 'msg': str(e)})


@app.route('/api/card/ai_summary', methods=['POST'])
@login_required
def api_card_ai_summary():
    """卡片实时 AI 摘要（传入话术/评论文本列表，直接总结）"""
    req = request.get_json(force=True) or {}
    speech_texts = req.get('speech', [])
    comment_texts = req.get('comments', [])

    result = {'speech_summary': '', 'comment_summary': ''}
    try:
        from src.gemini_api import call_ai

        if speech_texts and len(speech_texts) >= 3:
            combined = '\n'.join(speech_texts[:30])
            prompt = f"""以下是 TikTok 直播主播的话术片段：

{combined}

请用中文写一句简洁的摘要（60字以内），概括主播正在说什么/推什么。
只输出摘要，不要加标题。"""
            result['speech_summary'] = call_ai(prompt, max_tokens=120) or ''

        if comment_texts and len(comment_texts) >= 5:
            combined = '\n'.join(comment_texts[:40])
            prompt = f"""以下是 TikTok 直播间的观众评论：

{combined}

请用中文写一句简洁的摘要（60字以内），说明观众当前最关注什么。
只输出摘要，不要加标题。"""
            result['comment_summary'] = call_ai(prompt, max_tokens=120) or ''

    except Exception as e:
        logger.warning(f'卡片 AI 摘要失败: {e}')

    return jsonify(result)




@app.route('/api/session/<int:session_id>/download_speech')
@login_required
def api_download_speech(session_id):
    """下载话术 Word 文档"""
    try:
        import io
        from src.word_export import export_speech_docx
        from src.database import get_speech_summary as db_speech, get_session_by_id

        sp = db_speech(session_id)
        sess = get_session_by_id(session_id) or {}
        docx_bytes = export_speech_docx(sp.get('records', []), session_info=sess)

        username = sess.get('username', 'session')
        start = (sess.get('start_time', '') or '')[:10].replace('-', '')
        filename = f'话术_{username}_{start or session_id}.docx'

        return send_file(
            io.BytesIO(docx_bytes),
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f'Word 下载失败: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/session/<int:session_id>/download_comments')
@login_required
def api_download_comments(session_id):
    """下载评论 Word 文档"""
    try:
        import io
        from src.word_export import export_comments_docx
        from src.database import get_session_summary, get_session_by_id

        sess_data = get_session_summary(session_id)
        sess = get_session_by_id(session_id) or {}
        comments = sess_data.get('comments', [])
        docx_bytes = export_comments_docx(comments, session_info=sess)

        username = sess.get('username', 'session')
        start = (sess.get('start_time', '') or '')[:10].replace('-', '')
        filename = f'评论_{username}_{start or session_id}.docx'

        return send_file(
            io.BytesIO(docx_bytes),
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f'Word 下载失败: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/session/<int:session_id>/review')
@login_required
def api_session_review(session_id):
    """主播复盘对比数据（本场 vs 上场 vs 近7场均值 vs 历史最佳）"""
    data = get_review_data(session_id)
    return jsonify(data)


@app.route('/api/accounts')
@login_required
def api_accounts():
    """返回所有历史账号列表 + 当前直播中的账号（用于下拉选择）"""
    u = get_current_user()
    uid = None if u['is_admin'] else u['id']
    sessions = get_all_sessions(limit=500, owner_user_id=uid)
    seen = {}
    for s in sessions:
        un = s.get('username', '')
        if un and un not in seen:
            seen[un] = s.get('status') == 'live'
    active_now = set(get_live_usernames())
    result = [
        {'username': un, 'is_live': (un in active_now)}
        for un, _is_live in seen.items()
    ]
    result.sort(key=lambda x: (0 if x['is_live'] else 1, x['username']))
    return jsonify(result)


@app.route('/api/account/group', methods=['POST'])
@login_required
def api_set_account_group():
    """设置账号分组"""
    u = get_current_user()
    data = request.get_json() or {}
    username = data.get('username', '').strip().lstrip('@')
    group = data.get('group', 'own')
    if not username:
        return jsonify({'success': False, 'msg': '用户名不能为空'}), 400
    if group not in ('own', 'rival', 'watch'):
        return jsonify({'success': False, 'msg': '分组无效'}), 400
    set_account_group(username, group, owner_user_id=u['id'])
    return jsonify({'success': True})


@app.route('/api/rivals')
@login_required
def api_rivals():
    """返回所有竞品账号 + 历史均值数据"""
    u = get_current_user()
    from src.database import get_conn
    conn = get_conn()
    try:
        c = conn.cursor()
        uid = u['id'] if not u['is_admin'] else None
        if uid:
            c.execute("SELECT username, display_name FROM account_groups WHERE group_name='rival' AND owner_user_id=?", (uid,))
        else:
            c.execute("SELECT username, display_name FROM account_groups WHERE group_name='rival'")
        rival_rows = [(r['username'], r['display_name']) for r in c.fetchall()]
        active_now = set(get_live_usernames())
        result = []
        for username, display_name in rival_rows:
            c.execute(
                "SELECT id, peak_viewers, total_viewers, total_gift_value, total_comments, new_followers, start_time, end_time FROM live_sessions WHERE username=? AND status='ended' ORDER BY start_time DESC LIMIT 10",
                (username,)
            )
            sessions = [dict(r) for r in c.fetchall()]
            n = len(sessions)
            # 计算均值、最佳、近3场
            def avg(key): return round(sum(s[key] or 0 for s in sessions) / n) if n else 0
            def best(key): return max((s[key] or 0 for s in sessions), default=0)
            def dur(s):
                try:
                    from datetime import datetime as dt
                    a = dt.fromisoformat(s['start_time'])
                    b = dt.fromisoformat(s['end_time'])
                    return round((b - a).total_seconds() / 60)
                except: return 0
            avg_dur = round(sum(dur(s) for s in sessions) / n) if n else 0
            # 粉丝快照数据
            from src.database import get_latest_follower_snapshot, get_follower_snapshots
            latest_snap = get_latest_follower_snapshot(username)
            follower_count = latest_snap['follower_count'] if latest_snap else 0
            # 7天涨粉
            snaps_7d = get_follower_snapshots(username, days=8)
            follower_growth_7d = 0
            if len(snaps_7d) >= 2:
                follower_growth_7d = snaps_7d[0]['follower_count'] - snaps_7d[-1]['follower_count']
            result.append({
                'username': username,
                'display_name': display_name or username,
                'is_live': username in active_now,
                'session_count': n,
                'follower_count': follower_count,
                'follower_growth_7d': follower_growth_7d,
                'avg_peak_viewers': avg('peak_viewers'),
                'avg_total_viewers': avg('total_viewers'),
                'avg_gift_value': avg('total_gift_value'),
                'avg_comments': avg('total_comments'),
                'avg_followers': avg('new_followers'),
                'avg_duration_min': avg_dur,
                'best_peak_viewers': best('peak_viewers'),
                'best_gift_value': best('total_gift_value'),
                'recent_sessions': [{'id': s['id'], 'peak': s['peak_viewers'], 'gift': s['total_gift_value'], 'start': s['start_time']} for s in sessions[:5]],
            })
        result.sort(key=lambda x: (0 if x['is_live'] else 1, -x['avg_peak_viewers']))
        return jsonify({'rivals': result})
    finally:
        conn.close()


@app.route('/api/compare')
@login_required
def api_compare():
    """对比：自营账号 vs 竞品账号，近N场均值横向对比"""
    u = get_current_user()
    uid = u['id'] if not u['is_admin'] else None
    from src.database import get_conn
    conn = get_conn()
    try:
        c = conn.cursor()

        active_now = set(get_live_usernames())

        def get_stats(username, limit=10):
            c.execute(
                "SELECT id, peak_viewers, total_viewers, total_gift_value, total_comments, new_followers, start_time, end_time FROM live_sessions WHERE username=? AND status='ended' ORDER BY start_time DESC LIMIT ?",
                (username, limit)
            )
            sessions = [dict(r) for r in c.fetchall()]
            n = len(sessions)
            if not n:
                return None
            def avg(key): return round(sum(s[key] or 0 for s in sessions) / n, 1)
            def dur(s):
                try:
                    from datetime import datetime as dt
                    return round((dt.fromisoformat(s['end_time']) - dt.fromisoformat(s['start_time'])).total_seconds() / 60)
                except: return 0
            # 近期场次（旧→新，最多10条，含 peak/gift/start_time）
            recent = [
                {
                    'id': s['id'],
                    'peak': s['peak_viewers'] or 0,
                    'gift': s['total_gift_value'] or 0,
                    'start': (s['start_time'] or '')[:10],
                }
                for s in reversed(sessions)
            ]
            return {
                'username': username,
                'is_live': username in active_now,
                'session_count': n,
                'avg_peak': avg('peak_viewers'),
                'avg_total': avg('total_viewers'),
                'avg_gift': round(sum(s['total_gift_value'] or 0 for s in sessions) / n, 2),
                'avg_comments': avg('total_comments'),
                'avg_followers': avg('new_followers'),
                'avg_duration': round(sum(dur(s) for s in sessions) / n),
                'recent_sessions': recent,
            }

        # 自营账号
        if uid:
            c.execute("SELECT username FROM account_groups WHERE group_name='own' AND owner_user_id=?", (uid,))
        else:
            c.execute("SELECT username FROM account_groups WHERE group_name='own'")
        own_users = [r['username'] for r in c.fetchall()]
        # 竞品账号
        if uid:
            c.execute("SELECT username FROM account_groups WHERE group_name='rival' AND owner_user_id=?", (uid,))
        else:
            c.execute("SELECT username FROM account_groups WHERE group_name='rival'")
        rival_users = [r['username'] for r in c.fetchall()]

        own_stats = [s for s in (get_stats(un) for un in own_users) if s]
        rival_stats = [s for s in (get_stats(un) for un in rival_users) if s]

        return jsonify({'own': own_stats, 'rivals': rival_stats})
    finally:
        conn.close()


@app.route('/api/tunnel')
@login_required
def api_tunnel():
    """获取当前 Cloudflare Tunnel 公网 URL"""
    with _tunnel_lock:
        url = _tunnel_url
    return jsonify({'url': url, 'local': 'http://localhost:5001'})


@app.route('/api/rivals/save', methods=['POST'])
@login_required
def api_save_rivals():
    """保存竞品账号列表（持久化到 account_groups）"""
    u = get_current_user()
    data = request.get_json() or {}
    usernames = data.get('usernames', [])
    saved = []
    for un in usernames:
        un = un.strip().lstrip('@').lower()
        if un:
            set_account_group(un, 'rival', owner_user_id=u['id'])
            saved.append(un)
    return jsonify({'success': True, 'saved': saved})


@app.route('/api/rivals/remove', methods=['POST'])
@login_required
def api_remove_rival():
    """真正移除竞品（从 account_groups 删除，历史数据保留）"""
    u = get_current_user()
    data = request.get_json() or {}
    username = (data.get('username') or '').strip().lstrip('@').lower()
    if not username:
        return jsonify({'success': False, 'msg': '用户名不能为空'}), 400
    from src.database import get_conn
    conn = get_conn()
    conn.execute(
        "DELETE FROM account_groups WHERE username=? AND owner_user_id=? AND group_name='rival'",
        (username, u['id'])
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'msg': f'@{username} 已从竞品列表移除（历史数据保留）'})


@app.route('/rival/<username>')
@login_required
def rival_detail_page(username):
    """竞品详情页"""
    return render_template('rival_detail.html', rival_username=username)


@app.route('/api/rival/<username>/detail')
@login_required
def api_rival_detail(username):
    """竞品详情：历史场次 + 粉丝趋势 + 开播规律"""
    username = username.strip().lstrip('@').lower()
    from src.database import get_conn, get_follower_snapshots, get_latest_follower_snapshot
    conn = get_conn()
    try:
        c = conn.cursor()
        # 历史场次（最近30场）
        c.execute('''
            SELECT id, start_time, end_time, peak_viewers, total_viewers,
                   total_gift_value, total_comments, new_followers
            FROM live_sessions
            WHERE username=? AND status='ended'
            ORDER BY start_time DESC LIMIT 30
        ''', (username,))
        sessions = []
        for r in c.fetchall():
            s = dict(r)
            # 计算时长
            try:
                from datetime import datetime as dt
                st = dt.fromisoformat(s['start_time'])
                et = dt.fromisoformat(s['end_time'])
                s['duration_min'] = round((et - st).total_seconds() / 60)
                s['hour'] = st.hour  # 开播时段
                s['weekday'] = st.weekday()  # 星期几 0=周一
            except Exception:
                s['duration_min'] = 0
                s['hour'] = 0
                s['weekday'] = 0
            sessions.append(s)

        # 开播规律分析
        from collections import Counter
        hour_dist = Counter(s['hour'] for s in sessions)
        weekday_dist = Counter(s['weekday'] for s in sessions)
        weekday_names = ['周一','周二','周三','周四','周五','周六','周日']

        # 粉丝快照趋势
        snapshots = get_follower_snapshots(username, days=30)
        snapshots.reverse()  # 旧→新
        latest_snapshot = get_latest_follower_snapshot(username)

        # 7天涨粉计算
        follower_growth_7d = 0
        if len(snapshots) >= 2:
            recent = [s for s in snapshots if s['snapshot_date'] >= (datetime.now().strftime('%Y-%m-%d')[:8] + '01')]
            if len(recent) >= 2:
                follower_growth_7d = recent[-1]['follower_count'] - recent[0]['follower_count']

        n = len(sessions)
        def avg(key): return round(sum(s.get(key, 0) or 0 for s in sessions) / n) if n else 0

        avg_duration = round(sum(s['duration_min'] for s in sessions) / n) if n else 0

        return jsonify({
            'username': username,
            'session_count': n,
            'avg_peak_viewers': avg('peak_viewers'),
            'avg_total_viewers': avg('total_viewers'),
            'avg_gift_value': avg('total_gift_value'),
            'avg_comments': avg('total_comments'),
            'avg_followers': avg('new_followers'),
            'avg_duration_min': avg_duration,
            'best_peak_viewers': max((s.get('peak_viewers', 0) or 0 for s in sessions), default=0),
            'sessions': sessions,
            'hour_dist': [{'hour': h, 'count': hour_dist.get(h, 0)} for h in range(24)],
            'weekday_dist': [{'day': weekday_names[i], 'count': weekday_dist.get(i, 0)} for i in range(7)],
            'follower_snapshots': snapshots,
            'latest_snapshot': latest_snapshot,
            'follower_growth_7d': follower_growth_7d,
        })
    finally:
        conn.close()


@app.route('/api/rival/<username>/fetch_profile', methods=['POST'])
@login_required
def api_rival_fetch_profile(username):
    """立即抓取某竞品账号的最新粉丝数"""
    username = username.strip().lstrip('@').lower()
    from src.rival_tracker import fetch_tiktok_profile
    from src.database import save_follower_snapshot
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
    return jsonify(profile)


@app.route('/api/rivals/refresh_all_profiles', methods=['POST'])
@login_required
def api_rivals_refresh_profiles():
    """触发所有竞品账号粉丝数快照"""
    result = trigger_snapshot_now()
    return jsonify(result)


# ==================== 自动监控列表 ====================

@app.route('/automonitor')
@login_required
def automonitor_page():
    """自动监控列表管理页"""
    return render_template('automonitor.html')


@app.route('/api/automonitor/list')
@login_required
def api_automonitor_list():
    """获取当前用户的自动监控列表"""
    u = get_current_user()
    uid = None if u['is_admin'] else u['id']
    rows = get_auto_monitor_list(owner_user_id=uid)
    active_now = set(get_live_usernames())
    for r in rows:
        r['is_live'] = r['username'] in active_now
    return jsonify({'list': rows})


@app.route('/api/automonitor/import', methods=['POST'])
@login_required
def api_automonitor_import():
    """批量导入自动监控账号"""
    u = get_current_user()
    data = request.get_json() or {}
    accounts = data.get('accounts', [])  # [{username, display_name, group_name, note}]
    saved = []
    for a in accounts:
        un = (a.get('username') or '').strip().lstrip('@').lower()  # 统一小写，防止大小写重复
        if not un:
            continue
        upsert_auto_monitor(
            owner_user_id=u['id'],
            username=un,
            display_name=a.get('display_name', ''),
            group_name=a.get('group_name', 'own'),
            enabled=1,
            note=a.get('note', '')
        )
        # 同时加入账号分组
        set_account_group(un, a.get('group_name', 'own'), owner_user_id=u['id'])
        saved.append(un)
    return jsonify({'success': True, 'saved': saved, 'count': len(saved)})


@app.route('/api/automonitor/delete', methods=['POST'])
@login_required
def api_automonitor_delete():
    """从自动监控列表移除"""
    u = get_current_user()
    data = request.get_json() or {}
    username = (data.get('username') or '').strip().lstrip('@')
    delete_auto_monitor(u['id'], username)
    return jsonify({'success': True})


@app.route('/api/automonitor/toggle', methods=['POST'])
@login_required
def api_automonitor_toggle():
    """启用/禁用自动监控"""
    u = get_current_user()
    data = request.get_json() or {}
    username = (data.get('username') or '').strip().lstrip('@')
    enabled = bool(data.get('enabled', True))
    toggle_auto_monitor(u['id'], username, enabled)
    return jsonify({'success': True})


@app.route('/api/automonitor/start_all', methods=['POST'])
@login_required
def api_automonitor_start_all():
    """一键启动所有已启用的自动监控账号"""
    u = get_current_user()
    rows = get_enabled_auto_monitors(owner_user_id=u['id'])
    results = []
    for r in rows:
        un = r['username']
        group = r.get('group_name', 'own')
        ok = start_monitor(un, socketio=socketio, is_auto=True, group_name=group)
        results.append({'username': un, 'started': ok})
    return jsonify({'success': True, 'results': results})


@app.route('/api/rivals/recommend')
@login_required
def api_rivals_recommend():
    """竞品智能推荐：基于自营账号的历史话术关键词和语言特征，推荐可能的竞品候选"""
    u = get_current_user()
    uid = u['id'] if not u['is_admin'] else None
    from src.database import get_conn
    import re
    from collections import Counter
    conn = get_conn()
    c = conn.cursor()

    # 1. 获取自营账号列表
    if uid:
        c.execute("SELECT username FROM account_groups WHERE group_name='own' AND owner_user_id=?", (uid,))
    else:
        c.execute("SELECT username FROM account_groups WHERE group_name='own'")
    own_users = [r['username'] for r in c.fetchall()]

    # 2. 已有竞品列表（排除）
    if uid:
        c.execute("SELECT username FROM account_groups WHERE group_name='rival' AND owner_user_id=?", (uid,))
    else:
        c.execute("SELECT username FROM account_groups WHERE group_name='rival'")
    rival_users = set(r['username'] for r in c.fetchall())

    if not own_users:
        conn.close()
        return jsonify({'recommendations': [], 'msg': '请先在自动监控列表中添加自营账号', 'profile': {}})

    # 3. 分析自营账号特征：语言分布、高频关键词、常用直播时段
    word_pat = re.compile(r'[\w\u4e00-\u9fff\u0600-\u06ff]+')
    stopwords = {'the','a','an','is','are','i','you','he','she','it','we','they','my','your'}
    all_keywords = Counter()
    lang_dist = Counter()
    hour_dist = Counter()

    for own_un in own_users:
        # 获取该账号最近 5 场话术
        c.execute("""
            SELECT sr.text, sr.lang, ls.start_time
            FROM speech_records sr
            JOIN live_sessions ls ON sr.session_id = ls.id
            WHERE ls.username=? AND ls.status='ended'
            ORDER BY ls.start_time DESC LIMIT 200
        """, (own_un,))
        for row in c.fetchall():
            if row['text']:
                words = [w.lower() for w in word_pat.findall(row['text']) if len(w)>2 and w.lower() not in stopwords]
                all_keywords.update(words)
            if row['lang']:
                lang_dist[row['lang'].split('-')[0]] += 1
            if row['start_time']:
                try:
                    h = int(row['start_time'][11:13])
                    hour_dist[f"{h:02d}:00"] += 1
                except Exception:
                    pass

    top_keywords = [w for w, _ in all_keywords.most_common(20)]
    top_langs = [lang for lang, _ in lang_dist.most_common(3)]
    top_hours = [h for h, _ in hour_dist.most_common(3)]

    # 4. 基于账号名关键词匹配历史记录中的非自营、非竞品账号
    candidate_pool = set()
    if top_keywords and own_users:
        kw_conditions = ' OR '.join(["text LIKE ?" for _ in top_keywords[:5]])
        params = [f'%{kw}%' for kw in top_keywords[:5]]
        c.execute(f"""
            SELECT DISTINCT ls.username
            FROM speech_records sr
            JOIN live_sessions ls ON sr.session_id=ls.id
            WHERE ({kw_conditions}) AND ls.username NOT IN ({','.join('?'*len(own_users))})
            LIMIT 30
        """, params + own_users)
        for r in c.fetchall():
            un = r['username']
            if un not in rival_users and un not in own_users:
                candidate_pool.add(un)
    elif top_keywords:
        kw_conditions = ' OR '.join(["text LIKE ?" for _ in top_keywords[:5]])
        params = [f'%{kw}%' for kw in top_keywords[:5]]
        c.execute(f"""
            SELECT DISTINCT ls.username
            FROM speech_records sr
            JOIN live_sessions ls ON sr.session_id=ls.id
            WHERE ({kw_conditions})
            LIMIT 30
        """, params)
        for r in c.fetchall():
            un = r['username']
            if un not in rival_users:
                candidate_pool.add(un)

    conn.close()

    # 5. 构造推荐结果
    recommendations = []
    for un in list(candidate_pool)[:10]:
        recommendations.append({
            'username': un,
            'reason': f'与你的话术关键词高度重合（{", ".join(top_keywords[:3])}...）',
            'already_rival': un in rival_users,
        })

    profile = {
        'own_accounts': own_users,
        'top_keywords': top_keywords[:10],
        'top_langs': top_langs,
        'top_hours': top_hours,
    }

    return jsonify({'recommendations': recommendations, 'profile': profile})


# ==================== SocketIO 事件 ====================

@socketio.on('connect')
def on_ws_connect():
    logger.info(f'前端已连接: {request.sid}')
    # 连接时推送当前 tunnel URL（如果有）
    from flask_socketio import emit
    with _tunnel_lock:
        url = _tunnel_url
    if url:
        emit('tunnel_url', {'url': url})


@socketio.on('disconnect')
def on_ws_disconnect():
    logger.info(f'前端已断开: {request.sid}')


@socketio.on('request_status')
def on_request_status():
    """前端请求当前状态（页面刷新/重连后恢复看板）"""
    from flask_socketio import emit
    emit('status_update', {
        'active_accounts': get_active_usernames(),
        'active_sessions': get_active_sessions(),
        'monitors_snapshot': get_monitors_snapshot(),  # 完整实时状态快照
    })


# ==================== 主播评分卡 API ====================

@app.route('/api/session/<int:session_id>/score')
@login_required
def api_session_score(session_id):
    """返回单场直播的主播评分卡"""
    score = calc_anchor_score(session_id)
    if not score:
        return jsonify({'error': '场次不存在'}), 404
    return jsonify(score)


@app.route('/api/anchor/<username>/score_history')
@login_required
def api_anchor_score_history(username):
    """返回某主播近N场评分趋势"""
    u = get_current_user()
    uid = None if u['is_admin'] else u['id']
    history = get_anchor_score_history(username, limit=20, owner_user_id=uid)
    return jsonify({'history': history})


# ==================== 时段热力图 API ====================

@app.route('/api/heatmap')
@login_required
def api_heatmap():
    """返回开播时段热力图数据"""
    u = get_current_user()
    uid = None if u['is_admin'] else u['id']
    username = request.args.get('username')
    days = int(request.args.get('days', 90))
    data = get_timeslot_heatmap(owner_user_id=uid, username=username or None, days=days)
    return jsonify(data)


# ==================== 竞品话术对比 API ====================

@app.route('/api/rivals/speech_compare')
@login_required
def api_rival_speech_compare():
    """竞品话术 vs 自营话术对比分析"""
    u = get_current_user()
    uid = None if u['is_admin'] else u['id']
    from src.database import get_conn
    conn = get_conn()
    c = conn.cursor()
    if uid:
        c.execute("SELECT username FROM account_groups WHERE owner_user_id=? AND group_name='own'", (uid,))
    else:
        c.execute("SELECT username FROM account_groups WHERE group_name='own'")
    own_users = [r['username'] for r in c.fetchall()]
    c.execute("SELECT DISTINCT username FROM account_groups WHERE group_name='rival'")
    rival_users = [r['username'] for r in c.fetchall()]
    conn.close()
    result = get_rival_speech_compare(own_users, rival_users, sessions_per_account=10)
    return jsonify(result)


@app.route('/api/notify/high_value_comment', methods=['POST'])
@login_required
def api_notify_high_value_comment():
    """高价值评论微信推送接口（前端触发，已做30秒节流）"""
    data = request.get_json() or {}
    from src.notifier import send_high_value_comment_notify
    ok = send_high_value_comment_notify(
        username=data.get('username', ''),
        intent=data.get('intent', ''),
        label=data.get('label', ''),
        comment=data.get('comment', ''),
        comment_zh=data.get('comment_zh', ''),
        commenter=data.get('commenter', ''),
    )
    return jsonify({'success': ok})


if __name__ == '__main__':
    print("=" * 50)
    print("🚀 TikTok 直播监测系统启动")
    print("📊 看板地址: http://localhost:5001")
    print("=" * 50)
    # 后台启动 Cloudflare Tunnel
    t = threading.Thread(target=_start_cloudflare_tunnel, args=(5001,), daemon=True)
    t.start()
    # 启动竞品追踪后台服务（每日粉丝快照）
    start_rival_tracker()
    # 自动恢复自动监控列表（按用户分组，携带 is_auto 和 group_name）
    def _auto_restore_monitors():
        import time
        time.sleep(3)  # 等服务启动稳定
        rows = get_enabled_auto_monitors()  # 不过滤用户，全部恢复
        if rows:
            print(f"🔄 自动恢复 {len(rows)} 个监控任务...")
            for r in rows:
                group = r.get('group_name', 'own')
                start_monitor(r['username'], socketio=socketio, is_auto=True, group_name=group)
                time.sleep(0.5)
    threading.Thread(target=_auto_restore_monitors, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)
