# Changelog

All notable changes to MemoWeft are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> This file is the user-facing summary of notable changes; the full commit history has the fine detail.
> While the API is pre-1.0, minor versions may include breaking changes. Stability tiers and the breaking-change policy are documented in [`docs/memory-surface-contract.md`](docs/memory-surface-contract.md).

## [Unreleased]

## [0.4.0] — 2026-07-05

English-first defaults with a zero-dependency bilingual layer, model-compatibility knobs (configurable temperature + reasoning-model response handling), and the Memory Surface Contract v1.

### Added

- **Bilingual layer (`config.language`)** — every prompt sent to the model and every host/user-facing message (errors, fallbacks, template questions) now resolves to English or Chinese through a zero-dependency constant table. Set `config.language` at runtime, or `MEMOWEFT_LANG=zh`; a new `type Lang` is exported. The cognitive-discipline instructions were translated with equivalent meaning (conflict exposure, no self-corroboration, hypothesis capping), with the offline eval suite kept green as the guard.
- **Configurable generation temperature** — `LLMConfig.temperature`, read from `MEMOWEFT_LLM_TEMPERATURE` / `MEMOWEFT_WRITE_LLM_TEMPERATURE` so chat and write paths can differ. Unset keeps the previous default of `0.3` (no behaviour change); `0` is honoured. Temperature only enters the generation request — it never flows into MemoWeft's self-computed confidence.
- **Reasoning-model response compatibility** — the client strips paired `<think>…</think>` segments from responses (an unclosed tag is left intact so a real answer is never dropped), and `extractJsonObject` now uses a brace-balanced scan instead of a greedy `lastIndexOf`, so a reasoning model's thinking (with stray braces) no longer poisons write-path JSON parsing.
- **Memory Surface Contract v1** — the public surface (`import 'memoweft'`) now carries a documented stability contract instead of unlabelled exports. New [`docs/memory-surface-contract.md`](docs/memory-surface-contract.md) is the single source of truth for hosts: it lists every facade method and data shape with a `stable` / `experimental` / `internal` tier, the breaking-change policy, and the implicit contracts hosts most often trip on. Each export group in `src/index.ts` carries a matching `// [stable]` / `// [experimental]` / `// [internal]` line (grep-verifiable).
- **Two more examples** — `examples/memory-management.ts` (controlled `core.memory.*`) and `examples/portable-bundle.ts` (portable-bundle round-trip, runs without a model). Examples now import by package name (`from 'memoweft'`; build first).
- **English documentation** — `docs/INSTALL.md` and `docs/integration.md` are now English (with `.zh-CN.md` counterparts and cross-links), matching the English-first README. A new note records that data at rest is unencrypted — disk encryption is the host/OS responsibility.

### Changed

- **Default output language is now English (`en`).** Chinese hosts that relied on the implicit Chinese output will see event summaries / hypotheses / questions produced in English (the cognitive structure is unchanged). Set `config.language = 'zh'` or `MEMOWEFT_LANG=zh` to restore Chinese.
- **Default `hostId` changed from `'testbench'` to `'local'`.** This affects only the `host_id` stamped on newly-written evidence (it is not a query key); existing databases read unchanged. The `subjectId` default (`'owner'`) is unchanged.

## [0.3.0] — 2026-07-05

补漏加固批次：隐私红线 B 下沉 Core、`prepublishOnly` 发布保险丝、加固版 JSON 解析、SQLite `busy_timeout`、可选 `better-sqlite3` 驱动（支持 Node 20/22）、扫尾、以及「永远全开源」公开承诺。

### Added

- **Optional `better-sqlite3` driver — adds Node 20 / 22 support (Node ≥24 stays the tested, zero-dependency default).** The built-in `node:sqlite` module only stabilized in Node 24, which shut out the still-large Node 20/22 install base. There is now a second SQLite driver behind the same internal seam: on Node ≥24 the built-in `node:sqlite` is used by default (still **zero runtime dependencies**), and if it is unavailable MemoWeft falls back to `better-sqlite3` when installed. Node 20/22 users run `npm i better-sqlite3` to opt in. `better-sqlite3` is declared as an **optional peer dependency** (`peerDependenciesMeta.optional`), so `npm install memoweft` still pulls **no** runtime dependencies and no native module by default. When neither driver is available, `import 'memoweft'` fails with a plain-language error listing both fixes (upgrade Node to ≥24, or install `better-sqlite3`). It is a native module usually installed as a prebuilt binary; if no prebuilt matches your platform it falls back to a `node-gyp` compile, so installation is not guaranteed on every environment.

