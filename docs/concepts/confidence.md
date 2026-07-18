# Confidence is computed by rule, not reported by the LLM

**English** | [简体中文](./confidence.zh-CN.md)

Every cognition MemoWeft keeps carries a `confidence` (0–1000) and a `credStatus`. `confidence` is a deterministic, configurable heuristic score — not a calibrated probability or a statement of truth. This page shows where the score comes from and why the model never gets to set it.

## See it (no API key)

This runs with no model and no network. `computeConfidence` and `deriveCredStatus` are pure functions.

```ts
import { createMemoWeftCore, computeConfidence, deriveCredStatus } from 'memoweft';

// Storing what the user said needs no model — it just records raw evidence, unscored.
const core = createMemoWeftCore({ dbPath: ':memory:' });
await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });
console.log('evidence stored:', core.memory.listEvidence({ subjectId: 'alice' }).length);
core.close();

// Confidence is a rule: same inputs -> same score, every run.
const stated = computeConfidence({
  contentType: 'fact',
  formedBy: 'stated',
  supportCount: 1,
  contradictCount: 0,
});
const affirmed = computeConfidence({
  contentType: 'fact',
  formedBy: 'confirmed',
  supportCount: 1,
  contradictCount: 0,
});
const guess = computeConfidence({
  contentType: 'fact',
  formedBy: 'inferred',
  supportCount: 1,
  contradictCount: 0,
});
const nagged = computeConfidence({
  contentType: 'fact',
  formedBy: 'inferred',
  supportCount: 20,
  contradictCount: 0,
});

console.log('stated fact      ', stated, deriveCredStatus(stated, 0, 'fact')); // 600 limited
console.log('assistant-led yes', affirmed, deriveCredStatus(affirmed, 0, 'fact')); // 280 candidate
console.log('inferred guess   ', guess, deriveCredStatus(guess, 0, 'fact')); // 200 candidate
console.log('guess x20 support', nagged, deriveCredStatus(nagged, 0, 'fact')); // 400 low
console.log('with 1 contradict', deriveCredStatus(stated, 1, 'fact')); // conflicted
```

## How the score is built

The formula is `base + support − contradict`, clamped to 50–1000:

- **Base by provenance classification** — `stated` 600, `ruled` 450, `observed` 350, `confirmed` 280, `inferred` 200. `confirmed` means the user affirmed an assistant-proposed proposition; an inference starts lowest.
- **Support** — each extra supporting piece of evidence adds 40, up to 5 pieces (+200 max).
- **Contradiction** — each contradicting piece subtracts 120.
- **Transient cap** — `state` cognitions (moods, "tired today") cap at 300, so a repeated feeling never hardens into a stable trait.

## Why this matters

Ask an LLM how sure it is and it can invent a number — high for a hallucination, low for a well-supported claim. MemoWeft ignores that. The score is a function of **where the claim came from and how the evidence stacks up**, so it is reproducible and auditable: rerun the inputs, get the same number. It is a ranking and gating heuristic, not a calibrated estimate of likelihood.

This also means a guess cannot become a direct user statement merely by accumulating support. Above, piling 20 supporting observations onto an `inferred` claim reaches 400 (`low`) and stops — an inference never accumulates its way to `stable`. Only new direct user evidence, classified as `stated`, earns the high base. See how provenance is decided in [Concepts](./).

## Credibility status

`deriveCredStatus` turns a score into a plain label. Any contradicting evidence overrides everything else:

| credStatus   | condition                                 |
| ------------ | ----------------------------------------- |
| `conflicted` | any contradicting evidence (see conflict) |
| `stable`     | confidence ≥ 750                          |
| `limited`    | confidence ≥ 500                          |
| `low`        | confidence ≥ 300                          |
| `candidate`  | confidence < 300                          |

`state` (transient) cognitions never rank above `low`, whatever the score.

## See it become a stored fact (needs a model)

Turning evidence into a scored cognition needs a chat model. Act 1 of the demo ingests "I own a red bicycle", distills it, and shows it land as a `fact` scored by rule at 600 (`limited`) — no key, no network, deterministic offline stub.

<!-- snippet:skip (needs a live model) -->

```ts
await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle' });
await core.updateProfile({ subjectId: 'alice' }); // distill -> consolidate -> score by rule
for (const c of core.memory.listCognitions({ subjectId: 'alice' })) {
  console.log(c.content, c.confidence, c.credStatus); // owns a red bicycle · 600 · limited
}
```

Run it: `npm run demo -- --act 1` ([demo walkthrough](../demo-script.md)).

## Related

- **Next in this series → [Correct vs conflict](./correct-conflict.md)**
- [Getting started](../getting-started.md) — store evidence and read it back in five minutes.
- [Concepts](./) — formation mode, conflict exposure, and time decay, one screen each.
- [Run the demo](../demo-script.md) — the four differentiators in 90 seconds.
