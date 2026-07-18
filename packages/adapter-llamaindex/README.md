# @memoweft/adapter-llamaindex

> 中文版 · [README.zh-CN.md](./README.zh-CN.md)

> [!IMPORTANT]
> **Unreleased source preview.** This adapter is not published on npm. Its package name resolves inside this repository's npm workspace only.

> ## ⚠️ Legacy — frozen (upstream archived)
>
> **LlamaIndex.TS ([run-llama/LlamaIndexTS](https://github.com/run-llama/LlamaIndexTS)) was archived read-only on 2026-04-30 and is officially deprecated / no longer maintained** (its team moved to the Python line and LlamaCloud). This adapter is therefore **frozen at its current functionality**: it still works, but it will **not** gain new MemoWeft faces — in particular it does **not** implement MemoWeft 0.6's conversation-context line (`recordAssistantReply`) that the actively-maintained adapters received. Its upstream dependencies (`llamaindex` / `@llamaindex/workflow`) will drift as the ecosystem moves.
>
> **If you are starting new work, prefer a maintained framework** — see [`@memoweft/adapter-mastra`](../adapter-mastra) or [`@memoweft/adapter-langchain`](../adapter-langchain). This package is kept for existing users only.

**LlamaIndex adapter for [MemoWeft](https://github.com/memoweft/memoweft).** Give your LlamaIndex agent (`llamaindex` + `@llamaindex/workflow`) long-term memory across three seams: **read** = a `BaseMemoryBlock` that recalls relevant memory and injects it as a neutral `role:'memory'` message every model call; **write** = a pass-through wrapper around `agent.runStream(...)` that persists the user's own words and each tool result while re-yielding every event untouched.

This is an **external integration package**. It wraps MemoWeft's public Core facade (`createMemoWeftCore`) — it does not touch Core internals. `llamaindex` and `@llamaindex/workflow` are peer dependencies (bring your own).

> **Upstream note (updated 2026-07-18).** The **entire `run-llama/LlamaIndexTS` repository was archived read-only on 2026-04-30** (last publish 2025-12). The granular `@llamaindex/*` packages and the umbrella `llamaindex@^0.12` this adapter peers on are frozen upstream: no further releases or bug fixes. The event-driven agent API it uses (`agent` / `runStream` / `agentToolCallResultEvent`) lives in `@llamaindex/workflow`. The adapter still works against the last-published versions, but is frozen with the framework.

## Try from a source checkout

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-llamaindex
```

`llamaindex` `^0.12`, `@llamaindex/workflow` `^1.1.24`, and `memoweft` `^0.5.1 || ^0.6.0` are peer dependencies.

## Why recall goes through a memory block, and writes through a stream tap

**Read — a memory block, not a manual prompt splice.** LlamaIndex's `Memory` calls each block's `get(messages)` **before every model call** and stitches the returned "memory context" into the prompt. That is exactly the recall-injection seam. `MemoWeftMemoryBlock` implements `get()` to run one semantic recall and return the neutral knowledge block as a single `role:'memory'` message — so once you drop the block into `createMemory({ memoryBlocks: [block] })`, injection is automatic and you write no prompt-splicing code.

**Write — a pass-through stream tap, not the block's `put()`.** A `BaseMemoryBlock` also has a `put()` hook, but `Memory` feeds it the **whole conversation** (assistant replies and already-injected memory included) — persisting there would store the assistant's output as if it were evidence (dirty data). So the block's `put()` is a **no-op**, and all writing goes through `persistFromAgentStream`: the user's words are passed in explicitly (held before injection), and tool results are recognized **only** from `agentToolCallResultEvent`.

## One factory, four pieces, three paths

`createMemoWeftLlamaIndex(core, opts?)` returns `{ memoryBlock, persistFromAgentStream, persistUserTurn, formatKnowledge }`.

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

Three paths, three pieces:

- **① Recall injection (read) — `memoryBlock`.** `mw.memoryBlock` is a `MemoWeftMemoryBlock extends BaseMemoryBlock`. `Memory` calls `block.get(messages)` before each model call; the block takes the last user message as the query, recalls, and returns **one** `role:'memory'` message whose content is the neutral knowledge block — MemoWeft's own wording, ported verbatim from Core's `knowledgeBlock`. Low-confidence items are explicitly marked _"only guesses — do not treat as established facts."_ The adapter **adds no persona / character prompt** of its own. (`priority: 0` means the block is always included in the memory context.)
- **② The user's words (write) — via `persistFromAgentStream`'s `userMessage`.** Pass the words you already hold (**before** injection) as `extras.userMessage`. Do not fish them back out of the stream: the model input has already been injected with recalled memory, so re-reading it would store the injected memory as if it were the user's words. Stored as `spoken` evidence. (A standalone `persistUserTurn({ text, originId })` closure is also provided for hosts that drive `runStream` themselves.)
- **③ Tool results (write) — via `persistFromAgentStream`'s event tap.** The wrapper re-yields every event and persists **only** the ones matching `agentToolCallResultEvent` (the tool's real **result**, `event.data.toolOutput.result`) → stored as `tool` evidence, keyed by `toolId` for idempotency. The result discriminator excludes `agentToolCallEvent`, so the source-role boundary is enforced by construction: tool-call **intent / arguments** and assistant-output events cannot reach the write path.

## Privacy hard constraint

`provenance` (evidence text + authorization bits) **never** enters the injected `role:'memory'` message, and never enters the `formatKnowledge` block. The injected content uses only `content` / `confidence` / `credStatus` (`buildKnowledgeBlock`). The richer recall surface — `id`, `contentType`, `score`, and (with `explain`) `provenance` including `allowCloudRead` / `allowInference` bits — is handed to the host **only** through the `onRecall` callback, so you can filter before forwarding anything to a cloud model. Because injection lands in the model prompt (never back into the captured user words), the stored `spoken` evidence can never contain injected memory.

## Degradation

- Recall is bounded by `recallTimeoutMs` (default 200ms). On timeout or error `block.get()` returns `[]` — the turn proceeds **without injection**; recall failure never blocks the reply, and never throws to `Memory`. The read path does not retry.
- Writes (`ingest`) retry once on real errors (a timed-out write is not retried, since it may have committed); a still-failing write is logged (if a `logger` is provided) and swallowed. Ingestion failure **never** throws to or interrupts the stream — every event is re-yielded regardless.

## Options

Factory: `{ subjectId?, lang?: 'en' | 'zh', contentTypes?, explain?, onRecall?, recallTimeoutMs?, ingestTimeoutMs?, logger?, memoryBlockId?, memoryBlockPriority? }`

Per-turn (via `persistFromAgentStream`): `{ userMessage, originId?, subjectId? }`

## Full example

See [`examples/basic.ts`](./examples/basic.ts) — a two-turn chat that stores each turn's words and tool results and recalls them into the next turn's injected memory block, with the real `agent(...).runStream(...)` wiring shown alongside an offline demo.

## Why not implement `FactExtractionMemoryBlock`-style extraction

LlamaIndex's built-in `FactExtractionMemoryBlock` uses an **LLM to self-report facts** and store them. That is orthogonal to MemoWeft, which **separates facts from guesses, computes confidence by rule (not model self-report), stores only the user's words and tool results, and never stores the assistant reply.** So the adapter's block does recall-injection only (`get()`), leaves `put()` a no-op, and routes all writing through the stream tap.

## What it does not do

- No persona / character prompt (Core is headless — tone/role is the host's job).
- Does not store the assistant reply, only the user's words and tool results.
- Never reads a tool call's arguments (`agentToolCallEvent`), and passes no cloud-authorization bits on the write path.

## License

MIT
