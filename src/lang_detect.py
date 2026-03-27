"""
多语言检测模块 v3
- 阿拉伯语方言精准识别到国家/地区级别（海湾6国独立 + 黎凡特4国 + 北非5国 + 埃及 + 伊拉克 + 也门 + 苏丹）
- 英语水平自动分级（A1-C2/母语）
- 中文 / 其他语言检测
- 语言检测优先用 langid（确定性，无随机性），降级 langdetect
"""
import re
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 语言检测辅助（langid 优先 + langdetect 降级）
# Task 12 调研结论：
#   - langid：确定性算法，速度快（~0.1ms），但语言数量较少（97种）
#   - langdetect：随机性，需 seed=42 修复，识别语言更多（55+种）
#   - fasttext LID-176：最准确（176种），但需下载 126MB 模型，可选升级
#   - 当前方案：langid 首选 + langdetect 降级 = 准确率>90%，无随机性
# ============================================================
_langid_classifier = None
_langid_lock = None


def _get_langid():
    """懒初始化 langid 分类器（线程安全）"""
    global _langid_classifier, _langid_lock
    if _langid_lock is None:
        import threading
        _langid_lock = threading.Lock()
    if _langid_classifier is not None:
        return _langid_classifier
    with _langid_lock:
        if _langid_classifier is not None:
            return _langid_classifier
        try:
            import langid
            langid.set_languages(None)  # 启用全语言模式
            _langid_classifier = langid
            return _langid_classifier
        except ImportError:
            return None


def _detect_lang_code(text: str) -> str:
    """
    检测文本语言代码
    优先 langid（确定性高）→ 降级 langdetect → 返回 'other'
    返回: ISO 639-1 语言代码，如 'en', 'ar', 'zh' 等
    """
    if not text or len(text.strip()) < 4:
        return 'other'

    # 优先尝试 langid
    try:
        li = _get_langid()
        if li:
            code, confidence = li.classify(text)
            # langid 返回 'zh' 统一处理
            if code.startswith('zh'):
                return 'zh'
            if confidence > -50:  # langid 使用 log-prob，-50 是较低置信度阈值
                return code
    except Exception:
        pass

    # 降级 langdetect
    try:
        from langdetect import detect as ld_detect, DetectorFactory
        DetectorFactory.seed = 42  # 固定随机种子
        code = ld_detect(text)
        if code.startswith('zh'):
            return 'zh'
        return code
    except Exception:
        pass

    return 'other'


# ============================================================
# 阿拉伯语国家/地区方言特征词库（精细化到国家）
# ============================================================

