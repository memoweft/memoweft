# Performance — measured numbers, not promises

> **Honest numbers only, no thresholds.** These are real measurements on one machine, not service
> levels. Your hardware, Node version, and store driver will move them. There is **no CI gate**
> on perf (benchmarks are slow and jittery) — this page just answers "how much can it take?" with a
> number you can reproduce.

## How to reproduce

The benchmark loads **10,000 evidence rows** into a throwaway in-memory database, then measures:

- **`updateProfile`** — one full write-path pass (distill → consolidate → attribute → rebuild recall
  index), read straight off the built-in `result.timings.totalMs` (no home-grown timer).
- **`recall`** — latency through the public `core.recall(query)` entry, averaged over several rounds.

To keep the numbers reproducible and offline, the script injects an **offline stub LLM** (no network,
deterministic), so what you measure is the **real store read/write + orchestration cost** with the model
round-trip removed as a noise source.

```bash
npm run build   # required first — the script imports from dist/, not src
npm run bench
```

Optional knobs: `BENCH_N=1000 npm run bench` (fewer rows), `BENCH_RECALL_ROUNDS=50 npm run bench`.

## Results

**10,000 evidence rows** (loaded in ≈ 543 ms, ~18k rows/sec):

| Step                      | Time                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `updateProfile` (total)   | ≈ **462 ms** — distill 129 · consolidate 332 · attribute ~0 · index 1                                                                                                                                                                                                                                                                                                                                                                                                 |
| `recall` (avg, 20 rounds) | ≈ **0 ms** — ⚠ **`NullRetriever` path: storage/entry-chain overhead only**. This bulk script explicitly uses an empty retriever, so recall returns nothing and does no vector scan. It is **not** semantic- or keyword-recall latency. Public Core construction without an embedder uses `KeywordRetriever` (local FTS5) when available. For semantic recall with an embedder, see [Recall with a real embedder (bge-m3)](#recall-with-a-real-embedder-bge-m3) below. |

**Test environment:** Node `24.15.0` · `win32 / x64` · single run, throwaway in-memory DB.
_— the model is stubbed out, so these measure store + orchestration cost (not model latency); these are this-machine numbers._

## Recall with a real embedder (bge-m3)

The `≈ 0 ms` recall in the table above comes from an explicitly injected **`NullRetriever`** in the bulk script: it returns nothing and scans no vectors, so it only measures the recall entry chain — **not** how long real retrieval takes. This differs from public Core construction, which selects FTS5 keyword recall when no embedder is configured and FTS5 is available. To measure semantic recall, a separate bench (`bench/perf-recall.mjs`) wires a `VectorRetriever` backed by **bge-m3**, seeds N cognitions, indexes their vectors, then times `core.recall(query)` end-to-end; each round re-embeds the query over the network.

```bash
node bench/perf-recall.mjs --selftest    # offline self-check (HashEmbedder, no network, asserts the recall chain works)
node bench/perf-recall.mjs               # real: reads MEMOWEFT_EMBED_* / DLA_EMBED_* (bge-m3 endpoint)
PERF_RECALL_N=1000 PERF_RECALL_ROUNDS=30 node bench/perf-recall.mjs
```

_(No `npm run build` first — this script imports from `src/` directly via Node ≥24 type-stripping, like `bench/locomo-eval.mjs`.)_

| Corpus                                               | `core.recall` P50 | P95       | min     | conditions                            |
| ---------------------------------------------------- | ----------------- | --------- | ------- | ------------------------------------- |
| 500 cognitions                                       | ≈ **36.8 ms**     | ≈ 42.6 ms | 33.2 ms | 30 rounds · topK 5                    |
| 1,000 cognitions                                     | ≈ **59.8 ms**     | ≈ 64.0 ms | 54.8 ms | 30 rounds · topK 5                    |
| _Explicit `NullRetriever`, same `core.recall` entry_ | ≈ **0.0 ms**      | 0.0 ms    | —       | storage-layer baseline (empty recall) |

**Where the time goes — two real components, don't conflate them:**

1. **query embed round-trip** — the observed minimum was ≈ **33 ms** at 500 rows: one bge-m3 embedding call to the local endpoint. Your embedder host (CPU vs GPU, local vs cloud) strongly affects this component.
2. **in-JS cosine scan** — `VectorRetriever.search` reads _every_ stored vector, `JSON.parse`s each 1024-float array, and scores it in JS. This is **O(N)**: +500 cognitions added ≈ +23 ms (500 → 1,000). So recall is embed-round-trip-bound for small corpora and increasingly scan-bound past ~1k rows — consistent with the retriever's "fine for a few thousand per person" design note (it deliberately skips native `sqlite-vec`).

Indexing the corpus (a **one-time** cost, _not_ part of recall latency) took ≈ 6.1 s for 500 / ≈ 11.4 s for 1,000 cognitions — a single batched bge-m3 pass, chunked 32 texts/request.

**Reference measurement conditions (single environment; do not compare rows as if the hardware differed):**

- Hardware: **RTX 3090**; bge-m3 served locally by **llama.cpp** through an OpenAI-compatible loopback endpoint (1024-dimensional vectors).
- The `NullRetriever` row runs the same `core.recall` entry point; it is ≈ 0 ms for this setup because the injected retriever returns an empty result and performs no vector scan. It is not comparable to the bge-m3 rows as recall latency.
- Single run, throwaway temporary database (deleted on exit); treat these as reference measurements, not service-level targets.
