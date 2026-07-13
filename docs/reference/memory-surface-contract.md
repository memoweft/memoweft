# MemoWeft Memory Surface Contract v1

**English** | [简体中文](./memory-surface-contract.zh-CN.md)

> For hosts (the side that does `import 'memoweft'`). This is a **promise document** to the host: what you can rely on, what not to touch, and what happens when a promise is broken.
> Single source of truth. Peer to `INSTALL` / `integration.md`; hosts read it directly.
> Belongs to: overall plan Step 2; the shared foundation for Step 7's plugin contract and Step 10's 1.0 API lock-down.

## How to read this contract (three tiers + break policy)

**Three stability tiers**:

- **stable**: the host relies on it day-to-day, it is already collected behind the facade, and its shape is settled. Promise: "won't be changed casually."
- **experimental**: exported, the host may touch it, but **explicitly stated to change**; changing it is not a broken promise (a one-line CHANGELOG mention is enough).
- **internal**: implementation pieces already collected behind the facade, that the host has no reason to touch. Still exported (deletion belongs to Step 10), but **don't depend on them**.

**Policy for breaking stable (pre-1.0, moderately loose)**:

- **What counts as a break**: renaming a field / deleting a field / changing nullability / changing semantics (e.g. `confidence` units).
- **Cost**: allowed to break in a minor version, but you must ① mark it clearly in the CHANGELOG ② give a one-line migration note (old→new, how to change) ③ where the old name can be kept, provide an `@deprecated` alias (follow the `DLA_VERSION` / `DlaConfig` template). **Not required to "keep it for a whole version before deleting."**
- **Adding enum values is not a break**: **adding new values** to `SourceKind` / `ContentType` / `CredStatus` etc. is not a break; **narrowing (deleting values) is a break**. Hosts **must keep a `default` fallback branch** for these enums (missing a branch is the host's responsibility, see implicit contract item 10).
- **experimental surface**: change freely in a minor version, a one-line CHANGELOG mention is enough, no migration note owed.

---

## I. Facade methods chapter (25 host-facing methods)

The host's main entry point is `createMemoWeftCore(options)`; after getting the `MemoWeftCore` facade, it does its work through the facade's methods and three sub-namespaces (`memory` / `portable` / `graph`). **Do not bypass the facade and directly assemble the underlying `Sqlite*Store` / operators.**

Count: `createMemoWeftCore`(1) + facade top-level 9 + `core.memory` 11 + `core.portable` 3 + `core.graph` 1 = **25**. All **stable**.

### 1.0 Factory

#### `createMemoWeftCore(options: CreateCoreOptions): MemoWeftCore` — **stable**
- **Input** `CreateCoreOptions`: `dbPath` (required), `llm?` (`LLMPool | LLMClient`), `embedder?`, `retriever?`, `config?`, `vectorDbPath?`.
- **Returns**: the `MemoWeftCore` facade (the 9 top-level methods described below + `memory`/`portable`/`graph`/`health`/`close`).
- **Implicit contract**: **a core can be built even without `.env`** — missing model config does not crash; only paths that actually call a model degrade/error (see implicit contract item 9). `vectorDbPath` defaults to the same store as `dbPath`; the existing contract of one vector instance per subject is unchanged.
- Basis: `src/core/createCore.ts:39-52` (input), `:155-174` (assembly degradation).

### 1.1 Facade top-level 9 methods (`MemoWeftCore.*`)

| Method | Input | Returns | Tier | Implicit behavior contract |
|---|---|---|---|---|
| `ingestUserMessage(input)` | `UserMessageInput` | `Promise<Evidence>` | stable | Stores `spoken` evidence, only stores and does not reply (the "store" half of "store first, then reply"). |
| `ingestObservation(input)` | `ObservationInput` | `Promise<Evidence[]>` | stable | Stores `observed` evidence, **does not upload to cloud by default**; idempotent via `originId`; returns what was **newly persisted** this time (idempotent hits are not included). |
| `ingestToolResult(input)` | `ToolResultInput` | `Promise<Evidence>` | stable | Stores one tool-execution **result payload** as `tool` evidence (AD-3/D-0013), **does not upload to cloud by default** (`config.toolDefaults`); idempotent via `originId`. Only the tool's returned output is ingested — never the model's tool-call intent/arguments (iron rule 3a). To make a stored `tool` evidence cloud-readable, go through `memory.updateEvidenceAuthorization` (audited), not the ingest path. |
| `recall(input)` | `RecallInput` | `Promise<RecalledCognition[]>` | stable | Shares recall semantics with the same segment as `Conversation` (invalid/archived/out-of-scope/decay gating all apply). |
| `handleConversationTurn(input)` | `ConversationInput` | `Promise<TurnOutcome>` | stable | Store evidence → recall → reply; same `conversationId` reuses the instance, window is continuous; `systemPrompt`/`seedTurns` only take effect when the instance is first created (see implicit contract item 4). |
| `dropConversation(conversationId)` | `string` | `void` | stable | Drops the active conversation instance in memory (does not touch the store); the next call with the same id rebuilds it (at which point new `systemPrompt`/`seedTurns` take effect); a nonexistent id is silently skipped. |
| `updateProfile(input?)` | `UpdateProfileInput` | `Promise<UpdateProfileResult>` | stable | One-shot distill→consolidate→attribute→rebuild recall index. A failed index rebuild does not roll back the profile (`indexError` reports the cause). |
| `health()` | — | `HealthReport` | stable | Judged from the **parts this core actually holds**, not re-checking env: `llmReady`=holds a real conversation client; `embedReady`=holds a vector recaller. An injected stub/empty recaller is judged false. |
| `close()` | — | `void` | stable | Closes the shared connection + self-created vector store connection; **an injected retriever is the caller's to manage and is not touched**. |

Basis: `src/core/createCore.ts:120-145` (interface), `:181-287` (implementation).

### 1.2 `core.memory` (controlled memory management API, 11 methods)

Write operations all carry `reason` (required, goes into the audit table); read-only listing does not write audit records. Default `subjectId` = `config.identity.subjectId` (v1 single-person single-host).

| Method | Input | Returns | Tier | Implicit behavior contract |
|---|---|---|---|---|
| `invalidateCognition(input)` | `InvalidateCognitionInput` | `Cognition \| null` | stable | Marks invalid (`invalidAt=now`) + audit; returns `null` if nonexistent (no audit). |
| `updateEvidenceAuthorization(input)` | `UpdateEvidenceAuthorizationInput` | `Evidence \| null` | stable | Changes authorization bits + audit (detail records before/after); returns `null` if nonexistent; **a zero-change returns as-is with no audit record**. |
| `removeEvidenceSafely(input)` | `RemoveEvidenceSafelyInput` | `RemoveEvidenceResult` | stable | Has references and not `force` → refused, returns impact set; `force` → deletes evidence + clears links + audit within a transaction. `removed=false && blockers empty = nonexistent` (see implicit contract item 7). |
| `removeCognitionSafely(input)` | `RemoveCognitionSafelyInput` | `RemoveCognitionResult` | stable | Deletes cognition together with its provenance chain + audit; audit detail **stores only metadata, not the original content**. |
| `mergeCognition(input)` | `MergeCognitionInput` | `MergeCognitionResult` | stable | Same subject only; source chain moved to target (deduplicated), target confidence recomputed, source marked invalid rather than hard-deleted. Source/target nonexistent, cross-subject, target already invalid/already archived → **throws** (changes nothing). |
| `archiveCognition(input)` | `ArchiveCognitionInput` | `Cognition \| null` | stable | Archives (`archivedAt=now`) + audit; recall skips archived; data retained and recoverable; returns `null` if nonexistent. |
| `checkIntegrity()` | — | `IntegrityReport` | stable | Read-only, no change, no audit record, no `reason`; reports orphan join rows. |
| `listEvidence(input?)` | `ListMemoryInput` | `Evidence[]` | stable | Lists all evidence of a subject; read-only, no audit record. |
| `listCognitions(input?)` | `ListMemoryInput` | `CognitionWithMeta[]` | stable | Lists all cognitions of a subject, each with its provenance chain + **read-time computed** `effectiveConfidence` (not persisted, see implicit contract item 5). |
| `listEvents(input?)` | `ListMemoryInput` | `EventWithEvidence[]` | stable | Lists all events of a subject, each with the list of evidence ids it covers. |
| `resetSubject(input)` | `ResetSubjectInput` | `ResetSubjectResult` | stable | Destructive: clears the three layers + clears audit + clears the vector index. The four in-store tables are wrapped in one transaction; `indexAll([])` is outside the transaction and **clears the entire vectors table** (v1 single-person limitation, see implicit contract item 8). |

Basis: `src/memory/managementApi.ts:143-177` (interface), `:214-441` (implementation), `src/core/createCore.ts:249` (facade mounting).

> Stale-comment note: the docs at `createCore.ts:135` and `managementApi.ts:142` still say "7 operations", when it is actually **11** now (batch 5 step 0 added 4 read-only lists). This is a stale comment, to be corrected in S2-2 or separately; this contract uses 11 as authoritative.

### 1.3 `core.portable` (portable memory bundle, 3 methods)

| Method | Input | Returns | Tier | Implicit behavior contract |
|---|---|---|---|---|
| `exportBundle(opts?)` | `ExportOptions & { subjectId? }` | `MemoryBundle` | stable | Exports a subject's three layers + provenance chain as versionable JSON; does not include vector index/logs/.env/UI state. |
| `importBundle(bundle, opts)` | `MemoryBundle, ImportOptions` | `ImportPlan` | stable | `dryRun` only computes, does not write; `merge` writes deduplicated by id/originId (`ImportMode.replace` reserved for V2, see experimental). |
| `validateBundle(bundle)` | `unknown` | `ValidateResult` | stable | Only validates structure, does not write. |

Basis: `src/core/createCore.ts:100-104` (`PortableAPI`), `:251-260` (implementation).

### 1.4 `core.graph` (graph view, 1 method)

| Method | Input | Returns | Tier | Implicit behavior contract |
|---|---|---|---|---|
| `buildMemoryGraph(opts?)` | `BuildGraphOptions & { subjectId? }` | `MemoryGraphPayload` | stable | Backend uniformly produces a force-directed graph `{nodes, edges}`; `conflicts_with`/`corrects` edges are not generated in v1 (data not stored, see experimental). |

Basis: `src/core/createCore.ts:107-109` (`MemoryGraphAPI`), `:262-267` (implementation).

---

## II. Key data shapes chapter (≥30 items)

Each item is marked stable/experimental. "Complete post-persistence shape" and "facade input/return" are stable; `*Input` (inputs the host directly constructs) is stable as the facade is stable; intermediate inputs produced only by internal operators and not directly constructed by the host are marked experimental.

### 2.1 Three-layer persistence shapes (stable)

1. **`Evidence`** — stable. Complete post-persistence shape of evidence: `id / subjectId / sourceKind / hostId / originId / occurredAt / recordedAt / rawContent / summary / allowLocalRead / allowCloudRead / allowInference / correctsEvidenceId`. Basis `src/evidence/model.ts:14-40`.
2. **`EvidenceInput`** — stable (the host produces it indirectly via `ingestUserMessage`; directly constructing `evidenceStore.put` is an internal path). `id/recordedAt` are generated by the storage layer, default authorization bits are routed by `sourceKind`. Basis `src/evidence/model.ts:48-60`.
3. **`SourceKind`** — stable enum: `'spoken' | 'inferred' | 'observed' | 'tool'` (`'tool'` added in AD-3/D-0013 = a tool-execution result, an external data point). Adding values is not a break, must keep default. Basis `src/evidence/model.ts:11`.
4. **`Event`** — stable. Event persistence shape: `id / subjectId / summary / occurredAt / createdAt`. Basis `src/event/model.ts:10-18`.
5. **`EventInput`** — **experimental**. The host generally does not directly construct it (produced internally by `distill`); no direct construction point on the Host side (grep `apps/memoweft-host` has no hits). Basis `src/event/model.ts:20-26`.
6. **`EventWithEvidence`** — stable (`core.memory.listEvents` return item): `Event + evidenceIds: string[]`. Basis `src/event/model.ts:28-30`.
7. **`Cognition`** — stable. Cognition persistence shape: `id / subjectId / content / contentType / formedBy / confidence(0~1000) / credStatus / scope / validAt / invalidAt / askedAt / archivedAt? / createdAt / updatedAt`. The `askedAt` field itself is stable, but its **write timing** (M5 proactive asking) belongs to the experimental surface. Basis `src/cognition/model.ts:40-60`.
8. **`CognitionInput`** — **experimental**. The host does not directly construct it (`confidence`/`credStatus` are computed by `consolidate` and passed in; Host grep has no hits). Basis `src/cognition/model.ts:63-75`.
9. **`ContentType`** — stable enum: `fact | preference | goal | project | state | trait | hypothesis | trend`. Adding values is not a break, must keep default. Basis `src/cognition/model.ts:15-23`.
10. **`FormedBy`** — stable enum: `stated | observed | ruled | inferred`. Basis `src/cognition/model.ts:26`.
11. **`CredStatus`** — stable enum: `candidate | low | limited | stable | conflicted`. Basis `src/cognition/model.ts:29`.
12. **`EvidenceRelation`** — stable enum: `support | contradict`. Basis `src/cognition/model.ts:32`.
13. **`EvidenceLink`** — stable: `{ evidenceId, relation: EvidenceRelation }`. Basis `src/cognition/model.ts:34-37`.
14. **`CognitionWithSources`** — stable: `Cognition + sources: EvidenceLink[]`. Basis `src/cognition/model.ts:78-80`.

### 2.2 Input shapes of the facade methods

15. **`CreateCoreOptions`** — stable: `dbPath` required + `llm?/embedder?/retriever?/config?/vectorDbPath?` + **`clock?: Clock` (experimental, Phase 4)**. The `clock` injects the store time source (`recordedAt`/`created_at`/`updated_at`) for determinism / time-travel; defaults to real system time (additive, existing callers unaffected). It only produces timestamps and never enters confidence self-computation (iron rule 3b). **As of D-0015 the clock is wired through the entire facade path (stores + consolidate/attribute/management-audit + read-path decay `now`). The two remaining non-facade paths — proactive asking (`ProposeAskDeps`/`RevisitDeps`, `askedAt`) and the dev run-log (`RunLoggerOptions`, `ts`) — take their own optional `clock?` (D-0020), completing "every time source is injectable"; both are internal-tier and not reached by `CreateCoreOptions.clock`.** Basis `src/core/createCore.ts`.
15b. **`Clock`** — experimental (Phase 4): `type Clock = () => Date`; `systemClock` is the default (real system time). Injected via `CreateCoreOptions.clock` / `openStores(dbPath, cfg, clock)`. Basis `src/clock.ts`.
16. **`UserMessageInput`** — stable: `content` + `subjectId?/hostId?/sourceKind?/originId?/occurredAt?`. Basis `:56-66`.
17. **`ObservationInput`** — stable: `observations: Observation[]` + `subjectId?/hostId?`. Basis `:68-73`.
17a. **`ToolResultInput`** — stable (AD-3/D-0013): `content` (the tool's returned result payload) + `subjectId?/hostId?/originId?/occurredAt?`. Ingested as `tool` evidence, cloud-read defaults false (`config.toolDefaults`). Basis `src/core/createCore.ts`.
18. **`RecallInput`** — stable: `query` + `subjectId?`. Basis `:75-78`.
19. **`ConversationInput`** — stable: `message` + `conversationId?/subjectId?/hostId?/originId?/occurredAt?/systemPrompt?/seedTurns?`. Basis `:80-93`.
20. **`UpdateProfileInput`** — stable: `subjectId?`. Basis `:95-97`.
21. **`ListMemoryInput`** — stable: `subjectId?`. Basis `src/memory/managementApi.ts:115-117`.

### 2.3 Return shapes of the facade methods

22. **`TurnOutcome`** — stable: `reply / storedEvidence: Evidence / recall: RecalledCognition[] / llmCalls / error: string | null`. Non-empty `error` = reply degraded but evidence already persisted (see implicit contract item 6). Basis `src/pipeline/conversation.ts:44-50`.
23. **`RecalledCognition`** — stable (`recall`/`TurnOutcome.recall` item): `RelevantCognition + score + id?`. Basis `src/pipeline/conversation.ts:38-42`.
24. **`UpdateProfileResult`** — stable: `distilled / consolidated / attributed / indexed / indexError: string | null / timings`. Basis `src/consolidation/updateProfile.ts:45-55`.
25. **`UpdateProfileTimings`** — stable: `distillMs / consolidateMs / attributeMs / indexMs / totalMs`. Basis `:37-43`.
26. **`HealthReport`** — stable: `llmReady / embedReady`. Basis `src/core/createCore.ts:112-117`.
27. **`CognitionWithMeta`** — stable (`listCognitions` item): `Cognition + sources: EvidenceLink[] + effectiveConfidence` (read-time computed). Basis `src/memory/managementApi.ts:120-125`.

### 2.4 Management API input/output shapes

28. **`InvalidateCognitionInput`** — stable: `cognitionId + reason`. Basis `src/memory/managementApi.ts:22-26`.
29. **`UpdateEvidenceAuthorizationInput`** — stable: `evidenceId + allowCloudRead? + allowInference? + reason`. Basis `:28-34`.
30. **`RemoveEvidenceSafelyInput`** — stable: `evidenceId + reason + force?`. Basis `:36-41`.
31. **`RemovalBlocker`** — stable: `kind: 'event'|'cognition' + id + relation?`. Basis `:44-51`.
32. **`RemoveEvidenceResult`** — stable: `removed + blockers: RemovalBlocker[]`. Basis `:53-58`.
33. **`RemoveCognitionSafelyInput`** — stable: `cognitionId + reason`. Basis `:60-63`.
34. **`RemoveCognitionResult`** — stable: `removed + removedLinks: EvidenceLink[]`. Basis `:65-69`.
35. **`MergeCognitionInput`** — stable: `sourceId + targetId + reason`. Basis `:71-77`.
36. **`MergeCognitionResult`** — stable: `merged + movedLinks + duplicateLinks + target: Cognition + source: Cognition`. Basis `:79-89`.
37. **`ArchiveCognitionInput`** — stable: `cognitionId + reason`. Basis `:91-94`.
38. **`IntegrityIssue`** — stable: `kind + eventId? + cognitionId? + evidenceId + missing`. Basis `:97-104`.
39. **`IntegrityReport`** — stable: `ok + issues: IntegrityIssue[] + checkedAt`. Basis `:106-110`.
40. **`ResetSubjectInput`** — stable: `subjectId? + reason?` (`reason` is only for semantics, not persisted). Basis `:129-133`.
41. **`ResetSubjectResult`** — stable: `evidenceRemoved / eventRemoved / cognitionRemoved / auditRemoved`. Basis `:135-140`.
42. **`ManagementLogEntry`** — **experimental** (weakly typed: `op`/`targetKind` are currently `string`; the facade **does not expose** a path to read audit — the Host writes via `core.memory.*` but does not read audit history through the facade, only the underlying `SqliteManagementLog.list` can read): `op / targetKind / targetId / reason / detail: Record<string,unknown>|null / createdAt`. Basis `src/memory/managementLog.ts:23-33`.

### 2.5 Bundle shapes

43. **`MemoryBundle`** — stable: `format / schemaVersion / exportedAt / memoWeftVersion / subjectId / source{hostId,exportMode:'full'} / data{evidence,events,eventEvidence,cognitions,cognitionEvidence,unconsolidatedEventIds} / metadata{counts,notes}`. Basis `src/portable/model.ts:33-60`.
44. **`EventEvidenceLink`** — stable: `{eventId, evidenceId}`. Basis `:20-23`.
45. **`CognitionEvidenceLink`** — stable: `{cognitionId, evidenceId, relation}`. Basis `:26-30`.
46. **`ImportMode`** — stable type, but the `'replace'` value is **experimental** (reserved for V2; currently only `'dryRun' | 'merge'`). Basis `:63`.
47. **`ValidateResult`** — stable: `valid + errors[] + warnings[]`. Basis `:66-70`.
48. **`ImportPlan`** — stable: `mode + valid + errors[] + warnings[] + counts{...} + duplicates{...}`. Basis `:73-92`.
49. **`BUNDLE_FORMAT` / `BUNDLE_SCHEMA_VERSION`** — stable constants: `'memoweft-bundle'` / `1`. Basis `:15-17`.

### 2.6 Graph payload shapes

50. **`MemoryGraphPayload`** — stable: `subjectId / generatedAt / scope / depth / nodes / edges / stats`. Basis `src/graph/model.ts:71-79`.
51. **`MemoryGraphNode`** — stable: `id / kind / label / summary? / (cognition:) contentType?/formedBy?/confidence?/credStatus? / (evidence:) sourceKind?/allowCloudRead?/allowInference? / time fields / archivedAt? / val?/colorKey?`. Basis `:26-50`.
52. **`MemoryGraphEdge`** — stable: `id / source / target / kind / label? / dashed?`. Basis `:52-59`.
53. **`MemoryGraphStats`** — stable: `nodeCount / edgeCount / hiddenCount / activeCognitionCount / conflictedCount / hypothesisCount / observedEvidenceCount / toolEvidenceCount` (`toolEvidenceCount` added in AD-3/D-0013, additive). Basis `:61-69`.
54. **`MemoryGraphNodeKind`** — stable enum: `subject|evidence|event|cognition`. Basis `:16`.
55. **`MemoryGraphEdgeKind`** — stable enum, but the two values `conflicts_with`/`corrects` are **experimental** (not generated in v1, data not stored). Basis `:18-24`.

### 2.7 Perception input shapes

56. **`Observation`** — stable (cross-layer contract "collector plugin→Host→Core"): `kind / occurredAt / content / originId? / meta? / allow*?`. **However**: the `meta` field is **experimental** (this version only carries it, does not persist), and `kind` is an **open set** experimental (currently fixed to `'active_window'`, more values added later). Basis `src/perception/ingest.ts:19-34`.

### 2.8 Version / config

57. **`MEMOWEFT_VERSION`** — stable constant. `DLA_VERSION` is an `@deprecated` alias (keep, do not delete). Basis `src/index.ts:208-211`.
58. **`MemoWeftConfig` (what config items exist)** — stable: field structure of identity / privacyMode / observedDefaults / consolidation / retrieval / attribution / background etc. **0.4.0 adds an optional `language: 'zh' | 'en'` (additive, non-breaking — old hosts that don't pass it keep running; defaults to `'en'`, switch to Chinese via env `MEMOWEFT_LANG=zh` or by setting `config.language` at runtime) + exports `type Lang` (stable, for hosts to set values)**. **AD-3/D-0013 adds `toolDefaults: { allowLocalRead; allowCloudRead; allowInference }` (additive) — the conservative default authorization for `tool` evidence (local✓ / cloud✗ / infer✓), applied by `put()` per `sourceKind`, mirroring `observedDefaults`.** **However, "how you obtain config" (`config` singleton access) is marked experimental** and may be adjusted during the pre-1.0 period. `DlaConfig` is an `@deprecated` alias. `cloudReadDefault()` / `resolveLang()` are stable (the latter reads the current store language, which only decides text output and never enters the confidence self-computation). Basis `src/config.ts`.

---

## III. Implicit contract chapter (the pitfalls hosts most easily step on)

1. **`confidence` is on a 0~1000 scale, computed by MemoWeft rather than self-reported by the LLM**. Don't treat it as a 0~1 probability, and don't trust the score the LLM reports back. Basis `src/cognition/model.ts:46-47`, `src/consolidation/confidence.ts:4` ("do not accept the LLM's self-report"), `:24-34`.
2. **The required `reason` on management write operations is a privacy audit contract**, and must not be relaxed to optional — the audit table answers "what was done to my memory." Basis `src/memory/managementApi.ts:22-94` (each Input's `reason: string` is not optional), `managementLog.ts` schema `reason TEXT NOT NULL`.
3. **`observed` AND `tool` evidence default to `allowCloudRead=false` (privacy red line B)**. Ingested observations and tool results default to not uploading to cloud (tool results often carry sensitive external data — web pages, files, API responses); only an explicit `allowCloudRead:true` on the input goes to cloud. Enforced as the last line of defense in `evidenceStore.put()` per `sourceKind` (`observed` → `observedDefaults`, `tool` → `toolDefaults`). Basis `src/evidence/store.ts` (conservative branch), `src/perception/ingest.ts:7-10`, `:79-82`.
4. **`systemPrompt` / `seedTurns` only take effect when the conversation instance is first created** (to change the persona/re-seed the continuation window you must first `dropConversation(id)` then call again, otherwise the old instance is hit and the new values are silently ignored). Basis `src/core/createCore.ts:89-92` (input comment), `:207-234` (reuse/rebuild logic).
5. **`effectiveConfidence` is a read-time computed derived value, not persisted**. What's stored is the raw `confidence`; the `effectiveConfidence` returned by `listCognitions` = `confidence × decay factor`, computed fresh on each read. Basis `src/memory/managementApi.ts:123-124`, `:397-405`.
6. **Non-empty `TurnOutcome.error` = reply degraded but evidence already persisted (store first, then reply)**. When the host sees `error != null` it should understand "this turn did not reply normally, but the user's message has been stored into the evidence store," and should not retry ingestion (which would either duplicate the persistence or rely on originId idempotency). Basis `src/pipeline/conversation.ts:44-50`, `:63-78` (store-before, a failed recall is treated as no recall and proceeds as usual).
7. **`RemoveEvidenceResult`: `removed=false && blockers empty = target nonexistent`** (disambiguation). Refusal only happens when there are references (`blockers` non-empty); `removed=false` with `blockers=[]` means "the evidence never existed," not "it was blocked." Basis `src/memory/managementApi.ts:55-57`, `:250`.
8. **`resetSubject` v1 single-person limitation**: in-store cleanup is by subject, but clearing the vector index goes through `indexAll([])`, which **clears the entire vectors table (all subjects' vectors)**, not just this subject. Harmless under v1 single-person single-host; when moving to multi-subject it must be changed to subject granularity. Basis `src/memory/managementApi.ts:435-438`.
9. **A core can be built even without `.env`**: when model config is missing, work that doesn't touch a model — such as "storing evidence / managing memory" — is still usable; only read/write paths that actually call a model (reply, semantic recall, profile generation) degrade/error. `health()` tells you which capabilities remain (`llmReady`/`embedReady`). This is the key promise for the host to judge "which capabilities remain when config is missing." Basis `src/core/createCore.ts:5-8` (factory header comment), `:147-174`, `:269-280` (health).
10. **The fallback responsibility for enum value sets is the host's**: `SourceKind` / `ContentType` / `FormedBy` / `CredStatus` / `EvidenceRelation` — **narrowing (deleting values) is a break; adding values is not a break, but the host must keep a `default` fallback branch**. If the host `switch`es these enums without a `default`, it will miss a branch after values are added — the responsibility is the host's. Basis this contract's "break policy" section + `src/evidence/model.ts:11`, `src/cognition/model.ts:15-32`.

---

## IV. experimental list chapter (a consolidated list of "will change later")

Exported, the host may touch them, but **explicitly stated to change**, changeable freely in a minor version (a one-line CHANGELOG mention is enough, no migration note owed). Don't depend on these as a stable surface.

- **`Observation.meta`** — this version only carries it, does not persist (Evidence has no meta column); the persistence shape will change later. Basis `src/perception/ingest.ts:28-29`.
- **`Observation.kind` (open set)** — currently fixed to `'active_window'`, later adding `'clipboard'`/`'device'` etc. Basis `src/perception/ingest.ts:23-24`.
- **`ImportMode.replace`** — currently only supports `'dryRun'|'merge'`, `'replace'` reserved for V2. Basis `src/portable/model.ts:62-63`.
- **Graph `conflicts_with` / `corrects` edges** — not generated in v1 (cognition↔cognition chain data not stored); the enum is reserved, to be produced once the data model is completed. Basis `src/graph/model.ts:7-12`, `:23-24`.
- **`Cognition.askedAt` (write timing)** — the field itself is stable, but "when it's written" (written after M5 proactive asking `proposeAsk` asks) is an experimental-period capability. Basis `src/cognition/model.ts:53`, `src/asking/proposeAsk.ts`.
- **`ManagementLogEntry` (reading audit history)** — weakly typed (`op`/`targetKind` are `string`), the facade does not expose a read path; the host writes via `core.memory.*` only and does not read audit history through the facade. Basis `src/memory/managementLog.ts:23-33`.
- **Extension-point interfaces `Retriever` / `Embedder` / `LLMClient`** — replaceable injection points whose interface signatures may evolve later. Basis `src/index.ts:88-105`. **New (tier 2, non-breaking)**: `LLMClient.tier?` and `LLMConfig.tier?` (`ModelTier='cloud'|'local'`, already exported) are optional fields, defaulting to `cloud`; an `LLMClient` the host injects itself runs even without tier.
- **Plugin contract `MemoWeftPlugin` / `PluginContext` / `PluginPermissions` / hook types** (Step 7 · v2 · **experimental**) — exported from `src/plugin/contract.ts`; `createMemoWeftCore` adds an optional `plugins?` (not passing it = same behavior as before). Pre-1.0 hook signatures may evolve (e.g. adding fields). **See [`plugin-contract.md`](../plugin-contract.md) for the authoritative definition and semantics**; not repeated here.
- **config's "way of obtaining" (singleton access)** — "what config items exist" is stable, "how you obtain config (`config` singleton)" is experimental and may be adjusted during the pre-1.0 period. Basis `src/config.ts`.
- **`EventInput` / `CognitionInput`** — see "questionable tier": internal inputs the host does not directly construct.

---

## V. Breaking-change policy (pre-1.0, moderately loose)

> The top of the contract, "How to read this contract," already gives a one-line summary; this chapter is the **full written policy text**, by which the host judges "will a given upgrade break my integration, do I need to change code."

### 5.1 What counts as "breaking stable"

For the **stable** surface (the facade methods and data shapes listed in Chapters I/II), the following changes are breaks: **renaming a field / deleting a field / changing nullability (optional↔required, nullable↔non-nullable) / changing semantics** (e.g. `confidence` changed from a 0~1000 scale to a 0~1 probability).

### 5.2 The three requirements for breaking stable

Breaking stable is allowed in a **minor version**, but **all three** must be satisfied at once, none omitted:

1. **① Clear CHANGELOG marking** — in the `Changed` (or `Removed`) section of `CHANGELOG.md`, name clearly which symbol / which field was broken.
2. **② A one-line migration note** — old→new, how to change it (the host can finish the change in one step by following it), written in the same CHANGELOG entry.
3. **③ Where the old name can be kept, provide an `@deprecated` alias** — for any "rename / swap constant" case where the old name can be kept, keep the old name with an `@deprecated` mark pointing to the new name (**follow the existing template**: `DLA_VERSION` (`src/index.ts:210` @deprecated alias pointing to `MEMOWEFT_VERSION`), `DlaConfig` (`src/config.ts:136` @deprecated alias pointing to `MemoWeftConfig`) — these two are the "already-deprecated template," don't delete them).

**Not required to "keep it for a whole version before deleting"**: there is no hard cool-down period on deletion timing; keep an alias where you can, and where you can't (e.g. deleting a field) follow the ①② marking + migration note.

### 5.3 Enum value-adding rule

For enums like `SourceKind` / `ContentType` / `FormedBy` / `CredStatus` / `EvidenceRelation`:

- **Adding new values ≠ break** — a minor version may add values. **But the host must keep a `default` fallback branch for these enums** (a `switch` with no `default` will miss a branch after values are added — **the responsibility is the host's**, see implicit contract item 10).
- **Narrowing (deleting values) = break** — follow the three requirements in 5.2.

### 5.4 experimental surface (loose rule)

For the **experimental** surface (the Chapter IV list: `Observation.meta` / `Observation.kind` open set / `ImportMode.replace` / graph `conflicts_with`·`corrects` edges / `Cognition.askedAt` write timing / `Retriever`·`Embedder`·`LLMClient` extension points / config's way of obtaining / `ManagementLogEntry` / `EventInput`·`CognitionInput` etc.):

- **Change freely in a minor version**, changing it is not a broken promise.
- **A one-line CHANGELOG mention is enough**, no migration note owed, no `@deprecated` alias owed.

### 5.5 internal surface

**Don't depend on it**. Still exported only because this step is "mark-only, no deletion" (deletion belongs to Step 10); once collected and deleted, it does not follow the three requirements of stable, a one-line CHANGELOG mention is enough.

---

## VI. Questionable symbol tiering (conclusions confirmed back to source)

The following 6 easily-misjudged symbols are tiered item by item according to their usage in the source:

| Symbol | Tier | Tiering basis (pointed to source) |
|---|---|---|
| `AskProposal` / `AskPolicy` / `proposeAsk` (`src/asking/`) | **internal** | The facade `MemoWeftCore` (`createCore.ts:120-145`) **does not expose** proposeAsk; the Host (`apps/memoweft-host`) grep has **no hits**. The only direct-use point is the **development testbench** `testbench/server.mjs:25-26,463-464` — the testbench is a dev debugging harness, not a product host, and does not constitute a contract surface to the host. Hence tiered internal (AskProposal/AskPolicy are its input/output, internal accordingly). `revisitConflicts` is internal for the same reason. |
| `Cognition` / `Evidence` domain shapes | **stable** | `recall`/`TurnOutcome.storedEvidence` return the whole `Evidence` (`createCore.ts:122`, `conversation.ts:46`); `listCognitions`/`listEvidence`/`listEvents` return the whole `Cognition`/`Evidence`/`Event` to the host (`managementApi.ts:167-171`). Returned to the host → promoted to stable. |
| `Conversation` class | **internal** | The facade `handleConversationTurn` **wraps** `Conversation` internally (`createCore.ts:207-228` creates the instance, caches, reuses); the host does not directly new it. The facade already collects it → the class is judged internal. |
| `TurnOutcome` / `RecalledCognition` | **stable** | As the return shapes of `handleConversationTurn` / `recall`, returned to the host (`createCore.ts:126,128`). → stable. |
| `Observation` (`src/perception/ingest.ts`) | **stable** (`meta` field experimental) | The "collector plugin→Host→Core" cross-layer contract, the input of `ingestObservation` (`createCore.ts:124`, `ingest.ts:19-34`). The `meta` field's source notes "this version only carries it, does not persist" → that field is experimental. |
| `EventInput` / `CognitionInput` | **experimental** | The host generally does not directly construct them (produced internally by distill/consolidate; Host grep has no direct construction point). Not listed in the host's main surface. Basis `event/model.ts:20-26`, `cognition/model.ts:63-75`. |
| `ManagementLogEntry` | **experimental** | Weakly typed fields (`op`/`targetKind` are `string`, `managementLog.ts:23-33`); the facade does not expose a path to read audit history, the Host writes only via `core.memory.*` and does not read audit through the facade. → experimental. |

---

## VII. Adapter degradation semantics (§16.2)

> Scope: the official MemoWeft adapters (`@memoweft/adapter-ai-sdk`, `@memoweft/mcp-server`). When the memory layer (`core.recall` / `core.ingestUserMessage`) fails or times out, an adapter **degrades instead of interrupting the conversation**. Human-approved wording, 2026-07-11 (see `DECISIONS.md` D-0012). This section governs the adapters only; it does not add any obligation to the Core facade above.

- **recall timeout**: the read path wraps `core.recall` in a **200ms** timeout by default, **configurable** through the adapter factory option (`recallTimeoutMs`). A timeout counts as a failure.
- **Retry**: the **read path (recall) does not retry** — on failure/timeout it degrades immediately; the **write path (ingest) retries once** before giving up.
- **Degradation behavior**: on failure/timeout the adapter **injects an empty context (no memory) and the conversation is not interrupted**; one line is recorded through the **injected logger** (no logger by default = silent; the host may inject one).
- **Implementation boundary**: the timeout is a `Promise.race` wrapping `core.recall` inside the adapter; the logger is an optional adapter-factory parameter. This **does not touch the Core api-freeze** — Core's `src/index.ts` export surface / `tests/api/api-surface.snapshot` are unchanged (`npm run api:check` still passes).
- **Logger records structured degradation events only** — shape `{ event: 'memory_degraded', op: 'recall' | 'ingest', reason: 'timeout' | 'error' }` (the MCP server adds an optional `tool` field) — and **never records user content, verbatim text, or secrets** (cognitive discipline + privacy).
- **Degradation vs. real error**: only memory-layer internal faults/timeouts (`core.recall` / `core.ingestUserMessage` throwing or timing out) degrade. Caller errors — invalid parameters, protocol-level errors — are **not** swallowed as degradation and still surface as errors. In the MCP server, input-schema (`zod`) validation runs before the handler, so a bad parameter stays a protocol error with `isError: true`; only the wrapped `core.*` call degrades.

Basis: `packages/adapter-ai-sdk/src/recallMiddleware.ts` (recall timeout + degrade), `packages/adapter-ai-sdk/src/persistOnEnd.ts` (write retry-once + degrade), `packages/mcp-server/src/tools.ts` (read/write tool guards), `packages/adapter-ai-sdk/src/degrade.ts` + `packages/mcp-server/src/degrade.ts` (shared `DEFAULT_RECALL_TIMEOUT_MS = 200`, `withTimeout`, logger event types).
