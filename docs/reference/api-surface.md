# MemoWeft API surface (draft)

> **Status: 1.0 freeze — dispositions decided and signed off.** This document classifies every
> public export as **stable**, **experimental**, or **internal**, so 1.0 knows
> exactly what it is freezing. It is the planning counterpart to two existing
> artifacts and does not replace them:
>
> - [`memory-surface-contract.md`](./memory-surface-contract.md) — the
>   _behavioural_ contract of the `createMemoWeftCore()` facade (what each method
>   does, what callers may rely on).
> - `tests/api/api-surface.snapshot` — freezes the _full_ set of exported symbols
>   so accidental additions or removals fail `npm run api:check`.
>
> The stable/experimental lists below are derived from the `[stable]` /
> `[experimental]` / `[internal]` annotations already present in
> [`src/index.ts`](../../src/index.ts); this document systematizes them and adds
> the cross-language and policy view. Individual method behaviour is not repeated
> here — follow the links to the contract.

## Export ≠ support

The root `memoweft` package re-exports many low-level building blocks (stores,
write-path operators, JSON repair, retrieval internals) so advanced hosts _can_
compose them. **Exporting a symbol is not a support promise.** `api-surface.snapshot`
freezes the full export set to catch drift; this document says which of those
exports an application may build a contract on. When in doubt, integrate through
the `createMemoWeftCore()` facade — the low-level pieces are behind it for a
reason.

## Stability labels

Aligned with `memory-surface-contract.md`:

- **Stable** — covered by compatibility snapshots and intended for application
  use across the current minor line. After 1.0, breaking changes require a major
  version and a deprecation notice.
- **Experimental** — usable, but may change in a pre-1.0 minor (or, after 1.0, in
  a minor) with a changelog notice. Extension points and diagnostics live here.
- **Internal** — implementation detail. Exported for composition/diagnostics
  only; do not build application contracts around it. May change in any release.

---

## Core package (`memoweft`)

### Stable

The supported host-facing surface. This is the **candidate set to freeze at 1.0**.

- **Facade entry** — `createMemoWeftCore`, `MemoWeftCore`, `CreateCoreOptions`,
  and its I/O types: `UserMessageInput`, `ObservationInput`, `ToolResultInput`,
  `RecallInput`, `ExplainCognitionInput`, `CognitionExplanation`,
  `ConversationInput`, `RecordAssistantReplyInput`, `UpdateProfileInput`,
  `UpdateProfileResult`, `PortableAPI`, `MemoryGraphAPI`, `HealthReport`, `UsageReport`.
- **Controlled memory management** (`core.memory`) — `MemoryManagementAPI` and its I/O
  types (`InvalidateCognitionInput`,
  `UpdateEvidenceAuthorizationInput`, `RemoveEvidenceSafelyInput`,
  `RemoveEvidenceResult`, `RemovalBlocker`, `RemoveCognitionSafelyInput`,
  `RemoveCognitionResult`, `ReinforceCognitionInput`, `ReinforceCognitionResult`,
  `MergeCognitionInput`, `MergeCognitionResult`, `ArchiveCognitionInput`,
  `MuteCognitionInput`, `IntegrityIssue`, `IntegrityReport`, `ListMemoryInput`,
  `CognitionWithMeta`, `ResetSubjectInput`, `ResetSubjectResult`).
- **Domain shapes returned by the facade** — Evidence: `Evidence`,
  `EvidenceInput`, `SourceKind`. Event: `Event`, `EventWithEvidence`. Cognition:
  `Cognition`, `CognitionWithSources`, `ContentType`, `FormedBy`, `CredStatus`,
  `EvidenceLink`, `EvidenceRelation`. Interaction: `InteractionContext`,
  `SemanticResolution`, `VisibleTurn`, `ResponseAct`, `PromptAct`,
  `PropositionOrigin`, `AssertionStrength`. Conversation: `TurnOutcome`,
  `RecalledCognition`, `RecalledEvidence`.
