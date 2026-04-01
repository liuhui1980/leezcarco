"""
语言总结性分析模块
对整场直播的评论和话术进行总结性语言分析
- 英文：判断CEFR等级（A1-C2）
- 阿拉伯语：判断地区分布（海湾、埃及、黎凡特、北非）
- 整体语言使用概况
- 排除感叹词干扰
"""

import re
from collections import Counter
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

# CEFR英语等级评估关键词
CEFR_KEYWORDS = {
    # A1 (入门级)
    'A1': [
        'hello', 'hi', 'thank you', 'please', 'sorry', 'yes', 'no', 'goodbye',
        'my name is', 'how are you', 'i am', 'you are', 'he is', 'she is',
        'i have', 'you have', 'what is', 'where is', 'who is',
    ],
    # A2 (初级)
    'A2': [
        'i like', 'i don\'t like', 'i want', 'i need', 'can i', 'could you',
        'what time', 'how much', 'how many', 'every day', 'usually', 'sometimes',
        'morning', 'afternoon', 'evening', 'breakfast', 'lunch', 'dinner',
        'family', 'friend', 'home', 'work', 'school', 'shop', 'buy', 'price',
    ],
    # B1 (中级)
    'B1': [
        'i think that', 'in my opinion', 'because', 'so that', 'although',
        'however', 'therefore', 'for example', 'on the other hand',
        'experience', 'important', 'difficult', 'interesting', 'problem',
        'solution', 'discuss', 'explain', 'understand', 'agree', 'disagree',
        'future', 'past', 'present', 'plan', 'decision', 'possibility',
    ],
    # B2 (中高级)
    'B2': [
        'according to', 'as a result', 'consequently', 'furthermore',
        'in addition', 'nevertheless', 'nonetheless', 'subsequently',
        'complex', 'complicated', 'significant', 'considerable', 'essential',
        'crucial', 'fundamental', 'interpretation', 'evaluation', 'analysis',
        'hypothesis', 'theoretical', 'practical', 'controversial', 'debate',
        'argument', 'perspective', 'approach', 'methodology', 'strategy',
    ],
    # C1 (高级)
    'C1': [
        'notwithstanding', 'conversely', 'paradoxically', 'ironically',
        'simultaneously', 'concurrently', 'subsequently', 'consequently',
        'sophisticated', 'comprehensive', 'exhaustive', 'meticulous',
        'rigorous', 'profound', 'insightful', 'nuanced', 'subtle', 'abstract',
        'conceptual', 'theoretical', 'empirical', 'methodological',
        'epistemological', 'ontological', 'paradigm', 'framework', 'discourse',
    ],
    # C2 (精通级)
    'C2': [
        'idiosyncratic', 'serendipitous', 'ubiquitous', 'myriad', 'plethora',
        'quintessential', 'ephemeral', 'ineffable', 'inextricable',
        'paradigmatic', 'hermeneutic', 'phenomenological', 'deconstructive',
        'poststructuralist', 'postmodern', 'hegemonic', 'dichotomy',
        'dialectical', 'teleological', 'ontological', 'epistemological',
        'methodological', 'conceptual', 'theoretical', 'empirical',
    ]
}

# 感叹词/无意义词过滤
FILLER_WORDS = {
    'en': ['oh', 'ah', 'um', 'uh', 'hmm', 'huh', 'wow', 'oops', 'yay', 'yikes',
           'yeah', 'yep', 'nope', 'uh-huh', 'uh-uh', 'huh', 'hm', 'er', 'erm',
           'like', 'you know', 'i mean', 'actually', 'basically', 'literally',
           'really', 'so', 'well', 'just', 'kind of', 'sort of', 'a bit',
           'totally', 'absolutely', 'exactly'],
    'ar': ['آه', 'أوه', 'إم', 'هم', 'يا', 'يا إلهي', 'يا للعجب', 'يا للهول',
           'يا للروعة', 'يا للجمال', 'طيب', 'حسناً', 'تمام', 'ماشي', 'يعني',
           'بصراحة', 'أصلاً', 'فعلاً', 'جداً', 'شوي', 'شوية', 'بس', 'لكن',
           'بعدين', 'خلاص', 'تمام', 'ماشي', 'يا سلام', 'يا عيني'],
    'zh': ['啊', '哦', '呃', '嗯', '唉', '哇', '呀', '嘛', '呢', '吧',
           '哈', '哼', '呵', '呸', '哎', '诶', '哟', '喂', '嗨',
           '就是说', '那个', '然后', '其实', '就是', '基本上', '实际上',
           '真的', '确实', '太', '非常', '特别', '有点', '稍微']
}

