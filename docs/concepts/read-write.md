# Read/write split: store first, digest later

**English** | [简体中文](./read-write.zh-CN.md)

MemoWeft separates the ingestion/recall path from profile updates. Storing a message is a synchronous write; turning stored evidence into a profile is the heavier step. Core exposes the one-shot update, while the host chooses and implements its schedule. A recall failure is handled as an empty recall for that turn.

## Storing and profile updates are separate steps (no API key)

This runs with no model and no network.

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// Storing is one cheap write. No model call, no digest.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I ship on Fridays.' });

// The evidence is there immediately...
console.log('evidence:', core.memory.listEvidence({ subjectId: 'alice' }).length); // → 1
// ...but no cognition yet. Distilling evidence into a profile is a separate, heavier step.
console.log('cognitions:', core.memory.listCognitions({ subjectId: 'alice' }).length); // → 0

core.close();
```

After ingestion resolves, the evidence write is complete. A judgment (cognition) only appears after `updateProfile` runs the digest — and the host chooses when that happens.

## Store first, then reply

`handleConversationTurn` runs a fixed order: **store** the user's message as evidence, **recall** the relevant profile, then **reply**. The store happens before recall, so the message is safe even if the model call fails.

If recall throws or times out, MemoWeft treats it as "no memory this turn" and proceeds with the turn. On failure, `TurnOutcome.error` is set, but the message is already stored; do not re-ingest it.

<!-- snippet:skip (needs a live model) -->

```ts
const turn = await core.handleConversationTurn({
  subjectId: 'alice',
  message: 'When should I release?',
});
if (turn.error) console.log('reply degraded, but the message was stored:', turn.error);
console.log(turn.reply);
```

## Digest on your own schedule

`updateProfile` is the heavy path: distill → consolidate → attribute → re-index. Hosts commonly batch it after N turns, on idle, or nightly rather than running it for every message. `config.profileUpdate` supplies default policy values (`batchSize: 12`, `idleMinutes: 30`); it does not create timers, queues, or asynchronous workers. The host owns those mechanisms and any per-subject serialization.

This separation lets hosts choose their own latency, resource, and consistency trade-offs for profile updates.

## Next

- **Next in this series → [Sourcing](./sourcing.md)**
- **[Getting started](../getting-started.md)** — store one message and read it back in five minutes.
- **[Concepts](./)** — the other five disciplines, one screen each.
- **[API reference](../reference/memory-surface-contract.md)** — `TurnOutcome`, `updateProfile`, and `config.profileUpdate` shapes.