- **Cross-layer ingestion contract** — `Observation` (the collector → host → core
  shape). The frozen part is the fields core consumes (`occurredAt`, `content`,
  `originId`, the authorization flags); `kind` and `meta` are **experimental-reserved** —
  the ingest path neither reads nor persists them today and their shape/semantics may
  still change (see the contract).
- **Configuration shape** — `MemoWeftConfig`, `cloudReadDefault`, `Lang`, and the
  `@deprecated` alias `DlaConfig` (kept for compatibility; do not remove).
- **Portable bundle** — `BUNDLE_FORMAT`, `BUNDLE_SCHEMA_VERSION`, `MemoryBundle`,
  `ExportOptions`, `ImportOptions`, `ImportMode`, `ImportPlan`, `ValidateResult`,
  `EventEvidenceLink`, `CognitionEvidenceLink`.
- **Memory graph** — `MemoryGraphPayload`, `MemoryGraphNode`, `MemoryGraphEdge`,
  `MemoryGraphNodeKind`, `MemoryGraphEdgeKind`, `MemoryGraphStats`, and
  `BuildGraphOptions` (the option bag referenced by the stable
  `core.graph.buildMemoryGraph` signature). `MemoryGraphEdgeKind` freezes only the four
  emitted edges (`belongs_to_subject` / `distilled_into` / `supports` / `contradicts`);
  `conflicts_with` / `corrects` are reserved-but-unemitted **experimental** values
  (cognition-to-cognition links are not persisted; see the contract).
- **Version** — `MEMOWEFT_VERSION` (and `@deprecated` `DLA_VERSION`).

### Experimental

Usable, but the shape may still move before 1.0.

- **`CreateCoreOptions.contradictionGuard`** — A5 first-pass contradiction guard
  (inline, during consolidate), default off (see the contract).
- **`reconcileContradictions`, `ReconcileDeps`, `ReconcileResult`** — A5
  second-pass whole-profile reconciler (the D-09 "full fix"), reached through the
  facade as `core.reconcileContradictions()`; an opt-in background operator in the
  same family as `core.expire()` / `core.aggregateTrends()`, default off. Whether
  A5 (both passes) is promoted to stable or kept experimental at 1.0 is an open
  decision (see below).
- **Background-operator facade return types** — `ExpireResult`, `TrendResult`
  (returned by the experimental `core.expire()` / `core.aggregateTrends()`;
  `ReconcileResult` is covered by the entry above). Their free-function operators
  and `*Deps` stay internal.
- **Injectable extension points** — Retrieval: `Retriever`, `RetrievalHit`,
  `NullRetriever`, `VectorRetriever`. Embedding: `Embedder`, `EmbedConfig`,
  `OpenAICompatEmbedder`, `loadEmbedConfig`. LLM: `LLMClient`, `ChatMessage`,
  `ModelTier`, `UsageStats`, `OpenAICompatClient`, `loadLLMConfig`,
  `loadLLMPool`, `LLMPool`, `LLMPurpose`. Clock: `Clock`, `systemClock`.
- **Plugin contract v2** — `MemoWeftPlugin`, `PluginType`, `PluginContext`,
  `PluginPermissions`, `PluginObservationInput`, `PluginUserMessage`.
- **Storage assembly & migration** (diagnostics / tooling) — `openStores`,
  `StoreBundle`, `runMigrations`, `getSchemaVersion`, `LATEST_SCHEMA_VERSION`,
  `Migration`, `MigrationResult`, `RunMigrationsOptions`.
- **Observability** — `createRunLogger`, `RunLogger`, `RunLoggerOptions`,
  `TurnRecord`, `Hypothesis`, `ProfileUpdateRecord`, `ProfileUpdateTimings`,
  `LogRecallItem`.
- **Config access** — `config` (the singleton _access pattern_ may change; the
  config _shape_ itself is stable, above).
- **Internally-produced input shapes** — `EventInput`, `CognitionInput` (hosts do
  not construct these; produced by distill/consolidate).
- **Weakly-typed audit row** — `ManagementLogEntry` (the facade does not expose a
  read-audit path).
- **`createMemoryManagementAPI` factory** — the low-level assembler behind `core.memory`
  (already wired by `createMemoWeftCore`). Its signature takes the experimental
  `StoreBundle` and the internal `MemoryManagementDeps`, so it is not frozen at 1.0 even
  though the `MemoryManagementAPI` interface and its I/O types (in Stable, above) are.
