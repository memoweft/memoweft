# @memoweft/adapter-claude-agent-sdk

> English · [README.md](./README.md)

> [!IMPORTANT]
> **尚未发布的源码预览。** 此适配器尚未发布到 npm；文中的包名目前仅能在本仓库的 npm workspace 中解析。

**[MemoWeft](https://github.com/memoweft/memoweft) 的 Claude Agent SDK 适配器。** 通过 **hooks** 给你的 Claude Agent SDK 应用接上长期记忆：**读** = 召回相关记忆并注入本轮；**写** = 沉淀用户原话与每条工具结果。

这是一个**外部集成包**。它封装 MemoWeft 的公开 Core facade（`createMemoWeftCore`），不碰 Core 内部。`@anthropic-ai/claude-agent-sdk` 是 peer 依赖（自带）。

## 从源码检出试用

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-claude-agent-sdk
```

`@anthropic-ai/claude-agent-sdk` `^0.3.207` 与 `memoweft` `^0.5.1 || ^0.6.0` 是 peer 依赖。

## 一个工厂、两个 hook、三条路径

`createMemoWeftAgentHooks(core, opts?)` 返回 `{ hooks }`，直接摊进 `query` 的 `options.hooks`：

```ts
import { query } from '@anthropic-ai/claude-agent-sdk';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftAgentHooks } from '@memoweft/adapter-claude-agent-sdk';

const core = createMemoWeftCore({ dbPath: './memory.db' });
const { hooks } = createMemoWeftAgentHooks(core, { lang: 'zh' });

for await (const msg of query({
  prompt: '解释一下递归。',
  options: { hooks: { ...hooks } },
})) {
  // ...
}
```

- **`UserPromptSubmit`** 每轮做两件事：先**存用户原话**（`core.ingestUserMessage`，一条 `spoken` 证据），再**召回**相关记忆、经 hook 返回值（`hookSpecificOutput.additionalContext`）注入。注入走返回值、不改 `input.prompt`，所以存下的原话永不含注入内容——**by design** 干净。
- **`PostToolUse`** 存每条**工具结果**（`core.ingestToolResult`，一条 `tool` 证据）。它**只**读 `tool_response` 与 `tool_use_id`，**绝不**读/引用 `tool_input`，因此模型的调用意图不可能成为证据。

注入块用 MemoWeft 自己的中性措辞（逐字照搬 Core 的 `knowledgeBlock`）。低置信条目明确标注*"只是假设——别当定论"*。适配器**不自造任何人格/人设 prompt**。

## 隐私保证

`provenance`（证据原文 + 授权位）、`contentType`、`score`、`id` **绝不**进注入的 `additionalContext`。`buildKnowledgeBlock` 只用 `content` / `confidence` / `credStatus`。更完整的召回面**只**经 `onRecall` 回调交给宿主，宿主转发云模型前自行按授权位过滤。

## 降级策略

- 召回受 `recallTimeoutMs`（默认 200ms）约束。超时/抛错则本轮**不注入**继续——召回失败绝不阻塞对话。读路径不重试。
- 写（`ingest`）失败重试一次；仍失败则记事件（若注入了 `logger`）并静默吞。任何情况都不向 SDK 抛。

## 选项

`{ subjectId?, lang?: 'en' | 'zh', contentTypes?, explain?, onRecall?, recallTimeoutMs?, ingestTimeoutMs?, logger? }`

## 完整示例

见 [`examples/basic.ts`](./examples/basic.ts)——两轮对话：第 1 轮的话被存进去、召回进第 2 轮的 prompt，途中工具结果也一并沉淀。

## 备选：直接挂 MCP server（近零代码）

如果你压根不想接 hooks，Claude Agent SDK 可以经 `options.mcpServers`（stdio 传输）直接挂上 [`@memoweft/mcp-server`](../mcp-server)。这样记忆是模型**自己调用的 MCP 工具**：

```ts
import { query } from '@anthropic-ai/claude-agent-sdk';

for await (const msg of query({
  prompt: 'Explain recursion.',
  options: {
    mcpServers: {
      memoweft: {
        command: 'npx',
        args: ['-y', '@memoweft/mcp-server'],
        env: { MEMOWEFT_DB_PATH: '/absolute/path/to/memoweft.db' },
      },
    },
  },
})) {
  // ...
}
```

这是**近零代码**路径——没有任何适配器接线；server 暴露 5 个只读工具外加 3 个轻写（`memoweft_ingest_user_message`、`memoweft_ingest_tool_result`、`memoweft_mute_cognition`）。相较上面的 hooks，取舍是：

- **Hooks（本包）：** **每轮**都注入召回，用户原话与每条工具结果自动、如实地沉淀——记忆层对模型透明。
- **挂 MCP：** 记忆是**模型自行决定去调的工具**。集成近零，但召回/写入只在模型主动调用时才发生，且写面刻意收窄（三个轻写——两个 `spoken` / `tool` 证据摄入 + 一个可逆的召回静音；破坏性/改授权的操作从不暴露）。

想要每轮都稳拿召回、如实沉淀，就用 hooks；想要尽可能小的集成、并接受由模型驱动的工具式记忆访问，就挂 MCP。

## 它不做什么

- 不加人格/人设 prompt（Core 无头——语气/角色是宿主的事）。
- 不存助手回复，只存用户原话与工具结果。
- 绝不读 `tool_input`，写路径不传任何上云授权位。

## 许可

MIT
