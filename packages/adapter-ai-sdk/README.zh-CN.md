# @memoweft/adapter-ai-sdk

> English · [README.md](./README.md)

**[MemoWeft](https://github.com/memoweft/memoweft) 的 Vercel AI SDK 适配器。** 给你的 AI SDK 应用接上长期记忆：**读** = 召回相关记忆、注入进 prompt；**写** = 每轮对话结束后，沉淀【用户原话】。

这是个**外部集成包**，只消费 MemoWeft 的公开 Core 门面（`createMemoWeftCore`），不碰 Core 内部。`ai` 是 peer 依赖（你自备）。

## 安装

npm 已发布的是 `@memoweft/adapter-ai-sdk@0.1.0`。可由 npm 正常解析的组合是：

```bash
npm i ai memoweft@0.5.1 @memoweft/adapter-ai-sdk@0.1.0
```

`main` 上的 `0.2.0` 尚未发布。请通过本仓库的 workspace 试用源码预览：

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-ai-sdk
```

它的 peer 范围为 `ai` `^7` 和 `memoweft` `^0.5.1 || ^0.6.0`。不要用 `--legacy-peer-deps` 将已发布的 `0.1.0` 与 Core `0.6` 强行组合。运行真实模型还需一个 `ai` provider（如 `@ai-sdk/openai`）。

## 两条路：读和写

### 读 —— 用 middleware 召回

`createMemoWeftMiddleware(core)` 返回一个 Vercel AI SDK 的 `LanguageModelMiddleware`。它在 `transformParams` 里取**最后一条 user 消息的文本**，调 `core.recall({ query })`，把召回到的认知注入回那条 user 消息，再交给模型。

```ts
import { wrapLanguageModel, generateText } from 'ai';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftMiddleware } from '@memoweft/adapter-ai-sdk';

const core = createMemoWeftCore({ dbPath: './memory.db' });

const model = wrapLanguageModel({
  model: baseModel, // 你的 @ai-sdk/* 模型
  middleware: createMemoWeftMiddleware(core),
});

const { text } = await generateText({ model, prompt: '讲讲递归。' });
```

注入的这段说明**照搬 MemoWeft Core 现成的中性措辞**（逐字对齐 Core 的 `knowledgeBlock`）：低置信条目明确标"只是假设，别当定论"。适配器**不自造任何人格/人设 prompt**——语气和角色仍归宿主。

召回为空、没有 user 文本、或召回失败时，适配器会原样透传 params。

选项：`{ subjectId?, lang?: 'en' | 'zh', onRecall? }`。

### 写 —— 对话结束沉淀用户原话

`createPersistOnEnd(core, { userMessage, originId })` 返回一个传给 `generateText`/`streamText` 的 **`onEnd`** 回调（SDK 的 `onFinish` 是 `onEnd` 的 @deprecated 别名）。这一轮结束后，它调用 `core.ingestUserMessage`，把【用户原话】存成一条 `spoken` 证据。

```ts
import { createPersistOnEnd } from '@memoweft/adapter-ai-sdk';

const userMessage = '我很偏好简短、直接的回答。';
await generateText({
  model,
  prompt: userMessage,
  onEnd: createPersistOnEnd(core, { userMessage, originId: 'turn-1' }),
});
```

**为什么要你显式传 `userMessage`：** SDK 的 `onEnd` 事件只带【结果侧】字段（text、steps、usage、response…），**不带**原始用户输入；而发给 provider 的请求体已经被读 middleware 改过（注入了召回记忆）。所以"用户真正说的那句原话"唯一干净的来源，就是你调 `generateText` 时本来就持有的那份。`onEnd` 事件对象**不被使用**——它只当触发时机。

- **只存用户原话、绝不存助手回复**（Core 纪律：助手回复不落证据）。
- 给稳定的 `originId`（你的 turnId/messageId）保证**幂等**——同一轮即便 `onEnd` 触发多次也只落一条。
- **不传任何上云授权位**：`ingestUserMessage` 存 `spoken` 证据，本就不涉 observed 的上云授权位。
- 空串/纯空白跳过不落库；落库出错走 `onError`（或静默吞），由宿主继续处理本轮对话。

也有一个直白的 `persistUserTurn(core, { userMessage, originId? })`，想在 `onEnd` 之外自己调时用。

## 完整示例

见 [`examples/basic.ts`](./examples/basic.ts)——两轮对话：第 1 轮的话被存进去、召回进第 2 轮的 prompt。

## 与 MemoWeft Host 的关系

Host（`apps/memoweft-host`）是 MemoWeft 的*参考应用*——聊天界面、多会话、备份。本适配器是*反方向*：让**你自己**的应用（基于 Vercel AI SDK 搭的）把 MemoWeft 当记忆后端复用，不需要 Host。两者都对着同一个公开 Core 门面，彼此不依赖。想要现成 UI 就用 Host；已经有 AI SDK 应用、只想要记忆层，就用本适配器。

## 不做什么

- 不做人格/人设 prompt（Core 无头——语气/角色归宿主）。
- 不存助手回复，只存用户原话。
- 不放宽 `observed` 上云，写路径不传授权位。

## 许可

MIT
