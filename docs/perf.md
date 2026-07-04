# Performance — measured numbers, not promises

> **Honest numbers only, no thresholds.** These are real measurements on one machine, not guaranteed
> service levels. Your hardware, Node version, and store driver will move them. There is **no CI gate**
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

| Step | Time |
| --- | --- |
| `updateProfile` (total) | ≈ **462 ms** — distill 129 · consolidate 332 · attribute ~0 · index 1 |
| `recall` (avg, 20 rounds) | ≈ **0 ms** — this is the `NullRetriever` path (no embedder configured); real recall latency is dominated by your embedder's round-trip |

**Test environment:** Node `24.15.0` · `win32 / x64` · single run, throwaway in-memory DB.
_— the model is stubbed out, so these measure store + orchestration cost (not model latency); this-machine numbers, not a guarantee._
