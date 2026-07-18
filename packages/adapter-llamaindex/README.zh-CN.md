# @memoweft/adapter-llamaindex

> English · [README.md](./README.md)

> [!IMPORTANT]
> **尚未发布的源码预览。** 此适配器尚未发布到 npm；文中的包名目前仅能在本仓库的 npm workspace 中解析。

> ## ⚠️ Legacy —— 已冻结（上游归档）
>
> **LlamaIndex.TS（[run-llama/LlamaIndexTS](https://github.com/run-llama/LlamaIndexTS)）已于 2026-04-30 归档为只读、官方弃维护**（团队转向 Python 线与 LlamaCloud）。因此本适配器**冻结在当前功能面**:它仍能用,但**不会**再获得 MemoWeft 的新面——尤其**不实现** MemoWeft 0.6 的会话上下文线（`recordAssistantReply`,活跃维护的适配器本轮都补了）。其上游依赖（`llamaindex` / `@llamaindex/workflow`）会随生态前进逐渐腐化。
>
> **若你在起新项目,请选维护中的框架**——见 [`@memoweft/adapter-mastra`](../adapter-mastra) 或 [`@memoweft/adapter-langchain`](../adapter-langchain)。本包仅为存量用户保留。

**[MemoWeft](https://github.com/memoweft/memoweft) 的 LlamaIndex 适配器。** 通过三条缝给你的 LlamaIndex agent（`llamaindex` + `@llamaindex/workflow`）接上长期记忆：**读** = 一个 `BaseMemoryBlock`，每次模型调用前召回相关记忆、作为一条中性的 `role:'memory'` 消息注入；**写** = 一个包住 `agent.runStream(...)` 的透传式包装器，原样 re-yield 每个事件、顺路沉淀用户原话与每条工具结果。

这是一个**外部集成包**。它封装 MemoWeft 的公开 Core facade（`createMemoWeftCore`），不碰 Core 内部。`llamaindex` 与 `@llamaindex/workflow` 是 peer 依赖（自带）。

> **上游说明（2026-07-18 更新）。** **整个 `run-llama/LlamaIndexTS` 仓库已于 2026-04-30 归档为只读**（最后发布 2025-12）。本适配器 peer 依赖的细分 `@llamaindex/*` 包与伞包 `llamaindex@^0.12` 上游全部冻结：不再发版、不再修 bug。它使用的事件驱动 agent API（`agent` / `runStream` / `agentToolCallResultEvent`）位于 `@llamaindex/workflow`。适配器仍能针对最后发布的版本工作，但会随框架保持冻结。

## 从源码检出试用

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-llamaindex
```

`llamaindex` `^0.12`、`@llamaindex/workflow` `^1.1.24` 与 `memoweft` `^0.5.1 || ^0.6.0` 是 peer 依赖。

## 为何召回走 memory-block、写走 stream-tap

**读——走 memory-block，而非手工拼 prompt。** LlamaIndex 的 `Memory` 会在**每次模型调用前**调各 block 的 `get(messages)`、把返回的「记忆上下文」拼进 prompt——这正是召回注入的缝。`MemoWeftMemoryBlock` 在 `get()` 里做一次语义召回、把中性知识块作为一条 `role:'memory'` 消息返回——故你只要把它加入 `createMemory({ memoryBlocks: [block] })`，注入就自动发生，你不写一行拼 prompt 的代码。

**写——走透传式 stream-tap，而非 block 的 `put()`。** `BaseMemoryBlock` 也有 `put()` 钩子，但 `Memory` 会将**整段会话**（含助手回复与已注入的记忆）传给它——在此持久化会错误地将助手输出视为证据。故本块的 `put()` 是**空实现**，写全走 `persistFromAgentStream`：用户原话由宿主显式传入（注入前持有），工具结果【只】从 `agentToolCallResultEvent` 认。

## 一个工厂、四件套、三条路径

`createMemoWeftLlamaIndex(core, opts?)` 返回 `{ memoryBlock, persistFromAgentStream, persistUserTurn, formatKnowledge }`。

```ts
import { createMemory } from '@llamaindex/core/memory';
import { agent } from '@llamaindex/workflow';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftLlamaIndex } from '@memoweft/adapter-llamaindex';

const core = createMemoWeftCore({ dbPath: './memory.db' });
const mw = createMemoWeftLlamaIndex(core, { lang: 'en' });

// ① read: drop the block into Memory → recall is injected automatically before every model call.
const memory = createMemory({ memoryBlocks: [mw.memoryBlock] });
const myAgent = agent({ llm, tools, memory }); // bring your own ToolCallLLM + tools

// ②③ write: wrap runStream — re-yields every event untouched, persisting the user's words + tool results in passing.
for await (const ev of mw.persistFromAgentStream(myAgent.runStream(userText), {
  userMessage: userText,
  originId: turnId,
})) {
  // …consume ev as usual (events pass through untouched)…
}
```

三条路径、三件事：

- **① 召回注入（读）——`memoryBlock`。** `mw.memoryBlock` 是 `MemoWeftMemoryBlock extends BaseMemoryBlock`。`Memory` 在每次模型调用前调 `block.get(messages)`；本块取末条 user 消息当 query、召回、返回**一条** `role:'memory'` 消息，其 content 是中性知识块——MemoWeft 自己的措辞，逐字照搬 Core 的 `knowledgeBlock`。低置信条目明确标注*"only guesses — do not treat as established facts"*。适配器**不自造任何人格/人设 prompt**。（`priority: 0` 表示该块总是被纳入记忆上下文。）
- **② 用户原话（写）——经 `persistFromAgentStream` 的 `userMessage`。** 将宿主在注入前持有的原始文本作为 `extras.userMessage` 传入。不要从事件流重新派生该文本：模型输入已包含召回记忆，这样做会将注入内容错误地存为用户证据。原始文本存为 `spoken` 证据。（另提供独立的 `persistUserTurn({ text, originId })` 闭包，供自行驱动 `runStream` 的宿主单独持久化原始文本。）
- **③ 工具结果（写）——经 `persistFromAgentStream` 的事件 tap。** 包装器 re-yield 每个事件，只沉淀匹配 `agentToolCallResultEvent` 的工具真实**返回结果**（`event.data.toolOutput.result`）→ 存成 `tool` 证据，以 `toolId` 作幂等键。结果判别器排除 `agentToolCallEvent`，因此来源角色边界由结构保证：工具**调用意图 / 入参**与助手输出事件无法进入写路径。

## 隐私保证

`provenance`（证据原文 + 授权位）**绝不**进入被注入的 `role:'memory'` 消息，也绝不进入 `formatKnowledge` 块。被注入的内容只用 `content` / `confidence` / `credStatus`（`buildKnowledgeBlock`）。更丰富的召回面——`id`、`contentType`、`score`，以及（带 `explain` 时）含 `allowCloudRead` / `allowInference` 授权位的 `provenance`——**只**经 `onRecall` 回调交给宿主，你可在转发云模型前自筛。因注入落在模型 prompt（绝不回写进捕获的用户原话），存下的 `spoken` 证据永不含被注入的记忆。

## 降级策略

- 召回受 `recallTimeoutMs`（默认 200ms）限制。超时/抛错则 `block.get()` 返回 `[]`——本轮**不注入**继续；召回失败绝不阻塞对话，也绝不向 `Memory` 抛。读路径不重试。
- 写（`ingest`）遇真错重试一次（超时的写不重试，因它可能已提交）；仍失败则记日志（若提供 `logger`）并静默吞。摄入失败**绝不**向流抛或中断流——每个事件都照样 re-yield。

## 选项

工厂：`{ subjectId?, lang?: 'en' | 'zh', contentTypes?, explain?, onRecall?, recallTimeoutMs?, ingestTimeoutMs?, logger?, memoryBlockId?, memoryBlockPriority? }`

每轮（经 `persistFromAgentStream`）：`{ userMessage, originId?, subjectId? }`

## 完整示例

见 [`examples/basic.ts`](./examples/basic.ts)——两轮对话：把每轮的话与工具结果存进去、经召回注入进下一轮的记忆块；真实的 `agent(...).runStream(...)` 接线与离线演示一并示意。

## 为何不实现 `FactExtractionMemoryBlock` 式抽取

LlamaIndex 内建的 `FactExtractionMemoryBlock` 用**LLM 自报事实**再存下。这与 MemoWeft 正交：MemoWeft **区分事实与猜测、置信度由规则算（非模型自报）、只存用户原话与工具结果、绝不存助手回复。** 故本适配器的 block 只做召回注入（`get()`）、`put()` 留空实现，写全走 stream-tap。

## 它不做什么

- 不注入人格/人设 prompt（Core 无头——语气/角色是宿主的事）。
- 不存助手回复，只存用户原话与工具结果。
- 从不读工具调用的入参（`agentToolCallEvent`），写路径不带任何上云授权位。

## License

MIT