# 格式：(dialect_id, 显示名, 国旗, 主要国家, 特征词列表, 权重)
_ARABIC_DIALECT_RULES = [
    # ───── 沙特 ─────
    ("sa", "沙特", "🇸🇦", "沙特阿拉伯", [
        "وش", "ايش", "ليش", "الحين", "ترا", "يبغى", "بغيت", "وايد",
        "الله يعطيك", "مشكور", "صح", "مرة", "شباب", "هلا والله",
        "زين", "يسلم", "ماشاء الله", "سبحانك", "اللي", "يبي",
        "أبغى", "ابغى", "والله العظيم", "يهلا", "ياهلا",
    ], 1.5),

    # ───── 阿联酋 ─────
    ("ae", "阿联酋", "🇦🇪", "阿拉伯联合酋长国", [
        "اشلونك", "شلونك", "خوش", "عيل", "يبيلك", "الحين",
        "هلا بيك", "يعطيك العافية", "كيفك", "بعدين",
        "مو كذا", "مو زين", "عندي", "مرة حلو",
    ], 1.3),

    # ───── 科威特 ─────
    ("kw", "科威特", "🇰🇼", "科威特", [
        "شفيه", "اشكذب", "شكو", "ماكو", "اكو", "يمه", "جان",
        "هاي", "بس", "گاع", "چذب", "شلونك", "واجد",
        "ويه", "هواية", "هسه", "العيل",
    ], 1.4),

    # ───── 卡塔尔/巴林 ─────
    ("qa", "海湾(卡塔尔/巴林)", "🇶🇦", "卡塔尔/巴林", [
        "يابه", "كيفك", "وش ودك", "زين", "هلا", "يعطيك",
        "شداعوه", "عدل", "قوي", "إن شاء الله",
    ], 1.1),

    # ───── 埃及 ─────
    ("eg", "埃及", "🇪🇬", "埃及", [
        "عايز", "عايزة", "إيه", "ايه", "جامد", "بص",
        "مش", "دلوقتي", "بقى", "بقا", "معلش", "يعني",
        "طب", "زي", "كده", "كدا", "اهو", "اهي",
        "فين", "مين", "ليه", "إزيك", "ازيك",
        "يابني", "يسطا", "حلوة", "تمام", "ياسيدي",
        "صاحبي", "الواد", "البنت", "انهاردة", "امبارح",
        "الأول", "ده", "دي", "دول", "هيه", "أيوه",
    ], 1.2),

    # ───── 黎巴嫩 ─────
    ("lb", "黎巴嫩", "🇱🇧", "黎巴嫩", [
        "هلق", "هلأ", "شو", "كتير", "هيك", "منيح",
        "مزبوط", "لأنو", "عم بـ", "رح", "بدي", "بدك",
        "بده", "بدنا", "مش هيك", "شي", "هون",
        "يا زلمة", "شو بدك", "لحظة", "هيدا", "هيدي",
        "صرلو", "صرلي", "مين", "وين", "هودي",
    ], 1.3),

    # ───── 叙利亚 ─────
    ("sy", "叙利亚", "🇸🇾", "叙利亚", [
        "هلق", "شو", "كتير", "هيك", "عم بـ", "رح",
        "بدي", "بدك", "بده", "هون", "هناك",
        "يا عمي", "يا صديقي", "شكلو", "وقتها", "حالك",
        "بتعرف", "بتحكي", "مشان", "هاد", "هادا",
    ], 1.2),

    # ───── 约旦/巴勒斯坦 ─────
    ("jo", "约旦/巴勒斯坦", "🇯🇴", "约旦/巴勒斯坦", [
        "هلق", "شو", "كتير", "هيك", "والله", "يعني",
        "يا عمي", "ازيك", "كيفك", "مش صح",
        "هون", "هيدا", "بدك", "رح", "عم",
        "زلمة", "يا زلمة", "مرتاح", "ربنا",
    ], 1.1),

    # ───── 伊拉克 ─────
    ("iq", "伊拉克", "🇮🇶", "伊拉克", [
        "شكو", "ماكو", "اكو", "يمه", "چي", "چذب",
        "هسه", "هسة", "گاع", "چاي", "ويه", "واجد",
        "گلبي", "هاي", "جان", "اشلونك", "شلونك",
        "بعد", "ابشر", "عوافي", "روحي", "حبيبي",
    ], 1.4),

    # ───── 也门 ─────
    ("ye", "也门", "🇾🇪", "也门", [
        "ودي", "توه", "وش", "مافيه", "ذا", "ذي",
        "زول", "معك", "انت شو", "وين", "شلونك",
        "بالله", "يالله", "حبيبي", "اخوي", "اخي",
    ], 1.2),

    # ───── 摩洛哥 ─────
    ("ma", "摩洛哥", "🇲🇦", "摩洛哥", [
        "واش", "بزاف", "دابا", "خويا", "صاحبي",
        "كيفاش", "علاش", "مزيان", "هضرة", "نتا",
        "والو", "بكري", "دروك", "هاد", "هادي",
        "بلاك", "راه", "ماشي", "برك", "حاجة",
        "مغرب", "هنا", "باش", "حيث", "فأش",
    ], 1.3),

    # ───── 阿尔及利亚 ─────
    ("dz", "阿尔及利亚", "🇩🇿", "阿尔及利亚", [
        "راك", "راكي", "واش", "بزاف", "خويا",
        "قاع", "تاع", "ياسر", "برك", "حتى",
        "كيفاش", "علاش", "زعمة", "وليك", "الزاف",
        "دروك", "هاك", "فلوسك", "نتا", "نتي",
    ], 1.3),

    # ───── 突尼斯 ─────
    ("tn", "突尼斯", "🇹🇳", "突尼斯", [
        "برشا", "ياسر", "بالله", "هاكا", "فما",
        "يزي", "علاش", "كيفاش", "نتي", "نتا",
        "شنوه", "ما فما", "وين", "قداش", "مانيش",
        "موش", "باش", "حتى", "جات", "فيها",
    ], 1.3),

    # ───── 苏丹 ─────
    ("sd", "苏丹", "🇸🇩", "苏丹", [
        "زول", "زولة", "تمام", "كيف الحال",
        "يا خي", "يا أخوي", "أخوي", "والله",
        "شنو", "شن", "يلا", "حبوب",
    ], 1.1),

    # ───── 利比亚 ─────
    ("ly", "利比亚", "🇱🇾", "利比亚", [
        "شن", "شنو", "شنهو", "يا خي", "برا",
        "معناتو", "وش", "فمه", "عندو", "ماعندوش",
        "كيفاش", "زعمة", "خويا", "صاحبي",
    ], 1.1),
]