# 阿拉伯语地区特征词
ARABIC_REGIONAL_KEYWORDS = {
    'ar-gulf': [  # 海湾地区（沙特、阿联酋、卡塔尔等）
        'الله', 'والله', 'يا رب', 'ان شاء الله', 'ما شاء الله', 'الحمد لله',
        'عسى', 'يس', 'ياهلا', 'مرحبا', 'شلونك', 'شلون', 'وين', 'ايش',
        'وش', 'شفت', 'شوف', 'بس', 'مو', 'ما', 'عادي', 'طيب', 'ماشي',
        'ربي', 'يا اخي', 'يا حبيبي', 'يا عمي', 'والنعم', 'يالله',
    ],
    'ar-egypt': [  # 埃及
        'يا ربي', 'يا ربنا', 'ان شاء الله', 'ما شاء الله', 'الحمد لله',
        'ايوه', 'لأ', 'مش', 'عايز', 'عاوز', 'عايزة', 'عاوزة', 'محتاج',
        'هي', 'هي دي', 'دي', 'ده', 'الي', 'اللي', 'عشان', 'علشان',
        'بس', 'طب', 'طيب', 'تمام', 'ماشي', 'يا حبيبي', 'يا عم', 'يا ست',
        'يا بنت', 'يا ولد', 'يا جماعة', 'يا ناس',
    ],
    'ar-levant': [  # 黎凡特（叙利亚、黎巴嫩、约旦、巴勒斯坦）
        'يا رب', 'ان شاء الله', 'ما شاء الله', 'الحمد لله',
        'اي', 'لا', 'مو', 'منيح', 'طيب', 'مليح', 'زين', 'شو', 'وين',
        'ليش', 'كيف', 'شلون', 'قديش', 'ايش', 'مين', 'عم', 'بده', 'بدي',
        'عندي', 'عندك', 'عنده', 'عندها', 'عندنا', 'عندكم', 'عندهم',
        'يا حبيبي', 'يا عزيزي', 'يا غالي', 'يا حلو', 'يا جميل',
    ],
    'ar-maghreb': [  # 北非（摩洛哥、阿尔及利亚、突尼斯）
        'الله', 'يا ربي', 'ان شاء الله', 'ما شاء الله', 'الحمد لله',
        'واك', 'لا', 'ماشي', 'زينة', 'بزاف', 'خويا', 'خويا', 'ختي',
        'عزيزي', 'غالي', 'باهي', 'مزيان', 'زوين', 'شحال', 'واش',
        'فين', 'علاش', 'كيفاش', 'مين', 'اشنو', 'واشنو', 'دابا',
        'دير', 'قد', 'قداش', 'بغيت', 'بغا', 'باغي', 'خاصني', 'خاصك',
    ]
}

def filter_fillers(text: str, lang: str = 'en') -> str:
    """过滤感叹词和无意义词"""
    if not text:
        return text
    
    # 获取对应语言的感叹词
    fillers = FILLER_WORDS.get(lang, FILLER_WORDS['en'])
    
    # 创建正则表达式模式
    pattern = r'\b(' + '|'.join(re.escape(f) for f in fillers) + r')\b'
    
    # 过滤感叹词
    filtered = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # 清理多余空格
    filtered = re.sub(r'\s+', ' ', filtered).strip()
    
    return filtered

