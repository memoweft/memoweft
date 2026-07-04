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

<!-- 数字占位符：总控跑 `npm run build && npm run bench` 后，用打印出的真实数字替换下面的 __ -->

**10,000 evidence rows:**

| Step | Time |
| --- | --- |
| `updateProfile` (total) | ≈ __ ms  _(run `npm run bench` to fill)_ |
| `recall` (avg) | ≈ __ ms  _(run `npm run bench` to fill)_ |

**Test environment:** Node `__` · `__` (OS/arch) · `__` (CPU / RAM)
_— fill from the `[bench] ── 填文档用 ──` line the script prints; these are this-machine numbers, not a guarantee._