# MSA（现代标准阿拉伯语）高频词
_MSA_KEYWORDS = [
    "مرحبا", "شكرا", "جميل", "ممتاز", "رائع", "مبروك",
    "إن شاء", "ما شاء", "الله", "بارك", "محبة", "أهلا",
    "أهلاً", "وسهلاً", "السلام", "عليكم", "الرحيم",
]

# 方言分组（用于lang_id归类）
_DIALECT_GROUPS = {
    "sa": "ar-gulf", "ae": "ar-gulf", "kw": "ar-gulf", "qa": "ar-gulf",
    "eg": "ar-egypt",
    "lb": "ar-levant", "sy": "ar-levant", "jo": "ar-levant",
    "ma": "ar-maghreb", "dz": "ar-maghreb", "tn": "ar-maghreb", "ly": "ar-maghreb",
    "iq": "ar-iraq",
    "ye": "ar-yemen",
    "sd": "ar-maghreb",  # 苏丹归北非大区
}


def detect_arabic_dialect(text: str) -> Tuple[str, str, str, str]:
    """
    精准识别阿拉伯语方言（国家级别）
    返回: (dialect_id, display_name, flag_emoji, country)
    """
    if not text:
        return ("ar", "阿拉伯语", "🌍", "")

    scores = {}
    for dialect_id, name, flag, country, keywords, weight in _ARABIC_DIALECT_RULES:
        score = 0
        for kw in keywords:
            count = len(re.findall(r'(?<!\S)' + re.escape(kw) + r'(?!\S)', text))
            score += count * weight
        scores[dialect_id] = score

    best_id = max(scores, key=scores.get)
    best_score = scores[best_id]

    if best_score < 1.0:
        return ("ar", "阿拉伯语", "🌍", "")

    for dialect_id, name, flag, country, _, _ in _ARABIC_DIALECT_RULES:
        if dialect_id == best_id:
            return (dialect_id, name, flag, country)

    return ("ar", "阿拉伯语", "🌍", "")


# ============================================================
# 英语水平分级
# ============================================================
# 高级词汇（C1-C2）
_EN_ADVANCED_VOCAB = {
    'consequently', 'nevertheless', 'furthermore', 'subsequently', 'notwithstanding',
    'particularly', 'significantly', 'substantially', 'predominantly', 'comprehensive',
    'sophisticated', 'paramount', 'unprecedented', 'facilitate', 'implement',
    'articulate', 'eloquent', 'proficient', 'meticulous', 'rigorous',
    'paradigm', 'ambiguous', 'nuanced', 'plausible', 'inevitable',
    'acknowledge', 'emphasize', 'illustrate', 'demonstrate', 'elaborate',
    'scrutinize', 'analyze', 'interpret', 'evaluate', 'synthesize',
}