def assess_english_level(texts: List[str]) -> Dict:
    """
    评估英语水平（CEFR等级）
    返回：{'level': 'B1', 'confidence': 0.75, 'explanation': '...'}
    """
    if not texts:
        return {'level': 'Unknown', 'confidence': 0.0, 'explanation': 'No English text found'}
    
    # 合并所有文本
    combined = ' '.join(texts).lower()
    
    # 过滤感叹词
    cleaned = filter_fillers(combined, 'en')
    
    # 统计各等级关键词出现次数
    level_scores = {}
    total_score = 0
    
    for level, keywords in CEFR_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            # 使用单词边界匹配
            pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
            matches = len(re.findall(pattern, cleaned))
            score += matches
        
        level_scores[level] = score
        total_score += score
    
    if total_score == 0:
        return {'level': 'A1', 'confidence': 0.3, 'explanation': 'Basic vocabulary only'}
    
    # 找到最高分的等级
    best_level = max(level_scores.items(), key=lambda x: x[1])
    
    # 计算置信度（最高分占总分的比例）
    confidence = best_level[1] / total_score if total_score > 0 else 0
    
    # 根据分数确定具体等级
    level_map = {'A1': 1, 'A2': 2, 'B1': 3, 'B2': 4, 'C1': 5, 'C2': 6}
    level_num = level_map.get(best_level[0], 1)
    
    # 生成解释
    explanations = {
        'A1': '初学者水平，使用基本日常用语和简单句子',
        'A2': '初级水平，能进行简单日常交流，描述熟悉话题',
        'B1': '中级水平，能处理工作、学习、旅行中的常见场景',
        'B2': '中高级水平，能进行专业领域讨论，表达复杂观点',
        'C1': '高级水平，流利自然，能处理复杂学术和专业话题',
        'C2': '精通水平，接近母语者，能理解并表达细微差别'
    }
    
    explanation = explanations.get(best_level[0], 'English proficiency detected')
    
    return {
        'level': best_level[0],
        'confidence': round(confidence, 2),
        'explanation': explanation,
        'scores': level_scores
    }

def identify_arabic_region(texts: List[str]) -> Dict:
    """
    识别阿拉伯语地区
    返回：{'primary': 'ar-gulf', 'confidence': 0.8, 'distribution': {...}}
    """
    if not texts:
        return {'primary': 'Unknown', 'confidence': 0.0, 'distribution': {}}
    
    # 合并所有文本
    combined = ' '.join(texts)
    
    # 过滤感叹词
    cleaned = filter_fillers(combined, 'ar')
    
    # 统计各地区特征词出现次数
    region_scores = {}
    total_score = 0
    
    for region, keywords in ARABIC_REGIONAL_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            # 阿拉伯语需要精确匹配
            if keyword in cleaned:
                score += 1
        
        region_scores[region] = score
        total_score += score
    
    if total_score == 0:
        # 如果没有检测到特征词，尝试基于常见词判断
        return {'primary': 'Modern Standard Arabic', 'confidence': 0.3, 'distribution': {}}
    
    # 找到最主要的地区
    primary_region = max(region_scores.items(), key=lambda x: x[1])
    
    # 计算置信度
    confidence = primary_region[1] / total_score if total_score > 0 else 0
    
    # 地区名称映射
    region_names = {
        'ar-gulf': '海湾地区阿拉伯语（沙特、阿联酋、卡塔尔等）',
        'ar-egypt': '埃及阿拉伯语',
        'ar-levant': '黎凡特阿拉伯语（叙利亚、黎巴嫩、约旦、巴勒斯坦）',
        'ar-maghreb': '北非阿拉伯语（摩洛哥、阿尔及利亚、突尼斯）'
    }
    
    # 计算分布比例
    distribution = {}
    for region, score in region_scores.items():
        if score > 0:
            pct = round(score / total_score * 100)
            distribution[region_names.get(region, region)] = f'{pct}%'
    
    return {
        'primary': region_names.get(primary_region[0], primary_region[0]),
        'confidence': round(confidence, 2),
        'distribution': distribution,
        'scores': region_scores
    }

