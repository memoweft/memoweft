# 内置摄入不会把助手回复持久化为证据

[English](./no-self-evidence.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./no-self-evidence.md) 为准。

Core 的内置摄入会记录用户说了什么、宿主观察到什么、工具返回了什么。这些入口不会把助手自己的回复或模型提议的工具调用参数持久化为证据。这条边界避免内置路径把模型自己的猜测回流成来源记录。

## 内置摄入没有助手证据入口（不需要 API key）

这段代码不用模型、不联网。复制进一个文件直接跑。

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// The user speaks — this records evidence.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I love strong coffee.' });

// 内置摄入方法中没有 ingestAssistantMessage。
// What's recorded is exactly what the user said, tagged with who said it.
const evidence = core.memory.listEvidence({ subjectId: 'alice' });
console.log(evidence.length, evidence[0].sourceKind, '·', evidence[0].rawContent);
// → 1 spoken · I love strong coffee.

core.close();
```

门面提供 `ingestUserMessage`、`ingestObservation` 和 `ingestToolResult`——它们都不会把助手回复变成证据。`recordAssistantReply` 只把回复保存在内存会话上下文中，供后续用户回合使用。

## 规则

对于内置摄入方法，证据来自模型之外：

- **`spoken`** —— 用户的亲口原话（`ingestUserMessage`）。
- **`observed`** —— 宿主观察到的一个行为（`ingestObservation`）。
- **`tool`** —— 工具返回的负载（`ingestToolResult`）。

模型自己的输出——它的聊天回复，以及它提议的工具调用参数——不会由这些内置路径摄入。Core 与适配器测试覆盖这条不变式。绕过这些入口或自行持久化任意内容的宿主，仍需自行维护同一边界。Vercel AI SDK 适配器遵循内置规则：`persistOnEnd` 存的是用户逐字的那一轮和 `tool` 角色的结果，不读取助手消息。

## 为什么重要：不让猜测自我强化

一条认知的支撑链引用的是证据 id——从不引用另一条认知，也从不引用模型早先说过的话。因此在内置路径中，助手无法引用自己的猜测来抬高那个猜测的置信度。没有这条边界，模型上一轮说出口的一个假设，就可能作为"用户说过的话"回来，滚成一条误导性的主张。置信度锚定在模型之外的来源记录上。（置信度本身是规则型启发式分数，不是自报也不是概率——见[置信度按规则算](./confidence.zh-CN.md)。）

## 在一次完整回合里（需要聊天模型）

<!-- snippet:skip (needs a live model) -->

```ts
const before = core.memory.listEvidence({ subjectId: 'alice' }).length;

const turn = await core.handleConversationTurn({ subjectId: 'alice', message: 'Any snack ideas?' });
console.log(turn.reply); // the assistant answers...

const after = core.memory.listEvidence({ subjectId: 'alice' });
console.log(after.length - before); // → 1 — only the user's new message; the reply is not stored
```

回复活在 `turn.reply` 里，不在这条内置路径使用的 evidence 存储中。在四幕 demo 里从头到尾看这个效果：[`examples/demo.ts`](../../examples/demo.ts)（`npm run demo`）。

## 另见

- **本系列下一篇 → [置信度按规则算（Confidence by rule）](./confidence.zh-CN.md)**
- [快速上手](../getting-started.zh-CN.md) —— 存一条消息再读回来。
- [记忆面契约（Memory Surface Contract）](../reference/memory-surface-contract.zh-CN.md) —— `SourceKind`、各个摄入方法、置信度怎么算。
- [Demo 脚本](../demo-script.md) —— 90 秒讲清四个差异点。
