# Benchmarks

MemoWeft's quality numbers come from a model-backed evaluation, not from fixed
assertions. This page records what was measured, how, and — just as importantly — what
these numbers do **not** cover.

## What was measured

|               |                                                                                         |
| ------------- | --------------------------------------------------------------------------------------- |
| Corpus        | `tests/consolidation-corpus/corpus.json`, version `2026-07-28-b1-120r2` (120 scenarios) |
| Subject model | `gpt-4o-2024-11-20`                                                                     |
| Judge         | `gpt-5.6-luna`, temperature 0, three votes per point, majority                          |
| Rounds        | 1                                                                                       |
| Commit        | `b2d5ac1`                                                                               |
| Judge calls   | 1011 · execution failures: **0**                                                        |

## Results

| metric                         | estimate            | 95% CI             | estimator                       |
| ------------------------------ | ------------------- | ------------------ | ------------------------------- |
| Structural assertion pass rate | **623/635 = 98.1%** | **[97.1%, 99.1%]** | cluster bootstrap (by scenario) |
| Scenario fully passed          | 108/120 = 90.0%     | [83.3%, 94.2%]     | Wilson                          |
| Average gistRecall             | 0.836               | [0.750, 0.914]     | bootstrap                       |
| Average overInferRate          | 0.018               | [0.004, 0.036]     | bootstrap                       |

The structural assertion rate is the tightest figure here — 635 checks put it in a ±1pp
band. The others are reported with their intervals and should be read as "this run cannot
resolve finer than this", not as precise values; see the limits below.

**On round-to-round movement.** An earlier run on the same corpus content scored the
structural rate at 98.6% against 98.1% here, and an earlier baseline on the 60-scenario
subset moved 97.17% → 98.11% → 97.5% across runs. Same-condition variation on this metric
is roughly a percentage point, comfortably inside the interval. Differences smaller than
that are noise, not movement, which is the whole reason the intervals are published
alongside the point estimates.

Three estimators appear because the metrics have different shapes, and using one for
another would produce a confident-looking but invalid interval:

- **Structural assertion pass rate** uses a **cluster bootstrap resampling whole
  scenarios**. The 4–8 assertions a scenario emits are correlated observations of a single
  model output, and an execution error collapses a scenario into one failed check. Treating
  them as independent trials would claim far more independent information than the run
  contains and report an artificially narrow interval.
- **Scenario pass rate** uses **Wilson**, because there each scenario contributes exactly
  one independent observation. Wilson rather than Wald, since these rates sit close to 1
  where Wald produces out-of-range bounds.
- **gistRecall / overInferRate** are per-scenario scores that are then averaged — not
  binomial proportions — so they use a **percentile bootstrap** (10,000 resamples, fixed
  seed, therefore reproducible).

## What these numbers do not cover

Read this section before quoting anything above.

- **One round.** A single replay carries no evidence about stability. The same
  configuration has previously produced swings between rounds. Treat the point estimates as
  one observation, and compare intervals rather than point estimates.
- **Not comparable to pre-1.0 figures.** The corpus grew from 60 to 120 scenarios for this
  release. A changed corpus is a **changed measuring instrument**, not merely a changed
  score, so aggregate numbers cannot be compared across `corpusVersion` values.
- **The judge is not ground truth.** Semantic scoring is an LLM judgement with its own
  error floor, and it is systematically weakest at telling a genuine contradiction from a
  position that merely evolved in wording. Rates derived from judge verdicts carry that
  floor.
- **Scale-triggered behaviour is out of scope.** Every corpus scenario involves a handful
  of pieces of evidence. Behaviour that only appears once a profile grows large — cluster
  sizes, anchor accumulation, shortlist truncation — is documented as known residual in the
  A5 decision record and is **not** measured here. Do not read a good score on small
  scenarios as evidence about behaviour at scale.
- **Per-scenario metrics remain wide.** gistRecall rests on 76 scenarios and the scenario
  pass rate on 120, against 635 checks behind the structural rate. Their intervals — 0.16
  and 10.9 percentage points wide — are reported honestly; where an interval is wide, the
  correct reading is "this run cannot distinguish", not "the score is the midpoint".
  Narrowing gistRecall to ±0.05 would take roughly 280 gist-bearing scenarios against the
  current 76, and that cost was judged out of proportion to the benefit for this release.
- **Per-discipline gistRecall varies**, and the spread is worth reading with the caveat
  above about small per-discipline samples rather than as a ranking of capability.
- **Per-discipline figures are noise.** With roughly 15 scenarios per discipline, those
  intervals are wide enough that a difference between disciplines should not be
  interpreted.

## Reproducing

```bash
node bench/eval-consolidation.mjs --subject-env GPT4O --judge-env LUNA
```

Model-backed and billable: a full run makes roughly a thousand judge calls plus the
subject-model calls. Results vary between runs; the report records the corpus version,
prompt versions, judge model, and scoring version so a comparison can check it is
comparing like with like.
