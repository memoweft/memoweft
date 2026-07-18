# 三层溯源：evidence → event → cognition

[English](./sourcing.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./sourcing.md) 为准。

MemoWeft 持有的每一条判断，都能追溯回支持或反驳它的来源记录。它记录的不只是*推导出了什么*，还有*这来自哪里*。

## 看溯源如何被记下（无需 API key）

这段不碰模型、不连网。复制到文件里直接跑。

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// Two pieces of evidence from two different sources. Neither call touches a model.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I switched to a standing desk.' });
await core.ingestToolResult({
  subjectId: 'alice',
  content: 'calendar: 3 gym sessions logged this week',
});

// Read the raw evidence back. Each row remembers how it came in.
for (const e of core.memory.listEvidence({ subjectId: 'alice' })) {
  console.log(e.sourceKind, '·', e.rawContent);
}
// → spoken · I switched to a standing desk.
// → tool   · calendar: 3 gym sessions logged this week

core.close();
```

每一条 evidence 记录都带一个 `sourceKind`。用户亲口说的话（`spoken`）和工具的输出（`tool`）是不同种类的来源记录；任一标签都不等于内容已被验证为真。四种来源是 `spoken`、`observed`、`inferred` 和 `tool`。其中三种经摄入调用进来（`spoken` / `observed` / `tool`）；`inferred` 由写路径的归因（attribution）步骤内部产出——绝不从模型自报回填（见[不拿自己当证据](./no-self-evidence.zh-CN.md)）。

## 三层结构

写入沿三层向上流动。每一层都链回下一层，所以信任始终有底。

- **Evidence（证据）** —— 来源记录：被说出、被观察、被推测，或由工具返回的内容。它带有 `sourceKind`、时间戳和授权标记；不证明内容为真，也不持有认知层判断。见 [`src/evidence/model.ts`](../../src/evidence/model.ts)。
- **Event（事件）** —— 对一段对话切片的带上下文摘要，链回它所覆盖的证据。判断由事件构建（事件携带上下文）；而追溯的落点仍然是原始话语。
- **Cognition（认知）** —— 关于用户的一条判断，带一个主 `contentType`（如事实、偏好、目标或状态）。每条认知都保留一个 `sources` 列表，记录**支持**或**反驳**它的证据。

这条链提供的是溯源，而不是真实性保证：一条认知始终关联着它下面的来源记录。抽掉这些记录，判断就失去了根基。

## 顺着链条追到源头（需要一个 chat 模型）

从证据构建认知需要一个 chat 模型。一旦构建完成，每条认知都会暴露它的 `sources` —— 一个由 `{ evidenceId, relation }` 组成的 `EvidenceLink[]`。

<!-- snippet:skip (needs a live model) -->

```ts
await core.updateProfile({ subjectId: 'alice' }); // distill → consolidate → attribute

for (const c of core.memory.listCognitions({ subjectId: 'alice' })) {
  console.log(
    c.content,
    '←',
    c.sources.map((s) => `${s.relation}:${s.evidenceId}`),
  );
}
// → The user uses a standing desk ← [ 'support:ev_...' ]
```

在四幕 demo 里看一条事实如何从原始证据成形：[`examples/demo.ts`](../../examples/demo.ts)（`npm run demo`）。

## 下一步

- **本系列下一篇 → [不拿自己当证据（No self-evidence）](./no-self-evidence.zh-CN.md)**
- **[快速上手](../getting-started.zh-CN.md)** —— 五分钟内存下一条证据并读回来。
- **[跑 demo](../demo-script.md)** —— 一条事实成形、被纠正、撞上冲突 —— 全程 sources 完好无损。
- **[API 参考](../reference/memory-surface-contract.zh-CN.md)** —— `listEvidence`、`listCognitions` 以及溯源链接的精确结构。
