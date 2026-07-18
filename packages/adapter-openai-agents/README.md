# @memoweft/adapter-openai-agents

> 中文版 · [README.zh-CN.md](./README.zh-CN.md)

> [!IMPORTANT]
> **Unreleased source preview.** This adapter is not published on npm. Its package name resolves inside this repository's npm workspace only.

**OpenAI Agents SDK adapter for [MemoWeft](https://github.com/memoweft/memoweft).** Give your `@openai/agents` app long-term memory by **wrapping `run`**: **read** = recall relevant memory and inject it before the model call; **write** = persist the user's own words and each tool result.

This is an **external integration package**. It wraps MemoWeft's public Core facade (`createMemoWeftCore`) — it does not touch Core internals. `@openai/agents` is a peer dependency (bring your own).

## Try from a source checkout

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-openai-agents
```

`@openai/agents` `^0.13` and `memoweft` `^0.6.0` are peer dependencies.

## v0.6 conversation context (optional)

With memoweft `0.6`, pass a stable `conversationId` per run to enable short-reply understanding across turns — so a later "yes" / "the latter" is resolved against the assistant's previous question:

```ts
const result = await mw.run(agent, input, {
  memoweft: { conversationId: threadId, spokenOriginId: turnId },
});
```

When `conversationId` is set (and the Core is 0.6), the user's turn is ingested with it — Core captures the **previous** turn's AI reply into `preceding_ai_context` — and after the run the wrapper reports the turn's **final AI reply** via `recordAssistantReply`. The source-role boundary is explicit: assistant replies are **context only, never evidence**. This lets the _next_ turn be understood without storing the reply as memory. On memoweft `0.5` (no `recordAssistantReply`) this whole line is skipped via a runtime capability probe, and everything else works unchanged.

## One factory, three pieces, three paths

`createMemoWeftRunner(core, opts?)` returns `{ run, callModelInputFilter, persistToolOutputs }`. The turnkey path is the `run` wrapper — a near-drop-in for the SDK's **non-streaming** `run` (same `(agent, input, options)` shape for `string | AgentInputItem[]` input; streaming runs and `RunState` resume aren't wrapped — for those, wire `callModelInputFilter` + `persistToolOutputs` into your own `run`/`Runner`):

```ts
import { Agent } from '@openai/agents';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftRunner } from '@memoweft/adapter-openai-agents';

const core = createMemoWeftCore({ dbPath: './memory.db' });
const mw = createMemoWeftRunner(core, { lang: 'en' });

const agent = new Agent({ name: 'Assistant', instructions: 'You are helpful.' });
const result = await mw.run(agent, 'What theme do I prefer?', {
  memoweft: { spokenOriginId: turnId }, // stable idempotency key for the user's turn (optional)
});
```

- **`run` wrapper** does three things. It **captures the user's original words** from the `input` argument (before any injection) and stores them as a `spoken` evidence (`core.ingestUserMessage`). It **chains the recall filter** into the run's `callModelInputFilter` option (recall injection, below). After the run finishes it **scans `RunResult.newItems`** and stores each **tool result** (`core.ingestToolResult`) — only items of type `tool_call_output_item`, reading their `output` and `rawItem.callId`; the model's `tool_call_item` (call intent / arguments) is a separate item type and never enters scope. This enforces the evidence invariant that call intent is not evidence.
- **`callModelInputFilter`** is the recall injection on its own — for hosts who drive `run` / `Runner` / `RunConfig` themselves. It recalls relevant memory and appends the neutral knowledge block to the model's `instructions`. A guard injects **once per turn** (only when the last input item is a `user` message), so tool-call rounds are not re-injected. If you pass your own `callModelInputFilter` via `opts`, it is chained **first** (your edit runs, then recall is appended).
- **`persistToolOutputs(newItems)`** is the tool-result write on its own — call it with `result.newItems` if you drive the run yourself.

The injected block uses MemoWeft's own neutral wording (ported verbatim from Core's `knowledgeBlock`). Low-confidence items are explicitly marked _"only guesses — do not treat as established facts."_ The adapter **adds no persona / character prompt** of its own.

## Privacy hard constraint

`provenance` (evidence text + authorization bits), `contentType`, `score`, and `id` **never** enter the injected `instructions`. `buildKnowledgeBlock` uses only `content` / `confidence` / `credStatus`. The richer recall surface is handed to the host **only** through the `onRecall` callback, so the host can filter before forwarding anything to a cloud model. Injection targets `instructions` (never the user `input` items), so the captured original input can never contain the injected memory.

## Degradation

- Recall is bounded by `recallTimeoutMs` (default 200ms). On timeout or error the turn proceeds **without injection** — recall failure never blocks the reply. The read path does not retry.
- Writes (`ingest`) retry once on real errors; a still-failing write is logged (if a `logger` is provided) and swallowed. Nothing is ever thrown to the SDK or the caller.

## Options

Factory: `{ subjectId?, lang?: 'en' | 'zh', contentTypes?, explain?, onRecall?, callModelInputFilter?, recallTimeoutMs?, ingestTimeoutMs?, logger? }`

Per-run (via `run`'s `options.memoweft`): `{ spokenOriginId?, conversationId? }` (`conversationId` enables the v0.6 conversation context above)

## Full example

See [`examples/basic.ts`](./examples/basic.ts) — a two-turn chat where turn 1's words are stored and recalled into turn 2's model call via the `run` wrapper, with the self-driven `callModelInputFilter` + `persistToolOutputs` wiring shown as an alternative.

## Alternative: mount the MCP server (near-zero code)

If you would rather not wrap `run` at all, `@openai/agents` can mount [`@memoweft/mcp-server`](../mcp-server) as a hosted/stdio MCP server. Memory then reaches the model as MCP **tools it calls on its own** — the smallest possible integration, at the cost of model-driven (not guaranteed per-turn) recall and a deliberately narrow write surface. Pick the `run` wrapper when you want guaranteed per-turn recall and faithful capture; pick the MCP mount when you want the smallest possible integration.

## What it does not do

- No persona / character prompt (Core is headless — tone/role is the host's job).
- Never stores the assistant reply **as evidence** — only the user's words and tool results become evidence. With a v0.6 `conversationId` the reply is reported as **conversation context** (`recordAssistantReply`), never as a memory.
- Never reads a tool call's arguments, and passes no cloud-authorization bits on the write path.

## License

MIT
