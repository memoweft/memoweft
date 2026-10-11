"""Freeze wholly fictional paraphrase cases; never used by the quality runner.

Each seed has three development queries. Challenge seeds have different facts
and vocabulary and two queries. Labels are explicit; no model labels the corpus.
"""
import json
from pathlib import Path

DEV = {
    "synonym": [
        ("我周末常去骑行。", "单车路线怎么选", "自行车活动有什么建议", "骑车适合我吗"),
        ("我一直喜欢徒步。", "远足装备怎么选", "步行旅行有什么建议", "hiking适合我吗"),
        ("我喜欢摄影。", "拍照器材怎么选", "照相活动有什么建议", "photography适合我吗"),
        ("我平时喜欢烹饪。", "做饭工具怎么选", "下厨有什么建议", "cooking适合我吗"),
        ("我喜欢游泳。", "swimming装备怎么选", "水中运动有什么建议", "游水适合我吗"),
        ("我经常跑步。", "慢跑路线怎么选", "jogging有什么建议", "running适合我吗"),
        ("我喜欢阅读纸质书。", "看书活动怎么选", "读书有什么建议", "reading适合我吗"),
        ("我喜欢绘画。", "画画工具怎么选", "painting有什么建议", "作画适合我吗"),
        ("我喜欢登山。", "爬山装备怎么选", "mountaineering有什么建议", "攀山适合我吗"),
        ("我喜欢园艺。", "种花工具怎么选", "养花有什么建议", "gardening适合我吗"),
    ],
    "hierarchy": [
        ("我养了一只狗。", "宠物用品怎么选", "犬类用品有什么建议", "pet需要什么"),
        ("我养了一只猫。", "宠物玩具怎么选", "猫科宠物有什么建议", "pet吃什么"),
        ("我喜欢川菜。", "中餐怎么选", "中国菜有什么建议", "川味菜适合我吗"),
        ("我喜欢粤菜。", "中餐怎么选", "中国菜有什么建议", "广东菜适合我吗"),
        ("我喜欢草莓。", "水果怎么选", "fruit有什么建议", "莓果适合我吗"),
        ("我喜欢拿铁。", "咖啡怎么选", "饮料有什么建议", "coffee适合我吗"),
        ("我喜欢爵士乐。", "音乐怎么选", "music有什么建议", "jazz适合我吗"),
        ("我经常打羽毛球。", "球类运动怎么选", "运动有什么建议", "badminton适合我吗"),
        ("我学过Python。", "编程语言怎么选", "programming有什么建议", "编程有什么建议"),
        ("我喜欢玫瑰。", "花卉怎么选", "flowers有什么建议", "鲜花适合我吗"),
    ],
    "bilingual": [
        ("我每周一开会。", "meeting安排在哪天", "MEETING是什么时候", "开个会选哪天"),
        ("我喜欢远程办公。", "WFH适合我吗", "work from home有什么建议", "居家办公怎么安排"),
        ("我每周五部署软件。", "deployment选哪天", "deploy是什么时候", "发布软件怎么安排"),
        ("我需要定期备份文件。", "backup怎么安排", "BACKUP有什么建议", "数据备份怎么安排"),
        ("我习惯先写待办事项。", "TODO怎么安排", "to-do list有什么建议", "任务清单怎么安排"),
        ("我习惯每天锻炼。", "workout怎么安排", "exercise有什么建议", "健身怎么安排"),
        ("我习惯午间休息。", "lunch break怎么安排", "午休有什么建议", "midday rest怎么安排"),
        ("我每周做代码评审。", "code review怎么安排", "CR有什么建议", "review代码怎么安排"),
        ("我喜欢用户界面设计。", "UI有什么建议", "user interface怎么选", "界面设计适合我吗"),
        ("我做过质量保证。", "QA适合我吗", "quality assurance有什么建议", "质量检测怎么安排"),
    ],
    "colloquial": [
        ("我最近入睡困难。", "睡不着怎么安排作息", "失眠有什么建议", "insomnia怎么办"),
        ("我乘车会晕动。", "晕车时该注意什么", "坐车难受怎么处理", "motion sickness有什么建议"),
        ("我对高处感到恐惧。", "恐高的活动怎么选", "怕高怎么安排", "fear of heights怎么办"),
        ("我不喜欢拥挤环境。", "人多的场所怎么选", "扎堆活动适合我吗", "crowded场所适合我吗"),
        ("我喜欢清淡饮食。", "少油少盐怎么点餐", "吃淡一点怎么选", "light meals有什么建议"),
        ("我容易疲劳。", "没劲的时候怎么安排", "累得快怎么安排", "fatigue有什么建议"),
        ("我喜欢提前规划。", "先打算好有什么建议", "预先安排怎么做", "planning ahead适合我吗"),
        ("我倾向节约开支。", "省钱方案怎么选", "少花钱有什么建议", "saving money怎么安排"),
        ("我喜欢独处。", "一个人待着怎么安排", "自己待会有什么建议", "solitude适合我吗"),
        ("我喜欢步行通勤。", "走路上班怎么安排", "步行上班有什么建议", "walk to work适合我吗"),
    ],
    "abbreviation": [
        ("我使用中华人民共和国标准时间。", "北京时间怎么安排", "中国标准时间是什么", "CST时间怎么选"),
        ("我喜欢科幻小说。", "科幻书怎么选", "science fiction有什么建议", "SF读物适合我吗"),
        ("我研究人工智能。", "AI方向怎么选", "artificial intelligence有什么建议", "人工智慧适合我吗"),
        ("我习惯公共交通出行。", "公交怎么选", "public transit有什么建议", "公共运输适合我吗"),
        ("我关注用户体验。", "UX方向怎么选", "user experience有什么建议", "体验设计怎么选"),
        ("我常看纪录片。", "documentary有什么建议", "纪实影片怎么选", "纪录电影适合我吗"),
        ("我每周做高强度间歇训练。", "HIIT怎么安排", "间歇训练有什么建议", "interval training适合我吗"),
        ("我喜欢电子书。", "ebook怎么选", "电纸书有什么建议", "e-book适合我吗"),
        ("我负责项目管理。", "PM怎么安排", "project management有什么建议", "项目经理任务怎么安排"),
        ("我每周写周报。", "weekly report怎么安排", "每周报告怎么写", "周度报告怎么安排"),
    ],
    "typo": [
        ("我周末骑自行车。", "自型车路线怎么选", "zixingche怎么安排", "自行車适合我吗"),
        ("我喜欢咖啡。", "咖非怎么选", "kafei有什么建议", "咖啡怎麼選"),
        ("我每周开会。", "kaihui怎么安排", "開會怎么安排", "开荟选哪天"),
        ("我喜欢游泳。", "游咏装备怎么选", "youyong有什么建议", "游泳怎麼安排"),
        ("我喜欢健身。", "建身怎么安排", "jianshen有什么建议", "健身怎麼安排"),
        ("我有花生过敏。", "花声过敏怎么点餐", "huasheng有什么建议", "花生過敏怎么点餐"),
        ("我习惯备份文件。", "備份怎么安排", "beifen有什么建议", "背份文件怎么安排"),
        ("我常去图书馆。", "圖書館怎么安排", "tushuguan有什么建议", "图书官怎么安排"),
        ("我喜欢旅行。", "lǚxíng怎么安排", "旅型有什么建议", "旅行怎麼安排"),
        ("我每周练瑜伽。", "瑜珈怎么安排", "yujia有什么建议", "瑜伽怎麼安排"),
    ],
    "indirect": [
        ("我对花生过敏。", "帮我点个泰国菜", "推荐泰餐给我", "泰式料理怎么选"),
        ("我有乳糖不耐受。", "拿铁怎么点", "奶茶怎么选", "冰淇淋有什么建议"),
        ("我不能吃麸质。", "面包怎么选", "意大利面怎么点", "烘焙食品怎么选"),
        ("我对海鲜过敏。", "寿司怎么点", "海鲜拼盘怎么选", "刺身有什么建议"),
        ("我吃素。", "帮我点个火锅", "烧烤怎么选", "自助餐有什么建议"),
        ("我不能摄入咖啡因。", "提神饮料怎么选", "咖啡怎么点", "能量饮料有什么建议"),
        ("我有晕车问题。", "长途巴士怎么安排", "大巴旅行怎么准备", "乘车出游有什么建议"),
        ("我有膝盖不适。", "爬山怎么安排", "跑步路线怎么选", "登山有什么建议"),
        ("我不能吃辣。", "川菜怎么点", "麻辣火锅怎么选", "湘菜有什么建议"),
        ("我对猫毛过敏。", "猫咖怎么选", "宠物咖啡馆有什么建议", "猫咪互动怎么安排"),
    ],
    "time": [
        ("我每周六上午骑行。", "周末早上骑车怎么安排", "星期六晨间单车怎么安排", "Saturday morning自行车活动怎么安排"),
        ("我每周日晚上读书。", "周日夜间看书怎么安排", "星期天晚间阅读怎么安排", "Sunday evening读书怎么安排"),
        ("我每周一上午开会。", "星期一早上meeting怎么安排", "Monday morning开会怎么安排", "礼拜一晨间会议怎么安排"),
        ("我每天中午休息。", "午间休息怎么安排", "noon午休怎么安排", "正午休息怎么安排"),
        ("我每晚睡前冥想。", "入睡前静坐怎么安排", "bedtime meditation怎么安排", "临睡前冥想怎么安排"),
        ("我每月月底整理账本。", "月末记账怎么安排", "month end财务整理怎么安排", "月底账目怎么安排"),
        ("我每年年末整理相片。", "年底照片怎么整理", "year end相册怎么安排", "年终照片怎么整理"),
        ("我每周五傍晚健身。", "星期五晚上锻炼怎么安排", "Friday evening健身怎么安排", "礼拜五晚间运动怎么安排"),
        ("我每两周清理书架。", "隔周整理书架怎么安排", "每隔一个星期书架怎么整理", "fortnightly书架整理怎么安排"),
        ("我每周三下午学习。", "星期三午后读书怎么安排", "Wednesday afternoon学习怎么安排", "礼拜三下午学习怎么安排"),
    ],
    "correction": [
        ("我喜欢骑行时走山路。", "更正，现在只走平路。", "单车路线怎么选", "自行车适合哪种路线", "骑行应该走哪里"),
        ("我开会喜欢上午。", "更正，现在只在下午。", "meeting选什么时候", "会议时段怎么选", "开会怎么安排"),
        ("我读书喜欢纸质版。", "更正，现在只看电子版。", "阅读载体怎么选", "看书版本怎么选", "读书该买什么版本"),
        ("我喝咖啡喜欢加糖。", "更正，现在只喝无糖。", "kafei怎么点", "咖啡应该加糖吗", "coffee怎么选"),
        ("我每周三游泳。", "更正，现在改周五。", "游水选哪天", "swimming怎么安排", "游泳选哪天"),
        ("我以前喜欢辣味川菜。", "更正，现在不能吃辣。", "中餐怎么点", "中国菜应该怎么选", "川菜口味怎么选"),
        ("我旅行喜欢坐飞机。", "更正，现在只坐火车。", "出游交通怎么选", "travel怎么安排", "旅行怎么出行"),
        ("我锻炼喜欢早晨。", "更正，现在只选傍晚。", "workout选什么时候", "健身时段怎么选", "锻炼时间怎么安排"),
        ("我摄影习惯带大相机。", "更正，现在只带小相机。", "拍照器材怎么选", "照相装备怎么选", "摄影该带什么"),
        ("我做饭喜欢放花生。", "更正，现在对花生过敏。", "烹饪材料怎么选", "下厨应该避开什么", "做饭怎么安排"),
    ],
    "nearby": [
        ("我的骑行路线必须是平路。", "单车路线坡度怎么选", "骑车路线坡度是什么", "自行车道路坡度怎么选", "我的骑行头盔是蓝色。"),
        ("我的咖啡必须无糖。", "咖啡甜度怎么选", "coffee应该加糖吗", "咖啡糖分怎么选", "我的咖啡杯是蓝色。"),
        ("我开会只能周一。", "meeting选哪天", "会议日期怎么选", "开会安排在星期几", "我开会用蓝色笔记本。"),
        ("我的宠物狗不能吃鸡肉。", "狗粮的肉类怎么选", "宠物食物怎么选", "犬类吃什么肉", "我的宠物狗喜欢蓝色玩具。"),
        ("我跑步每次只跑三公里。", "慢跑距离怎么选", "running应该跑多远", "跑步长度怎么安排", "我的跑步鞋是蓝色。"),
        ("我乘船喜欢靠窗座位。", "坐船座位怎么选", "boat seat怎么选", "乘船应该坐哪里", "我乘船带蓝色水壶。"),
        ("我看书只看科幻小说。", "阅读题材怎么选", "读书类型怎么选", "看书内容怎么选", "我的看书台灯是蓝色。"),
        ("我游泳只选室内泳池。", "游水地点怎么选", "swimming场所怎么选", "游泳环境怎么选", "我的游泳帽是蓝色。"),
        ("我锻炼只在傍晚。", "workout时间怎么选", "健身时段怎么选", "锻炼几点合适", "我的锻炼毛巾是蓝色。"),
        ("我摄影只拍黑白照片。", "拍照色彩怎么选", "照相颜色模式怎么选", "摄影图片颜色怎么选", "我的摄影包是蓝色。"),
    ],
    "polysemy": [
        ("我喜欢吃苹果。", "苹果手机怎么设置", "苹果电脑怎么选", "Apple设备怎么升级"),
        ("我使用苹果手机。", "吃苹果有什么建议", "苹果水果怎么选", "apple fruit怎么吃"),
        ("我喜欢吃小米粥。", "小米手机怎么设置", "小米电脑怎么选", "Xiaomi设备怎么用"),
        ("我使用小米手机。", "小米粥怎么煮", "小米粮食怎么选", "millet粥怎么吃"),
        ("我喜欢吃梨。", "离职怎么安排", "离线模式怎么设置", "分离变量怎么做"),
        ("我喜欢编程Python。", "蟒蛇宠物怎么养", "python snake怎么喂", "蛇的饲料怎么选"),
        ("我喜欢Java编程。", "爪哇岛旅行怎么安排", "java island怎么游览", "爪哇咖啡怎么点"),
        ("我有蓝牙耳机。", "牙齿护理怎么安排", "洗牙有什么建议", "牙科怎么预约"),
        ("我喜欢骑自行车。", "汽车单车道怎么驾驶", "小汽车车道怎么选", "汽车道路怎么安排"),
        ("我喜欢花生。", "花生壳服务器怎么设置", "花生壳域名怎么配置", "域名花生壳怎么选"),
    ],
    "lifecycle": [(state, query) for state, query in [
        ("forgotten", "单车路线怎么选"), ("invalid", "骑行路线怎么选"),
        ("archived", "自行车怎么安排"), ("muted", "骑行怎么安排"),
        ("expired", "骑行怎么安排"), ("future", "骑行怎么安排"),
        ("other_account", "单车路线怎么选"), ("local_denied", "自行车怎么安排"),
        ("cloud_denied", "骑行怎么安排"), ("cross_evidence", "自行车怎么安排"),
    ]],
}

