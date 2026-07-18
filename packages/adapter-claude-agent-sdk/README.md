# @memoweft/adapter-claude-agent-sdk

> 中文版 · [README.zh-CN.md](./README.zh-CN.md)

> [!IMPORTANT]
> **Unreleased source preview.** This adapter is not published on npm. Its package name resolves inside this repository's npm workspace only.

**Claude Agent SDK adapter for [MemoWeft](https://github.com/memoweft/memoweft).** Give your Claude Agent SDK app long-term memory through **hooks**: **read** = recall relevant memory and inject it into the turn; **write** = persist the user's own words and each tool result.

This is an **external integration package**. It wraps MemoWeft's public Core facade (`createMemoWeftCore`) — it does not touch Core internals. `@anthropic-ai/claude-agent-sdk` is a peer dependency (bring your own).

## Try from a source checkout

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-claude-agent-sdk
```

`@anthropic-ai/claude-agent-sdk` `^0.3.207` and `memoweft` `^0.5.1 || ^0.6.0` are peer dependencies.

## One factory, two hooks, three paths

`createMemoWeftAgentHooks(core, opts?)` returns `{ hooks }`, ready to spread into `query`'s `options.hooks`:

```ts
import { query } from '@anthropic-ai/claude-agent-sdk';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftAgentHooks } from '@memoweft/adapter-claude-agent-sdk';

const core = createMemoWeftCore({ dbPath: './memory.db' });
const { hooks } = createMemoWeftAgentHooks(core, { lang: 'en' });

for await (const msg of query({
  prompt: 'Explain recursion.',
  options: { hooks: { ...hooks } },
})) {
  // ...
}
```

- **`UserPromptSubmit`** does two things per turn. First it **stores the user's original words** (`core.ingestUserMessage`, a `spoken` evidence). Then it **recalls** relevant memory and injects it via the hook's return value (`hookSpecificOutput.additionalContext`). Because injection travels through the return value and never mutates `input.prompt`, the stored words never contain the injected memory — clean _by construction_.
- **`PostToolUse`** stores each **tool result** (`core.ingestToolResult`, a `tool` evidence). It reads **only** `tool_response` and `tool_use_id` — it never reads or references `tool_input`, so the model's call intent cannot become evidence.

The injected block uses MemoWeft's own neutral wording (ported verbatim from Core's `knowledgeBlock`). Low-confidence items are explicitly marked _"only guesses — do not treat as established facts."_ The adapter **adds no persona / character prompt** of its own.

## Privacy hard constraint

`provenance` (evidence text + authorization bits), `contentType`, `score`, and `id` **never** enter the injected `additionalContext`. `buildKnowledgeBlock` uses only `content` / `confidence` / `credStatus`. The richer recall surface is handed to the host **only** through the `onRecall` callback, so the host can filter before forwarding anything to a cloud model.

## Degradation

- Recall is bounded by `recallTimeoutMs` (default 200ms). On timeout or error the turn proceeds **without injection** — recall failure never blocks the reply. The read path does not retry.
- Writes (`ingest`) retry once on failure; a still-failing write is logged (if a `logger` is provided) and swallowed. Nothing is ever thrown to the SDK.

## Options

`{ subjectId?, lang?: 'en' | 'zh', contentTypes?, explain?, onRecall?, recallTimeoutMs?, ingestTimeoutMs?, logger? }`

## Full example

See [`examples/basic.ts`](./examples/basic.ts) — a two-turn chat where turn 1's words are stored and recalled into turn 2's prompt, with tool results persisted along the way.

## Alternative: mount the MCP server (near-zero code)

If you would rather not wire hooks at all, the Claude Agent SDK can mount [`@memoweft/mcp-server`](../mcp-server) directly through `options.mcpServers` (stdio transport). Memory then reaches the model as MCP **tools it calls on its own**:

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

This is the **near-zero-code** path — no adapter wiring; the server exposes 5 read tools plus 3 light writes (`memoweft_ingest_user_message`, `memoweft_ingest_tool_result`, `memoweft_mute_cognition`). The trade-off versus the hooks above:

- **Hooks (this package):** recall is injected on **every** turn, and the user's verbatim words plus every tool result are captured automatically and faithfully — the memory layer is transparent to the model.
- **MCP mount:** memory is a set of **tools the model chooses to call**. Near-zero integration, but recall/write happen only when the model decides to, and the write surface is deliberately narrow (three light writes — two `spoken` / `tool` evidence ingests and a reversible recall-mute; destructive / authorization-changing ops are never exposed).

Pick hooks when you want guaranteed per-turn recall and faithful capture; pick the MCP mount when you want the smallest possible integration and are fine with model-driven, tool-shaped memory access.

## What it does not do

- No persona / character prompt (Core is headless — tone/role is the host's job).
- Does not store the assistant reply, only the user's words and tool results.
- Never reads `tool_input`, and passes no cloud-authorization bits on the write path.

## License

MIT
