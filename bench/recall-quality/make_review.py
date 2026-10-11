"""Exposed independent review samples; reproduction only, never a blind set."""
import json
from pathlib import Path

NOVEL = [('我出差习惯乘坐高铁。', '动车出行有什么需要记住的？'),
 ('我喜欢马铃薯。', '土豆怎么做适合我？'),
 ('我家有一只仓鼠。', '宠物笼子怎么选？'),
 ('我对虾过敏。', '甲壳类海鲜能安排吗？'),
 ('我喜欢打乒乓球。', '球类运动有哪些适合我？'),
 ('我通勤只坐地铁。', 'subway出行怎么安排？'),
 ('我平时使用降噪耳机。', 'ANC设备怎么选？'),
 ('我习惯把文件压缩后发送。', 'zip附件怎么准备？'),
 ('我不吃香菜。', 'coriander菜品适合我吗？'),
 ('我睡觉很轻，一有声音就醒。', '安排住宿时隔音有什么要求？'),
 ('我晨间固定练习瑜伽。', 'yoga放在morning合适吗？'),
 ('我做饭只用电磁炉。', 'cooking用什么加热设备？'),
 ('我喜欢黑白摄影。', 'photography的色彩怎么选？'),
 ('我喜欢边散步边听播客。', 'podcast活动怎么安排？'),
 ('我乘船时总在甲板休息。', 'boat休息有什么习惯？'),
 ('我每周日整理旧文件。', 'sunday应该安排什么？'),
 ('我只在午后锻炼。', 'workout放在afternoon怎么样？'),
 ('我喜欢用钢琴演奏慢曲。', 'instrument练习怎么安排？'),
 ('我喜欢酸味的芒果。', 'fruit口味怎么选？'),
 ('我习惯睡前阅读传记。', 'bedtime看书怎么安排？')]

REGRESSION = [('我摄影一般去海边。', '摄影海边地点怎么选？', ['我在室内清洁摄影器材。']),
 ('我喜欢吃苹果。', '吃苹果时怎么用手机记录？', ['我的手机用了三年。']),
 ('我使用苹果电脑。', '用苹果电脑记录水果清单怎么做？', []),
 ('我给单车买了保险。', '骑行单车保险怎么续？', ['我的骑行路线在室内。'])]

NEGATIVE = [('我养了一只猫。', 'cat命令怎么显示文件？'),
 ('我喜欢玫瑰。', 'rose过去式怎么用？'),
 ('我喜欢奶茶。', 'tea tree精油怎么保存？'),
 ('我负责项目管理。', '8 pm是什么意思？'),
 ('我经常慢跑。', 'running process怎么停止？'),
 ('我喜欢园艺。', '种花家这个网络用语是什么意思？'),
 ('我每周开会。', 'meeting point在几何学里是什么意思？'),
 ('我喜欢咖啡。', '美式英语怎么练发音？'),
 ('我习惯每天锻炼。', 'exercise book封面怎么设计？'),
 ('我喜欢画画。', 'painting软件崩溃怎么修？')]

def build():
    cases = []
    for i, (content, query, others) in enumerate(REGRESSION):
        memories = [{'id': 'target', 'content': content}] + [{'id': f'd{i}', 'content': s} for i, s in enumerate(others)]
        cases.append(dict(id=f'review-regression-{i}', seed=f'review-regression-{i}', category='review_regression', query=query, tier='local', memories=memories, expect=['target'], forbid=[m['id'] for m in memories[1:]], must_hit=True))
    for i, (content, query) in enumerate(NEGATIVE):
        cases.append(dict(id=f'review-negative-{i}', seed=f'review-negative-{i}', category='review_negative', query=query, tier='local', memories=[{'id': 'target', 'content': content}], expect=[], forbid=['target'], must_abstain=True))
    for name, label in [('樂', 'traditional'), ('云', 'control')]:
        for tier in ['local', 'cloud']:
            text = f'虚构朋友昵称“{name}”，她喜欢在安静的角落拼复杂的木制拼图。'
            cases.append(dict(id=f'review-name-{label}-{tier}', seed=f'review-name-{label}', category='review_name', query=f'{name}现在喜欢什么？', tier=tier, memories=[{'id': 'person-fact', 'content': text}], person_name=name, expect=['person-fact'], forbid=[], must_hit=True))
    memories = [{'id': f'target-{i}', 'content': c} for i, (c, _) in enumerate(NOVEL)]
    for i, (_, query) in enumerate(NOVEL):
        cases.append(dict(id=f'shared20-{i}', seed='shared20', category='review_shared20', query=query, tier='local', memories=memories, expect=[f'target-{i}'], forbid=[m['id'] for m in memories if m['id'] != f'target-{i}']))
    return cases


if __name__ == '__main__':
    cases = build()
    assert len(cases) == 38
    data = {'version': 1, 'provenance': 'Independent REV-MEM-4 probes.py, names_probe.py and shared20.py at 6535ea4; now exposed regression data. 36 distinct samples, 38 queries including both local/cloud name probes.', 'cases': cases}
    (Path(__file__).parent / 'review.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
