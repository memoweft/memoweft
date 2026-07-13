# Changelog

All notable changes to MemoWeft are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> This file is the user-facing summary of notable changes; the full commit history has the fine detail.
> While the API is pre-1.0, minor versions may include breaking changes. Stability tiers and the breaking-change policy are documented in [`docs/memory-surface-contract.md`](docs/memory-surface-contract.md).

## [Unreleased]

### Added

- **Injectable clock (`Clock` / `systemClock`, Phase 4)** — `createMemoWeftCore({ clock })` and `openStores(dbPath, cfg, clock)` accept an optional `clock: Clock` (`() => Date`) that is the time source for store writes (`recordedAt` / `created_at` / `updated_at`). Injecting a fixed or advancing clock gives **determinism** (two runs produce identical timestamps) and **time-travel** (advance the clock to age out transient state while facts persist) — the foundation for the Phase 4 deterministic, no-key demo. Defaults to real system time, so existing callers are unaffected (**purely additive**). The clock only produces timestamps and **never enters confidence self-computation** (iron rule 3b: decay is still computed read-time from `updatedAt` vs `clock()`). Beyond the stores, the injected clock flows through the **entire facade path** (consolidate / attribute / management-audit / read-path decay `now`) via `createMemoWeftCore({ clock })`. The two **non-facade** paths — proactive asking (`ProposeAskDeps` / `RevisitDeps`, `askedAt`) and the dev run-log (`RunLoggerOptions`, `ts`) — take their own optional `clock?`, completing "every time source is injectable" (no stray `new Date()` timestamps left). All additive. See `DECISIONS.md` D-0015 (facade) and D-0020 (asking / run-log tail).
- **Tool-result ingestion — `SourceKind` gains `'tool'` + `core.ingestToolResult` (AD-3, memory surface contract §16.1; `DECISIONS.md` D-0013)** — MemoWeft can now record a tool execution's **returned result** (an external, objective data point = legitimate evidence) as `tool` evidence. New Core facade method `ingestToolResult(input: ToolResultInput): Promise<Evidence>` (verbatim result payload → one `tool` evidence, idempotent via `originId`). **Privacy: `tool` evidence defaults to local-only (`allowCloudRead=false`)** via a new `config.toolDefaults` (`{ allowLocalRead: true, allowCloudRead: false, allowInference: true }`), enforced as the last line of defense in `evidenceStore.put()` per `sourceKind` — tool outputs often carry sensitive external data (web pages, files, API responses), so they never default to cloud upload. **Iron rule 3a boundary: only the tool's returned output is ingested — never the model's tool-call intent/arguments (that is assistant output, which must never become evidence).** Both official adapters gain a tool-result ingestion surface: `@memoweft/adapter-ai-sdk` adds `persistToolResults(core, { messages, originIdPrefix? })` (reads only `role:'tool'` messages' `tool-result` parts from a turn's `response.messages`; the write path retries once then degrades per §16.2), and `@memoweft/mcp-server` adds a seventh whitelisted tool `memoweft_ingest_tool_result` (light write — stores one `tool` evidence, no profile update, no consolidation, no authorization grant). The memory graph payload's `MemoryGraphStats` gains `toolEvidenceCount` and `tool` evidence nodes get a distinct color key. **Purely additive**: adding a `SourceKind` value follows the enum value-adding rule (§5.3, no migration — `source_kind` is a free-text column); the new config field, facade method, graph stat, and `ToolResultInput` type are all additions, so existing callers need no changes. **Known follow-up (deferred to ROADMAP, out of AD-3 scope)**: distill/consolidate drop `sourceKind` when feeding the LLM, so a tool result could in principle be consolidated as if the user said it — this is pre-existing behavior (`observed` evidence has the same gap) and treating it touches the discipline-sensitive write path. See the adapter-kit AD-3 contract (now `applicable` for both adapters) and `DECISIONS.md` D-0013.
- **Adapter degradation semantics (memory surface contract §16.2)** — the two official adapters (`@memoweft/adapter-ai-sdk`, `@memoweft/mcp-server`) now **degrade instead of interrupting the conversation** when the memory layer fails or times out. The read path (`recall`) is wrapped in a **200ms timeout, configurable** via a new optional factory option `recallTimeoutMs` (`createMemoWeftMiddleware(core, { recallTimeoutMs })` / `createMcpServer(core, { recallTimeoutMs })`); a timeout counts as a failure. The **read path does not retry** — it degrades immediately (injects an empty context / returns an empty tool result with `isError: false`); the **write path (ingest) retries once** before giving up. Both adapters take a new optional `logger` that records **structured degradation events only** (shape `{ event: 'memory_degraded', op: 'recall' | 'ingest', reason: 'timeout' | 'error' }`, aligned across both adapters; the MCP server adds an optional `tool` field) — never user content, verbatim text, or secrets; no logger = silent (the previous behavior). This turns the MCP server from "a memory-layer throw surfaces as a protocol error / crashes the process" into "degrade and keep the conversation going", while **invalid parameters and other caller errors are still surfaced as real errors, not swallowed as degradation**. Purely additive hardening: the new factory options are optional (unset = previous behavior plus the built-in 200ms timeout and silent degradation), and **Core's public API surface is unchanged** (`npm run api:check` still passes — the timeout/retry/logger live entirely inside the adapter packages). New exported types for host-typed loggers: `MemoWeftLogger` / `MemoWeftDegradedEvent` (ai-sdk) and `McpServerLogger` / `McpDegradedEvent` (mcp-server). See the memory surface contract §16.2 and `DECISIONS.md` D-0012.