# Initial holdout, exposed during tuning; permanently retained as challenge data.
HOLD = {
    "synonym": [("我喜欢滑雪。", "skiing怎么安排", "雪上运动怎么选"), ("我喜欢钓鱼。", "垂钓装备怎么选", "fishing怎么安排"), ("我喜欢唱歌。", "歌唱怎么安排", "singing怎么选"), ("我喜欢手工。", "crafting怎么安排", "手作活动怎么选"), ("我喜欢骑摩托车。", "motorcycling怎么安排", "摩托骑行怎么选")],
    "hierarchy": [("我养兔子。", "宠物怎么喂", "pet用品怎么选"), ("我喜欢闽菜。", "中餐怎么点", "中国菜怎么选"), ("我喜欢芒果。", "水果怎么选", "fruit怎么点"), ("我弹钢琴。", "乐器怎么选", "instrument怎么练"), ("我喜欢牡丹。", "花卉怎么选", "鲜花怎么选")],
    "bilingual": [("我习惯视频通话。", "video call怎么安排", "VC怎么安排"), ("我需要截止日期提醒。", "deadline怎么安排", "DDL怎么提醒"), ("我使用双因素认证。", "2FA怎么设置", "two factor authentication怎么选"), ("我常做头脑风暴。", "brainstorm怎么安排", "brainstorming怎么准备"), ("我喜欢播客。", "podcast怎么选", "PODCAST怎么安排")],
    "colloquial": [("我患有近视。", "看不清远处怎么安排", "myopia怎么选眼镜"), ("我容易紧张。", "心里发慌怎么安排", "nervous怎么应对"), ("我避免熬夜。", "别睡太晚怎么安排", "早些睡怎么安排"), ("我喜欢热闹。", "人多一点怎么选", "lively活动怎么安排"), ("我倾向量入为出。", "预算以内怎么选", "不超支怎么安排")],
    "abbreviation": [("我研究大型语言模型。", "LLM怎么选", "large language model怎么研究"), ("我喜欢角色扮演游戏。", "RPG怎么选", "role playing game怎么选"), ("我用便携式文档格式。", "PDF怎么选", "portable document format怎么安排"), ("我学习结构化查询语言。", "SQL怎么练", "structured query language怎么安排"), ("我关注应用程序接口。", "API怎么设计", "application programming interface怎么安排")],
    "typo": [("我喜欢面条。", "麵條怎么选", "miantiao怎么点"), ("我常写笔记。", "筆記怎么安排", "biji怎么写"), ("我每周跑步。", "pǎobù怎么安排", "跑歩怎么安排"), ("我需要预约。", "預約怎么安排", "yuyue怎么安排"), ("我每周摄影。", "攝影怎么安排", "sheying怎么准备")],
    "indirect": [("我对芝麻过敏。", "芝麻酱拌面怎么点", "sesame dressing怎么选"), ("我需要少盐。", "腌菜怎么选", "咸味零食怎么选"), ("我避免酒精。", "鸡尾酒怎么点", "cocktail怎么选"), ("我对鸡蛋过敏。", "蛋糕怎么点", "omelette怎么选"), ("我有恐高问题。", "高空观景怎么安排", "摩天轮怎么选")],
    "time": [("我每周二清理电脑。", "Tuesday电脑整理怎么安排", "星期二电脑清理怎么安排"), ("我每周四练琴。", "Thursday乐器练习怎么安排", "礼拜四练琴怎么安排"), ("我每季末整理衣柜。", "季度末衣柜怎么整理", "quarter end衣柜怎么安排"), ("我每个工作日午后散步。", "weekday afternoon散步怎么安排", "平日下午步行怎么安排"), ("我每天清晨喝水。", "early morning补水怎么安排", "早上饮水怎么安排")],
    "correction": [("我滑雪喜欢陡坡。", "更正，现在只选缓坡。", "skiing坡度怎么选", "滑雪路线怎么选"), ("我钓鱼每次三小时。", "更正，现在只用一小时。", "fishing时长怎么选", "垂钓多久合适"), ("我周二练钢琴。", "更正，现在改周四。", "piano选哪天", "钢琴练习怎么安排"), ("我每月初备份。", "更正，现在改月末。", "backup时间怎么选", "备份选什么时候"), ("我喜欢冰奶茶。", "更正，现在只喝热的。", "milk tea温度怎么选", "奶茶该点什么温度")],
    "nearby": [("我滑雪只走缓坡。", "滑雪坡度怎么选", "skiing路线怎么选", "我的滑雪手套是绿色。"), ("我钓鱼只去淡水。", "钓鱼水域怎么选", "fishing地点怎么选", "我的钓鱼帽是绿色。"), ("我钢琴只在晚上练。", "钢琴时间怎么安排", "piano时段怎么选", "我的钢琴凳是绿色。"), ("我备份必须加密。", "备份安全怎么安排", "backup加密怎么设置", "我的备份硬盘是绿色。"), ("我的奶茶必须热的。", "奶茶温度怎么选", "milk tea冷热怎么选", "我的奶茶杯是绿色。")],
    "polysemy": [("我喜欢吃橙子。", "橙色油漆怎么选", "orange paint怎么选"), ("我喜欢打网球。", "网络球形拓扑怎么设置", "网络怎么连接"), ("我学过Rust编程。", "铁锈怎么去除", "rust removal怎么安排"), ("我喜欢吃火龙果。", "火龙游戏怎么设置", "火龙显卡怎么选"), ("我喜欢海鲜。", "海鲜市场软件怎么安装", "软件海鲜市场怎么设置")],
    "lifecycle": [(state, query) for state, query in [("forgotten", "skiing怎么安排"), ("expired", "滑雪怎么安排"), ("other_account", "滑雪怎么安排"), ("cloud_denied", "滑雪怎么安排"), ("cross_evidence", "滑雪怎么安排")]],
}