def analyze_language_summary(comments: List[Dict], speeches: List[Dict]) -> Dict:
    """
    对整场直播进行总结性语言分析
    注意：只分析主播话术（speeches），评论数据完全忽略
    """
    # 分离不同语言的文本（仅来自话术）
    english_texts = []
    arabic_texts = []
    chinese_texts = []
    other_texts = []
    
    # 只处理话术，完全忽略评论
    for speech in speeches:
        text = speech.get('text', '')
        lang = speech.get('lang', '')
        
        if not text or not lang:
            continue
            
        if lang.startswith('en'):
            english_texts.append(text)
        elif lang.startswith('ar'):
            arabic_texts.append(text)
        elif lang == 'zh':
            chinese_texts.append(text)
        else:
            other_texts.append(text)
    
    # 话术为空时，直接返回无数据
    if not speeches:
        return {
            'overall_stats': {
                'total_speeches': 0,
                'english_count': 0,
                'arabic_count': 0,
                'chinese_count': 0,
                'other_count': 0
            },
            'english_analysis': None,
            'arabic_analysis': None,
            'chinese_analysis': None,
            'summary': '本场无主播话术数据，无法进行语言分析'
        }
    
    # 分析结果
    analysis = {
        'overall_stats': {
            'total_speeches': len(speeches),
            'english_count': len(english_texts),
            'arabic_count': len(arabic_texts),
            'chinese_count': len(chinese_texts),
            'other_count': len(other_texts)
        },
        'english_analysis': None,
        'arabic_analysis': None,
        'chinese_analysis': None,
        'summary': ''
    }
    
    # 英语分析
    if english_texts:
        analysis['english_analysis'] = assess_english_level(english_texts)
    
    # 阿拉伯语分析
    if arabic_texts:
        analysis['arabic_analysis'] = identify_arabic_region(arabic_texts)
    
    # 中文分析（简单判断）
    if chinese_texts:
        # 检查是否包含方言特征
        has_dialect = any('啦' in text or '咩' in text or '嘅' in text for text in chinese_texts[:20])
        analysis['chinese_analysis'] = {
            'is_standard': not has_dialect,
            'has_dialect_features': has_dialect,
            'note': '检测到标准中文' + ('，含少量方言特征' if has_dialect else '')
        }
    
    # 生成总结
    summary_parts = []
    
    if analysis['english_analysis']:
        eng = analysis['english_analysis']
        summary_parts.append(
            f"英语水平：{eng['level']}级（{eng['explanation']}），置信度{eng['confidence']*100:.0f}%"
        )
    
    if analysis['arabic_analysis']:
        ar = analysis['arabic_analysis']
        summary_parts.append(
            f"阿拉伯语地区：{ar['primary']}，置信度{ar['confidence']*100:.0f}%"
        )
        if ar['distribution']:
            dist_str = '，'.join([f'{k} {v}' for k, v in ar['distribution'].items()])
            summary_parts[-1] += f"，分布：{dist_str}"
    
    if chinese_texts:
        summary_parts.append(f"中文内容：{len(chinese_texts)}条，主要为标准中文")
    
    if other_texts:
        # 尝试识别其他语言
        other_langs = set()
        for text in other_texts[:10]:  # 只检查前10条
            # 简单语言检测（基于字符集）
            if re.search(r'[\u0400-\u04FF]', text):  # 西里尔字母
                other_langs.add('俄语/斯拉夫语系')
            elif re.search(r'[\uAC00-\uD7AF]', text):  # 韩文
                other_langs.add('韩语')
            elif re.search(r'[\u3040-\u309F\u30A0-\u30FF]', text):  # 日文
                other_langs.add('日语')
            elif re.search(r'[\u0E00-\u0E7F]', text):  # 泰文
                other_langs.add('泰语')
        
        if other_langs:
            summary_parts.append(f"其他语言：{', '.join(other_langs)}")
        else:
            summary_parts.append(f"其他语言：{len(other_texts)}条未识别内容")
    
    if not summary_parts:
        summary_parts.append("本场直播语言数据不足，无法进行详细分析")
    
    analysis['summary'] = ' | '.join(summary_parts)
    
    return analysis

