# @memoweft/adapter-openai-agents

> English · [README.md](./README.md)

**[MemoWeft](https://github.com/memoweft/memoweft) 的 OpenAI Agents SDK 适配器。** 通过**包装 `run`** 给你的 `@openai/agents` 应用接上长期记忆：**读** = 召回相关记忆并在模型调用前注入；**写** = 沉淀用户原话与每条工具结果。

这是一个**外部集成包**。它封装 MemoWeft 的公开 Core facade（`createMemoWeftCore`），不碰 Core 内部。`@openai/agents` 是 peer 依赖（自带）。

## 安装

```bash
npm i @openai/agents memoweft @memoweft/adapter-openai-agents
```

`@openai/agents` `^0.13` 与 `memoweft` `^0.5.0 || ^0.6.0` 是 peer 依赖。

## v0.6 会话上下文（可选）

在 memoweft `0.6` 下，每轮传一个稳定的 `conversationId`，即可跨轮理解短回答——让后面一句「是的」/「后者」能对着 AI 上一句问题被解出：

```ts
const result = await mw.run(agent, input, {
  memoweft: { conversationId: threadId, spokenOriginId: turnId },
});
```

传了 `conversationId`（且 Core 是 0.6）时：本轮用户原话带它摄入——Core 据此把【上一轮】AI 那句捕获进 `preceding_ai_context`——run 结束后包装器再把本轮**最终 AI 回复**经 `recordAssistantReply` 报告。该回复**只作上下文、永不落证据**（铁律 3a）：它让【下一轮】能被理解，绝不存成记忆。memoweft `0.5`（无 `recordAssistantReply`）下这条线经运行时能力探测整条跳过，其余照常。

## 一个工厂、三件套、三条路径

`createMemoWeftRunner(core, opts?)` 返回 `{ run, callModelInputFilter, persistToolOutputs }`。开箱路径是 `run` 包装器——SDK **非流式** `run` 的近似替身（同 `(agent, input, options)` 签名、input 为 `string | AgentInputItem[]`；流式 run 与 `RunState` resume 不在包装范围——那些场景把 `callModelInputFilter` + `persistToolOutputs` 接进你自己的 `run`/`Runner`）：

```ts
import { Agent } from '@openai/agents';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftRunner } from '@memoweft/adapter-openai-agents';

const core = createMemoWeftCore({ dbPath: './memory.db' });
const mw = createMemoWeftRunner(core, { lang: 'zh' });

const agent = new Agent({ name: 'Assistant', instructions: '你是一个乐于助人的助手。' });
const result = await mw.run(agent, '我偏好什么主题？', {
  memoweft: { spokenOriginId: turnId }, // 用户这轮的稳定幂等键（可选）
});
```

- **`run` 包装器**做三件事。它从 `input` 实参**捕获用户原话**（在任何注入之前）并存成 `spoken` 证据（`core.ingestUserMessage`）；把**召回 filter chain** 进本轮 run 的 `callModelInputFilter` 选项（召回注入，见下）；run 结束后**扫 `RunResult.newItems`** 存每条**工具结果**（`core.ingestToolResult`）——只取 `tool_call_output_item` 类型项，读其 `output` 与 `rawItem.callId`；模型的 `tool_call_item`（调用意图/入参）是另一种 item 类型，从不进入作用域（AD-3 / 铁律 3a，代码级 by-construction）。
- **`callModelInputFilter`** 是单独的召回注入——供自己驱动 `run` / `Runner` / `RunConfig` 的宿主使用。它召回相关记忆，把中性知识块追加进模型的 `instructions`。guard 保证**每轮注一次**（只在末条 input 为 `user` 消息时），工具回合不会重复注入。若你经 `opts` 传了自己的 `callModelInputFilter`，它被**前置** chain（先跑你的编辑，再追加召回块）。
- **`persistToolOutputs(newItems)`** 是单独的工具结果写入——自驱动 run 时拿到 `result.newItems` 直接调它。

注入块用 MemoWeft 自己的中性措辞（逐字照搬 Core 的 `knowledgeBlock`）。低置信条目明确标注*"only guesses — do not treat as established facts"*。适配器**不自造任何人格/人设 prompt**。

## 隐私硬约束（D-0024）

`provenance`（证据原文 + 授权位）、`contentType`、`score`、`id` **绝不**进入注入的 `instructions`。`buildKnowledgeBlock` 只用 `content` / `confidence` / `credStatus`。更丰富的召回面**只**经 `onRecall` 回调交给宿主，宿主转发云模型前可自筛。注入落在 `instructions`（绝不碰 `input` 里的 user 项），故捕获的原始 input 永不含被注入的记忆。

## 降级（§16.2）

- 召回受 `recallTimeoutMs`（默认 200ms）限制。超时/抛错则本轮**不注入**继续——召回失败绝不阻塞回话。读路径不重试。
- 写（`ingest`）遇真错重试一次；仍失败则记日志（若提供 `logger`）并静默吞。绝不向 SDK / 调用方抛。

## 选项

工厂：`{ subjectId?, lang?: 'en' | 'zh', contentTypes?, explain?, onRecall?, callModelInputFilter?, recallTimeoutMs?, ingestTimeoutMs?, logger? }`

每轮（经 `run` 的 `options.memoweft`）：`{ spokenOriginId?, conversationId? }`（`conversationId` 启用上面的 v0.6 会话上下文）

## 完整示例

见 [`examples/basic.ts`](./examples/basic.ts)——两轮对话：第 1 轮的话被存进去、经 `run` 包装器召回进第 2 轮的模型调用；自驱动的 `callModelInputFilter` + `persistToolOutputs` 接线作为备选一并示意。

## 另一条路：挂载 MCP server（近零代码）

若你不想包装 `run`，`@openai/agents` 可把 [`@memoweft/mcp-server`](../mcp-server) 挂成 hosted/stdio MCP server。记忆则以模型**自行调用的 MCP 工具**触达——最小集成，代价是模型驱动（非保证每轮）的召回与刻意收窄的写面。要保证每轮召回与忠实捕获选 `run` 包装器；要最小集成选 MCP 挂载。

## 它不做什么

- 不注入人格/人设 prompt（Core 无头——语气/角色是宿主的事）。
- 绝不把助手回复存成**证据**——只有用户原话与工具结果成为证据。带 v0.6 `conversationId` 时，回复经 `recordAssistantReply` 作**会话上下文**上报，永不成记忆。
- 从不读工具调用的入参，写路径不带任何上云授权位。

## License

MIT
