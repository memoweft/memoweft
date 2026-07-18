# 按类型衰减：短期状态更早淡出

[English](./decay.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./decay.md) 为准。

用户上周的压力，不该给这周每一句回复都染上底色。MemoWeft **按类型**衰减置信度：情绪状态的默认半衰期较短；`fact` 和 `preference` 默认未配置半衰期。宿主可以配置不同策略。

## 亲眼看（无需 API key）

衰减是纯读时计算。下面这段不跑模型、不走网络。

```ts
import { effectiveConfidence } from 'memoweft';

const anchoredAt = '2026-01-01T00:00:00.000Z'; // when this cognition was last confirmed
const oneWeekLater = new Date('2026-01-08T00:00:00.000Z');

// An emotional 'state' has a default 1.5-day half-life. A week on, it has largely faded.
const mood = { confidence: 300, contentType: 'state', updatedAt: anchoredAt } as const;
console.log(effectiveConfidence(mood, oneWeekLater)); // → 12  (below the recall threshold of 80)

// Under the default configuration, a 'fact' has no configured half-life.
const bicycle = { confidence: 700, contentType: 'fact', updatedAt: anchoredAt } as const;
console.log(effectiveConfidence(bicycle, oneWeekLater)); // → 700  (unchanged)
```

淡去的情绪掉到 `retrieval.minEffectiveConfidence`（默认 80）之下，recall 便不再注入它。该事实在此默认配置下保持不变。

## 工作原理

有效置信度，就是存储的强度按"距上次确认的时长"缩放：

```
effective = confidence × 2^(-age / halfLifeDays)
```

- **读时算，从不落库。** 存储的 `confidence` 保持原义——由证据推导出的规则型分数（见[置信度由计算得出，不靠自报](./confidence.zh-CN.md)）。衰减只在 MemoWeft 读取时施加，所以时钟走动时没有任何东西去改写数据库。
- **半衰期由类型决定**，在 `config.background.halfLifeDays` 里设。默认值：`state` 1.5 天、`hypothesis` 2、`trend` 7、`goal`/`project` 14、`trait` 60。`fact` 和 `preference` 没列进默认表，因此在该配置下半衰期为 0。宿主可为它们配置不同值。
- **年龄从 `updatedAt` 起算**，也就是证据上一次重新确认这条认知的时刻。反复出现的状态会保持新鲜；没人再提起的则渐渐淡出。

## 看完整效果

demo 的第 4 幕会确认一段低落情绪，把时间快进，再次召回——情绪已衰减到召回门槛以下，而默认不衰减的类型仍可被召回。

```bash
npm run demo -- --fast-forward 30d
```

时间旅行是确定性的，因为时钟可通过 `CreateCoreOptions.clock` 注入。同样的输入、同样的日期，得同样的输出——不用真等上几周。

<!-- snippet:skip (needs the full write path; run the demo above instead) -->

```ts
let nowMs = Date.parse('2026-01-14T09:00:00.000Z');
const core = createMemoWeftCore({ dbPath: ':memory:', clock: () => new Date(nowMs) });

await core.ingestUserMessage({
  subjectId: 'alice',
  content: 'I have been really stressed this week',
});
await core.updateProfile({ subjectId: 'alice' });

nowMs += 30 * 24 * 3600 * 1000; // fast-forward 30 days
const hits = await core.recall({ subjectId: 'alice', query: 'how are they doing' });
// the stress state has decayed out; facts still surface
```

## 下一步

- **[快速上手](../getting-started.zh-CN.md)** —— 安装，并存下你的第一条证据。
- **[跑 demo](../demo-script.md)** —— 90 秒看完四大差异点。
- **[`examples/demo.ts`](../../examples/demo.ts)** —— 第 4 幕背后可运行的源码。