### Changed

- **`engines.node` relaxed from `>=24` to `>=20`** — reflecting the new optional `better-sqlite3` driver. Node ≥24 remains the zero-dependency, out-of-the-box path; Node 20/22 need the optional driver installed.
- **SQLite `busy_timeout` now set to 5000ms on every self-opened connection** — when two processes write to the same database file (e.g. a host and a testbench pointed at the same `dla.db`), the later writer previously failed *immediately* with `SQLITE_BUSY`; it now waits up to 5 seconds for the write lock before erroring. Single-process use is unaffected (the synchronous SQLite API already serializes within a process). This is an infrastructure default, not a config knob. (WAL is intentionally *not* enabled in this change — it needs a matching backup-strategy change first.)
- **Observed evidence never defaults to cloud-readable** — the non-cloud default for `sourceKind: 'observed'` evidence is now enforced in `SqliteEvidenceStore.put` itself (not just the observation ingest entry). Callers that write observed evidence directly through `put` without an explicit authorization bit now get `allowCloudRead: false` by default (previously it followed the general default and could be cloud-readable). To send observed evidence to the cloud you must now pass `allowCloudRead: true` explicitly. `spoken` / `inferred` defaults and any explicitly-passed bit are unchanged; imports via `insert` are unaffected (they preserve the bundled bits).

## [0.2.0] — 2026-07

Durable schema: your `0.1.0` database upgrades losslessly on first open, with an automatic backup taken before any schema-changing migration.

### Added

- **Schema versioning & migrations** — the database now carries a `PRAGMA user_version`; `openStores` runs an ordered migration runner on open. New databases are stamped to the latest version; existing ones (e.g. a `0.1.0` database) are migrated forward, each migration in its own transaction (rolls back on failure), with an automatic pre-migration backup for schema-changing migrations and a dry-run mode. Exposed as `runMigrations` / `getSchemaVersion` / `LATEST_SCHEMA_VERSION`.
- **Downgrade protection** — opening a database whose schema version is *newer* than the running `memoweft` is now refused with a clear error, instead of silently reading/writing an unknown schema (which could corrupt data).
- A `0.1.0` database opens losslessly under the new version — verified against a frozen `0.1.0` fixture (`tests/fixtures/memoweft-0.1.0.db`) plus a schema-parity check that a freshly-created database and a migrated old one converge to the identical schema.

## [0.1.0] — 2026-07

First tidied pre-release. Core, a reference host, and the first plugins are in place and tested; interfaces may still change.

### Added

- **Cognitive core** — `evidence → event → cognition` layers with profile + recall, correction loop, attribution + proactive asking, and a periodic background pass (decay, typed expiry, recall gating, conflict revisit, trends).
- **Unified Core entry** — `createMemoWeftCore` facade plus a controlled memory-management API (invalidate, authorization, safe delete, merge, archive, integrity check) so hosts never touch the stores directly.
- **Portability & graph** — portable memory bundle (`exportBundle` / `validateBundle` / `importBundle`) and a memory-graph backend payload (`buildMemoryGraph`).
- **Cloud Guard** — cloud-read filtering on the write / trends / ask paths; observed behavior defaults to non-cloud-readable.
- **Reference host** (`apps/memoweft-host`) — chat, setup wizard, memory-management page, multi-session, backup / restore, and factory reset, all through the Core public API.
- **Experience plugin contract v1** — swappable personas over one core (`plain` + 星瑶 / Xingyao).
- **Collector plugin** (`@memoweft/collector-active-window`) — active-window collector that feeds the host via `POST /api/observe` (host audits, then `core.ingestObservation`).

### Notes

- Published as `memoweft@0.1.0` on the npm registry (`npm install memoweft`).
- `MEMOWEFT_*` environment variables are the primary names; the legacy `DLA_*` prefix remains supported for backward compatibility.
- Not yet: memory-graph front-end, schema versioning / migration hardening.

[Unreleased]: https://github.com/memoweft/memoweft/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/memoweft/memoweft/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/memoweft/memoweft/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/memoweft/memoweft/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/memoweft/memoweft/releases/tag/v0.1.0