- **`UpdateProfileResult` stage payloads** — `UpdateProfileResult` is stable as the
  `core.updateProfile()` return envelope (the presence of `indexed` / `indexError` /
  `metrics`), but its `distilled` / `consolidated` / `attributed` fields and `timings`
  (`UpdateProfileTimings`) are experimental stage-diagnostic payloads whose shapes track
  the write-path stages; do not build durable contracts on them.

### Internal (exported, unsupported)

Exported for composition and diagnostics; the facade already wraps them and a
host has no reason to wire them directly. **Do not build application contracts on
these.** Grouped by area:

> **1.0 note:** the cleanest internal building blocks were removed from the root
> re-export at 1.0 to shrink the accidental-dependency surface — the six `Sqlite*` store
> implementation classes (marked ✗ below), the JSON-repair helpers (`extractJsonObject`,
> `parseJsonObject`, `parseJsonObjectWithRepair`, `ParseWithRepairDeps`), and
> `noopTransaction`. **For an installed package this removes them outright, not just from
> the root**: `package.json` declares a single `"."` export — so any deep subpath fails with
> `ERR_PACKAGE_PATH_NOT_EXPORTED` — and `src` is not among the published `files`. There is
> therefore no deep-import escape hatch for them, and 1.0 deliberately does not add one —
> that is exactly what the internal tier permits. Adding a supported subpath export later is
> additive and can land in any minor if a real consumer turns up.
> In-repo callers are unaffected because they import by relative path (for example
> `bench/eval-consolidation.mjs` → `../src/evidence/store.ts`), which never goes through the
> package `exports` map. The store _interface_ types (`EvidenceStore`, `EventStore`,
> `CognitionStore`, `CognitionPatch`, `InteractionContextStore`, `SemanticResolutionStore`,
> `ManagementLog`) and `Transaction` stay root-exported because the experimental
> `StoreBundle` references them.

(✗ = no longer exported from the package root as of 1.0; see the note above.)

- **Store implementations** — `SqliteEvidenceStore`✗/`EvidenceStore`,
  `SqliteEventStore`✗/`EventStore`, `SqliteCognitionStore`✗/`CognitionStore`/
  `CognitionPatch`, `SqliteInteractionContextStore`✗/`InteractionContextStore`,
  `SqliteSemanticResolutionStore`✗/`SemanticResolutionStore`,
  `SqliteManagementLog`✗/`ManagementLog`, `MemoryManagementDeps`.
- **Write-path operators** — `distill`, `consolidate`, `updateProfile`,
  `computeConfidence`, `deriveCredStatus`, `isHedgedStated`, `attribute`,
  `proposeAsk`, `revisitConflicts` (plus their `*Deps`/input types and their
  `*Result` types — **except `UpdateProfileResult`**, which is stable, returned by
  the `core.updateProfile()` facade above).
- **Background operators** — `decayFactor`, `halfLifeOf`, `effectiveConfidence`,
  `expire`, `aggregateTrends` (plus `ExpireDeps`/`AggregateTrendsDeps`). Note:
  `expire`/`aggregateTrends` are also reachable through the facade as
  `core.expire()`/`core.aggregateTrends()`, the supported (experimental) entry
  points — see the contract. Their return types `ExpireResult`/`TrendResult` are
  therefore experimental (listed above), not internal.
- **Pipeline & shared recall** — `Conversation`, `ConversationDeps`, `perceive`,
  `PerceiveOptions`, `WorkingMemory`, `Turn`, `recallCognitions`, `RecallDeps`,
  `RecalledCognitionItem`, `ingestObservations`, `IngestDeps`, `IngestResult`.
- **Free-function portable/graph** — `exportBundle`, `validateBundle`,
  `importBundle`, `buildMemoryGraph` (plus their `*Deps`/`*Options`); the
  supported path is `core.portable` / `core.graph`.