def generate_detailed_report(analysis: Dict) -> str:
    """生成详细的语言分析报告"""
    report = []
    
    # 总体统计
    stats = analysis['overall_stats']
    report.append("📊 语言总体统计")
    report.append(f"  评论总数：{stats['total_comments']}")
    report.append(f"  话术总数：{stats['total_speeches']}")
    report.append(f"  英语内容：{stats['english_count']}条")
    report.append(f"  阿拉伯语：{stats['arabic_count']}条")
    report.append(f"  中文内容：{stats['chinese_count']}条")
    report.append(f"  其他语言：{stats['other_count']}条")
    report.append("")
    
    # 英语详细分析
    if analysis['english_analysis']:
        eng = analysis['english_analysis']
        report.append("🇬🇧 英语水平分析")
        report.append(f"  等级评估：{eng['level']}")
        report.append(f"  置信程度：{eng['confidence']*100:.0f}%")
        report.append(f"  特点描述：{eng['explanation']}")
        
        # 显示各等级分数
        if 'scores' in eng:
            report.append("  等级分数分布：")
            for level in ['A1', 'A2', 'B1', 'B2', 'C1', 'C2']:
                score = eng['scores'].get(level, 0)
                if score > 0:
                    report.append(f"    {level}: {score}分")
        report.append("")
    
    # 阿拉伯语详细分析
    if analysis['arabic_analysis']:
        ar = analysis['arabic_analysis']
        report.append("🇸🇦 阿拉伯语地区分析")
        report.append(f"  主要地区：{ar['primary']}")
        report.append(f"  置信程度：{ar['confidence']*100:.0f}%")
        
        if ar['distribution']:
            report.append("  地区分布：")
            for region, pct in ar['distribution'].items():
                report.append(f"    {region}: {pct}")
        
        if 'scores' in ar:
            report.append("  特征词匹配：")
            for region, score in ar['scores'].items():
                if score > 0:
                    region_name = {
                        'ar-gulf': '海湾', 'ar-egypt': '埃及',
                        'ar-levant': '黎凡特', 'ar-maghreb': '北非'
                    }.get(region, region)
                    report.append(f"    {region_name}: {score}个特征词")
        report.append("")
    
    # 中文分析
    if analysis['chinese_analysis']:
        zh = analysis['chinese_analysis']
        report.append("🇨🇳 中文分析")
        report.append(f"  是否标准中文：{'是' if zh['is_standard'] else '否'}")
        report.append(f"  方言特征：{'有' if zh['has_dialect_features'] else '无'}")
        report.append(f"  备注：{zh['note']}")
        report.append("")
    
    # 总结
    report.append("📋 分析总结")
    report.append(f"  {analysis['summary']}")
    
    return '\n'.join(report)

# 测试代码
if __name__ == '__main__':
    # 测试数据
    test_comments = [
        {'content': 'Hello everyone, welcome to my live stream!', 'lang': 'en'},
        {'content': 'Thank you for the gift, I really appreciate it!', 'lang': 'en'},
        {'content': 'Today we will discuss about AI technology and its impact.', 'lang': 'en'},
        {'content': 'مرحبا بكم في البث المباشر', 'lang': 'ar'},
        {'content': 'شلونكم يا جماعة؟', 'lang': 'ar-gulf'},
        {'content': '大家好，欢迎来到我的直播间！', 'lang': 'zh'},
    ]
    
    test_speeches = [
        {'text': 'In my opinion, the future of AI is very promising.', 'lang': 'en'},
        {'text': 'We need to consider the ethical implications carefully.', 'lang': 'en'},
        {'text': 'السلام عليكم ورحمة الله وبركاته', 'lang': 'ar'},
        {'text': 'وان شاء الله بتكون معاكم طول البث', 'lang': 'ar-egypt'},
    ]
    
    print("测试语言分析模块...")
    analysis = analyze_language_summary(test_comments, test_speeches)
    report = generate_detailed_report(analysis)
    print(report)