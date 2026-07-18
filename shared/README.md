# `shared/` — language-neutral cross-language assets

Generated assets that the **Python parity kernel** (`py/`) loads so both languages read **one source of truth** instead of hand-copying values that can drift.

**The TypeScript source is authoritative.** These files are _generated_ from it by [`scripts/gen-shared-assets.mjs`](../scripts/gen-shared-assets.mjs), which imports the real TS functions/constants. Do not hand-edit them.

| File                        | What                                                                                                                                      | Source of truth                            |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| `config-constants.json`     | Numeric constants the pure-logic layer reads (`baseByFormedBy`, thresholds, half-lives, `transientCap`, `CARRIER_RANK`, `MIN_ID_PREFIX`…) | `src/config.ts` (+ a couple noted in-code) |
| `prompts.json`              | The 8 governed prompts `{id, version, text:{zh,en}}` verbatim                                                                             | `src/prompts/registry.ts`                  |
| `parity/confidence.json`    | `computeConfidence` input→output over all `formedBy×contentType×support×contradict`                                                       | `src/consolidation/confidence.ts`          |
| `parity/cred-status.json`   | `deriveCredStatus` at threshold boundaries ±1                                                                                             | `src/consolidation/confidence.ts`          |
| `parity/formed-by.json`     | `deriveFormedBy` — all `deriveOne` branches + weakest-of-set                                                                              | `src/consolidation/deriveFormedBy.ts`      |
| `parity/decay.json`         | `decayFactor` (raw double) + `effectiveConfidence` (int)                                                                                  | `src/background/decay.ts`                  |
| `parity/hash-embedder.json` | `fnv1a32` (uint32), `tokenize`, `HashEmbedder.embed` vectors                                                                              | `tests/retrieval/hashEmbedder.ts`          |
| `parity/echoed-id.json`     | `resolveEchoedId` — 3-tier resolution + guardrails                                                                                        | `src/llm/echoedId.ts`                      |

## How to use (Python side)

The `parity/*.json` files are **`{input, expected}` fixtures**: the Python port runs its own reimplementation on each `input` and asserts it produces `expected` — bit-exact cross-language verification (porting _is_ the test). `config-constants.json` and `prompts.json` are loaded directly as the single source.

**Three cross-language details to reproduce exactly:**

1. `computeConfidence` / `effectiveConfidence` use JS `Math.round` (**half-up**), not Python's banker's `round()` → use `floor(x + 0.5)`.
2. `fnv1a32` uses `Math.imul` (32-bit signed) + `>>> 0` (unsigned 32-bit) → mask with `& 0xFFFFFFFF` / numpy int32.
3. Constants must come from `config-constants.json`, never hand-copied.

## Regenerate / verify

```bash
npm run shared:update   # regenerate all files from TS
npm run shared:check    # verify committed == freshly generated (drift → exit 1)
```

The drift guard also runs in `npm test` via [`tests/shared/shared-assets.test.ts`](../tests/shared/shared-assets.test.ts), which additionally checks that `prompts.json`'s per-language sha256 matches `tests/prompts/prompt-hashes.snapshot` (proving the shared prompts are byte-identical to the governed ones).
