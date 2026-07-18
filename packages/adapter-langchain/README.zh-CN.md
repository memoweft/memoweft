# @memoweft/adapter-langchain

> English · [README.md](./README.md)

> [!IMPORTANT]
> **尚未发布的源码预览。** 此适配器尚未发布到 npm；文中的包名目前仅能在本仓库的 npm workspace 中解析。

**[MemoWeft](https://github.com/memoweft/memoweft) 的 LangChain 适配器。** 通过三条缝给你的 `@langchain/core` 应用接上长期记忆：**读** = 一个 `BaseRetriever`，把相关记忆召回成 `Document[]` 供你拼进 prompt；**写** = 一个 `BaseCallbackHandler` 沉淀每条工具结果，外加一个宿主闭包沉淀用户原话。

这是一个**外部集成包**。它封装 MemoWeft 的公开 Core facade（`createMemoWeftCore`），不碰 Core 内部。`@langchain/core` 是 peer 依赖（自带）。

## 从源码检出试用

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-langchain
```

`@langchain/core` `^1` 与 `memoweft` `^0.6.0` 是 peer 依赖。`langchain` `^1`（携带 v1 agent middleware API 的伞包）是**可选** peer——只有使用下面的 v1 middleware 入口才需要它；retriever + callback 集成只需 `@langchain/core`。

## LangChain v1 agent middleware（`createAgent` 首选）

如果你用 LangChain v1 的 `createAgent` 搭智能体，推荐使用统一 middleware：`createMemoWeftMiddleware(core, opts?)`：

```ts
import { createAgent } from 'langchain';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftMiddleware } from '@memoweft/adapter-langchain';

const core = createMemoWeftCore({ dbPath: './memory.db' });

const agent = createAgent({
  model, // 自带对话模型
  tools,
  middleware: [createMemoWeftMiddleware(core, { lang: 'zh' })],
});
```

middleware 在哪个 hook 接了什么：

- **召回注入（读）— `wrapModelCall`。** 每次模型调用前，为本轮最后一条 human 消息召回记忆，把中性知识块**临时**注入进 request 的 **`systemMessage`**（只对本次调用生效，不累积进会话 state）。这正是 retriever 老路自己做不到的注入（callbacks 仅观察）。
- **用户原话（写）— `beforeAgent`。** 本轮最后一条 human 原话存一次为 `spoken` 证据（按消息 id 幂等）。
- **工具结果（写）— `wrapToolCall`。** 证据边界由结构保证：只存工具返回的 `ToolMessage.content` 为 `tool` 证据，不读取工具调用入参。
- **AI 回复（上下文）— `afterAgent`。** 在 memoweft `0.6` 下，本轮最终 AI 回复经 `recordAssistantReply` 上报，好让**下一轮**的短回答（「是的」「后者」）能对着它被理解。它**只作上下文、永不落证据**。`0.5` 上此步跳过（运行时能力探测），其余照常。

会话线程默认用 `runtime.configurable.thread_id` 作 MemoWeft 的 `conversationId`（可用选项 `conversationId` / `getConversationId` 覆盖）。隐私与降级同下面的 retriever 路径（provenance 绝不注入、召回受 `recallTimeoutMs` 上界、写路径失败重试一次）。

下面的 retriever + callback API 仍完整保留,供非 agent 链或 LangChain v0 式接线使用。

## 为何召回走 retriever 而非 callback

**框架行为：** LangChain 的 callbacks 是**仅观察**——`CallbackManager` 丢弃 handler 的返回值，故 handler 无法把任何东西**注入**进模型输入。因此召回注入**不能**搭 callback，必须走 `BaseRetriever`（一个 `Runnable`）：你调 `retriever.invoke(query)` 获取 `Document[]`，自行把中性块拼进 prompt。两条写路径——工具结果与用户原话——才是搭 callback / 闭包的东西。

## 一个工厂、四件套、三条路径

`createMemoWeftLangChain(core, opts?)` 返回 `{ retriever, writeCallback, formatKnowledge, persistUserTurn }`。

```ts
import { ChatPromptTemplate } from '@langchain/core/prompts';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftLangChain } from '@memoweft/adapter-langchain';

const core = createMemoWeftCore({ dbPath: './memory.db' });
const mw = createMemoWeftLangChain(core, { lang: 'en' });

const prompt = ChatPromptTemplate.fromMessages([
  ['system', 'You are a helpful assistant.{memory}'],
  ['human', '{question}'],
]);

