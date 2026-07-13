# 读写分离：先存下来，稍后再消化

[English](./read-write.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./read-write.md) 为准。

MemoWeft 把快路径和慢路径分开。存一条消息又便宜又同步。把存下来的证据（evidence）加工成画像（profile）才是那个费时的步骤，它按自己的节奏跑。召回（recall）失败绝不会卡住回复。

## 存证据是瞬时的；画像是另一个步骤（不用 API key）

这段不调模型、不走网络。

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// Storing is one cheap write. No model call, no digest.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I ship on Fridays.' });

// The evidence is there immediately...
console.log('evidence:', core.memory.listEvidence({ subjectId: 'alice' }).length);   // → 1
// ...but no cognition yet. Distilling evidence into a profile is a separate, heavier step.
console.log('cognitions:', core.memory.listCognitions({ subjectId: 'alice' }).length); // → 0

core.close();
```

你一摄入（ingest），证据当场落地。而判断（也就是认知，cognition）只有在 `updateProfile` 跑完消化（digest）后才出现——什么时候跑，由你决定。

## 先存，再回复

`handleConversationTurn` 按固定顺序走：先把用户的消息**存**成证据，再**召回**相关画像，然后**回复**。存在召回之前，所以就算模型调用失败，消息也已经安全落库。

如果召回抛异常或超时，MemoWeft 就当作"这一轮没有记忆"，照样回复——记忆出点岔子绝不会卡住对话。失败时会设上 `TurnOutcome.error`，但消息已经存好了，别再重复摄入一遍。

<!-- snippet:skip (needs a live model) -->
```ts
const turn = await core.handleConversationTurn({ subjectId: 'alice', message: 'When should I release?' });
if (turn.error) console.log('reply degraded, but the message was stored:', turn.error);
console.log(turn.reply);
```

## 按你自己的节奏消化

`updateProfile` 是那条重路径：蒸馏（distill）→ 固化（consolidate）→ 归因（attribute）→ 重建索引。要**批量**跑——攒够 N 轮后、空闲时、或者夜里跑——别每条消息都跑。批量触发条件配在 `config.profileUpdate` 里（默认：每 5 轮，或空闲 30 分钟）。

读保持轻、写攒着批量做，这就是为什么一轮对话能保持灵敏，而画像还在后台持续长大。

## 下一步

- **本系列下一篇 → [标注来源（Sourcing）](./sourcing.zh-CN.md)**
- **[快速上手](../getting-started.zh-CN.md)** —— 五分钟内存下一条消息再读回来。
- **[核心概念](./README.zh-CN.md)** —— 另外五条纪律，每条一屏。
- **[API 参考](../reference/memory-surface-contract.zh-CN.md)** —— `TurnOutcome`、`updateProfile` 和 `config.profileUpdate` 的形状。