## [0.5.1] — 2026-07-08

Repository surface refresh for the current GitHub `main`, keeping Core APIs and runtime behavior stable.

### Changed

- Refreshed the public README and Chinese README so npm and GitHub present MemoWeft as a library-first package with the reference host clearly framed as a demo.
- Added the public docs index, examples index, reference-host guide, bilingual docs links, refreshed hero assets, screenshots, and the reference host demo GIF.
- Cleaned public repository surface by moving or removing historical task logs and internal operation notes from the default public path.
- Improved the reference host setup flow so saving model configuration writes the expected `.env` file and reads from a stable location.
- Synchronized `MEMOWEFT_VERSION` with the package version.

## [0.5.0] — 2026-07-06

Ecosystem release: an MCP server, a Vercel AI SDK adapter, and LLM token usage accounting — plus the local/cloud model tiers and plugin-contract v2 that landed on `main` since 0.4.0. Core stays zero runtime dependencies.

### Added

- **Local/cloud model tier for the write path (`ModelTier = 'cloud' | 'local'`)** — the write-path privacy gate is now `filterReadableByTier(items, tier)` instead of a fixed cloud filter. Set `MEMOWEFT_WRITE_LLM_TIER=local` (default `cloud`) so a **local write model digests `observed` behavioural evidence** (cloud=false by default) into the profile — the behavioural-observation collection path is now a real closed loop, not "authorize upload or it never gets digested." The tier is a new optional field on `LLMConfig` / `LLMClient` (`ModelTier` is exported; unset = cloud, non-breaking for hosts that inject their own `LLMClient`), bound to the client instance so a missing write model that falls back to the chat model inherits the chat tier (no "declared local, actually cloud" leak). The config wizard (`/api/gen-env`) emits `MEMOWEFT_WRITE_LLM_TIER` and warns when no local write model is configured.
- **`DistillResult.tierBlockedCount`** — how many pending evidence the current write-model tier cannot read (surfaced via `updateProfile().distilled`), so a host can tell when `observed` evidence is waiting for a local model or cloud authorization.
- **Plugin contract v2 (`MemoWeftPlugin` + hooks + `PluginContext`)** — the plugin contract moves into Core (`src/plugin/`, exported, experimental) and grows real hooks: `onLoad` / `onUserMessage` / `onObservation`, fired at the Core method layer (the conversation and ingestion pipelines are untouched). Register via `createMemoWeftCore({ plugins: [...] })` (optional — unset behaves as before). Hooks are **observe-only**: return values are discarded, they cannot mutate the reply/message, and a throwing hook is logged without crashing the turn or ingestion. Each hook gets a restricted `PluginContext` (`submitObservation` + `requestMemory`) that is closure-built (never hands over the stores), permission-gated by a declarative `permissions` field, and cannot mark a submitted observation cloud-readable (auth bits are stripped → conservative `observed` defaults). The reference host lists registered plugins and their declared permissions in a new "Plugins" tab (`GET /api/plugins`). See `docs/plugin-contract.md` and the runnable `examples/plugin-hook.ts`.
- **Collector platform seam (`createForegroundSampler`)** — `@memoweft/collector-active-window` now selects its foreground-window sampler by platform through a factory; the collection loop is platform-agnostic. Adding macOS/Linux means adding one sampler case (node built-ins only, no npm deps). Windows is the only implemented platform today; other platforms exit with an explicit "how to add it" message.
- **LLM token usage accounting (`core.usage()`)** — the built-in `OpenAICompatClient` / `OpenAICompatEmbedder` now capture the `usage` returned by OpenAI-compatible endpoints (previously read off the wire and discarded) into a monotonic per-instance `UsageStats` (`promptTokens` / `completionTokens` / `totalTokens` / `callsWithUsage`), and `core.usage()` reports the running total bucketed into `llm` / `embed` / `total` so a host can price its own spend. **Raw counts only — the library ships no price table** (pricing drifts by vendor/time and is the host's assumption; multiply the counts yourself). Endpoints that don't return `usage` (common for local models) are skipped silently: the count stays 0, nothing crashes — hence `callsWithUsage` (≤ `callCount`) so a host can compute an honest average. `usage` is a new **optional** field on `LLMClient` / `Embedder` (unset for hosts that inject their own — non-breaking, same as `tier?`); it never flows into self-computed confidence. The reference host exposes `GET /api/usage`.
- **`@memoweft/mcp-server` (new package)** — a Model Context Protocol server that exposes MemoWeft's memory to external AI clients as MCP tools. **Six tools only**: five read (`memoweft_recall`, `memoweft_list_cognitions` / `_evidence` / `_events`, `memoweft_graph`) and one light write (`memoweft_ingest_user_message`, which stores a single verbatim user message as spoken evidence — no profile update, no digestion, no authorization grant). Destructive operations, authorization changes, and full-conversation digestion are **intentionally not registered as tools**, so an external LLM cannot delete memory, flip cloud-read bits, or rewrite the profile. Ships as a standalone publishable package — `@modelcontextprotocol/sdk` lives in *its* dependencies; Core's stay `{}`. Registry submission is a manual publishing step.
- **`@memoweft/adapter-ai-sdk` (new package)** — a Vercel AI SDK adapter. **Read**: `createMemoWeftMiddleware(core)` wraps a language model via `wrapLanguageModel` and injects recalled memory into the prompt through `transformParams`, reusing Core's neutral knowledge-block wording (low-confidence items flagged as guesses). **Write**: `createPersistOnEnd(core, { userMessage })` persists the user's verbatim turn on `onEnd` — the assistant reply is never stored, and because the user message is captured explicitly (not scraped from the result event) the injected recall can't leak back in as "user input." `ai` is a peer dependency; Core's dependencies stay `{}`.

### Changed

- **Coverage fix:** `distill` now marks as covered only the evidence it actually digested into the event. Previously, when `observed` (cloud=false) evidence shared a batch with cloud-readable evidence, it was marked covered without ever being digested and could not be recovered — switching to a local model or authorizing upload later would not re-process it. Blocked evidence now stays pending and re-scannable.
- **`allowInference` gate is now enforced in `distill` and `consolidate`** (previously only in `attribute`), keeping all three write-path steps consistent. Consequence: evidence with `allowInference=false` **and** `allowCloudRead=true` no longer feeds the profile on the cloud path either (it used to). This state does not exist by default — it requires explicitly revoking inference on a specific piece of evidence — so most deployments see no change; where it applies, it is the authorization bit taking effect as documented.

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

[0.5.1]: https://github.com/memoweft/memoweft/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/memoweft/memoweft/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/memoweft/memoweft/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/memoweft/memoweft/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/memoweft/memoweft/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/memoweft/memoweft/releases/tag/v0.1.0