- **Transactions & JSON repair** — `noopTransaction`✗, `Transaction`,
  `extractJsonObject`✗, `parseJsonObject`✗, `parseJsonObjectWithRepair`✗,
  `ParseWithRepairDeps`✗.

---

## Subpackages

Each subpackage versions independently of Core and re-declares its own surface.
All subpackages share the degrade contract constant `DEFAULT_RECALL_TIMEOUT_MS`
(recall times out → empty recall, never blocks the turn).

### `@memoweft/mcp-server`

Stable host-facing surface: `createMcpServer`, `createCoreFromEnv`,
`registerTools`, `MCP_SERVER_VERSION`, the tool-name whitelists
(`READ_TOOL_NAMES`, `WRITE_TOOL_NAMES`, `ALL_TOOL_NAMES`, `ToolName`),
`RegisterToolsOptions`, and the degrade types (`McpServerLogger`,
`McpDegradedEvent`, `DEFAULT_RECALL_TIMEOUT_MS`). The exposed tool set is
deliberately **5 reads + 3 light writes**; destructive / authorization-changing /
full-profile methods are intentionally not registered.

### `@memoweft/adapter-ai-sdk`

Stable host-facing surface: read middleware `createMemoWeftMiddleware`
(+ `MemoWeftMiddlewareOptions`, and the helpers `buildKnowledgeBlock`,
`getLastUserMessageText`, `addToLastUserMessage`); write path `createPersistOnEnd`,
`persistUserTurn`, `persistToolResults` (+ `PersistOnEndOptions`,
`PersistUserTurnInput`, `PersistToolResultsInput`); degrade types
(`MemoWeftLogger`, `MemoWeftDegradedEvent`, `DEFAULT_RECALL_TIMEOUT_MS`).

The five other adapters (claude-agent-sdk, langchain, llamaindex, mastra,
openai-agents) are unreleased source previews; their surfaces are not frozen.

---

## Python parity (`memoweft`, experimental)

The Python package is an **experimental parity implementation, not a
feature-complete SDK**. Its stable top-level exports are limited to the
deterministic **rule kernel**, and the package as a whole carries no 1.0
stability promise yet.

Top-level exports (`py/src/memoweft/__init__.py`): `CONFIG`, `Config`;
`compute_confidence`, `derive_cred_status`, `is_hedged_stated`, `is_transient`;
`decay_factor`, `effective_confidence`, `half_life_of`; `MIN_ID_PREFIX`,
`resolve_echoed_id`; `derive_formed_by`; `DEFAULT_DIM`, `HashEmbedder`,
`fnv1a32`, `tokenize`; and rule-kernel types (`CarrierFormedBy`, `CarrierInput`,
`ConfidenceInputs`, `ContentType`, `CredStatus`, `FormedBy`, `HedgeInput`,
`PropositionOrigin`, `Resolution`, `ResponseAct`, `SourceKind`).

### TS ↔ Python surface mismatch (intended, for now)

The two languages classify the rule kernel differently, and that is expected at
this stage:

- In **TS**, the confidence/formation rules (`computeConfidence`,
  `deriveCredStatus`, …) are **internal** — they are wrapped by the Core facade,
  so hosts never call them directly.
- In **Python**, the same rules are the **top-level (experimental) surface** —
  there is no Core facade yet, so the rule kernel _is_ the public API.

Bit-exact parity is enforced on the shared deterministic functions via
`shared/parity/*.json` regardless of how each language classifies them. A
future Python Core facade (if built) would move the kernel behind it, matching
the TS split. Until then, treat the Python surface as "experimental rule kernel
only".

---

## Stability policy

The authoritative policy is [STABILITY.md](../STABILITY.md); the essentials:

- **Pre-1.0** (`0.x`): minor releases may contain **documented breaking changes**
  with migration notes in `CHANGELOG.md`. Stable symbols are held steady within a
  minor line but not across minors.
- **1.0 and after**: breaking a **stable** symbol requires a **major** version and
  a prior deprecation notice. **Experimental** symbols may change in a minor with
  a changelog notice. **Internal** symbols may change in any release.
- **Deprecated aliases** (`DLA_VERSION`, `DlaConfig`, the `DLA_*` env prefixes)
  are retained for compatibility and removed only on a major with notice.
