"""
Gemini AI API 工具模块
用于直播复盘的话术/评论智能总结功能
免费模型：gemini-1.5-flash（每天1500次请求）
"""
import logging
import json

logger = logging.getLogger(__name__)


def _get_config():
    """获取 Gemini 配置"""
    try:
        import config
        return config.GEMINI_API_KEY or '', getattr(config, 'GEMINI_MODEL', 'gemini-1.5-flash')
    except Exception:
        return '', 'gemini-1.5-flash'


def _get_proxy():
    """获取代理配置"""
    try:
        import config
        proxy = getattr(config, 'PROXY_HTTP', '')
        if proxy and 'socks5' in proxy:
            # httpx 需要格式: socks5h://host:port
            return proxy.replace('socks5://', 'socks5h://')
        return proxy
    except Exception:
        return ''


def call_gemini(prompt: str, max_tokens: int = 1024) -> str:
    """
    调用 Gemini API
    返回: AI 回复文本，失败返回空字符串
    """
    api_key, model = _get_config()
    if not api_key:
        return ''

    try:
        import httpx
        proxy = _get_proxy()
        proxies = {'all://': proxy} if proxy else None

        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        headers = {'Content-Type': 'application/json'}
        params = {'key': api_key}
        body = {
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {
                'maxOutputTokens': max_tokens,
                'temperature': 0.3,
            }
        }

        with httpx.Client(proxies=proxies, timeout=30) as client:
            resp = client.post(url, headers=headers, params=params, json=body)
            resp.raise_for_status()
            data = resp.json()
            text = data['candidates'][0]['content']['parts'][0]['text']
            return text.strip()

    except Exception as e:
        logger.warning(f'Gemini API 调用失败: {e}')
        return ''


def summarize_speech(speech_records: list) -> str:
    """
    对话术记录生成智能总结
    speech_records: [{'text': ..., 'text_zh': ..., 'timestamp': ..., 'lang': ...}]
    返回: 中文总结段落
    """
    if not speech_records:
        return ''

    api_key, _ = _get_config()
    if not api_key:
        return _rule_based_speech_summary(speech_records)

    # 准备话术文本（优先用中文翻译，没有则用原文）
    texts = []
    for r in speech_records[:80]:  # 最多80条，避免token超限
        t = r.get('text_zh') or r.get('text', '')
        if t and t.strip():
            texts.append(t.strip())

    if not texts:
        return ''

    combined = '\n'.join(texts)
    prompt = f"""以下是一场 TikTok 直播的主播话术内容（部分为翻译）：

{combined}

请用中文写一段简洁的总结（200字以内），包括：
1. 主播的主要推介内容或主题
2. 话术风格和节奏特点
3. 高频提到的关键词或卖点

只输出总结内容，不要加标题或序号。"""

    result = call_gemini(prompt, max_tokens=512)
    return result or _rule_based_speech_summary(speech_records)


def summarize_comments(comment_records: list) -> str:
    """
    对评论记录生成智能总结
    comment_records: [{'content': ..., 'text_zh': ..., 'timestamp': ..., 'lang': ...}]
    返回: 中文总结段落
    """
    if not comment_records:
        return ''

    api_key, _ = _get_config()
    if not api_key:
        return _rule_based_comment_summary(comment_records)

    # 准备评论文本（过滤表情、去重）
    seen = set()
    texts = []
    for r in comment_records[:100]:
        t = r.get('text_zh') or r.get('content', '')
        if not t or len(t.strip()) < 2:
            continue
        t = t.strip()
        if t in seen:
            continue
        seen.add(t)
        texts.append(t)

    if len(texts) < 3:
        return ''

    combined = '\n'.join(texts[:80])
    prompt = f"""以下是一场 TikTok 直播的观众评论内容（部分为翻译）：

{combined}

请用中文写一段简洁的总结（150字以内），包括：
1. 观众最关注的问题或话题
2. 观众情绪倾向（正面/负面/中性）
3. 高频提及的内容或诉求

只输出总结内容，不要加标题或序号。"""

    result = call_gemini(prompt, max_tokens=400)
    return result or _rule_based_comment_summary(comment_records)


def _rule_based_speech_summary(records: list) -> str:
    """规则兜底：无 Gemini 时用规则生成简单总结"""
    if not records:
        return ''
    total = len(records)
    langs = {}
    for r in records:
        l = r.get('lang', 'other')
        langs[l] = langs.get(l, 0) + 1
    dominant = max(langs, key=langs.get) if langs else 'other'
    lang_name = {'zh': '中文', 'en': '英语', 'ar': '阿拉伯语'}.get(dominant.split('-')[0], dominant)
    return f'本场共采集 {total} 段话术，主要语言为{lang_name}。（配置 Gemini API 可获得更深度的 AI 分析）'


def _rule_based_comment_summary(records: list) -> str:
    """规则兜底：无 Gemini 时用规则生成简单总结"""
    if not records:
        return ''
    total = len(records)
    langs = {}
    for r in records:
        l = r.get('lang', 'other')
        langs[l] = langs.get(l, 0) + 1
    dominant = max(langs, key=langs.get) if langs else 'other'
    lang_name = {'zh': '中文', 'en': '英语', 'ar': '阿拉伯语'}.get(dominant.split('-')[0], dominant)
    return f'本场共有 {total} 条评论，主要语言为{lang_name}。（配置 Gemini API 可获得更深度的 AI 分析）'
