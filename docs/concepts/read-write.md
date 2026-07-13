# Read/write split: store first, digest later

**English** | [简体中文](./read-write.zh-CN.md)

MemoWeft separates the fast path from the slow path. Storing a message is cheap and synchronous. Turning stored evidence into a profile is the expensive step, and it runs on its own schedule. A recall failure never blocks a reply.

## Storing is instant; the profile is a separate step (no API key)

This runs with no model and no network.

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// Storing is one cheap write. No model call, no digest.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I ship on Fridays.' });

// The evidence is there immediately...
console.log('evidence:', core.memory.listEvidence({ subjectId: 'alice' }).length);   // → 1
// ...but no cognition yet. Distilling evidence into a profile is a separate, heavier step.
console.log('cognitions:', core.memory.listCognitions({ subjectId: 'alice' }).length); // → 0

core.close();
```

Evidence lands the moment you ingest. A judgment (cognition) only appears after `updateProfile` runs the digest — and you choose when that happens.

## Store first, then reply

`handleConversationTurn` runs a fixed order: **store** the user's message as evidence, **recall** the relevant profile, then **reply**. The store happens before recall, so the message is safe even if the model call fails.

If recall throws or times out, MemoWeft treats it as "no memory this turn" and replies anyway — a memory hiccup never blocks the conversation. On failure, `TurnOutcome.error` is set, but the message is already stored; do not re-ingest it.

<!-- snippet:skip (needs a live model) -->
```ts
const turn = await core.handleConversationTurn({ subjectId: 'alice', message: 'When should I release?' });
if (turn.error) console.log('reply degraded, but the message was stored:', turn.error);
console.log(turn.reply);
```

## Digest on your own schedule

`updateProfile` is the heavy path: distill → consolidate → attribute → re-index. Run it **batched** — after N turns, on idle, or nightly — not on every message. The batch triggers live in `config.profileUpdate` (default: every 5 turns, or 30 minutes idle).

Keeping reads light and writes batched is why a chat turn stays responsive while the profile still grows in the background.

## Next

- **Next in this series → [Sourcing](./sourcing.md)**
- **[Getting started](../getting-started.md)** — store one message and read it back in five minutes.
- **[Concepts](./)** — the other five disciplines, one screen each.
- **[API reference](../reference/memory-surface-contract.md)** — `TurnOutcome`, `updateProfile`, and `config.profileUpdate` shapes.
