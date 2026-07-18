# 快速上手（Getting started）

[English](./getting-started.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./getting-started.md) 为准。

装好 MemoWeft，存下一句用户说过的话，再把它读回来——五分钟搞定。第一步不需要 API key。

## 安装

```bash
npm install memoweft
```

MemoWeft **运行时零依赖**。在 **Node 24+** 上，它直接用内置的 `node:sqlite`——不用再装别的。在 Node 20 或 22 上，还要装一个可选驱动：

```bash
npm install better-sqlite3   # only on Node 20 / 22
```

## 存下来再读回去（不需要 API key）

这段不调模型、不走网络。复制到一个文件里直接跑。

```ts
import { createMemoWeftCore } from 'memoweft';

// One line assembles the storage, recall, and model layers. ':memory:' = a throwaway in-memory db.
const core = createMemoWeftCore({ dbPath: ':memory:' });

// Store one thing the user said. This makes no model call — it just records raw evidence.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });

// Read it back through the controlled API — you never touch the database directly.
for (const e of core.memory.listEvidence({ subjectId: 'alice' })) {
  console.log(e.sourceKind, '·', e.rawContent); // → spoken · I own a red bicycle.
}

core.close();
```

你刚写下了一条证据（evidence），又把它读了回来。注意 `sourceKind: 'spoken'`——MemoWeft 记的是**谁说的、怎么说的**，因为用户亲口说的话和机器的猜测不是同一种事实。这个区分正是整件事的重点（见 [Concepts](./concepts/README.zh-CN.md)）。

## 把证据蒸馏成画像（需要一个聊天模型）

存证据不需要模型。把它变成**画像（profile）**——蒸馏（distill）事实、把猜测压成低置信度、暴露冲突——才需要一个聊天模型。在你的应用根目录放一个 `.env`，把 MemoWeft 指向任意兼容 OpenAI 的端点：

```ini
MEMOWEFT_LLM_BASE_URL=https://your-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-...
MEMOWEFT_LLM_MODEL=your-model
# 可选：嵌入器可启用语义召回。未配置时，召回降级为本地 FTS5 关键词检索。
MEMOWEFT_EMBED_BASE_URL=...
MEMOWEFT_EMBED_API_KEY=...
MEMOWEFT_EMBED_MODEL=...
```

<!-- snippet:skip (needs a live model) -->

```ts
const core = createMemoWeftCore({ dbPath: './memory.db' });

await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });
await core.updateProfile({ subjectId: 'alice' }); // distill → consolidate → attribute → index

// 下一轮会召回这条自行车事实，并把它注入回复上下文。
const turn = await core.handleConversationTurn({
  subjectId: 'alice',
  message: 'What color is my bicycle?',
});
console.log(turn.reply);
```

配置缺了不会崩，而是降级（degrade）：没有聊天模型 → 画像那一步报错，但证据照样存下；没有 embedder → 召回（recall）降级为关键词检索（FTS5），仍能出结果（语义召回才需 embedder）。用 `core.health()` 查状态。完整可跑的闭环见 [`examples/minimal.ts`](../examples/minimal.ts)。

## 下一步

- **[30 秒离线运行](./demo-script.zh-CN.md)** —— 依赖安装后，无 key、无网络的确定性验证。
- **[核心概念](./concepts/README.zh-CN.md)** —— 为什么把事实、猜测、冲突和过期状态分开来放。
- **[接入配方](./recipes/)** —— 五分钟把 MemoWeft 接进 Vercel AI SDK 或 MCP server。
- **[API 参考](./reference/memory-surface-contract.zh-CN.md)** —— 每个面向宿主的方法和数据形状。
- **[参考宿主](./reference-host.zh-CN.md)** —— 本地单用户宿主演示，不是生产模板。
- **[部署清单](./deployment.zh-CN.md#生产部署清单)** —— 真实宿主必须自行承担的运维和安全工作。
