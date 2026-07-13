# 助手输出永远不算证据（Assistant output is never evidence）

[English](./no-self-evidence.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./no-self-evidence.md) 为准。

MemoWeft 记录用户说了什么、宿主观察到什么、工具返回了什么。它从不记录助手自己的回复，也不记录模型提议的那次工具调用。模型没法把自己的猜测当成事实喂回去。

## 存储没有留给助手输出的门（不需要 API key）

这段代码不用模型、不联网。复制进一个文件直接跑。

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// The user speaks — this records evidence.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I love strong coffee.' });

// There is no ingestAssistantMessage. A reply has no way into the store.
// What's recorded is exactly what the user said, tagged with who said it.
const evidence = core.memory.listEvidence({ subjectId: 'alice' });
console.log(evidence.length, evidence[0].sourceKind, '·', evidence[0].rawContent);
// → 1 spoken · I love strong coffee.

core.close();
```

门面提供 `ingestUserMessage`、`ingestObservation` 和 `ingestToolResult`——没有任何一个方法能把助手回复变成证据。

## 规则

证据只来自模型之外：

- **`spoken`** —— 用户的亲口原话（`ingestUserMessage`）。
- **`observed`** —— 宿主观察到的一个行为（`ingestObservation`）。
- **`tool`** —— 工具返回的负载（`ingestToolResult`）。

模型自己的输出——它的聊天回复，以及它提议的工具调用参数——从不被摄入。这是铁律 3a（见 [`AGENTS.md`](../../AGENTS.md)）。Vercel AI SDK 适配器守的是同一条线：`persistOnEnd` 存的是用户逐字的那一轮和 `tool` 角色的结果，从不读助手消息。

## 为什么重要：不让猜测自我强化

一条认知的支撑链引用的是证据 id——从不引用另一条认知，也从不引用模型早先说过的话。所以助手没法引用自己的猜测来抬高那个猜测的置信度。没有这条规则，模型上一轮说出口的一个假设，就可能作为"用户说过的话"回来，滚成一个假事实。置信度始终锚定在真正来自模型之外的东西上。（置信度本身由规则算出，不是自报——见[记忆面契约（Memory Surface Contract）](../reference/memory-surface-contract.zh-CN.md)，隐式契约第 1 条。）

## 在一次完整回合里（需要聊天模型）

<!-- snippet:skip (needs a live model) -->
```ts
const before = core.memory.listEvidence({ subjectId: 'alice' }).length;

const turn = await core.handleConversationTurn({ subjectId: 'alice', message: 'Any snack ideas?' });
console.log(turn.reply); // the assistant answers...

const after = core.memory.listEvidence({ subjectId: 'alice' });
console.log(after.length - before); // → 1 — only the user's new message; the reply is not stored
```

回复活在 `turn.reply` 里，不在存储里。在四幕 demo 里从头到尾看这个效果：[`examples/demo.ts`](../../examples/demo.ts)（`npm run demo`）。

## 另见

- **本系列下一篇 → [置信度按规则算（Confidence by rule）](./confidence.zh-CN.md)**
- [快速上手](../getting-started.zh-CN.md) —— 存一条消息再读回来。
- [记忆面契约（Memory Surface Contract）](../reference/memory-surface-contract.zh-CN.md) —— `SourceKind`、各个摄入方法、置信度怎么算。
- [Demo 脚本](../demo-script.md) —— 90 秒讲清四个差异点。
