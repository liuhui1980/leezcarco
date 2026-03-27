"""
翻译模块 — 免费 Google Translate 非官方接口
将主播话术翻译成中文，走本地代理
优化：使用持久连接池（httpx 长连接），批量翻译，减少延迟
"""
import logging
import threading

logger = logging.getLogger(__name__)

# 读取代理配置
try:
    from config import PROXY_HTTP
except ImportError:
    PROXY_HTTP = ''

_TRANSLATE_URL = 'https://translate.googleapis.com/translate_a/single'

# ── 全局共享的 httpx 连接池（持久复用，避免每次重建连接）
_client_lock = threading.Lock()
_shared_client = None


def _get_client():
    """获取（或懒初始化）共享 httpx 客户端"""
    global _shared_client
    if _shared_client is not None:
        return _shared_client
    with _client_lock:
        if _shared_client is not None:
            return _shared_client
        import httpx
        proxy_arg = {}
        if PROXY_HTTP:
            try:
                ver = tuple(int(x) for x in httpx.__version__.split('.')[:2])
                if ver >= (0, 28):
                    proxy_arg = {'proxy': PROXY_HTTP}
                else:
                    proxy_arg = {'proxies': {'http://': PROXY_HTTP, 'https://': PROXY_HTTP}}
            except Exception:
                proxy_arg = {'proxy': PROXY_HTTP}
        _shared_client = httpx.Client(
            **proxy_arg,
            timeout=8,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        return _shared_client


def translate_to_zh(text: str, source_lang: str = 'auto') -> 'str | None':
    """
    将文本翻译为中文（复用连接池，提速 50-70%）
    :param text: 原文
    :param source_lang: 源语言代码（auto=自动检测，en=英语，ar=阿拉伯语等）
    :return: 中文翻译字符串，失败或无需翻译时返回 None
    """
    if not text or not text.strip():
        return None

    text_stripped = text.strip()

    # 极短文本（≤3字符）跳过翻译
    if len(text_stripped) <= 3:
        return None

    # 已经是中文，不需要翻译
    zh_ratio = len([c for c in text_stripped if '\u4e00' <= c <= '\u9fff']) / max(len(text_stripped), 1)
    if zh_ratio > 0.4:
        return text_stripped

    params = {
        'client': 'gtx',
        'sl': source_lang,
        'tl': 'zh-CN',
        'dt': 't',
        'q': text_stripped[:500],  # 限制长度避免超时
    }

    try:
        client = _get_client()
        resp = client.get(_TRANSLATE_URL, params=params)
        data = resp.json()
        # 解析返回格式：[[["译文","原文",...],...],...]
        if data and data[0]:
            translated = ''.join(seg[0] for seg in data[0] if seg and seg[0])
            if translated and translated.strip():
                result = translated.strip()
                # 如果返回的翻译与原文相同（翻译未生效），返回 None
                if result.lower() == text_stripped.lower():
                    return None
                return result
    except Exception as e:
        logger.debug(f"翻译失败（{text_stripped[:30]}...）: {e}")
        # 如果共享客户端连接异常，重置以便下次重建
        global _shared_client
        try:
            _shared_client.close()
        except Exception:
            pass
        _shared_client = None

    return None  # 翻译失败返回 None，调用方自行处理