- The **snapshot is the enforcement**: `api-surface.snapshot` fails CI on any
  export add/remove. This document adds the human-facing _support tier_ on top of
  that mechanical freeze.

## 1.0 disposition (decided)

The 1.0 API surface review worked through every open item. These outcomes are **decided**
and govern the freeze:

1. **Every Experimental symbol stays experimental at 1.0.** None was promoted: the
   injectable extension points, plugin contract v2, storage/migration, observability, the
   `config` singleton, the background-operator return types (`ExpireResult`/`TrendResult`),
   and the internally-produced input shapes (`EventInput`/`CognitionInput`/
   `ManagementLogEntry`) are all replaceable extension points, diagnostics, or
   facade-wrapped producer shapes — promoting any would freeze a signature or behaviour that
   is not settled. The first candidates to promote in a later minor, once their seams
   settle, are `Clock` (its shape cannot grow) and `UsageStats` (already pinned structurally
   through the stable `UsageReport`).
2. **A5 (`contradictionGuard` + `reconcileContradictions`) stays experimental** per decision
   D-10: its at-scale side effects were never quantified on a real model, so it is not
   promoted despite the D-09 "full fix" landing. Default off; a documented known residual.
3. **Python 1.0 scope = hold the whole package experimental.** The rule kernel is bit-exact
   but classified `internal` on the TypeScript side; promising stability on symbols a future
   Python Core facade will move behind it would be a self-set trap. Parity is enforced
   independently of the tier label.
4. **Internal exports: the cleanest were dropped from the root** — the six `Sqlite*` store
   implementation classes, the JSON-repair helpers (`extractJsonObject`, `parseJsonObject`,
   `parseJsonObjectWithRepair`, `ParseWithRepairDeps`), and `noopTransaction` (see the
   Internal-section note and the changelog). The store interface types and `Transaction` stay
   because the experimental `StoreBundle` references them; the write-path operators and
   pipeline internals stay for now. **No escape hatch, decided:** because the package declares
   only a `"."` export, dropping these from the root removes them outright for installed
   consumers — there is no deep-import fallback, and none will be added. Subpath exports were
   considered and rejected: they would trade back part of the surface reduction, and the
   asymmetry favours waiting — adding a subpath export later is additive and can land in any
   minor, whereas withdrawing one after 1.0 would take a major. These eleven symbols have no
   measured consumers; if a real one appears, the door is open to add a supported path then.
5. **Stable-list self-consistency fixes applied** so no stable symbol depends on a
   non-frozen shape: `BuildGraphOptions` promoted to stable; `createMemoryManagementAPI`
   demoted to experimental; and `UpdateProfileResult` stage payloads, `Observation`
   `kind`/`meta`, and the graph `conflicts_with`/`corrects` edges marked experimental within
   their stable enclosing shapes.

**Built-in retrievers — asymmetric on purpose, not an oversight.** Two of the four built-in
`Retriever` implementations are root-exported (`NullRetriever`, `VectorRetriever`) and two
are not: `KeywordRetriever` (the FTS5 fallback `createMemoWeftCore` selects when no embedder
is configured — so, the real default for a key-less setup) and `HybridRetriever` (the RRF
fusion wrapper). Both unexported classes carry an explicit note at the top of their source
saying they are kept out of `src/index.ts` to leave the frozen public API untouched, so the
asymmetry is a decision that was already made, not a slip. Hosts do not need them on the
default path — the facade picks the fallback chain itself (`VectorRetriever` →
`KeywordRetriever` → `NullRetriever`) — and a host wanting different behaviour injects its
own `Retriever`.

**1.0 disposition: leave them unexported.** Exporting a built-in later is additive and can
land in any minor; un-exporting one after 1.0 would take a major. The uses that would argue
for opening them up — picking the FTS5 tokenizer explicitly through
`KeywordRetrieverOptions`, or composing RRF channels with `HybridRetriever` — have no
reported demand. Revisit when a host asks for one.

_This records the intended support tiers so the 1.0 freeze is a decision, not an archaeology
exercise. It changes no runtime behaviour or schema._
