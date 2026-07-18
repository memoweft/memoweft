# Typed decay: short-lived states fade sooner

**English** | [简体中文](./decay.zh-CN.md)

A user's stress last week should not colour every reply this week. MemoWeft decays confidence **by type**: emotional states have a short default half-life, while `fact` and `preference` have no default half-life. Hosts can configure a different policy.

## See it (no API key)

Decay is a pure, read-time calculation. This runs with no model and no network.

```ts
import { effectiveConfidence } from 'memoweft';

const anchoredAt = '2026-01-01T00:00:00.000Z'; // when this cognition was last confirmed
const oneWeekLater = new Date('2026-01-08T00:00:00.000Z');

// An emotional 'state' has a default 1.5-day half-life. A week on, it has largely faded.
const mood = { confidence: 300, contentType: 'state', updatedAt: anchoredAt } as const;
console.log(effectiveConfidence(mood, oneWeekLater)); // → 12  (below the recall threshold of 80)

// Under the default configuration, a 'fact' has no configured half-life.
const bicycle = { confidence: 700, contentType: 'fact', updatedAt: anchoredAt } as const;
console.log(effectiveConfidence(bicycle, oneWeekLater)); // → 700  (unchanged)
```

The faded mood drops below `retrieval.minEffectiveConfidence` (default 80), so recall does not inject it. The fact remains unchanged under this default configuration.

## How it works

Effective confidence is the stored strength scaled by age since last confirmation:

```
effective = confidence × 2^(-age / halfLifeDays)
```

- **Read-time, never persisted.** The stored `confidence` keeps its meaning — a rule-based score derived from evidence (see [confidence is computed, not self-reported](./confidence.md)). Decay is applied only when MemoWeft reads, so nothing rewrites the database as the clock ticks.
- **Half-life comes from the type**, set in `config.background.halfLifeDays`. Defaults: `state` 1.5 days, `hypothesis` 2, `trend` 7, `goal`/`project` 14, `trait` 60. `fact` and `preference` are omitted from the default map, so their half-life is 0 under that configuration. A host may configure them differently.
- **Age counts from `updatedAt`**, the last time evidence re-confirmed the cognition. A state that keeps recurring stays fresh; one nobody mentions again fades out.

## See the full effect

Act 4 of the demo confirms a low mood, fast-forwards time, and recalls again — the mood has decayed below the recall gate while default non-decaying types remain eligible.

```bash
npm run demo -- --fast-forward 30d
```

Time travel is deterministic because the clock is injectable via `CreateCoreOptions.clock`. Same input, same day, same output — no waiting for real weeks to pass.

<!-- snippet:skip (needs the full write path; run the demo above instead) -->

```ts
let nowMs = Date.parse('2026-01-14T09:00:00.000Z');
const core = createMemoWeftCore({ dbPath: ':memory:', clock: () => new Date(nowMs) });

await core.ingestUserMessage({
  subjectId: 'alice',
  content: 'I have been really stressed this week',
});
await core.updateProfile({ subjectId: 'alice' });

nowMs += 30 * 24 * 3600 * 1000; // fast-forward 30 days
const hits = await core.recall({ subjectId: 'alice', query: 'how are they doing' });
// the stress state has decayed out; facts still surface
```

## Next

- **[Getting started](../getting-started.md)** — install and store your first evidence.
- **[Run the demo](../demo-script.md)** — all four differentiators in 90 seconds.
- **[`examples/demo.ts`](../../examples/demo.ts)** — the runnable source behind Act 4.