// ② write: persist the user's own words (before any injection); originId is a stable idempotency key.
await mw.persistUserTurn({ text: question, originId: turnId });

// ① read: recall → Document[] → neutral knowledge block → you stitch it into the prompt.
const memory = mw.formatKnowledge(await mw.retriever.invoke(question));

// ③ write: attach the callback so every tool result in the chain is persisted automatically.
const chain = prompt.pipe(model); // bring your own chat model, e.g. @langchain/openai's ChatOpenAI
const reply = await chain.invoke({ memory, question }, { callbacks: [mw.writeCallback] });
```

三条路径、三件事：

- **① 召回注入（读）——`retriever` + `formatKnowledge`。** `mw.retriever` 是 `MemoWeftRetriever extends BaseRetriever`；`retriever.invoke(query)` 召回并返回 `Document[]`（`pageContent` = 认知的 content；`metadata` 带 host-facing 的 `confidence` / `credStatus` / `id` / `contentType` / `score`）。`mw.formatKnowledge(docs)` 把它们渲染成中性知识块——MemoWeft 自己的措辞，逐字照搬 Core 的 `knowledgeBlock`。低置信条目明确标注*"only guesses — do not treat as established facts"*。适配器**不自造任何人格/人设 prompt**；块在 prompt 中的位置由你决定。
- **② 用户原话（写）——`persistUserTurn`。** 在调用点、注入**之前**调 `mw.persistUserTurn({ text, originId })`，传入宿主持有的原始文本。不要从链事件重新派生该文本：模型输入已包含召回记忆，这样做会将注入内容错误地存为用户证据。原始文本存为 `spoken` 证据。
- **③ 工具结果（写）——`writeCallback`。** 把 `mw.writeCallback` 挂进任意链的 `config.callbacks`。它**只**实现 `handleToolEnd`（工具真实的**返回结果**）→ 存成 `tool` 证据，以 `runId` 作幂等键。它不声明 `handleToolStart`，因此来源角色边界由结构保证：`CallbackManager` 不会把工具**调用意图 / 入参**投递给该适配器。

## 隐私保证

`provenance`（证据原文 + 授权位）**绝不**进入 `pageContent` 或 `Document.metadata`，也绝不进入 `formatKnowledge` 块。`pageContent` 只放 `content`；`buildKnowledgeBlock` 只用 `content` / `confidence` / `credStatus`。更丰富的召回面——`id`、`contentType`、`score`，以及（带 `explain` 时）含 `allowCloudRead` / `allowInference` 授权位的 `provenance`——**只**经 `onRecall` 回调交给宿主，你可在转发云模型前自筛。因注入落在你的 prompt（绝不回写进捕获的用户原话），存下的 `spoken` 证据永不含被注入的记忆。

## 降级策略

- 召回受 `recallTimeoutMs`（默认 200ms）限制。超时/抛错则 `retriever.invoke` 返回 `[]`——本轮**不注入**继续；召回失败绝不阻塞对话。读路径不重试。
- 写（`ingest`）遇真错重试一次；仍失败则记日志（若提供 `logger`）并静默吞。绝不向链 / 调用方抛。

## 选项

工厂：`{ subjectId?, lang?: 'en' | 'zh', contentTypes?, explain?, onRecall?, recallTimeoutMs?, ingestTimeoutMs?, logger? }`

每轮（经 `persistUserTurn`）：`{ text, originId?, subjectId?, hostId?, occurredAt? }`

## 完整示例

见 [`examples/basic.ts`](./examples/basic.ts)——两轮对话：把每轮的话存进去、经召回注入进下一轮的 prompt；`RunnablePassthrough.assign` 的 LCEL 接线作为备选一并示意。

## 为何不实现 `BaseMemory`

LangChain 的 `BaseMemory` 是**会话缓冲**——将过往对话重新加入 prompt。这与 MemoWeft 的长期记忆正交：MemoWeft **区分事实与猜测、置信度由规则算（非模型自报）、只存用户原话与工具结果、绝不存助手回复。** 将 MemoWeft 接入 `BaseMemory` 会把助手回复与注入记忆当"历史"存回，违反 MemoWeft 的“助手输出不自证”规则。故本适配器走三条缝——retriever（读）加 callback 与闭包（写）——而非 `BaseMemory`。

## 它不做什么

- 不注入人格/人设 prompt（Core 无头——语气/角色是宿主的事）。
- 不存助手回复，只存用户原话与工具结果。
- 从不读工具调用的入参，写路径不带任何上云授权位。

## License

MIT
