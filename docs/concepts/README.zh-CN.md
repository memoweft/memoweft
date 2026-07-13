# 认知纪律：六条规则（Cognitive discipline）

[English](./README.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./README.md) 为准。

MemoWeft 把**事实和猜测分开**——用户真正说过的话，和模型推测出来的东西——绝不让一方悄悄变成另一方。这个区分就是全部意义所在。

六条纪律守住这道分界。每一条写入和读取路径都遵守这全部六条；机制细节见[架构](../internals/architecture.md)，下面每一页讲一条规则，能配上可运行的检查就配上。

- **[读写分离（Read/write split）](./read-write.zh-CN.md)** — 读取保持同步、轻量；把内容消化成画像的重活在后台批量跑，聊天永不阻塞。
- **[标注来源（Sourcing）](./sourcing.zh-CN.md)** — 每条事实都标上它是怎么来的（`spoken`、`observed`、`inferred`、`tool`），每个判断都能回溯到它所依据的用户原话。
- **[不拿自己当证据（No self-evidence）](./no-self-evidence.zh-CN.md)** — 助手自己的回复绝不成为证据；引不出用户真实原话的判断，直接丢弃。
- **[置信度按规则算（Confidence by rule）](./confidence.zh-CN.md)** — 置信度由一个固定公式从证据算出，绝不采信模型自报的数值。
- **[纠正 vs 冲突（Correct vs conflict）](./correct-conflict.zh-CN.md)** — 用户明确的纠正会让旧结论退役；单纯的矛盾则被暴露、并排保留，绝不自动裁决。
- **[分类衰减（Typed decay）](./decay.zh-CN.md)** — 认知按不同速度淡出：一时的情绪很快被遗忘，明确表达的偏好则永不自动遗忘。

第一次接触 MemoWeft？先读[快速上手](../getting-started.zh-CN.md)，再回到这里。碰到不认识的词（证据、认知、置信度…）？[术语表](../glossary.zh-CN.md)里都有定义。
