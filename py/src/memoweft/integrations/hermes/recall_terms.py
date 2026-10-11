"""Bounded, read-only multilingual recall vocabulary.

These labels help select existing claims; they never become Evidence, aliases,
or facts. Equivalence, directed categories, and practical constraint triggers
are intentionally separate. Unknown words retain ordinary lexical behavior.
No stemming, edit-distance guesses, single-letter expansion, or model calls.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

_TRADITIONAL = str.maketrans(
    "車會書圖館備過敏怎麼選開攝影麵條筆記預約電腦體運動時週騎學習歡貓飯話語樂藝畫讀遊網線間愛無鹽糖發現雞蝦魚",
    "车会书图馆备过敏怎么选开摄影面条笔记预约电脑体运动时周骑学习欢猫饭话语乐艺画读游网线间爱无盐糖发现鸡虾鱼",
)


def normalize(text: str) -> str:
    if text.isascii():
        return text.lower()
    normalized = unicodedata.normalize("NFKC", text).casefold().translate(_TRADITIONAL)
    # Remove tone marks on Latin pinyin only, not combining marks in other scripts.
    if any("\u00c0" <= c <= "\u024f" for c in normalized):
        normalized = unicodedata.normalize("NFD", normalized)
        normalized = re.sub(r"[a-zü]+(?:[\u0300-\u036f]+[a-zü]*)+", lambda m: "".join(c for c in m[0] if not unicodedata.combining(c)), normalized)
    return normalized


# Common equivalent topic phrases. Parent categories live below, not here.
_GROUPS: dict[str, tuple[str, ...]] = {
    "cycling": ("骑行", "单车", "自行车", "骑车", "cycling", "bicycle", "bike", "zixingche", "自型车"),
    "hiking": ("徒步", "远足", "步行旅行", "hiking"),
    "photo": ("摄影", "拍照", "照相", "photography", "sheying"),
    "cooking": ("烹饪", "做饭", "下厨", "cooking"),
    "swimming": ("游泳", "游水", "水中运动", "swimming", "游咏", "youyong"),
    "running": ("跑步", "慢跑", "jogging", "running", "paobu", "跑歩"),
    "reading": ("阅读", "看书", "读书", "reading"),
    "painting": ("绘画", "画画", "作画", "painting"),
    "mountain": ("登山", "爬山", "攀山", "mountaineering"),
    "garden": ("园艺", "种花", "养花", "gardening"),
    "dog": ("狗", "犬类", "dog", "canine"),
    "cat": ("猫", "猫科宠物", "cat"),
    "sichuan": ("川菜", "川味菜", "sichuan cuisine"),
    "cantonese": ("粤菜", "广东菜", "cantonese cuisine"),
    "strawberry": ("草莓", "strawberry"),
    "coffee": ("咖啡", "拿铁", "美式", "coffee", "latte", "kafei", "咖非"),
    "jazz": ("爵士乐", "jazz"),
    "badminton": ("羽毛球", "badminton"),
    "python": ("python",),
    "rose": ("玫瑰", "rose"),
    "meeting": ("开会", "开个会", "会议", "meeting", "kaihui", "开荟"),
    "wfh": ("远程办公", "居家办公", "wfh", "work from home"),
    "deploy": ("部署软件", "发布软件", "deployment", "deploy"),
    "backup": ("备份", "backup", "beifen", "背份"),
    "todo": ("待办事项", "任务清单", "todo", "to-do list"),
    "exercise": ("锻炼", "健身", "workout", "exercise", "jianshen", "建身"),
    "lunch_rest": ("午间休息", "午休", "lunch break", "midday rest"),
    "review": ("代码评审", "code review", "cr", "review代码"),
    "ui": ("用户界面", "界面设计", "ui", "user interface"),
    "qa": ("质量保证", "质量检测", "qa", "quality assurance"),
    "insomnia": ("入睡困难", "睡不着", "失眠", "insomnia"),
    "motion_sickness": ("晕动", "晕车", "坐车难受", "motion sickness"),
    "heights": ("高处感到恐惧", "恐高", "怕高", "fear of heights"),
    "crowd": ("拥挤", "人多", "扎堆", "crowded"),
    "light_food": ("清淡饮食", "少油少盐", "吃淡一点", "light meals"),
    "fatigue": ("容易疲劳", "没劲", "累得快", "fatigue"),
    "planning": ("提前规划", "先打算好", "预先安排", "planning ahead"),
    "saving": ("节约开支", "省钱", "少花钱", "saving money"),
    "solitude": ("独处", "一个人待着", "自己待会", "solitude"),
    "walk_commute": ("步行通勤", "走路上班", "步行上班", "walk to work"),
    "cst": ("中华人民共和国标准时间", "北京时间", "中国标准时间", "cst"),
    "scifi": ("科幻小说", "科幻书", "science fiction", "sf"),
    "ai": ("人工智能", "人工智慧", "artificial intelligence", "ai"),
    "transit": ("公共交通", "公交", "公共运输", "public transit"),
    "ux": ("用户体验", "体验设计", "user experience", "ux"),
    "documentary": ("纪录片", "纪实影片", "纪录电影", "documentary"),
    "hiit": ("高强度间歇训练", "间歇训练", "interval training", "hiit"),
    "ebook": ("电子书", "电纸书", "ebook", "e-book"),
    "project_management": ("项目管理", "项目经理任务", "project management", "pm"),
    "weekly_report": ("周报", "每周报告", "周度报告", "weekly report"),
    "peanut": ("花生", "花声", "peanut", "huasheng"),
    "library": ("图书馆", "图书官", "library", "tushuguan"),
    "travel": ("旅行", "出游", "旅游", "travel", "旅型", "luxing"),
    "yoga": ("瑜伽", "瑜珈", "yoga", "yujia"),
    "boat": ("乘船", "坐船", "boat"),
    "tea": ("奶茶", "milk tea", "tea"),
    "monday": ("周一", "星期一", "礼拜一", "monday"),
    "tuesday": ("周二", "星期二", "礼拜二", "tuesday"),
    "wednesday": ("周三", "星期三", "礼拜三", "wednesday"),
    "thursday": ("周四", "星期四", "礼拜四", "thursday"),
    "friday": ("周五", "星期五", "礼拜五", "friday"),
    "saturday": ("周六", "星期六", "礼拜六", "saturday"),
    "sunday": ("周日", "周天", "星期天", "星期日", "sunday"),
    "morning": ("上午", "早上", "早晨", "清晨", "晨间", "morning"),
    "afternoon": ("下午", "午后", "afternoon"),
    "evening": ("晚上", "傍晚", "夜间", "晚间", "evening"),
    "noon": ("中午", "午间", "正午", "noon"),
    "bedtime": ("睡前", "入睡前", "临睡前", "bedtime"),
    "month_end": ("月底", "月末", "month end"),
    "year_end": ("年末", "年底", "年终", "year end"),
    "fortnight": ("每两周", "隔周", "每隔一个星期", "fortnightly"),
    "meditation": ("冥想", "静坐", "meditation"),
    "bookkeeping": ("整理账本", "记账", "财务整理", "账目"),
    "album": ("相片", "照片", "相册", "photo album"),
    # General vocabulary, including topics beyond the development corpus.
    "ski": ("滑雪", "skiing"), "fishing": ("钓鱼", "垂钓", "fishing"),
    "singing": ("唱歌", "歌唱", "singing"), "craft": ("手工", "手作", "crafting"),
    "piano": ("钢琴", "piano"), "rabbit": ("兔子", "rabbit"),
    "mango": ("芒果", "mango"), "podcast": ("播客", "podcast"),
    "deadline": ("截止日期", "deadline", "ddl"),
    "llm": ("大型语言模型", "大语言模型", "llm", "large language model"),
    "sql": ("结构化查询语言", "sql", "structured query language"),
    "api": ("应用程序接口", "api", "application programming interface"),
    "pdf": ("便携式文档格式", "pdf", "portable document format"),
    "notes": ("笔记", "biji", "notes"), "booking": ("预约", "yuyue", "booking"),
}

_TERM_KEYS = {normalize(term): key for key, terms in _GROUPS.items() for term in terms}


def _term_pattern(term: str) -> str:
    if term.isascii():
        return rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])"
    if len(term) == 1:
        # A single CJK animal label needs a plausible word boundary; 猫眼石
        # and 狗尾草 are different concepts, not pet aliases.
        return re.escape(term) + r"(?=$|[^\u3400-\u4dbf\u4e00-\u9fff]|的|粮|食|毛|窝|叫|咪|咖|不|能|会|要|用品|玩具|应该|需要|可以)"
    return re.escape(term)


_TERMS = re.compile("|".join(
    _term_pattern(term)
    for term in sorted(_TERM_KEYS, key=lambda term: (-len(term), term))
))


def topics(text: str) -> frozenset[str]:
    return frozenset(_TERM_KEYS[match[0]] for match in _TERMS.finditer(normalize(text)))


# Directed query categories: a dog can answer a pet question; a generic pet
# claim does not establish that the user has a dog. No transitive expansion.
_CATEGORIES: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (("宠物", "pet"), frozenset({"dog", "cat", "rabbit"})),
    (("中餐", "中国菜", "chinese cuisine"), frozenset({"sichuan", "cantonese"})),
    (("水果", "fruit", "莓果"), frozenset({"strawberry", "mango"})),
    (("饮料", "beverage"), frozenset({"coffee", "tea"})),
    (("音乐", "music"), frozenset({"jazz", "singing"})),
    (("球类运动", "运动", "sport"), frozenset({"badminton", "exercise", "running", "swimming"})),
    (("编程", "编程语言", "programming"), frozenset({"python", "sql"})),
    (("花卉", "鲜花", "flowers"), frozenset({"rose", "garden"})),
    (("乐器", "instrument"), frozenset({"piano"})),
    (("周末", "weekend"), frozenset({"saturday", "sunday"})),
)


_PATTERNS: dict[tuple[str, ...], re.Pattern[str]] = {}


def _contains(text: str, terms: Iterable[str]) -> bool:
    # Cache vocabulary regexes only; never cache user queries or memory text.
    key = tuple(terms)
    pattern = _PATTERNS.get(key)
    if pattern is None:
        pattern = re.compile("|".join(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])" if term.isascii() else re.escape(term) for term in key))
        _PATTERNS[key] = pattern
    return pattern.search(text) is not None


def query_topics(text: str) -> frozenset[str]:
    normalized = normalize(text)
    return topics(normalized) | frozenset(key for terms, keys in _CATEGORIES if _contains(normalized, terms) for key in keys)


# A recommendation may need an existing personal constraint even without its
# name. Both trigger AND restrictive claim wording are required. This selects
# a precaution for the host; it does not infer ingredients or medical advice.
_CONSTRAINTS = (
    (("泰国菜", "泰餐", "泰式料理"), ("花生",), ("过敏", "不能", "避免")),
    (("拿铁", "奶茶", "冰淇淋", "latte"), ("乳糖",), ("不耐受", "过敏", "不能")),
    (("面包", "意大利面", "烘焙"), ("麸质",), ("不能", "过敏", "避免")),
    (("寿司", "刺身", "海鲜拼盘"), ("海鲜",), ("过敏", "不能", "避免")),
    (("火锅", "烧烤", "自助餐"), ("吃素", "素食"), ("吃素", "素食")),
    (("咖啡", "提神饮料", "能量饮料"), ("咖啡因",), ("不能", "避免", "过敏")),
    (("长途巴士", "大巴", "乘车出游"), ("晕车", "晕动"), ("晕车", "晕动")),
    (("爬山", "登山", "跑步"), ("膝盖",), ("不适", "受伤", "疼痛")),
    (("川菜", "麻辣", "湘菜"), ("辣",), ("不能", "避免", "过敏")),
    (("猫咖", "宠物咖啡馆", "猫咪互动"), ("猫毛",), ("过敏", "不能", "避免")),
)


def constraint_match(query: str, content: str) -> bool:
    q, c = normalize(query), normalize(content)
    return any(_contains(q, triggers) and _contains(c, objects) and _contains(c, restrictions) for triggers, objects, restrictions in _CONSTRAINTS)


# Ambiguous spellings never expand across these incompatible domains.
_DOMAINS = (
    (("苹果", "apple"), ("手机", "电脑", "设备", "phone", "computer"), ("吃", "水果", "fruit")),
    (("小米", "xiaomi", "millet"), ("手机", "电脑", "设备"), ("粥", "粮食", "煮")),
    (("python", "蟒蛇"), ("编程", "代码", "programming"), ("蛇", "宠物", "喂", "snake")),
    (("java", "爪哇"), ("编程", "代码", "programming"), ("岛", "咖啡", "island")),
    (("单车", "自行车", "骑行"), ("骑", "自行车", "bike"), ("汽车", "car", "手机", "电脑", "品牌")),
    (("花生",), ("吃", "过敏", "花生。"), ("花生壳", "域名", "服务器")),
    (("海鲜",), ("吃", "过敏", "喜欢海鲜"), ("软件", "安装")),
    (("rust", "铁锈"), ("编程", "代码", "programming"), ("铁锈", "去除", "removal")),
)


def incompatible(query: str, content: str) -> bool:
    q, c = normalize(query), normalize(content)
    return any(_contains(q, names) and _contains(c, names) and (
        _contains(q, left) and _contains(c, right) and not _contains(c, left)
        or _contains(q, right) and _contains(c, left) and not _contains(c, right)
    ) for names, left, right in _DOMAINS)


# Asked-for details prevent a matching topic alone from flooding five slots.
# Unknown detail requests keep the established lexical behavior.
_DETAILS = (
    (("坡度", "路线", "道路"), ("平路", "山路", "坡", "路线", "公里", "路径")),
    (("甜度", "加糖", "糖分"), ("糖", "甜")),
    (("哪天", "日期", "星期几"), ("周", "星期", "日期", "礼拜")),
    (("肉类", "食物", "吃什么肉"), ("吃", "食", "粮", "肉")),
    (("距离", "多远", "长度"), ("公里", "距离", "长度", "米")),
    (("座位", "seat", "坐哪里"), ("座位", "靠窗", "走道", "seat")),
    (("题材", "类型", "内容"), ("小说", "科幻", "题材", "类型", "内容")),
    (("地点", "场所", "环境"), ("室内", "室外", "地点", "泳池", "场所", "环境")),
    (("时段", "几点", "什么时候", "时间"), ("周", "星期", "上午", "下午", "晚上", "傍晚", "晨", "睡前", "月底", "月末", "年末", "时间", "点", "时")),
    (("色彩", "颜色模式", "图片颜色"), ("黑白", "彩色", "色彩", "颜色")),
    (("温度", "冷热"), ("热", "冰", "冷", "温度")),
)


def detail_compatible(query: str, content: str) -> bool:
    q, c = normalize(query), normalize(content)
    requested = [terms for cues, terms in _DETAILS if _contains(q, cues)]
    return not requested or any(_contains(c, terms) for terms in requested)


@dataclass(frozen=True)
class QueryTerms:
    """Query-local prepared predicates; lifetime is one recall invocation."""

    wanted: frozenset[str]
    constraints: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]
    excluded_domains: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...]
    details: tuple[tuple[str, ...], ...]

    @classmethod
    def parse(cls, query: str) -> QueryTerms:
        q = normalize(query)
        exclusions: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []
        for names, left, right in _DOMAINS:
            if _contains(q, names):
                if _contains(q, left):
                    exclusions.append((names, right, left))
                if _contains(q, right):
                    exclusions.append((names, left, right))
        return cls(query_topics(q), tuple((objects, restrictions) for triggers, objects, restrictions in _CONSTRAINTS if _contains(q, triggers)), tuple(exclusions), tuple(terms for cues, terms in _DETAILS if _contains(q, cues)))

    def constraint(self, text: str) -> bool:
        c = normalize(text)
        return any(_contains(c, objects) and _contains(c, restrictions) for objects, restrictions in self.constraints)

    def incompatible(self, text: str) -> bool:
        c = normalize(text)
        return any(_contains(c, names) and _contains(c, excluded) and not _contains(c, allowed) for names, excluded, allowed in self.excluded_domains)

    def detail(self, text: str) -> bool:
        c = normalize(text)
        return not self.details or any(_contains(c, terms) for terms in self.details)
