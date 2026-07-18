# Built-in ingestion does not persist assistant replies as evidence

**English** | [简体中文](./no-self-evidence.zh-CN.md)

Core's built-in ingestion records what the user said, what the host observed, and what a tool returned. Those entry points do not persist the assistant's own reply or model-proposed tool-call arguments as evidence. This boundary prevents the built-in path from feeding the model's own guesses back as source records.

## Built-in ingestion has no assistant-evidence method (no API key)

This runs with no model and no network. Copy it into a file and run it.

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// The user speaks — this records evidence.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I love strong coffee.' });

// There is no ingestAssistantMessage among Core's built-in ingestion methods.
// What's recorded is exactly what the user said, tagged with who said it.
const evidence = core.memory.listEvidence({ subjectId: 'alice' });
console.log(evidence.length, evidence[0].sourceKind, '·', evidence[0].rawContent);
// → 1 spoken · I love strong coffee.

core.close();
```

The facade offers `ingestUserMessage`, `ingestObservation`, and `ingestToolResult` — and none of those methods turns an assistant reply into evidence. `recordAssistantReply` keeps a reply only in the in-memory conversation context used by a later user turn.

## The rule

For the built-in ingestion methods, evidence comes from outside the model:

- **`spoken`** — the user's own words (`ingestUserMessage`).
- **`observed`** — a behavior the host observed (`ingestObservation`).
- **`tool`** — the payload a tool returned (`ingestToolResult`).

The model's own output — its chat reply, and the tool-call arguments it proposes — is not ingested by these built-in paths. This invariant is covered by Core and adapter tests. Hosts that bypass these entry points or persist arbitrary content remain responsible for preserving the same boundary. The Vercel AI SDK adapter follows the built-in rule: `persistOnEnd` stores the user's verbatim turn and `tool`-role results, and does not read assistant messages.

## Why it matters: no self-reinforcing guesses

A cognition's support chain cites evidence ids — never another cognition, never the model's earlier words. So the assistant cannot cite its own guess to raise that guess's confidence through the built-in path. Without this boundary, a hypothesis the model voiced last turn could return as "a thing the user said" and snowball into a misleading claim. Confidence stays anchored to source records outside the model. (Confidence itself is a rule-based heuristic, not self-reported or a probability — see [Confidence by rule](./confidence.md).)

## In a full turn (needs a chat model)

<!-- snippet:skip (needs a live model) -->

```ts
const before = core.memory.listEvidence({ subjectId: 'alice' }).length;

const turn = await core.handleConversationTurn({ subjectId: 'alice', message: 'Any snack ideas?' });
console.log(turn.reply); // the assistant answers...

const after = core.memory.listEvidence({ subjectId: 'alice' });
console.log(after.length - before); // → 1 — only the user's new message; the reply is not stored
```

The reply lives in `turn.reply`, not in the evidence store used by this built-in path. See the effect end to end in the four-act demo: [`examples/demo.ts`](../../examples/demo.ts) (`npm run demo`).

## See also

- **Next in this series → [Confidence by rule](./confidence.md)**
- [Getting started](../getting-started.md) — store one message and read it back.
- [Memory Surface Contract](../reference/memory-surface-contract.md) — `SourceKind`, the ingest methods, how confidence is computed.
- [Demo script](../demo-script.md) — the four differentiators in 90 seconds.
