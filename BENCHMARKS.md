# Benchmarking and reproducibility

MemoWeft ships benchmark runners and synthetic regression fixtures so that changes to retrieval and memory formation can be measured under explicit conditions. This page describes what a public checkout can reproduce today. It does not make leaderboard claims.

## What is included

| Evaluation               | Data in this repository                        | Network or model required                                                           | Purpose                                                                                               |
| ------------------------ | ---------------------------------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Retrieval regression     | 36 synthetic cognitions and 65 labeled queries | No for the default deterministic hash run; an embedding endpoint only with `--real` | Detect ranking regressions across direct, paraphrase, and multi-hop queries                           |
| Consolidation discipline | 49 fully synthetic scenarios                   | No for `--selftest`; an OpenAI-compatible subject model and judge for a full run    | Exercise evidence grounding, corrections, conflicts, confidence discipline, and over-inference checks |
| Confidence sensitivity   | Deterministic generated grid                   | No                                                                                  | Show how confidence tiers and decay windows respond to parameter changes                              |
| Recall performance       | Generated local database                       | No for `--selftest`; a configured endpoint for semantic runs                        | Measure local recall latency and index-build time on the current machine                              |

The synthetic fixtures are designed for regression coverage, not for estimating real-world user behavior. Model-based results are stochastic and should be treated as point-in-time observations.

## Run the local checks

The benchmark scripts import TypeScript source directly and require Node.js 24 or newer.

```bash
# Offline retrieval regression (deterministic hash embeddings)
npm run bench:retrieval
node bench/eval-retrieval.mjs --ablation

# Offline evaluator checks: no credentials, network calls, or billable model use
node bench/eval-consolidation.mjs --selftest
node bench/longmemeval-eval.mjs --selftest
node bench/perf-recall.mjs --selftest

# Offline confidence/decay sensitivity report
node bench/sensitivity-confidence.mjs

# Model-backed consolidation evaluation (requires configured credentials; may incur cost)
npm run bench:consolidation

# Add endpoint-backed embedding arms only when intentionally requested
node bench/eval-retrieval.mjs --real --ablation
```

The offline commands above do not read model credentials or make network calls. Commands that intentionally use a model read credentials from environment variables and may incur provider charges. Persistent reports use ignored, commit-stamped paths under `bench/runs/` by default; `--out` is an explicit override.

## External datasets

MemoWeft includes runners for [LoCoMo](https://snap-research.github.io/locomo/) and [LongMemEval](https://github.com/xiaowu0162/LongMemEval). Their datasets are not redistributed here. Obtain data from the upstream project, follow its license, and point the runner at the local file:

```bash
LOCOMO_PATH=/path/to/locomo10.json node bench/locomo-eval.mjs --dry --limit 1 --qa 5
node bench/longmemeval-eval.mjs --selftest
```

External benchmark scores are not published in this repository until a non-sensitive aggregate manifest is committed with all of the following:

- dataset source, version, license, scope, exclusions, and SHA-256;
- exact MemoWeft commit and runtime versions;
- subject, embedding, answer, and judge model identifiers or revisions;
- ingestion policy, retrieval layer, top-k, prompt versions, and metric implementation;
- sample counts, failed or retried shards, and aggregate results;
- enough commands and configuration to reproduce the run without private infrastructure assumptions.

Results produced under different ingestion policies, memory layers, embeddings, top-k values, answer models, or judges are not directly comparable.

## Publication standard

A result can move from a local report into this page only when its aggregate manifest is reviewable from a clean checkout and contains no user data, model transcripts, secrets, or licensed dataset rows. Replacing a fixture, prompt, model, judge, or scoring rule starts a new result series; old and new numbers must not be presented as the same experiment.

## Known limitations

- Current repository fixtures are intentionally small and synthetic.
- Cloud-model runs can drift even with identical parameters.
- No confidence intervals or repeated-run variance are published yet.
- The external runners exercise MemoWeft under specific ingestion policies; they are not neutral cross-system leaderboards.
- Local performance depends on CPU, storage, SQLite build, corpus size, and retrieval configuration. See the [performance protocol](docs/internals/perf.md) for reporting requirements.

If you publish a MemoWeft result, include the complete run manifest and link back to the exact commit used.
