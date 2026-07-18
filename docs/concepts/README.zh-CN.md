# 认知纪律：六条规则（Cognitive discipline）

[English](./README.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./README.md) 为准。

MemoWeft 把**来源记录和派生判断分开**——用户或工具实际传入了什么，以及模型从中推测了什么。内建路径保留这一区分，不把推断包装成用户提供的证据。

六条纪律定义了公共 Core 中的这道分界；机制细节见[架构](../internals/architecture.md)，下面每一页讲一条规则，能配上可运行的检查就配上。

- **[读写分离（Read/write split）](./read-write.zh-CN.md)** — 召回和画像更新是两个独立操作；宿主决定何时以及如何调度模型写路径。
- **[标注来源（Sourcing）](./sourcing.zh-CN.md)** — 每条证据都标上它是怎么来的（`spoken`、`observed`、`inferred`、`tool`），派生认知保留到支撑记录的链接。
- **[不拿自己当证据（No self-evidence）](./no-self-evidence.zh-CN.md)** — 内建摄入路径不把助手回复持久化为证据，派生认知必须带来源链接。
- **[置信度按规则算（Confidence by rule）](./confidence.zh-CN.md)** — 置信度是根据证据元数据确定性计算的启发式分数，不是模型自报的概率。
- **[纠正 vs 冲突（Correct vs conflict）](./correct-conflict.zh-CN.md)** — 用户明确的纠正会让旧结论退役；单纯的矛盾则被暴露、并排保留，而不由系统自动裁决。
- **[分类衰减（Typed decay）](./decay.zh-CN.md)** — 不同内容类型的召回权重可以按不同速度衰减；默认配置不对事实和偏好做时间衰减，但纠正与失效仍然有效。

第一次接触 MemoWeft？先读[快速上手](../getting-started.zh-CN.md)，再回到这里。碰到不认识的词（证据、认知、置信度…）？[术语表](../glossary.zh-CN.md)里都有定义。
