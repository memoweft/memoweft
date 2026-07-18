# MemoWeft Integration Guide

**English** | [简体中文](./integration.zh-CN.md)

> For host developers: how to integrate MemoWeft — the "user cognition framework" layer — into your app / agent.
>
> The default path is **Cloud-first**: get running with an OpenAI-compatible cloud endpoint, then upgrade to Cloud-guarded or Hybrid / local-sensitive by data sensitivity.

---

## 1. The boundary between MemoWeft and the host

MemoWeft is a **library the host imports**. It does not do the chat UI, the persona, or the UI, and it does not decide the host's privacy policy.

| MemoWeft                                                              | Host application                                       |
| --------------------------------------------------------------------- | ------------------------------------------------------ |
| Store evidence, generate events, settle cognition, compute confidence | Owns chat, persona, tone, UI                           |
| Recall relevant user context                                          | Decides when to use it and how to phrase it            |
| Provide evidence authorization bits such as `allowCloudRead`          | Owns privacy policy, user consent, visibility settings |
| Keep the model / embedder swappable                                   | Decides cloud, local, or hybrid deployment             |

In one line: **MemoWeft provides "the understanding of the user"; the host decides "how to use that understanding".**

---

## 2. Install & import

MemoWeft is published on npm; hosts install it directly:

```bash
npm install memoweft
```

(TypeScript projects just add `@types/node` as usual; on Node 20/22 also install the optional `better-sqlite3` driver.) Integrating from source (to modify the library itself / track the latest commits) also works:

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm install
npm run typecheck && npm test && npm run build
```

When integrating from source, a host can use:

- a git submodule;
- `npm install <local-path>`;
- referencing `../memoweft/src/index.ts` directly;
- or referencing the built `dist/index.js`.

> Requirement: **Node ≥ 24 works out of the box** (storage uses the built-in `node:sqlite`); on **Node 20/22** the built-in module is unavailable, so install the optional `better-sqlite3` driver (`npm i better-sqlite3`). Developing / running the `.ts` tests still requires Node ≥ 24 (Node 22 needs 22.18+ to strip `.ts` types natively; Node 20 cannot). See [`INSTALL.md`](./INSTALL.md) for install details.

---

## 3. Cloud-first config

MemoWeft reads `.env`. The recommended start is a cloud OpenAI-compatible endpoint:

```ini
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# Optional: small/fast write-path model. Falls back to the chat model if unset.
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model

# Optional: semantic recall. If unset, recall falls back to keyword search (FTS5).
MEMOWEFT_EMBED_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_EMBED_API_KEY=sk-xxxx
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

The legacy `DLA_*` prefix still works; new integrations should use `MEMOWEFT_*`.

See the deployment modes in [`deployment.md`](./deployment.md):

- **Cloud-first**: fastest to run; good for demos / prototypes / general developer integration.
- **Cloud-guarded**: still cloud models, while MemoWeft's write path filters records with `allowCloudRead=false` from its cloud-model prompts. Hosts remain responsible for any other forwarding, storage, and access controls.
- **Hybrid / local-sensitive**: sensitive observations route to local models or a local embedder; low-risk calls can go to the cloud.

---

## 4. The 30-second mental model

```txt
evidence (source record)     → event (contextualized)      → cognition (judgment)
what was said/observed/returned a situational summary of a  understanding of the user,
                               conversation                  with confidence and provenance
```

- **Read path**: one conversation turn → store evidence → recall relevant cognition → inject into the reply.
- **Write path**: batch and distill evidence → generate events → update cognition → attribute → reindex.
- **Read/write decoupling**: profile updates are separate from the conversation path; the host may run them in the background or on demand.

---

## 5. End-to-end minimal integration

Prefer going through the unified entry `createMemoWeftCore` for everything: one line assembles the three-layer stores + retriever + model pool (all read from `.env`, degrading gracefully instead of crashing when config is missing), so the host does not hand-wire the low-level parts.

<!-- snippet:skip (needs a live model) -->

```ts
import { createMemoWeftCore } from 'memoweft';

// One-line assembly: three-layer stores + retriever + model pool, all from .env,
// degrading gracefully when config is missing.
// If subjectId / hostId are omitted, the defaults apply (config.identity: 'owner' / 'local').
const core = createMemoWeftCore({ dbPath: './my-app.db' });

// Self-check: can it chat / can it recall (decides whether to prompt the user to fill in .env).
const { llmReady, embedReady } = core.health();

// Read path: a user sends a turn → store evidence → recall relevant cognition → inject into the reply.
const turn = await core.handleConversationTurn({
  message: "I'm crunching on a side project lately and staying up late every night",
});
console.log(turn.reply);
console.log(turn.storedEvidence);
console.log(turn.recall);

// Write path: batch or manually trigger a profile update (distill → consolidate → attribute → reindex).
const upd = await core.updateProfile();
console.log(upd.consolidated.created);
console.log(upd.timings);

// Release connections when done (an injected retriever belongs to the caller and is not closed).
core.close();
```

