# Vercel AI SDK integration in five minutes

**English** | [简体中文](./vercel-ai-sdk.zh-CN.md)

Give your existing Vercel AI SDK app long-term memory. `@memoweft/adapter-ai-sdk` wraps MemoWeft's public Core: **read** recalls memory into the prompt, **write** stores the user's own words after each turn.

## Install

The npm-published adapter is `0.1.0`; install this fixed, compatible set:

```bash
npm i ai memoweft@0.5.1 @memoweft/adapter-ai-sdk@0.1.0
```

`0.2.0` on `main` is an unreleased workspace version. Build it from this checkout when pairing with Core `0.5.1` or `0.6`; its peer range is `memoweft` `^0.5.1 || ^0.6.0`. Do not use `--legacy-peer-deps` to force published `0.1.0` onto Core `0.6`. `ai` `^7` is also a peer dependency. Bring your own provider, e.g. `@ai-sdk/openai`.

## Wire it up

`createMemoWeftMiddleware` reads (recall then inject); `createPersistOnEnd` writes (user's words then store).

<!-- snippet:skip (needs a live model) -->

```ts
import { generateText, wrapLanguageModel } from 'ai';
import { openai } from '@ai-sdk/openai';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftMiddleware, createPersistOnEnd } from '@memoweft/adapter-ai-sdk';

const core = createMemoWeftCore({ dbPath: './memory.db' });

// Read: recall relevant memory and inject it before the model sees the prompt.
const model = wrapLanguageModel({
  model: openai('gpt-4o-mini'),
  middleware: createMemoWeftMiddleware(core, { subjectId: 'alice' }),
});

const userMessage = 'I strongly prefer short, direct answers.';
const { text } = await generateText({
  model,
  prompt: userMessage,
  // Write: after the turn ends, store the user's own words as one `spoken` evidence.
  onEnd: createPersistOnEnd(core, { subjectId: 'alice', userMessage, originId: 'turn-1' }),
});
```

That is the whole loop. Recall reflects the **profile** (cognitions), so a later turn recalls turn-1 only after your host has run `core.updateProfile` and configured an embedder; without them the write path still stores evidence, but recall stays empty.

## Why you pass `userMessage` yourself

`onEnd` carries only result-side fields (text, usage, steps), not the original input, and the request sent to the provider was already rewritten by the read middleware. So the only clean source of the user's real words is the value you already hold. Pass a stable `originId` (your turn id) for idempotency: the same turn stores at most one evidence. Recall or ingest failures leave the host to continue its normal reply handling. Only the user's words are stored, never the assistant reply (Core discipline).

## Persist tool results too

If your turn runs tools, store the tool's **returned output** as `tool` evidence, never the model's call arguments. This preserves the boundary between external results and model-generated intent.

<!-- snippet:skip (needs a live model) -->

```ts
import { persistToolResults } from '@memoweft/adapter-ai-sdk';

const result = await generateText({ model, prompt: userMessage, tools });
// Reads only role:'tool' messages; returns how many results were stored.
await persistToolResults(core, {
  subjectId: 'alice',
  messages: result.response.messages,
  originIdPrefix: 'turn-2',
});
```

## Verify the write path with no model

The write helpers call Core directly: no key, no network. This stores one turn and reads it back.

```ts
import { createMemoWeftCore } from 'memoweft';
import { persistUserTurn } from '@memoweft/adapter-ai-sdk';

const core = createMemoWeftCore({ dbPath: ':memory:' });
await persistUserTurn(core, {
  subjectId: 'alice',
  userMessage: 'I strongly prefer short, direct answers.',
  originId: 'turn-1',
});

for (const e of core.memory.listEvidence({ subjectId: 'alice' })) {
  console.log(e.sourceKind, '·', e.rawContent); // -> spoken · I strongly prefer short, direct answers.
}
core.close();
```

`persistUserTurn` is the same write `createPersistOnEnd` runs; use it outside an `onEnd` hook.

## Next

- Full two-turn example: [`packages/adapter-ai-sdk/examples/basic.ts`](../../packages/adapter-ai-sdk/examples/basic.ts).
- See recall, conflict, and decay in action: [the four-act demo](../demo-script.md) (`npm run demo`).
- Every method and shape: [API reference](../reference/memory-surface-contract.md).
