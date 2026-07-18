# 五分钟接入 Vercel AI SDK

[English](./vercel-ai-sdk.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./vercel-ai-sdk.md) 为准。

给你现有的 Vercel AI SDK 应用加上长期记忆。`@memoweft/adapter-ai-sdk` 封装了 MemoWeft 的公开 Core：**读**把记忆召回并注入到提示词里，**写**在每轮结束后存下用户亲口说的话。

## 安装

npm 已发布的适配器为 `0.1.0`；请安装这个固定且兼容的组合：

```bash
npm i ai memoweft@0.5.1 @memoweft/adapter-ai-sdk@0.1.0
```

`main` 上的 `0.2.0` 是尚未发布的 workspace 版本。需要配合 Core `0.5.1` 或 `0.6` 时，请从本检出构建；它的 peer 范围是 `memoweft` `^0.5.1 || ^0.6.0`。不要用 `--legacy-peer-deps` 把已发布的 `0.1.0` 强行配到 Core `0.6`。`ai` `^7` 同样是 peer 依赖；provider 自备，比如 `@ai-sdk/openai`。

## 接线

`createMemoWeftMiddleware` 负责读（先召回再注入）；`createPersistOnEnd` 负责写（先取用户的话再存）。

<!-- snippet:skip (needs a live model) -->

```ts
import { generateText, wrapLanguageModel } from 'ai';
import { openai } from '@ai-sdk/openai';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftMiddleware, createPersistOnEnd } from '@memoweft/adapter-ai-sdk';

const core = createMemoWeftCore({ dbPath: './memory.db' });

// Read: recall relevant memory and inject it before the model sees the prompt.
const model = wrapLanguageModel({
  model: openai('gpt-4o-mini'),
  middleware: createMemoWeftMiddleware(core, { subjectId: 'alice' }),
});

const userMessage = 'I strongly prefer short, direct answers.';
const { text } = await generateText({
  model,
  prompt: userMessage,
  // Write: after the turn ends, store the user's own words as one `spoken` evidence.
  onEnd: createPersistOnEnd(core, { subjectId: 'alice', userMessage, originId: 'turn-1' }),
});
```

整个循环就这么多。`recall` 反映的是**画像**（cognitions），所以要等你的宿主在某处跑过 `core.updateProfile` 并配了 embedder，下一轮才召回得到 turn-1；否则写路径照常存证据，但召回为空。

## 为什么 `userMessage` 要你自己传

`onEnd` 只带结果侧的字段（text、usage、steps），不带原始输入，而且发给 provider 的请求早已被读中间件改写过。所以用户真实说的那句话，唯一干净的来源就是你手里已经握着的那个值。传一个稳定的 `originId`（你的轮次 id）保证幂等：同一轮最多只存一条证据（evidence）。召回或摄入失败时，由宿主继续其正常的回复处理。只存用户的话，绝不存助手的回复（Core 纪律）。

## 工具结果也一并存下

如果这一轮跑了工具，把工具**返回的输出**当作 `tool` 证据存下来，绝不存模型的调用入参。这样可以维持外部结果与模型生成意图之间的来源边界。

<!-- snippet:skip (needs a live model) -->

```ts
import { persistToolResults } from '@memoweft/adapter-ai-sdk';

const result = await generateText({ model, prompt: userMessage, tools });
// Reads only role:'tool' messages; returns how many results were stored.
await persistToolResults(core, {
  subjectId: 'alice',
  messages: result.response.messages,
  originIdPrefix: 'turn-2',
});
```

## 不用模型也能验证写路径

写辅助函数直接调用 Core：不用 key，不走网络。下面存下一轮再读回来。

```ts
import { createMemoWeftCore } from 'memoweft';
import { persistUserTurn } from '@memoweft/adapter-ai-sdk';

const core = createMemoWeftCore({ dbPath: ':memory:' });
await persistUserTurn(core, {
  subjectId: 'alice',
  userMessage: 'I strongly prefer short, direct answers.',
  originId: 'turn-1',
});

for (const e of core.memory.listEvidence({ subjectId: 'alice' })) {
  console.log(e.sourceKind, '·', e.rawContent); // -> spoken · I strongly prefer short, direct answers.
}
core.close();
```

`persistUserTurn` 就是 `createPersistOnEnd` 跑的那份写逻辑；在 `onEnd` 钩子之外单独用它。

## 下一步

- 完整的两轮示例：[`packages/adapter-ai-sdk/examples/basic.ts`](../../packages/adapter-ai-sdk/examples/basic.ts)。
- 看召回、冲突、衰减实际跑起来：[四幕 demo](../demo-script.md)（`npm run demo`）。
- 每个方法和数据形状：[API 参考](../reference/memory-surface-contract.zh-CN.md)。