> In a real host, avoid calling `updateProfile()` for every turn. The write path is heavier; Core exposes the one-shot operation, while the host owns batching, idle timers, queues, and per-subject serialization.
>
> Need finer manual assembly (your own `new Sqlite*Store` / a custom injected retriever)? See [`examples/minimal.ts`](../examples/minimal.ts) and [Swap points](#8-swap-points).

---

## 6. Ingesting behavior observations

Besides conversation, a host can also feed desktop, device, and window observations into the evidence layer. Core provides only a **generic observation ingest port**, `core.ingestObservation({ observations })`: it lands the host's normalized `Observation`s as `observed` evidence (not cloud-readable by default; idempotent by `originId`).

<!-- snippet:skip (continues the snippet above; needs a live model) -->

```ts
import type { Observation } from 'memoweft';

// The host normalizes an external signal into an Observation (here, one active-window observation built by hand).
const obs: Observation = {
  kind: 'active_window',
  occurredAt: new Date().toISOString(),
  content: 'Stayed in VS Code (memoweft) for about 40 minutes',
  originId: 'win-session-123', // optional idempotency key: the same window session is not stored twice
  // Omit the authorization bits → conservative observed defaults (local-readable / not cloud-readable / inference-allowed).
};

const stored = await core.ingestObservation({ observations: [obs] });
console.log(stored.length); // number of new observed evidence rows written this time
```

> **The real collector is not in Core.** "How to capture the active window from the OS" is a collector-plugin job (the Plugin layer), not Core; see [boundaries](./internals/boundaries.md). The real data flow is: the collector plugin samples windows → maps them to `Observation`s → POSTs to the host's `/api/observe` (the host reviews: collector master switch and any authorization policy) → the host calls `core.ingestObservation` to store them. See the reference implementation in [`plugins/collector-active-window/README.md`](../plugins/collector-active-window/README.md).

Recommended default policy:

- User chat / explicit input: the host may mark it cloud-readable.
- Desktop / device / health / screen observations: default `allowCloudRead=false`.
- Only after explicit user authorization does the host mark the corresponding evidence as cloud-uploadable.

The library already sets `busy_timeout=5000`; even so, do not run the write path from two processes at once.

---

## 7. Discipline not to bypass on integration

- With Core's **built-in ingestion methods**, user messages, observations, and tool results become evidence records.
- The built-in paths do not persist assistant replies as evidence. Hosts that ingest or persist other content remain responsible for preserving that distinction.
- LLM guesses may only enter low-confidence candidates / hypotheses.
- Surface conflicts first; do not auto-overwrite.
- MemoWeft's write path uses `allowCloudRead` when selecting records for a cloud-model prompt; the flag is not an access-control or encryption mechanism, and hosts must apply their own controls to other data flows.
- Short-term state should decay / expire; do not inject it permanently.

---

## 8. Swap points

The unified entry `createMemoWeftCore` already wires the defaults below; to swap an implementation, inject it through its options (e.g. `retriever` / `embedder` / `llm`), or assemble by hand from source (see [`examples/minimal.ts`](../examples/minimal.ts)).

| Part       | Default implementation                                                                                             | Purpose of swapping                             |
| ---------- | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| LLM client | `OpenAICompatClient`                                                                                               | Connect a different cloud / local model service |
| LLM pool   | `loadLLMPool()`                                                                                                    | Distinguish chat / write models                 |
| Embedder   | `OpenAICompatEmbedder`                                                                                             | Connect a cloud or local embedding endpoint     |
| Retriever  | `VectorRetriever`, or `KeywordRetriever` (FTS5) without an embedder; `NullRetriever` only when FTS5 is unavailable | Later swap for hybrid / graph retrieval, etc.   |
| Stores     | SQLite stores                                                                                                      | Later migrate to another storage backend        |

---

## 9. Common exports

The vast majority of hosts only need `createMemoWeftCore` + the facades it returns (`core.memory` / `core.portable` / `core.graph`) + the domain types. The common surface is listed below.

| Category                                     | Exports                                                                                                            |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Unified entry                                | `createMemoWeftCore`, `MemoWeftCore`                                                                               |
| Evidence layer                               | `Evidence`, `EvidenceInput`, `SourceKind`                                                                          |
| Event layer                                  | `Event`, `EventWithEvidence`                                                                                       |
| Cognition layer                              | `Cognition`, `CognitionWithSources`, `ContentType`, `CredStatus`                                                   |
| Observation ingest                           | `Observation` (with `core.ingestObservation`)                                                                      |
| Conversation return shapes                   | `TurnOutcome`, `RecalledCognition`                                                                                 |
| Controlled memory management                 | `MemoryManagementAPI` (facade `core.memory`)                                                                       |
| Portable memory bundle                       | `MemoryBundle`, `ImportPlan` (facade `core.portable`)                                                              |
| Graph view                                   | `MemoryGraphPayload` (facade `core.graph`)                                                                         |
| Models / recall (swappable injection points) | `OpenAICompatClient`, `OpenAICompatEmbedder`, `loadLLMPool`, `loadEmbedConfig`, `VectorRetriever`, `NullRetriever` |
| Config / version                             | `config`, `MEMOWEFT_VERSION`                                                                                       |

The authoritative export list is [`src/index.ts`](../src/index.ts).

**Portable memory bundle**: `core.portable.exportBundle({ subjectId })` exports a user's full three-layer memory as verifiable JSON; `core.portable.importBundle(bundle, { mode: 'dryRun' | 'merge' })` imports it faithfully (preserving original ids and timestamps, idempotently deduplicating by id/originId, refusing to write an invalid bundle, and optionally wrapping in a transaction to avoid pollution). The vector index is not part of the bundle; after import, a profile update (`core.updateProfile`) rebuilds the recall index. See the runnable example in [`examples/portable-bundle.ts`](../examples/portable-bundle.ts).

---

## 10. The host's minimum responsibilities

At minimum, the host must decide:

1. which user inputs to store as evidence;
2. which observation data may be ingested;
3. which evidence may go to the cloud;
4. when to trigger `updateProfile()`;
5. how to display / use recalled context;
6. how the user can view, correct, delete, and export their own memory.

MemoWeft only provides the underlying cognition capability; the final user experience is the host's job.
