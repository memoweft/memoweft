# Getting started

**English** | [简体中文](./getting-started.zh-CN.md)

Install MemoWeft, store one thing a user said, and read it back — in five minutes. No API key for the first step.

## Install

```bash
npm install memoweft
```

MemoWeft has **zero runtime dependencies**. On **Node 24+** it uses the built-in `node:sqlite` — nothing else to install. On Node 20 or 22, also install the optional driver:

```bash
npm install better-sqlite3   # only on Node 20 / 22
```

## Store and read back (no API key)

This runs with no model and no network. Copy it into a file and run it.

```ts
import { createMemoWeftCore } from 'memoweft';

// One line assembles the storage, recall, and model layers. ':memory:' = a throwaway in-memory db.
const core = createMemoWeftCore({ dbPath: ':memory:' });

// Store one thing the user said. This makes no model call — it just records raw evidence.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });

// Read it back through the controlled API — you never touch the database directly.
for (const e of core.memory.listEvidence({ subjectId: 'alice' })) {
  console.log(e.sourceKind, '·', e.rawContent); // → spoken · I own a red bicycle.
}

core.close();
```

You just wrote one piece of evidence and read it back. Note `sourceKind: 'spoken'` — MemoWeft records **who said it and how**, because a user's own words and a machine's guess are not the same kind of fact. That distinction is the whole point (see [Concepts](./concepts/)).

## Turn evidence into a profile (needs a chat model)

Storing evidence needs no model. Turning it into a **profile** — distilling facts, keeping guesses low-confidence, exposing conflicts — needs a chat model. Point MemoWeft at any OpenAI-compatible endpoint with a `.env` at your app root:

```ini
MEMOWEFT_LLM_BASE_URL=https://your-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-...
MEMOWEFT_LLM_MODEL=your-model
# Optional: an embedder unlocks semantic recall. Without it, recall falls back to keyword search (FTS5); the write path still runs.
MEMOWEFT_EMBED_BASE_URL=...
MEMOWEFT_EMBED_API_KEY=...
MEMOWEFT_EMBED_MODEL=...
```

<!-- snippet:skip (needs a live model) -->

```ts
const core = createMemoWeftCore({ dbPath: './memory.db' });

await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });
await core.updateProfile({ subjectId: 'alice' }); // distill → consolidate → attribute → index

// The next turn recalls the bicycle fact and injects it into the reply.
const turn = await core.handleConversationTurn({
  subjectId: 'alice',
  message: 'What color is my bicycle?',
});
console.log(turn.reply);
```

Missing config degrades instead of crashing: no chat model → the profile step errors but evidence is still stored; no embedder → recall falls back to keyword search (FTS5), still returning results (semantic recall needs an embedder). Check with `core.health()`. The full runnable loop is [`examples/minimal.ts`](../examples/minimal.ts).

## Next

- **[Run offline in 30 seconds](./demo-script.md)** — a deterministic, no-key proof after dependencies are installed.
- **[Concepts](./concepts/)** — why facts, guesses, conflicts, and stale states are kept apart.
- **[Recipes](./recipes/)** — drop MemoWeft into the Vercel AI SDK or an MCP server in five minutes.
- **[API reference](./reference/memory-surface-contract.md)** — every host-facing method and shape.
- **[Reference host](./reference-host.md)** — a local single-user host demo; not a production template.
- **[Deployment checklist](./deployment.md#production-checklist)** — operations and security work a real host must own.
