# 术语表 · Glossary

[English](./glossary.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./glossary.md) 为准。

本页是 MemoWeft 核心术语的**唯一正式定义处**。第一列是代码名，源码 / API / 类型名只用这一列；第二列给工程师一句话定义；第三列是用户界面可改用的说法——同一概念，换成好懂的话。

## 术语

| 术语（code） | 一句话定义 | 用户侧说法 |
| --- | --- | --- |
| `evidence` | 落库的原料记录——用户说的话、一次观察、或一条工具结果。每个判断都必须引它；其他东西都不算来源。 | 记忆线索 / 原话 / 记录 |
| `event` | 一条或多条 `evidence` 归成一个片段（「发生过的一件事」），带摘要和 `occurredAt`。由 `distill` 生成。 | 经历片段 / 一次经历 |
| `cognition` | MemoWeft 对用户持有的一条判断（`fact` / `preference` / `goal` / `project` / `state` / `trait` / `hypothesis` / `trend`，见 `ContentType`），带按规则算出的 `confidence` / `credStatus`，并挂着它所依据的证据。 | 对你的理解 / 理解条目 |
| `profile` | 某个 subject 当前有效认知的集合——MemoWeft 拼出的这个用户的全貌。由 `updateProfile` 重建。 | 对你的了解 / 个人上下文（用户界面避免「用户画像」） |
| `confidence` | MemoWeft 从支撑证据算出的 0–1000 整数分，绝不采信模型自报。给算法调参用，不是 0–1 概率。见[把握度定性档](#把握度定性档)。 | 把握度（用定性档，不显示数字） |
| `credStatus` | 一条认知的把握度所在的定性档：`candidate \| low \| limited \| stable \| conflicted`。加取值不算破坏，宿主要留 `default` 兜底。 | 候选 / 低把握 / 有一定把握 / 比较确定 / 有冲突，需确认 |
| `sourceKind` | 一条证据是怎么来的：`spoken \| inferred \| observed \| tool`。它决定默认授权位——`observed` 和 `tool` 默认不上云。 | （对内；对外通过溯源体现） |
| `recall` | 一次同步、轻量的读取，返回与查询相关的认知（`RecalledCognition[]`），受有效性 / 归档 / scope / 衰减门控。永不阻塞聊天。 | 想起相关内容 / 相关记忆 |
| `attribution` | 写路径的一步：从一个现象（一条 `state` 认知）出发，拉时间窗内的证据，问「为什么」——产出可解释假设：低置信、挂证据、可推翻。 | 猜测原因 / 可能是因为 |
| `conflict` | 两条互相矛盾的认知。MemoWeft 把它们并排暴露、状态标 `conflicted`；绝不自动挑赢家。 | 需要确认的矛盾 / 说法不一致 |
| `hypothesis` | `ContentType` 的一个取值：`attribution` 产出的可解释猜测——`formedBy: inferred`、置信封顶、随时可被用户推翻。 | 暂时的猜测 / 待确认的想法 |
| `decay` / `expire` | 认知按分类速度淡出——一时的 `state` 淡得快，明确表达的偏好永不自动遗忘。`effectiveConfidence` = 落库的 `confidence` × 衰减系数，读时现算（不落库）。 | 变淡 / 不再当成现在的你 |
| `updateProfile` | 一次性批量写：`distill` → `consolidate` → `attribute` → 重建召回索引。读取永不等待的那件重活。 | 整理记忆 / 重新认识你 |
| `distill` | `updateProfile` 中把原料证据整理成 `event` 的一步。 | 整理成经历片段 |
| `consolidate` | `updateProfile` 中把 `event` 增量消化成认知的一步，同时算出 `confidence` / `credStatus`。 | 更新理解 |
| `retriever` | 给 `recall` 找相关认知的可注入底座（向量或关键词）。是实现接缝，不是用户概念。 | （不给用户看；对内叫「找相关记忆」） |
| source trace | 溯源链：一条认知引了哪些证据，每条链接标 `support` 或 `contradict`（`EvidenceLink`）。 | 根据哪句话知道的 / 从哪里看出来的 |
| `scope` | 一条认知适用的场景；`null` = 通用。 | （属于「对你的了解」的一部分） |

## 把握度定性档

MemoWeft 把 `confidence` 存成 0–1000 的整数，由规则从证据算出——绝不由模型自报。这个数字给算法调参用。别显示给终端用户，也别当成 0–1 概率读。

用户侧改用定性档，从 `credStatus` 映射而来：

| credStatus（code） | 分数区间 | 用户档（英文） | 用户档（中文） |
| --- | --- | --- | --- |
| `candidate` | 最低——刚提出 | just a candidate | 候选 |
| `low` | 低 | low confidence | 低把握 |
| `limited` | 中等 | some confidence | 有一定把握 |
| `stable` | 高、已稳 | fairly settled | 比较确定 / 已较稳定 |
| `conflicted` | 有矛盾 | conflicting, needs confirming | 有冲突，需确认 |

`conflicted` 不是分数更高或更低——它表示两条认知打架、该由人来确认（见 `conflict`）。新增 `credStatus` 取值不算破坏，所以用户界面要给未知档留兜底。

---

定位与话术纪律——怎么对外说 MemoWeft、哪一层才能说「她」——见 [internal/naming-positioning.md](./internal/naming-positioning.md)，不在本页。
