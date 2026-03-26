"""
翻译模块 — 免费 Google Translate 非官方接口
将主播话术翻译成中文，走本地代理
"""
import logging
import urllib.parse
import httpx

logger = logging.getLogger(__name__)

# 读取代理配置
try:
    from config import PROXY_HTTP
except ImportError:
    PROXY_HTTP = ''

_TRANSLATE_URL = 'https://translate.googleapis.com/translate_a/single'


def translate_to_zh(text: str, source_lang: str = 'auto') -> str:
    """
    将文本翻译为中文
    :param text: 原文
    :param source_lang: 源语言代码（auto=自动检测，en=英语，ar=阿拉伯语等）
    :return: 中文翻译，失败时返回原文
    """
    if not text or not text.strip():
        return text

    # 已经是中文，不需要翻译
    zh_ratio = len([c for c in text if '\u4e00' <= c <= '\u9fff']) / max(len(text), 1)
    if zh_ratio > 0.4:
        return text

    params = {
        'client': 'gtx',
        'sl': source_lang,
        'tl': 'zh-CN',
        'dt': 't',
        'q': text[:500],  # 限制长度避免超时
    }

    # httpx 0.28+ 改用 proxy 参数（单数），旧版用 proxies（复数）
    proxy_arg = {}
    if PROXY_HTTP:
        try:
            import httpx as _httpx_ver
            ver = tuple(int(x) for x in _httpx_ver.__version__.split('.')[:2])
            if ver >= (0, 28):
                proxy_arg = {'proxy': PROXY_HTTP}
            else:
                proxy_arg = {'proxies': {'http://': PROXY_HTTP, 'https://': PROXY_HTTP}}
        except Exception:
            proxy_arg = {'proxy': PROXY_HTTP}  # 默认用新 API

    try:
        with httpx.Client(**proxy_arg, timeout=10) as client:
            resp = client.get(_TRANSLATE_URL, params=params)
            data = resp.json()
            # 解析返回格式：[[["译文","原文",...],...],...]
            if data and data[0]:
                translated = ''.join(seg[0] for seg in data[0] if seg and seg[0])
                if translated and translated.strip():
                    result = translated.strip()
                    # 如果返回的翻译与原文相同（翻译未生效），返回 None
                    if result == text.strip():
                        return None
                    return result
    except Exception as e:
        logger.debug(f"翻译失败（{text[:30]}...）: {e}")

    return None  # 翻译失败返回 None，调用方自行处理
