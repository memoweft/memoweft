"""Final fresh holdout after retiring the first split as a challenge set.

Frozen after the generic ambiguity guard, before evaluation. No vocabulary or
rules are tuned against this split. Five new facts per category, two queries.
"""
import json
from pathlib import Path
from make_fixture import build

SEEDS = {
    "synonym": [
        ("我读书时需要安静。", "reading环境怎么选", "看书环境有什么建议"),
        ("我只喜欢用水彩绘画。", "painting材料怎么选", "画画材料有什么建议"),
        ("我每月徒步一次。", "hiking频率怎么安排", "远足频率怎么安排"),
        ("我喜欢折纸。", "origami工具怎么选", "纸艺怎么安排"),
        ("我喜欢踢足球。", "soccer活动怎么安排", "football装备怎么选"),
    ],
    "hierarchy": [
        ("我的狗需要软垫。", "宠物用品怎么准备", "pet bedding怎么选"),
        ("我喜欢川菜里的清蒸鱼。", "中餐怎么点", "中国菜有什么建议"),
        ("我早餐喜欢吃草莓。", "fruit早餐怎么选", "水果早餐有什么建议"),
        ("我养了一只鹦鹉。", "宠物用品怎么选", "pet food怎么选"),
        ("我常演奏小提琴。", "乐器练习怎么安排", "instrument怎么选"),
    ],
    "bilingual": [
        ("我开会只用十五分钟。", "meeting时长怎么安排", "MEETING需要多久"),
        ("我远程办公需要升降桌。", "WFH设备怎么选", "work from home设备怎么选"),
        ("我做代码评审时先看测试。", "code review步骤怎么选", "CR顺序怎么安排"),
        ("我每天做拉伸。", "stretching怎么安排", "stretch有什么建议"),
        ("我使用暗色模式。", "dark mode怎么设置", "dark theme怎么选"),
    ],
    "colloquial": [
        ("我喜欢独处时听轻音乐。", "一个人待着怎么安排", "solitude活动怎么选"),
        ("我乘车容易晕动，需要通风。", "坐车难受怎么安排", "晕车旅程怎么准备"),
        ("我倾向节约开支，每月设预算。", "少花钱方案怎么选", "saving money怎么安排"),
        ("我遇到麻烦容易犹豫。", "拿不定主意怎么安排", "纠结的时候怎么安排"),
        ("我讨厌拖延。", "磨蹭的时候怎么安排", "procrastination怎么应对"),
    ],
    "abbreviation": [
        ("我每周讨论人工智能的安全。", "AI话题怎么安排", "artificial intelligence讨论什么"),
        ("我看科幻小说只看短篇。", "SF篇幅怎么选", "science fiction篇幅怎么选"),
        ("我负责用户体验的访谈。", "UX任务怎么安排", "user experience工作怎么安排"),
        ("我用层叠样式表做设计。", "CSS工具怎么选", "cascading style sheets怎么用"),
        ("我使用超文本标记语言。", "HTML资料怎么选", "hypertext markup language怎么练"),
    ],
    "typo": [
        ("我每周骑自行车两次。", "自型车频率怎么安排", "zixingche频率怎么选"),
        ("我咖啡只喝小杯。", "咖非杯型怎么选", "kāfēi大小怎么选"),
        ("我每月去图书馆三次。", "圖書館频率怎么安排", "tushuguan行程怎么安排"),
        ("我喜欢饺子。", "餃子怎么点", "jiaozi怎么选"),
        ("我常去散步。", "sanbu怎么安排", "散歩怎么安排"),
    ],
    "indirect": [
        ("我有严重乳糖不耐受，饮食要避开牛奶。", "奶茶怎么点", "冰淇淋怎么选"),
        ("我不能摄入咖啡因，晚上尤其要避免。", "能量饮料怎么选", "提神饮料怎么点"),
        ("我对猫毛过敏，接触后会不舒服。", "猫咖怎么选", "猫咪互动怎么安排"),
        ("我对大豆过敏。", "豆浆怎么点", "豆腐料理怎么选"),
        ("我怕噪声。", "摇滚演出怎么选", "live concert怎么安排"),
    ],
    "time": [
        ("我每周四傍晚整理相册。", "Thursday evening照片怎么整理", "星期四晚间相片怎么安排"),
        ("我每周二上午开会。", "Tuesday morning会议怎么安排", "礼拜二早上meeting怎么安排"),
        ("我每年年末写一本相册。", "year end照片怎么安排", "年终相册怎么安排"),
        ("我每季第一天检查烟雾报警器。", "季度初报警器怎么检查", "first day of quarter烟雾报警器怎么安排"),
        ("我每月十五号写总结。", "月中总结怎么安排", "midmonth报告怎么安排"),
    ],
    "correction": [
        ("我开会每次三十分钟。", "更正，现在只用十五分钟。", "meeting时长怎么选", "会议要开多久"),
        ("我摄影喜欢彩色图像。", "更正，现在只拍黑白。", "拍照色彩怎么选", "photography怎么安排"),
        ("我每周一骑行。", "更正，现在改周日。", "cycling选哪天", "单车日期怎么安排"),
        ("我喜欢足球时穿短袖。", "更正，现在只穿长袖。", "soccer衣服怎么选", "足球着装怎么安排"),
        ("我喜欢饺子加辣。", "更正，现在不能加辣。", "jiaozi口味怎么选", "饺子该怎么点"),
    ],
    "nearby": [
        ("我跑步只用环形路线。", "running路线怎么选", "跑步道路怎么选", "我的跑步围巾是黄色。"),
        ("我会议只在周四。", "meeting日期怎么安排", "会议哪天举行", "我的会议笔袋是黄色。"),
        ("我游泳只去室外泳池。", "swimming环境怎么选", "游泳地点怎么选", "我的游泳背包是黄色。"),
        ("我烹饪只用蒸锅。", "cooking锅具怎么选", "做饭蒸锅怎么选", "我的烹饪围裙是黄色。"),
        ("我阅读时必须开台灯。", "reading光照怎么选", "阅读台灯怎么选", "我的阅读坐垫是黄色。"),
    ],
    "polysemy": [
        ("我吃苹果时削皮。", "苹果电脑怎么升级", "apple phone怎么设置"),
        ("我编写Java程序。", "java island怎么游览", "爪哇咖啡怎么点"),
        ("我用Python编程做笔记。", "python snake怎么养", "蟒蛇宠物怎么喂"),
        ("我喜欢吃石榴。", "石榴桌面软件怎么设置", "石榴手机软件怎么安装"),
        ("我有宠物猫。", "猫眼石如何保养", "cat eye gemstone怎么收藏"),
    ],
    "lifecycle": [(state, query) for state, query in [
        ("forgotten", "reading怎么安排"), ("expired", "看书怎么安排"),
        ("other_account", "阅读怎么安排"), ("cloud_denied", "reading怎么安排"),
        ("cross_evidence", "看书怎么安排"),
    ]],
}


if __name__ == "__main__":
    cases = build(SEEDS, "holdout")
    for case in cases:
        if case["category"] == "lifecycle":
            for memory in case["memories"]:
                if memory["id"] != "unrelated":
                    memory["content"] = "我每天阅读一本书。"
    assert len(cases) == 120
    (Path(__file__).parent / "holdout.json").write_text(json.dumps({"version": 2, "provenance": "Entirely fictional, manually labeled. Fresh final holdout after retiring the initial split as challenge data. Frozen after generic ambiguity guard, never used for tuning.", "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
