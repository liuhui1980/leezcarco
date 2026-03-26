"""
多语言检测模块
- 文字评论：langdetect + 阿拉伯语方言词库规则
- 语音转写：Whisper 返回的 language 代码 + 方言词汇特征
"""
import re
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 阿拉伯语方言特征词库
# 每个地区用最具代表性的高频方言词 / 发音特征词
# ============================================================
_ARABIC_DIALECT_RULES = [
    # (地区标识, 显示名, 国旗, 特征词列表, 权重)
    ("gulf",    "海湾",   "🇸🇦", [
        "يلا", "شباب", "والله", "كيفك", "وش", "ايش", "ليش",
        "بعد", "الحين", "ترا", "عشان", "زين", "مرة", "هلا",
        "يبغى", "يبي", "بغيت", "بغى", "ابغى", "اللي",
        "هذا", "ذا", "وايد", "صح", "حلو", "يسلم",
        "مشكور", "شكراً", "يعطيك", "الله", "ماشاء",
    ], 1.2),
    ("egypt",   "埃及",   "🇪🇬", [
        "عايز", "عايزة", "إيه", "ايه", "جامد", "بص",
        "مش", "دلوقتي", "بقى", "بقا", "معلش", "يعني",
        "طب", "زي", "كده", "كدا", "اهو", "اهي",
        "فين", "مين", "ليه", "إزيك", "ازيك",
        "يابني", "يسطا", "اللا", "حلوة", "تمام",
    ], 1.0),
    ("levant",  "黎凡特", "🇱🇧", [
        "هلق", "هلأ", "شو", "كتير", "يعني", "هيك",
        "منيح", "مزبوط", "اذا", "لأنو", "عم", "رح",
        "بدي", "بدك", "بده", "بدنا", "مش هيك",
        "لحظة", "شي", "اشي", "هون", "هناك",
        "يا زلمة", "يا عمي", "يا صديقي", "شكلو",
    ], 1.0),
    ("maghreb", "北非",   "🇲🇦", [
        "واش", "بزاف", "دابا", "بغيت", "خويا", "صاحبي",
        "حاجة", "ماشي", "برك", "كيفاش", "علاش",
        "فاهم", "ملي", "بلاك", "حتى", "غير",
        "مزيان", "هضرة", "نتا", "نتي", "راه",
        "والو", "بكري", "دروك", "هاد", "هادي",
    ], 1.0),
]

# MSA（现代标准阿拉伯语）高频词，用来识别是否是阿拉伯语但无明显方言特征
_MSA_KEYWORDS = [
    "مرحبا", "شكرا", "جميل", "ممتاز", "رائع", "مبروك",
    "انشاء", "ماشاء", "الله", "بارك", "محبة", "اهلا",
]


def detect_arabic_dialect(text: str) -> Tuple[str, str, str]:
    """
    识别阿拉伯语方言地区
    返回: (dialect_id, display_name, flag_emoji)
    如果无法识别具体方言，返回 ("ar", "阿拉伯语", "🌍")
    """
    if not text:
        return ("ar", "阿拉伯语", "🌍")

    scores = {}
    for dialect_id, name, flag, keywords, weight in _ARABIC_DIALECT_RULES:
        score = 0
        for kw in keywords:
            # 词边界匹配（阿拉伯语空格分词）
            count = len(re.findall(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text))
            score += count * weight
        scores[dialect_id] = score

    best_id = max(scores, key=scores.get)
    best_score = scores[best_id]

    if best_score < 1.0:
        # 没有明显方言特征，判定为现代标准阿拉伯语
        return ("ar", "阿拉伯语", "🌍")

    for dialect_id, name, flag, _, _ in _ARABIC_DIALECT_RULES:
        if dialect_id == best_id:
            return (dialect_id, name, flag)

    return ("ar", "阿拉伯语", "🌍")