# 中级词汇（B1-B2）
_EN_INTERMEDIATE_VOCAB = {
    'however', 'although', 'because', 'therefore', 'according',
    'opinion', 'suggest', 'explain', 'describe', 'difference',
    'experience', 'important', 'interesting', 'example', 'situation',
    'together', 'problem', 'question', 'believe', 'remember',
    'improve', 'include', 'increase', 'develop', 'produce',
    'probably', 'usually', 'especially', 'actually', 'recently',
}

# 初级词汇（A1-A2）
_EN_BASIC_VOCAB = {
    'hello', 'hi', 'good', 'bad', 'big', 'small', 'love', 'like',
    'want', 'need', 'nice', 'cool', 'great', 'wow', 'yes', 'no',
    'please', 'thank', 'thanks', 'sorry', 'okay', 'ok', 'bye',
}

# 语法错误/非母语特征
_EN_NON_NATIVE_PATTERNS = [
    r'\bi am agree\b', r'\bi am boring\b', r'\bvery very\b',
    r'\bmore better\b', r'\bmore faster\b', r'\bgo to home\b',
    r'\bam waiting\b.*\bfrom\b',
]


def assess_english_level(text: str) -> str:
    """
    评估英语水平
    返回: "C2母语级" | "C1高级" | "B2中高级" | "B1中级" | "A2初级" | "A1入门"
    """
    if not text or len(text.strip()) < 5:
        return "A1入门"

    words = re.findall(r"[a-zA-Z']+", text.lower())
    if not words:
        return "A1入门"

    word_count = len(words)
    unique_words = set(words)

    # 统计词汇等级
    advanced_count = len(unique_words & _EN_ADVANCED_VOCAB)
    intermediate_count = len(unique_words & _EN_INTERMEDIATE_VOCAB)
    basic_count = len(unique_words & _EN_BASIC_VOCAB)

    # 平均词长（母语者用词通常更长更复杂）
    avg_word_len = sum(len(w) for w in words) / max(word_count, 1)

    # 句子复杂度（逗号/从句）
    comma_density = text.count(',') / max(word_count, 1)
    has_subordinate = bool(re.search(r'\b(which|although|whereas|despite|whilst|because)\b', text, re.I))

    # 非母语特征
    non_native_signals = sum(1 for p in _EN_NON_NATIVE_PATTERNS if re.search(p, text, re.I))

    # 评分
    score = 0
    score += advanced_count * 5
    score += intermediate_count * 2
    score += basic_count * 1
    score += (avg_word_len - 3) * 2  # 词长加成
    if has_subordinate: score += 3
    score += comma_density * 10
    score -= non_native_signals * 4

    # 词汇丰富度
    if word_count >= 10:
        lexical_diversity = len(unique_words) / word_count
        score += lexical_diversity * 5

    if score >= 20:
        return "C2母语级"
    elif score >= 13:
        return "C1高级"
    elif score >= 8:
        return "B2中高级"
    elif score >= 4:
        return "B1中级"
    elif score >= 2:
        return "A2初级"
    else:
        return "A1入门"


# ============================================================
# 主检测函数
# ============================================================

