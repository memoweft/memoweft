# MemoWeft — Python port (parity kernel)

The Python line of [MemoWeft](https://github.com/memoweft/memoweft) — portable AI long-term memory that keeps **facts and guesses apart** (confidence is rule-derived, conflicts are surfaced not adjudicated). See `DECISIONS.md` **D-0042**.

**Phase 1 = the parity kernel.** This package ports the **pure-logic invariant layer** — the命脉 of the "portable memory" claim — and verifies it **bit-exact** against the TypeScript source:

| module | ports | TS source |
|---|---|---|
| `confidence` | `compute_confidence`, `derive_cred_status`, `is_transient` | `src/consolidation/confidence.ts` |
| `formed_by` | `derive_formed_by` (carrier dimension, weakest-of-set) | `src/consolidation/deriveFormedBy.ts` |
| `decay` | `decay_factor`, `half_life_of`, `effective_confidence` | `src/background/decay.ts` |
| `echoed_id` | `resolve_echoed_id` (3-tier id resolution) | `src/llm/echoedId.ts` |
| `hash_embedder` | `fnv1a32`, `tokenize`, `HashEmbedder` | `tests/retrieval/hashEmbedder.ts` |
| `config` | numeric constants (loaded from `../shared`) | `src/config.ts` |

Storage (SQLite stores), the portable bundle, and the LLM write path are later slices (Phase 1b/1c/2, D-0042).

## Single source of truth

The constants and the `{input, expected}` parity fixtures come from `../shared/` — generated from the TypeScript by `npm run shared:update` and guarded against drift on the TS side. The Python side never hand-copies them; it loads the same files and asserts its reimplementation reproduces every `expected` bit-exact.

**Three parity killers reproduced** (D-0042): JS `Math.round` half-up (→ `floor(x+0.5)`, `_math.round_half_up`); `Math.imul` 32-bit + `charCodeAt` UTF-16 code units (`_math`); constants loaded from `shared/`, never hand-copied.

## Develop

```bash
uv sync --extra dev   # create env + install deps (regex, pytest)
uv run pytest         # run the bit-exact parity suite against ../shared/parity/*.json
```

Requires the sibling `../shared/` assets (this package lives at `py/` inside the memoweft repo).
