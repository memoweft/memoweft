# 置信度由规则算出，不由 LLM 自报（confidence）

[English](./confidence.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./confidence.md) 为准。

MemoWeft 保留的每条 cognition 都带一个 `confidence`（0–1000）和一个 `credStatus`。`confidence` 是确定性、可配置的启发式分数，不是经校准的概率，也不等于真实陈述。本页讲这些数字从哪来——以及为什么模型永远无权设定它们。

## 亲眼看（无需 API key）

下面这段不用模型、不联网就能跑。`computeConfidence` 和 `deriveCredStatus` 都是纯函数。

```ts
import { createMemoWeftCore, computeConfidence, deriveCredStatus } from 'memoweft';

// Storing what the user said needs no model — it just records raw evidence, unscored.
const core = createMemoWeftCore({ dbPath: ':memory:' });
await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });
console.log('evidence stored:', core.memory.listEvidence({ subjectId: 'alice' }).length);
core.close();

// Confidence is a rule: same inputs -> same score, every run.
const stated = computeConfidence({
  contentType: 'fact',
  formedBy: 'stated',
  supportCount: 1,
  contradictCount: 0,
});
const affirmed = computeConfidence({
  contentType: 'fact',
  formedBy: 'confirmed',
  supportCount: 1,
  contradictCount: 0,
});
const guess = computeConfidence({
  contentType: 'fact',
  formedBy: 'inferred',
  supportCount: 1,
  contradictCount: 0,
});
const nagged = computeConfidence({
  contentType: 'fact',
  formedBy: 'inferred',
  supportCount: 20,
  contradictCount: 0,
});

console.log('stated fact      ', stated, deriveCredStatus(stated, 0, 'fact')); // 600 limited
console.log('assistant-led yes', affirmed, deriveCredStatus(affirmed, 0, 'fact')); // 280 candidate
console.log('inferred guess   ', guess, deriveCredStatus(guess, 0, 'fact')); // 200 candidate
console.log('guess x20 support', nagged, deriveCredStatus(nagged, 0, 'fact')); // 400 low
console.log('with 1 contradict', deriveCredStatus(stated, 1, 'fact')); // conflicted
```

## 分数是怎么算出来的

公式是 `base + support − contradict`，夹在 50–1000 之间：

- **按溯源分类定基线** —— `stated` 600、`ruled` 450、`observed` 350、`confirmed` 280、`inferred` 200。`confirmed` 表示用户确认了由助手提出的命题；推测天生分最低。
- **支持** —— 每多一条支持性 evidence 加 40，最多算 5 条（最高 +200）。
- **矛盾** —— 每一条矛盾性 evidence 减 120。
- **瞬时上限** —— `state` 类 cognition（情绪、"今天累"）封顶 300，所以反复出现的感受永远不会硬化成稳定特质。

## 为什么这样设计

问 LLM 有多确定，它可能编个数——幻觉给高分，支撑充分的主张给低分。MemoWeft 不理这一套。分数是**主张从哪来、证据怎么叠加**的函数，所以可复现、可审计：同样的输入再跑一遍，得到同样的数。它是排序和门控用的启发式，不是经校准的似然估计。

这也意味着猜测不会只靠累积支持就变成用户的直接陈述。上面把 20 条支持性观察堆到一条 `inferred` 主张上，到 400（`low`）就停了——推测永远攒不到 `stable`。只有新的用户直接 evidence、被分类为 `stated`，才能拿到高基线。主张的溯源如何判定，见 [Concepts](./README.zh-CN.md)。

## 可信状态（credStatus）

`deriveCredStatus` 把分数变成一个直白的标签。只要有矛盾性 evidence，就压过其他一切：

| credStatus   | 条件                            |
| ------------ | ------------------------------- |
| `conflicted` | 有任何矛盾性 evidence（见冲突） |
| `stable`     | 置信度 ≥ 750                    |
| `limited`    | 置信度 ≥ 500                    |
| `low`        | 置信度 ≥ 300                    |
| `candidate`  | 置信度 < 300                    |

`state`（瞬时）类 cognition 无论分数多少，永远不会高于 `low`。

## 看它变成存下来的 fact（需要模型）

把 evidence 变成打了分的 cognition 需要一个 chat 模型。demo 的第 1 幕摄入 "I own a red bicycle"，蒸馏它，展示它落地成一条按规则打了 600 分（`limited`）的 `fact`——无 key、不联网，用的是确定性离线桩。

<!-- snippet:skip (needs a live model) -->

```ts
await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle' });
await core.updateProfile({ subjectId: 'alice' }); // distill -> consolidate -> score by rule
for (const c of core.memory.listCognitions({ subjectId: 'alice' })) {
  console.log(c.content, c.confidence, c.credStatus); // owns a red bicycle · 600 · limited
}
```

跑起来：`npm run demo -- --act 1`（[demo 走查](../demo-script.md)）。

## 相关

- **本系列下一篇 → [纠正 vs 冲突（Correct vs conflict）](./correct-conflict.zh-CN.md)**
- [Getting started](../getting-started.zh-CN.md) —— 五分钟内存下 evidence 再读回来。
- [Concepts](./README.zh-CN.md) —— 形成方式、冲突暴露、时间衰减，每个概念一屏。
- [Run the demo](../demo-script.md) —— 90 秒看完四个差异点。