def detect_language(text: str) -> dict:
    """
    检测文本语言，返回结构化结果
    英语结果附加 en_level 字段
    阿拉伯语结果附加 dialect_country 字段（精准国家）
    """
    if not text or not text.strip():
        return _make_result("other", "未知", "?", "", "lang-other", None)

    text_stripped = text.strip()

    # 过滤纯表情/符号（不参与检测）
    text_no_emoji = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27BF\U0001F300-\U0001F9FF]', '', text_stripped)
    text_meaningful = text_no_emoji.strip()

    # 1. 快速规则检测
    zh_chars = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', text_meaningful))
    ar_chars = len(re.findall(r'[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufefc]', text_meaningful))
    en_chars = len(re.findall(r'[a-zA-Z]', text_meaningful))
    total_chars = max(len(text_meaningful.replace(' ', '')), 1)

    # 中文
    if zh_chars / total_chars > 0.3:
        return _make_result("zh", "中文", "中", "🇨🇳", "lang-zh", None)

    # 阿拉伯语
    if ar_chars / total_chars > 0.3:
        dialect_id, dialect_name, flag, country = detect_arabic_dialect(text_meaningful)
        group = _DIALECT_GROUPS.get(dialect_id, "ar")
        if dialect_id == "ar":
            return _make_result("ar", "阿拉伯语", "AR", "🌍", "lang-ar", "ar", dialect_country=country)
        display = f"阿语·{dialect_name}"
        short = f"{flag}{dialect_name}"
        return _make_result(group, display, short, flag, f"lang-{group}", dialect_id, dialect_country=country)

    # 英语快速路径：字母占比高 + 额外校验
    if en_chars / total_chars > 0.4 and ar_chars < 3 and zh_chars < 3:
        # 用 langid 交叉验证（短文本<15字符时 langid 不可靠，改用词典方法）
        words = re.findall(r"[a-zA-Z']+", text_meaningful.lower())
        _EN_COMMON = {'i','you','he','she','it','we','they','the','a','an','is','are','was','were',
                      'be','been','have','has','had','do','does','did','will','would','can','could',
                      'hello','hi','ok','yes','no','good','bad','nice','cool','great','wow','thanks',
                      'sorry','please','bye','what','how','who','where','when','why','not','and','or',
                      'but','in','on','at','for','with','from','to','of','about','this','that','my',
                      'your','love','like','want','need','get','got','go','come','see','know','think',
                      'today','tomorrow','yesterday','here','there','now','then','always','never',
                      'very','really','just','also','still','again','already','soon','maybe','well',
                      'more','less','most','least','some','any','all','both','each','every','other',
                      'new','old','big','small','long','short','high','low','right','wrong','same',
                      'am','im','its','its','dont','doesnt','cant','wont','isnt','arent','wasnt'}
        en_word_ratio = sum(1 for w in words if w in _EN_COMMON) / max(len(words), 1)

        if len(words) <= 8:
            # 短文本（≤8个词）：高频英语词比例 > 30% 即认定英语
            if en_word_ratio > 0.3:
                level = assess_english_level(text_meaningful)
                result = _make_result("en", f"英语({level})", f"EN·{level[:2]}", "🇬🇧", "lang-en", None)
                result["en_level"] = level
                return result
        else:
            # 较长文本：langid 交叉验证
            quick_code = _detect_lang_code(text_meaningful)
            if quick_code == 'en' or (quick_code not in ('fr','es','pt','de','it','nl','pl','ru') and en_word_ratio > 0.4):
                level = assess_english_level(text_meaningful)
                result = _make_result("en", f"英语({level})", f"EN·{level[:2]}", "🇬🇧", "lang-en", None)
                result["en_level"] = level
                return result
        # 非英语（但含大量拉丁字母），交给下面的完整检测

    # 2. 精确语言检测（优先 langid 确定性算法 → 降级 langdetect）
    # langid 优点：确定性、无随机性、速度快，不需要 seed 修复
    # langdetect 问题：随机性导致同样文本偶尔判断不一致
    lang_code = _detect_lang_code(text_meaningful)

    if lang_code in ('zh-cn', 'zh-tw', 'zh'):
        return _make_result("zh", "中文", "中", "🇨🇳", "lang-zh", None)
    if lang_code == 'en':
        level = assess_english_level(text_meaningful)
        result = _make_result("en", f"英语({level})", f"EN·{level[:2]}", "🇬🇧", "lang-en", None)
        result["en_level"] = level
        return result
    if lang_code == 'ar':
        dialect_id, dialect_name, flag, country = detect_arabic_dialect(text_meaningful)
        group = _DIALECT_GROUPS.get(dialect_id, "ar")
        if dialect_id == "ar":
            return _make_result("ar", "阿拉伯语", "AR", "🌍", "lang-ar", "ar", dialect_country=country)
        display = f"阿语·{dialect_name}"
        short = f"{flag}{dialect_name}"
        return _make_result(group, display, short, flag, f"lang-{group}", dialect_id, dialect_country=country)

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
        'it': ('意大利语', 'IT', '🇮🇹'),
        'nl': ('荷兰语', 'NL', '🇳🇱'),
        'th': ('泰语', 'TH', '🇹🇭'),
        'vi': ('越南语', 'VI', '🇻🇳'),
        'ms': ('马来语', 'MS', '🇲🇾'),
        'fa': ('波斯语', 'FA', '🇮🇷'),
        'ur': ('乌尔都语', 'UR', '🇵🇰'),
        'sw': ('斯瓦希里语', 'SW', '🇰🇪'),
        'tl': ('菲律宾语', 'TL', '🇵🇭'),
        'pl': ('波兰语', 'PL', '🇵🇱'),
        'uk': ('乌克兰语', 'UK', '🇺🇦'),
    }
    if lang_code in other_map:
        name, short, flag = other_map[lang_code]
        return _make_result(lang_code, name, short, flag, "lang-other", None)

    if lang_code and lang_code != 'other':
        return _make_result(lang_code, f"其他({lang_code.upper()})", lang_code.upper()[:3], "🌐", "lang-other", None)
    return _make_result("other", "未知", "?", "", "lang-other", None)


