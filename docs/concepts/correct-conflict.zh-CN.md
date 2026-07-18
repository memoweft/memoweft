# 纠正留下痕迹；冲突保持可见（correct 与 conflict）

[English](./correct-conflict.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./correct-conflict.md) 为准。

MemoWeft 从不悄悄覆盖一个判断。用户自我纠正时，旧的信念会被保留并标记为过时。新证据在不构成纠正的情况下与某个信念矛盾时，两者都保留，并把这处冲撞标出来。MemoWeft 只暴露冲突——它不选出赢家。

## 搭两轮对话（无需 API key）

先一个主张，再一个纠正。两者都作为原始证据存下，不调用任何模型。

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });
await core.ingestUserMessage({
  subjectId: 'alice',
  content: "Actually it isn't mine — my sister owns the red bicycle.",
});

console.log(core.memory.listEvidence({ subjectId: 'alice' }).length); // → 2
console.log(core.memory.listCognitions({ subjectId: 'alice' }).length); // → 0

core.close();
```

两轮都存下了，但还没有任何认知（cognition）。`correct` 和 `conflict` 都发生在 `updateProfile` 里，而它需要一个 chat 模型。想端到端看到完整效果，运行 [`npm run demo`](../demo-script.md)（第 2 幕和第 3 幕）——无需 key，确定性。

## 纠正（correct）：替换，但保留旧的

一次显式纠正会把旧认知的 `invalidAt` 设为当前时间，并把纠正后的内容写成一条**新**认知。旧那一行不会被删除——它连同溯源链一起留着，历史因此保持可追溯。

<!-- snippet:skip (needs a live model) -->

```ts
await core.updateProfile({ subjectId: 'alice' }); // distill → consolidate → attribute

for (const c of core.memory.listCognitions({ subjectId: 'alice' })) {
  console.log(c.content, c.invalidAt ? '(invalidated, kept)' : '(active)');
}
// → The user owns a red bicycle         (invalidated, kept)
// → The user's sister owns the red ...  (active)
```

`recall` 会跳过被作废的认知，所以那条过时的信念不再进入任何回复——但审计或图视图仍能看到改了什么、何时改的。这是 demo 的第 2 幕。

## 冲突（conflict）：两者都留，标出冲撞

当新证据与某个信念矛盾、但**不是**一次显式纠正时——比如口头说偏好美式咖啡，却反复点奶茶——`consolidate` 会用 `relation: 'contradict'` 把这条矛盾证据关联上，并把该信念的 `credStatus` 设为 `'conflicted'`。两条认知都保留。

<!-- snippet:skip (needs a live model) -->

```ts
for (const c of core.memory.listCognitions({ subjectId: 'alice' })) {
  if (c.credStatus === 'conflicted') console.log('CONFLICT:', c.content);
}
// → CONFLICT: The user likes americano
```

MemoWeft 不把任一方当作已验证的真实。一个与口头偏好相矛盾的行为，可能是一次性的、可能是改主意了、也可能只是噪声——替用户做决定就意味着猜。所以 MemoWeft 把这处张力摆出来，交给宿主或用户去化解。这是 demo 的第 3 幕。

## 为什么这很重要

一个会覆盖或自动裁决的记忆，会丢掉来源记录与系统判断之间的区分。把已作废的认知和未决冲突留在记录上，才能保住这条溯源链。

## 下一步

- **本系列下一篇 → [分类衰减（Typed decay）](./decay.zh-CN.md)**
- 跑起来：[`npm run demo`](../demo-script.md)——第 2 幕（纠正）和第 3 幕（冲突），无需 key。
- 字段形状：[记忆面契约](../reference/memory-surface-contract.zh-CN.md) 里的 `invalidAt`、`credStatus: 'conflicted'`。
- 产出这些的写入路径：[`architecture.md`](../internals/architecture.md)（distill → consolidate → attribute）。
