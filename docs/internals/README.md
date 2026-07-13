# How MemoWeft is built

> **`internals/` vs `internal/`** (one letter apart, easy to confuse): **this** directory (`internals/`) is the public **"how it's built"** mechanism — architecture, boundaries, performance, the numbering map. The maintainer-only ledgers (calibration facts, runbooks, feasibility, positioning discipline) live under [`docs/internal/`](../internal/).

Engineer-facing notes on how MemoWeft is built — the mechanism behind the [concepts](../concepts/). English single source.

- [`architecture.md`](./architecture.md) — evidence → event → cognition, the read/write paths, and how the cognitive disciplines land in code.
- [`boundaries.md`](./boundaries.md) — the long-term Core / Host / Plugin responsibility boundary (kept in Chinese).
- [`perf.md`](./perf.md) — measured performance numbers, reproducible.
- [`numbering-map.md`](./numbering-map.md) — newcomer navigation index mapping every Phase / D-xxxx / AD-x / Step(S) number to a one-line summary (written in Chinese).

For the public API surface see [reference/memory-surface-contract.md](../reference/memory-surface-contract.md). Maintainer-only ledgers (calibration, runbooks, feasibility) live under [`docs/internal/`](../internal/).