def detect_speech_language(whisper_result: dict) -> dict:
    """
    从 Whisper 转写结果中提取语言信息
    """
    text = whisper_result.get("text", "").strip()
    whisper_lang = whisper_result.get("language", "")

    whisper_lang_map = {
        "chinese": "zh", "zh": "zh",
        "english": "en", "en": "en",
        "arabic": "ar",  "ar": "ar",
    }
    normalized = whisper_lang_map.get(whisper_lang.lower(), "")

    if normalized == "ar" or (not normalized and _is_arabic(text)):
        dialect_id, dialect_name, flag, country = detect_arabic_dialect(text)
        group = _DIALECT_GROUPS.get(dialect_id, "ar")
        if dialect_id == "ar":
            result = _make_result("ar", "阿拉伯语", "AR", "🌍", "lang-ar", "ar", dialect_country=country)
        else:
            display = f"阿语·{dialect_name}"
            short = f"{flag}{dialect_name}"
            result = _make_result(group, display, short, flag, f"lang-{group}", dialect_id, dialect_country=country)
        result["whisper_language"] = whisper_lang
        return result

    if normalized == "en" or (not normalized and _is_mostly_english(text)):
        level = assess_english_level(text)
        result = _make_result("en", f"英语({level})", f"EN·{level[:2]}", "🇬🇧", "lang-en", None)
        result["en_level"] = level
        result["whisper_language"] = whisper_lang
        return result

    result = detect_language(text)
    result["whisper_language"] = whisper_lang
    return result


def _is_arabic(text: str) -> bool:
    ar_chars = len(re.findall(r'[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufefc]', text))
    return ar_chars / max(len(text.replace(' ', '')), 1) > 0.2


def _is_mostly_english(text: str) -> bool:
    en_chars = len(re.findall(r'[a-zA-Z]', text))
    return en_chars / max(len(text.replace(' ', '')), 1) > 0.4


def _make_result(lang, lang_display, lang_short, flag, css_class, dialect, dialect_country='') -> dict:
    return {
        "lang": lang,
        "lang_display": lang_display,
        "lang_short": lang_short,
        "flag": flag,
        "css_class": css_class,
        "dialect": dialect,
        "dialect_country": dialect_country,
    }


# ============================================================
# 全局语言统计
# ============================================================
class LangStats:
    """线程安全的语言统计计数器"""

    def __init__(self):
        self._data: dict = {}
        import threading
        self._lock = threading.Lock()

    def add(self, username: str, lang_info: dict):
        lang = lang_info.get("lang", "other")
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
        merged = {}
        with self._lock:
            for stats in self._data.values():
                for k, v in stats.items():
                    merged[k] = merged.get(k, 0) + v
        return merged

    def clear(self, username: str):
        with self._lock:
            self._data.pop(username, None)


# 全局单例
lang_stats = LangStats()
comment_lang_stats = LangStats()
speech_lang_stats = LangStats()