def build(seeds, split):
    cases = []
    for category, entries in seeds.items():
        for index, entry in enumerate(entries):
            if category == "lifecycle":
                state, query = entry
                content = "我周末骑行。" if split == "dev" else "我周末滑雪。"
                memories = [{"id": "blocked", "content": content, "state": state}]
                queries = [query, "请问，" + query, "请告诉我，" + query] if split == "dev" else [query, "请问，" + query]
                expected = []
            else:
                content, *queries = entry
                memories = [{"id": "target", "content": content}]
                expected = [] if category == "polysemy" else ["target"]
                if category == "correction":
                    successor, *queries = queries
                    memories[0]["state"] = "invalid"
                    memories.append({"id": "current", "content": successor, "predecessor": "target"})
                    expected = ["current"]
                if category == "nearby":
                    *queries, distractor = queries
                    memories.append({"id": "near", "content": distractor})
                    # Multiple nearby memories with different facts, one relevant.
                    memories += [{"id": f"noise-{i}", "content": distractor.replace("蓝色", color).replace("绿色", color)} for i, color in enumerate(["紫色", "橙色", "白色", "黑色", "灰色", "红色"])]
            memories += [{"id": "unrelated", "content": "我收藏虚构的星形邮票。"}, {"id": "other", "content": content if category != "lifecycle" else memories[0]["content"], "state": "other_account"}]
            for variant, query in enumerate(queries):
                cases.append({"id": f"{split}-{category}-{index:02}-{variant}", "seed": f"{split}-{category}-{index:02}", "category": category, "query": query + ("？" if not query.endswith("？") else ""), "tier": "cloud" if category == "lifecycle" and memories[0]["state"] == "cloud_denied" else "local", "memories": memories, "expect": expected, "forbid": [m["id"] for m in memories if m["id"] not in expected]})
    return cases


if __name__ == "__main__":
    root = Path(__file__).parent
    for split, seeds in [("dev", DEV), ("challenge", HOLD)]:
        cases = build(seeds, "dev" if split == "dev" else "holdout")
        assert len(cases) == (360 if split == "dev" else 120)
        (root / f"{split}.json").write_text(json.dumps({"version": 1, "provenance": "Entirely fictional, manually labeled. No real people, organizations, addresses, or memory stores. Frozen before tuning. Distinct held-out seeds.", "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
