# @memoweft/adapter-langchain

> 中文版 · [README.zh-CN.md](./README.zh-CN.md)

> [!IMPORTANT]
> **Unreleased source preview.** This adapter is not published on npm. Its package name resolves inside this repository's npm workspace only.

**LangChain adapter for [MemoWeft](https://github.com/memoweft/memoweft).** Give your `@langchain/core` app long-term memory across three seams: **read** = a `BaseRetriever` that recalls relevant memory as `Document[]` for you to stitch into your prompt; **write** = a `BaseCallbackHandler` that persists each tool result, plus a host closure that persists the user's own words.

This is an **external integration package**. It wraps MemoWeft's public Core facade (`createMemoWeftCore`) — it does not touch Core internals. `@langchain/core` is a peer dependency (bring your own).

## Try from a source checkout

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-langchain
```

`@langchain/core` `^1` and `memoweft` `^0.6.0` are peer dependencies. `langchain` `^1` (the umbrella that ships the v1 agent middleware API) is an **optional** peer — you only need it if you use the v1 middleware entry below; the retriever + callback path needs only `@langchain/core`.

## LangChain v1 agent middleware (recommended for `createAgent`)

If you build agents with LangChain v1's `createAgent`, the modern entry is **one middleware that does everything** — `createMemoWeftMiddleware(core, opts?)`:

```ts
import { createAgent } from 'langchain';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftMiddleware } from '@memoweft/adapter-langchain';

const core = createMemoWeftCore({ dbPath: './memory.db' });

const agent = createAgent({
  model, // bring your own chat model
  tools,
  middleware: [createMemoWeftMiddleware(core, { lang: 'en' })],
});
```

What the middleware wires, and on which hook:

- **Recall injection (read) — `wrapModelCall`.** Before each model call it recalls memory for the turn's last human message and injects the neutral knowledge block into the request's **`systemMessage`** for that call only — ephemeral, so it never accumulates in conversation state. This is the injection the retriever path can't do on its own (callbacks are observe-only).
- **The user's words (write) — `beforeAgent`.** The turn's last human message is stored once as `spoken` evidence (idempotent on the message id).
- **Tool results (write) — `wrapToolCall`.** The evidence boundary is enforced by construction: only the returned `ToolMessage.content` is stored as `tool` evidence, and tool-call arguments are never read.
- **Assistant reply (context) — `afterAgent`.** With memoweft `0.6`, the final AI reply is reported via `recordAssistantReply` so the **next** turn's short answer ("yes", "the latter") can be understood against it. It is **context only, never evidence**. On `0.5` this is skipped (runtime capability probe) and everything else still works.

Conversation threading uses `runtime.configurable.thread_id` as the MemoWeft `conversationId` by default (override with `conversationId` / `getConversationId` in options). Privacy and degradation are identical to the retriever path below (provenance never injected, recall bounded by `recallTimeoutMs`, writes retry once).

The retriever + callback API below remains fully supported for non-agent chains or LangChain v0-style wiring.

## Why recall goes through a retriever, not a callback

**Hard fact:** LangChain callbacks are **observe-only** — the `CallbackManager` discards a handler's return value, so a handler cannot **inject** anything into the model input. Recall injection therefore **cannot** ride a callback; it must go through a `BaseRetriever` (a `Runnable`). You call `retriever.invoke(query)`, get `Document[]`, and stitch the neutral block into your prompt yourself. The two write paths — tool results and the user's words — are the only things that ride the callback / a closure.

## One factory, four pieces, three paths

`createMemoWeftLangChain(core, opts?)` returns `{ retriever, writeCallback, formatKnowledge, persistUserTurn }`.

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

Three paths, three pieces:

- **① Recall injection (read) — `retriever` + `formatKnowledge`.** `mw.retriever` is a `MemoWeftRetriever extends BaseRetriever`; `retriever.invoke(query)` recalls and returns `Document[]` (`pageContent` = the cognition's content; `metadata` carries host-facing `confidence` / `credStatus` / `id` / `contentType` / `score`). `mw.formatKnowledge(docs)` renders them into the neutral knowledge block — MemoWeft's own wording, ported verbatim from Core's `knowledgeBlock`. Low-confidence items are explicitly marked _"only guesses — do not treat as established facts."_ The adapter **adds no persona / character prompt** of its own; where the block goes in the prompt is your call.
- **② The user's words (write) — `persistUserTurn`.** Call `mw.persistUserTurn({ text, originId })` at the call site, **before** injection, passing the words you already hold. Do not fish them back out of chain events: the model input has already been injected with recalled memory, so re-reading it would store the injected memory as if it were the user's words. Stored as `spoken` evidence.
- **③ Tool results (write) — `writeCallback`.** Add `mw.writeCallback` to any chain's `config.callbacks`. It implements **only** `handleToolEnd` (the tool's real **result**) → stored as `tool` evidence, keyed by `runId` for idempotency. It never declares `handleToolStart`, so the source-role boundary is enforced by construction: `CallbackManager` cannot dispatch tool-call **intent / arguments** to this adapter.

## Privacy hard constraint

`provenance` (evidence text + authorization bits) **never** enters `pageContent` or `Document.metadata`, and never enters the `formatKnowledge` block. `pageContent` holds only `content`; `buildKnowledgeBlock` uses only `content` / `confidence` / `credStatus`. The richer recall surface — `id`, `contentType`, `score`, and (with `explain`) `provenance` including `allowCloudRead` / `allowInference` bits — is handed to the host **only** through the `onRecall` callback, so you can filter before forwarding anything to a cloud model. Because injection lands in your prompt (never back into the captured user words), the stored `spoken` evidence can never contain injected memory.

## Degradation

- Recall is bounded by `recallTimeoutMs` (default 200ms). On timeout or error `retriever.invoke` returns `[]` — the turn proceeds **without injection**; recall failure never blocks the reply. The read path does not retry.
- Writes (`ingest`) retry once on real errors; a still-failing write is logged (if a `logger` is provided) and swallowed. Nothing is ever thrown to the chain or the caller.

## Options

Factory: `{ subjectId?, lang?: 'en' | 'zh', contentTypes?, explain?, onRecall?, recallTimeoutMs?, ingestTimeoutMs?, logger? }`

Per-turn (via `persistUserTurn`): `{ text, originId?, subjectId?, hostId?, occurredAt? }`

## Full example

See [`examples/basic.ts`](./examples/basic.ts) — a two-turn chat that stores each turn's words and recalls them into the next turn's prompt, with the `RunnablePassthrough.assign` LCEL wiring shown as an alternative.

## Why not implement `BaseMemory`

LangChain's `BaseMemory` is a **conversation buffer** — it stuffs prior turns back into the prompt. That is orthogonal to MemoWeft's long-term memory, which **separates facts from guesses, computes confidence by rule (not model self-report), stores only the user's words and tool results, and never stores the assistant reply.** Forcing MemoWeft into `BaseMemory` would store assistant replies and injected memory as "history", violating MemoWeft's no-self-evidence rule. So the adapter uses three seams — a retriever (read) plus a callback and a closure (write) — instead of `BaseMemory`.

## What it does not do

- No persona / character prompt (Core is headless — tone/role is the host's job).
- Does not store the assistant reply, only the user's words and tool results.
- Never reads a tool call's arguments, and passes no cloud-authorization bits on the write path.

## License

MIT
