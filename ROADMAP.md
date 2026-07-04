# Roadmap

MemoWeft is **library-first**. The product is the memory/cognition **Core** (`memoweft` on npm), meant to be `import`ed by any host. The bundled reference host (`apps/memoweft-host`) is a **demo**, not the product. The Core's public API follows semver — while pre-1.0, breaking changes bump the minor.

Priorities below; the further out, the more they may shift. Issues: <https://github.com/memoweft/memoweft/issues>. Detailed internal design lives in [`docs/internal/`](./docs/internal/).

## 0.2.0 — durable schema ✅ (landed on `main`, ships in 0.2.0)

The one blocking item is done. Adding a schema change now means: add a `Migration` entry in `src/store/migrations.ts` **and** update the corresponding store's `SCHEMA` const — never an ad-hoc `ALTER`.

- ✅ Schema versioning (`PRAGMA user_version`) + an ordered migration runner (`src/store/migrations.ts`).
- ✅ Each migration in its own transaction (rolls back on failure); auto pre-migration backup for schema-changing migrations; dry-run mode.
- ✅ Migration test: a `0.1.0`-shaped database (with data) opens losslessly and keeps every row (`tests/migrations.test.ts`).
- Still open: a small CLI wrapper (`migrate` / `migrate --dry-run`) and orphan-row checks — nice-to-have, not blocking.

## 0.3.x — evidence & quality

- **Minimal eval suite** (~20 conversation fixtures) asserting the discipline: conflicts exposed not overwritten, transient moods confidence-capped, recorded ≠ believed.
- A **"vs. plain memory store" behavior comparison table** in the README, backed by the eval.
- **Test coverage** (`node --experimental-test-coverage`) + a coverage badge.
- **ESLint / Prettier / editorconfig**, `SECURITY.md`, issue & PR templates — the bar for accepting outside contributions.

## Later

- **Recall quality v2** — similarity threshold, purpose / scope / contentType filters, recall explain, user negative feedback.
- **Memory-graph front-end** — the backend payload already exists (`src/graph/`).
- **Adapters** — LangChain / Vercel AI SDK / etc., so a host wires MemoWeft in a few lines.
- **More plugins** — additional collectors and experiences.

## Non-goals (for now)

- Turning the reference host into a shipped desktop product. It stays a demo; **the library is the product**. This keeps priorities on API stability, adapters, and evidence — not product UX.
