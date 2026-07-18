# Corrections leave a trail; conflicts stay visible

**English** | [简体中文](./correct-conflict.zh-CN.md)

MemoWeft never silently overwrites a judgment. When a user corrects themselves, the old belief is kept and marked stale. When new evidence contradicts a belief without correcting it, both are kept and the clash is flagged. MemoWeft exposes conflicts — it does not pick a winner.

## Set up two turns (no API key)

A claim, then a correction. Both are stored as raw evidence with no model call.

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });
await core.ingestUserMessage({
  subjectId: 'alice',
  content: "Actually it isn't mine — my sister owns the red bicycle.",
});

console.log(core.memory.listEvidence({ subjectId: 'alice' }).length); // → 2
console.log(core.memory.listCognitions({ subjectId: 'alice' }).length); // → 0

core.close();
```

Two turns are stored, but no cognition exists yet. `correct` and `conflict` both happen inside `updateProfile`, which needs a chat model. To see the full effect end to end, run [`npm run demo`](../demo-script.md) (acts 2 and 3) — no key, deterministic.

## Correct: replace, but keep the old

An explicit correction sets the old cognition's `invalidAt` to now and writes the corrected content as a **new** cognition. The old row is not deleted — it stays with its provenance chain, so history stays traceable.

<!-- snippet:skip (needs a live model) -->

```ts
await core.updateProfile({ subjectId: 'alice' }); // distill → consolidate → attribute

for (const c of core.memory.listCognitions({ subjectId: 'alice' })) {
  console.log(c.content, c.invalidAt ? '(invalidated, kept)' : '(active)');
}
// → The user owns a red bicycle         (invalidated, kept)
// → The user's sister owns the red ...  (active)
```

`recall` skips invalidated cognitions, so the stale belief no longer reaches a reply — but an audit or graph view can still show what changed and when. This is act 2 of the demo.

## Conflict: keep both, flag the clash

When new evidence contradicts a belief but is **not** an explicit correction — a stated preference for americano versus repeatedly ordering milk tea — `consolidate` links the contradicting evidence with `relation: 'contradict'` and sets the belief's `credStatus` to `'conflicted'`. Both cognitions stay.

<!-- snippet:skip (needs a live model) -->

```ts
for (const c of core.memory.listCognitions({ subjectId: 'alice' })) {
  if (c.credStatus === 'conflicted') console.log('CONFLICT:', c.content);
}
// → CONFLICT: The user likes americano
```

MemoWeft does not treat either side as verified truth. A behaviour that contradicts a stated preference might be a one-off, a change of mind, or noise — deciding for the user would mean guessing. So MemoWeft surfaces the tension and lets the host, or the user, resolve it. This is act 3 of the demo.

## Why this matters

A memory that overwrites or auto-resolves loses the distinction between source records and system judgments. Keeping invalidated cognitions and open conflicts on the record preserves that provenance.

## Next

- **Next in this series → [Typed decay](./decay.md)**
- Run it: [`npm run demo`](../demo-script.md) — acts 2 (correct) and 3 (conflict), no key.
- Field shapes: `invalidAt`, `credStatus: 'conflicted'` in the [memory surface contract](../reference/memory-surface-contract.md).
- The write path that produces these: [`architecture.md`](../internals/architecture.md) (distill → consolidate → attribute).