def detect_language(text: str) -> dict:
    """
    检测文本语言，返回结构化结果
    
    返回格式：
    {
        "lang": "zh" | "en" | "ar" | "other",
        "lang_display": "中文" | "英语" | "阿拉伯语-海湾" | ...,
        "lang_short": "中" | "EN" | "🇸🇦海湾" | ...,
        "flag": "🇨🇳" | "🇺🇸" | "🇸🇦" | ...,
        "css_class": "lang-zh" | "lang-en" | "lang-ar-gulf" | ...,
        "dialect": None | "gulf" | "egypt" | "levant" | "maghreb" | "ar"
    }
    """
    if not text or not text.strip():
        return _make_result("other", "未知", "?", "", "lang-other", None)

    text_stripped = text.strip()

    # 1. 快速规则检测（比 langdetect 快，减少误判）
    zh_chars = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', text_stripped))
    ar_chars = len(re.findall(r'[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufefc]', text_stripped))
    en_chars = len(re.findall(r'[a-zA-Z]', text_stripped))
    total_chars = max(len(text_stripped.replace(' ', '')), 1)

    # 中文占比超过 30%
    if zh_chars / total_chars > 0.3:
        return _make_result("zh", "中文", "中", "🇨🇳", "lang-zh", None)

    # 阿拉伯语占比超过 30%
    if ar_chars / total_chars > 0.3:
        dialect_id, dialect_name, flag = detect_arabic_dialect(text_stripped)
        if dialect_id == "ar":
            return _make_result("ar", "阿拉伯语", "AR", "🌍", "lang-ar", "ar")
        display = f"阿拉伯语·{dialect_name}"
        short = f"{flag}{dialect_name}"
        return _make_result(f"ar-{dialect_id}", display, short, flag, f"lang-ar-{dialect_id}", dialect_id)

    # 纯英文
    if en_chars / total_chars > 0.5 and ar_chars == 0 and zh_chars == 0:
        return _make_result("en", "英语", "EN", "🇺🇸", "lang-en", None)

    # 2. 用 langdetect 处理混合/其他语言
    try:
        from langdetect import detect as ld_detect, DetectorFactory
        DetectorFactory.seed = 42  # 稳定结果
        lang_code = ld_detect(text_stripped)

        if lang_code in ('zh-cn', 'zh-tw', 'zh'):
            return _make_result("zh", "中文", "中", "🇨🇳", "lang-zh", None)
        if lang_code == 'en':
            return _make_result("en", "英语", "EN", "🇺🇸", "lang-en", None)
        if lang_code == 'ar':
            dialect_id, dialect_name, flag = detect_arabic_dialect(text_stripped)
            if dialect_id == "ar":
                return _make_result("ar", "阿拉伯语", "AR", "🌍", "lang-ar", "ar")
            display = f"阿拉伯语·{dialect_name}"
            short = f"{flag}{dialect_name}"
            return _make_result(f"ar-{dialect_id}", display, short, flag, f"lang-ar-{dialect_id}", dialect_id)

        # 其他语言 - 展示 langdetect 代码
        other_map = {
            'ko': ('韩语', '한', '🇰🇷'),
            'ja': ('日语', '日', '🇯🇵'),
            'fr': ('法语', 'FR', '🇫🇷'),
            'de': ('德语', 'DE', '🇩🇪'),
            'es': ('西班牙语', 'ES', '🇪🇸'),
            'pt': ('葡萄牙语', 'PT', '🇧🇷'),
            'ru': ('俄语', 'RU', '🇷🇺'),
            'tr': ('土耳其语', 'TR', '🇹🇷'),
            'hi': ('印地语', 'HI', '🇮🇳'),
            'id': ('印尼语', 'ID', '🇮🇩'),
        }
        if lang_code in other_map:
            name, short, flag = other_map[lang_code]
            return _make_result(lang_code, name, short, flag, "lang-other", None)

        return _make_result("other", f"其他({lang_code})", lang_code.upper()[:2], "🌐", "lang-other", None)

    except Exception:
        return _make_result("other", "未知", "?", "", "lang-other", None)


def detect_speech_language(whisper_result: dict) -> dict:
    """
    从 Whisper 转写结果中提取语言信息
    whisper_result 是 model.transcribe() 的返回值，包含 'language' 和 'text' 字段
    
    返回格式同 detect_language()，但附加 whisper_language 字段
    """
    text = whisper_result.get("text", "").strip()
    whisper_lang = whisper_result.get("language", "")  # Whisper 返回的语言代码

    # Whisper 语言代码 -> 我们的标准化处理
    whisper_lang_map = {
        "chinese": "zh", "zh": "zh",
        "english": "en", "en": "en",
        "arabic": "ar",  "ar": "ar",
    }

    normalized = whisper_lang_map.get(whisper_lang.lower(), "")

    if normalized == "ar" or (not normalized and _is_arabic(text)):
        # 阿拉伯语：进一步识别方言（语音通过词汇特征）
        dialect_id, dialect_name, flag = detect_arabic_dialect(text)
        if dialect_id == "ar":
            result = _make_result("ar", "阿拉伯语", "AR", "🌍", "lang-ar", "ar")
        else:
            display = f"阿拉伯语·{dialect_name}"
            short = f"{flag}{dialect_name}"
            result = _make_result(f"ar-{dialect_id}", display, short, flag, f"lang-ar-{dialect_id}", dialect_id)
        result["whisper_language"] = whisper_lang
        return result

    # 否则用文字检测补充
    result = detect_language(text)
    result["whisper_language"] = whisper_lang
    return result


def _is_arabic(text: str) -> bool:
    ar_chars = len(re.findall(r'[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufefc]', text))
    return ar_chars / max(len(text.replace(' ', '')), 1) > 0.2


def _make_result(lang, lang_display, lang_short, flag, css_class, dialect) -> dict:
    return {
        "lang": lang,
        "lang_display": lang_display,
        "lang_short": lang_short,
        "flag": flag,
        "css_class": css_class,
        "dialect": dialect,
    }


# ============================================================
# 全局语言统计（用于看板的实时占比）
# ============================================================
class LangStats:
    """线程安全的语言统计计数器（按账号分别统计）"""

    def __init__(self):
        self._data: dict = {}  # username -> {lang_key: count}
        import threading
        self._lock = threading.Lock()

    def add(self, username: str, lang_info: dict):
        lang = lang_info.get("lang", "other")
        # 阿拉伯语方言归到父类 ar 统计，子类单独统计
        parent = lang.split("-")[0]  # "ar-gulf" -> "ar"
        with self._lock:
            if username not in self._data:
                self._data[username] = {}
            d = self._data[username]
            d[lang] = d.get(lang, 0) + 1
            if parent != lang:
                d[parent] = d.get(parent, 0) + 1

    def get_stats(self, username: str) -> dict:
        with self._lock:
            return dict(self._data.get(username, {}))

    def get_all_stats(self) -> dict:
        """返回所有账号合并的统计（用于全局看板）"""
        merged = {}
        with self._lock:
            for stats in self._data.values():
                for k, v in stats.items():
                    merged[k] = merged.get(k, 0) + v
        return merged

    def clear(self, username: str):
        with self._lock:
            self._data.pop(username, None)


# 全局单例（按来源分开统计）
lang_stats = LangStats()           # 兼容旧代码用（已不直接使用）
comment_lang_stats = LangStats()   # 评论区语言统计
speech_lang_stats = LangStats()    # 主播话术语言统计
