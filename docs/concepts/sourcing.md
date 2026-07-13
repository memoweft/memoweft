# Three-layer sourcing: evidence → event → cognition

**English** | [简体中文](./sourcing.zh-CN.md)

Every judgment MemoWeft holds can trace back to the raw words that produced it. It records not just *what* it knows, but *where that came from*.

## See the source get recorded (no API key)

This runs with no model and no network. Copy it into a file and run it.

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// Two pieces of evidence from two different sources. Neither call touches a model.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I switched to a standing desk.' });
await core.ingestToolResult({ subjectId: 'alice', content: 'calendar: 3 gym sessions logged this week' });

// Read the raw evidence back. Each row remembers how it came in.
for (const e of core.memory.listEvidence({ subjectId: 'alice' })) {
  console.log(e.sourceKind, '·', e.rawContent);
}
// → spoken · I switched to a standing desk.
// → tool   · calendar: 3 gym sessions logged this week

core.close();
```

Every piece of evidence carries a `sourceKind`. A user's own words (`spoken`) and a tool's output (`tool`) are different kinds of fact, and MemoWeft never loses that difference. The four kinds are `spoken`, `observed`, `inferred`, and `tool`. Three arrive through an ingest call (`spoken` / `observed` / `tool`); `inferred` is produced internally by the write path's attribution step — never written back from a model's self-report (see [no self-evidence](./no-self-evidence.md)).

## The three layers

Writes flow up through three layers. Each layer links back to the one below, so trust always has a floor.

- **Evidence** — the raw material. What was said, observed, inferred, or returned by a tool. It carries `sourceKind`, timestamps, and authorization flags. It never holds a judgment. See [`src/evidence/model.ts`](../../src/evidence/model.ts).
- **Event** — a contextualized summary of a slice of conversation, linked to the evidence it covers. Judgments are built from events (which carry context); the trail still lands on the original words.
- **Cognition** — a judgment about the user: a fact, preference, goal, state, and so on. Each cognition keeps a `sources` list of the evidence that **supports** or **contradicts** it.

The chain only points one way for trust: a cognition is only as trustworthy as the evidence beneath it. Remove the evidence and the judgment loses its ground.

## Follow the chain to its source (needs a chat model)

Building cognitions from evidence needs a chat model. Once built, each cognition exposes its `sources` — an `EvidenceLink[]` of `{ evidenceId, relation }`.

<!-- snippet:skip (needs a live model) -->
```ts
await core.updateProfile({ subjectId: 'alice' }); // distill → consolidate → attribute

for (const c of core.memory.listCognitions({ subjectId: 'alice' })) {
  console.log(c.content, '←', c.sources.map((s) => `${s.relation}:${s.evidenceId}`));
}
// → The user uses a standing desk ← [ 'support:ev_...' ]
```

Watch a fact form from raw evidence in the four-act demo: [`examples/demo.ts`](../../examples/demo.ts) (`npm run demo`).

## Next

- **Next in this series → [No self-evidence](./no-self-evidence.md)**
- **[Getting started](../getting-started.md)** — store one piece of evidence and read it back in five minutes.
- **[Run the demo](../demo-script.md)** — a fact forms, gets corrected, and hits a conflict — sources intact throughout.
- **[API reference](../reference/memory-surface-contract.md)** — exact shapes for `listEvidence`, `listCognitions`, and the source links.
