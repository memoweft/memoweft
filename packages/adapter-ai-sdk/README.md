# @memoweft/adapter-ai-sdk

> 中文版 · [README.zh-CN.md](./README.zh-CN.md)

**Vercel AI SDK adapter for [MemoWeft](https://github.com/memoweft/memoweft).** Give your AI SDK app long-term memory: **read** = recall relevant memory and inject it into the prompt; **write** = persist the user's own words after each turn.

This is an **external integration package**. It wraps MemoWeft's public Core facade (`createMemoWeftCore`) — it does not touch Core internals. `ai` is a peer dependency (bring your own).

## Install

The npm-published package is `@memoweft/adapter-ai-sdk@0.1.0`. Its compatible, resolver-clean installation is:

```bash
npm i ai memoweft@0.5.1 @memoweft/adapter-ai-sdk@0.1.0
```

The `0.2.0` package on `main` is unreleased. Use that source preview through the repository workspace:

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-ai-sdk
```

Its peer range is `ai` `^7` and `memoweft` `^0.5.1 || ^0.6.0`. Do not use `--legacy-peer-deps` to combine the published `0.1.0` package with Core `0.6`. You also need an `ai` provider (e.g. `@ai-sdk/openai`) for a real model.

## Two paths: read and write

### Read — recall via middleware

`createMemoWeftMiddleware(core)` returns a Vercel AI SDK `LanguageModelMiddleware`. In `transformParams` it takes the **last user message text**, calls `core.recall({ query })`, and injects the recalled cognitions into that user message before the model sees it.

```ts
import { wrapLanguageModel, generateText } from 'ai';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftMiddleware } from '@memoweft/adapter-ai-sdk';

const core = createMemoWeftCore({ dbPath: './memory.db' });

const model = wrapLanguageModel({
  model: baseModel, // your @ai-sdk/* model
  middleware: createMemoWeftMiddleware(core),
});

const { text } = await generateText({ model, prompt: 'Explain recursion.' });
```

The injected block uses MemoWeft's own neutral wording (ported verbatim from Core's `knowledgeBlock`). Low-confidence items are explicitly marked _"only guesses — do not treat as established facts."_ The adapter **adds no persona / character prompt** of its own — tone and role stay the host's job.

If recall returns nothing, the query has no user text, or recall fails, the adapter passes the original params through unchanged.

Options: `{ subjectId?, lang?: 'en' | 'zh', onRecall? }`.

### Write — persist the user's turn on finish

`createPersistOnEnd(core, { userMessage, originId })` returns a callback for `generateText`/`streamText`'s **`onEnd`** (the SDK's `onFinish` is a deprecated alias for `onEnd`). After the turn ends it calls `core.ingestUserMessage` to store **the user's own words** as one `spoken` evidence.

```ts
import { createPersistOnEnd } from '@memoweft/adapter-ai-sdk';

const userMessage = 'I strongly prefer short, direct answers.';
await generateText({
  model,
  prompt: userMessage,
  onEnd: createPersistOnEnd(core, { userMessage, originId: 'turn-1' }),
});
```

**Why you pass `userMessage` explicitly:** the SDK's `onEnd` event carries only _result-side_ fields (text, steps, usage, response…). It does not carry the original user input, and the request body sent to the provider has already been rewritten by the read middleware (recalled memory injected). So the only clean source of the user's real words is the value you already hold when you call `generateText`. The `onEnd` event object is **not used** — it is just the trigger.

- **User words only, never the assistant reply** (Core discipline: the assistant reply is not recorded as evidence).
- Give a stable `originId` (your turn/message id) for **idempotency** — the same turn stores at most one evidence even if `onEnd` fires more than once.
- No cloud-authorization bits are passed: `ingestUserMessage` stores `spoken` evidence, which does not involve the observed cloud-consent flags.
- Empty/whitespace-only input is skipped. Ingest errors go to `onError` (or are swallowed), leaving the host's turn handling intact.

There is also a plain `persistUserTurn(core, { userMessage, originId? })` if you want to call it outside an `onEnd` hook.

## Full example

See [`examples/basic.ts`](./examples/basic.ts) — a two-turn chat where turn 1's words are stored and recalled into turn 2's prompt.

## Relationship to the MemoWeft Host

The Host (`apps/memoweft-host`) is MemoWeft's _reference application_ — chat UI, multi-session, backups. This adapter is the _opposite direction_: it lets **your** app (built on the Vercel AI SDK) reuse MemoWeft as a memory backend, without the Host. Both talk to the same public Core facade; they don't depend on each other. Pick the Host if you want a ready UI; pick this adapter if you already have an AI SDK app and just want the memory layer.

## What it does not do

- No persona / character prompt (Core is headless — tone/role is the host's job).
- Does not store the assistant reply, only the user's words.
- Does not widen `observed` cloud sharing, and does not pass authorization bits on the write path.

## License

MIT
