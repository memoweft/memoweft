# MemoWeft public API contract

Applies to `memoweft` 0.6.x. This document describes the application-facing surface returned by `createMemoWeftCore()` and the behavior callers can rely on.

MemoWeft is pre-1.0. Additive fields and enum values may appear in minor releases. Breaking changes are documented in the [changelog](../../CHANGELOG.md) with migration notes. Low-level symbols exported from the root package remain available for compatibility, but the Core facade below is the supported integration path.

## Stability labels

- **Stable** — covered by compatibility snapshots and intended for application use throughout the 0.6 line.
- **Experimental** — usable, but may change in a pre-1.0 minor release with changelog notice.
- **Internal** — implementation detail; do not build application contracts around it.

Unless marked otherwise, the methods in this document are stable. `clock`, plugins, and low-level model/retrieval implementations are experimental extension points.

## Minimal lifecycle

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

await core.ingestUserMessage({
  subjectId: 'user-42',
  content: 'I prefer aisle seats.',
  originId: 'message-1001',
});

const evidence = core.memory.listEvidence({ subjectId: 'user-42' });
console.log(evidence[0]?.sourceKind); // spoken

core.close();
```

Creating a Core does not require a model configuration. Evidence storage and memory-management calls work without one; model-dependent calls fail only when invoked.

## Creating a Core

```text
createMemoWeftCore(options: CreateCoreOptions): MemoWeftCore
```

| Option         | Stability    | Meaning                                                                                                  |
| -------------- | ------------ | -------------------------------------------------------------------------------------------------------- |
| `dbPath`       | stable       | Required SQLite path. Use `:memory:` for an ephemeral store.                                             |
| `llm`          | experimental | An `LLMClient` or `LLMPool`. If omitted, MemoWeft reads its OpenAI-compatible environment configuration. |
| `embedder`     | experimental | Creates a vector retriever unless `retriever` is also supplied.                                          |
| `retriever`    | experimental | Highest-priority custom retrieval implementation. Caller-owned; `core.close()` does not close it.        |
| `config`       | experimental | MemoWeft configuration object. Omitted values use the package defaults.                                  |
| `vectorDbPath` | experimental | Vector-index database path; defaults to `dbPath`.                                                        |
| `clock`        | experimental | Injectable `() => Date` used for persistence timestamps and time-dependent rules.                        |
| `plugins`      | experimental | Plugin contracts and restricted hooks. See the [plugin contract](../plugin-contract.md).                 |

Without a custom retriever, MemoWeft chooses vector retrieval when an embedder is configured, otherwise local FTS5 keyword retrieval. If FTS5 is unavailable, recall degrades to an empty retriever instead of preventing Core creation.

Core owns and closes the SQLite stores and any vector or keyword retriever it created. Injected retrievers remain caller-owned.

## Core facade

### Ingestion

| Method                     | Persistent effects                                                                  | Model or network use | Notes                                                                                                                                           |
| -------------------------- | ----------------------------------------------------------------------------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `ingestUserMessage(input)` | Writes one evidence row; with `conversationId`, also maintains interaction context. | None.                | `sourceKind` defaults to `spoken`. `originId` provides idempotent evidence ingestion.                                                           |
| `ingestObservation(input)` | Writes zero or more `observed` evidence rows.                                       | None.                | Each observation may override authorization defaults. `kind` is an open string; `kind` and `meta` are accepted but are not currently persisted. |
| `ingestToolResult(input)`  | Writes one `tool` evidence row.                                                     | None.                | Stores the returned tool payload, not tool-call intent or arguments. `originId` is recommended for idempotency.                                 |

`observed` and `tool` evidence default to eligible for built-in local write-model prompts, ineligible for built-in cloud write-model prompts, and eligible for inference. An observation may override those flags at ingestion. `ToolResultInput` does not expose authorization overrides; use `core.memory.updateEvidenceAuthorization()` afterward. These flags do not restrict recall, list/read APIs, MCP tools, adapter injection, derived records, exports, logs, or custom host code.

### Recall and profile formation

| Method                                                   | Persistent effects                                                                                      | Model or network use                                                  | Failure behavior                                                                                                                        |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `recall({ query, subjectId?, explain?, contentTypes? })` | None.                                                                                                   | A vector retriever may call its embedder; keyword recall stays local. | Returns an empty array when no eligible result exists. Retriever failures degrade according to the configured retriever.                |
| `updateProfile({ subjectId? })`                          | Distills pending evidence, updates cognitions and semantic resolutions, then rebuilds the recall index. | Uses the write model and, when configured, the embedder.              | An index rebuild failure is returned in `indexError`; committed profile changes are not rolled back. Other failures reject the promise. |

Recall excludes invalid, archived, muted, and below-threshold cognitions. `contentTypes` is a post-filter over the retrieved top-K, so a filtered response may contain fewer than top-K items. With `explain: true`, each result may include provenance with evidence prompt-eligibility flags. Recall does not automatically suppress a derived cognition because one of its sources is ineligible for a cloud write prompt; the host must apply its own disclosure policy before forwarding the result.

`UpdateProfileResult` contains:

- `distilled`, `consolidated`, and `attributed` stage results;
- `indexed` and `indexError`;
- per-stage `timings` in milliseconds;
- `metrics.profileSize` and `metrics.promptChars`.

### Conversation helpers

| Method                                              | Behavior                                                                                                                                                                                                              |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handleConversationTurn(input)`                     | Stores the user message, recalls eligible memory, and calls the chat model. A `conversationId` reuses its active in-memory window. `systemPrompt` and `seedTurns` apply only when that conversation is first created. |
| `recordAssistantReply({ conversationId, content })` | Adds an assistant reply to an existing interaction window so a later user reply can be interpreted in context. It never creates evidence. An unknown conversation id is ignored.                                      |
| `dropConversation(conversationId)`                  | Drops the active in-memory conversation and interaction window. It does not delete stored memory. The next call may establish a new prompt and seed turns.                                                            |

Assistant text may be stored as interaction context, but it never receives an evidence id and cannot satisfy a cognition's provenance requirement.

### Diagnostics and lifecycle

| Method     | Behavior                                                                                                                                                                                                                                                                                                         |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `health()` | Returns `{ llmReady, embedReady }` for MemoWeft's built-in OpenAI-compatible client and vector retriever. Custom injected implementations may work while these flags remain `false`; the report is not a generic capability probe.                                                                               |
| `usage()`  | Returns cumulative `{ llm, embed, total }` token counters for the Core's owned clients. Each bucket has `promptTokens`, `completionTokens`, `totalTokens`, and `callsWithUsage`. Endpoints that omit usage produce no counted tokens; an injected retriever's embed usage is caller-owned and reports zero here. |
| `close()`  | Closes stores and retrievers owned by Core. Do not use the Core after closing it. Calling code remains responsible for injected retrievers.                                                                                                                                                                      |

## Controlled memory API

Use `core.memory`; applications should not write the SQLite tables directly.

### Read operations

| Method                           | Result                                                                    |
| -------------------------------- | ------------------------------------------------------------------------- |
| `listEvidence({ subjectId? })`   | All evidence for the subject.                                             |
| `listEvents({ subjectId? })`     | Events with their evidence ids.                                           |
| `listCognitions({ subjectId? })` | Cognitions with provenance links and read-time `effectiveConfidence`.     |
| `checkIntegrity()`               | A read-only report of orphan event/evidence and cognition/evidence links. |

### State, authorization, and deletion

| Method                               | Not-found or refusal behavior                                          | Notes                                                                                                            |
| ------------------------------------ | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `invalidateCognition(input)`         | Returns `null` when absent.                                            | Sets `invalidAt`; requires `reason`.                                                                             |
| `archiveCognition(input)`            | Returns `null` when absent.                                            | Sets `archivedAt`; requires `reason`.                                                                            |
| `muteCognition(input)`               | Returns `null` when absent.                                            | Mutes or unmutes recall without changing confidence; requires `reason`.                                          |
| `updateEvidenceAuthorization(input)` | Returns `null` when absent.                                            | Changes `allowCloudRead` and/or `allowInference`; no-op changes are not audited.                                 |
| `mergeCognition(input)`              | Throws for missing, cross-subject, invalid, or archived targets.       | Moves deduplicated provenance links, recomputes target confidence, and invalidates the source.                   |
| `removeEvidenceSafely(input)`        | Returns `{ removed: false, blockers }` when referenced and not forced. | `force: true` removes reference links in the same database transaction.                                          |
| `removeCognitionSafely(input)`       | Returns `removed: false` when absent.                                  | Removes the cognition and its links, not the underlying evidence.                                                |
| `resetSubject(input)`                | Returns removal counts.                                                | Destructive reset; `reason` is optional and is not retained because the subject's audit history is also removed. |

Successful management mutations are written to `management_log` with metadata and a reason, except `resetSubject`, which deliberately removes that log. Rejected and no-op operations do not create audit rows.

`resetSubject` transactionally removes the subject's evidence, events, cognitions, relationship rows, interaction contexts, semantic resolutions, and management audit rows. Its returned counts cover evidence, events, cognitions, and audit rows. Recall-index clearing happens afterward through `indexAll([])` and is not part of the database transaction; the method may return before an asynchronous external index finishes clearing.

MemoWeft's audit metadata does not retain the raw content of a deleted cognition.

## Portable bundles

```text
core.portable.exportBundle(options?)
core.portable.validateBundle(bundle)
core.portable.importBundle(bundle, { mode: 'dryRun' | 'merge' })
```

A bundle contains evidence, events, cognitions, provenance relationships, pending-event state, interaction contexts, and semantic resolutions for one subject. It preserves ids and timestamps. It excludes vector indexes, logs, API keys, environment files, and host UI state.

- `dryRun` validates and reports planned writes without modifying the database.
- `merge` imports transactionally through the Core facade and deduplicates by id and evidence `originId`.
- Bundle schema v2 imports schema v1 bundles with missing interaction sections treated as empty.
- There is no `replace` import mode in 0.6.x.

After an import, call `updateProfile()` when you want the retriever index rebuilt from the imported profile.

## Memory graph

```text
core.graph.buildMemoryGraph(options?): MemoryGraphPayload
```

The payload contains subject, evidence, event, and cognition nodes plus emitted `belongs_to_subject`, `distilled_into`, `supports`, and `contradicts` edges. `conflicts_with` and `corrects` are reserved edge values but are not emitted in 0.6.x because cognition-to-cognition links are not persisted.

## Behavioral guarantees

- Evidence and cognition are separate layers; a stored statement does not become a trusted belief automatically.
- Cognition confidence is an integer from 0 to 1000 computed by MemoWeft rules, not accepted from a model's self-report.
- Explicit corrections retain invalidated history. Unresolved contradictions remain visible instead of being silently overwritten.
- Built-in ingestion paths do not turn assistant replies into evidence. Context-dependent user replies can still be interpreted through a separate interaction-context record.
- `originId` makes supported ingestion paths idempotent for a given stored evidence record.
- In the default configuration, facts and preferences do not use the same time-decay policy as transient states and hypotheses.
- SQLite is not encrypted by MemoWeft. Hosts own consent, access control, deletion UX, backups, and encryption at rest.

## Errors and degradation

- Missing model configuration does not prevent Core creation; a model-dependent call rejects when it reaches the missing client.
- Missing embed configuration selects local keyword retrieval. Missing FTS5 degrades further to empty recall.
- Profile writes and index rebuilds are deliberately separated: an indexing error does not erase successful cognition updates.
- Management methods use `null`, result flags, or thrown errors as documented above; callers should not infer one universal not-found convention.
- `subjectId` defaults to `config.identity.subjectId` when omitted. Applications serving multiple users should pass it explicitly and enforce their own authorization boundary.

## Related references

- [Getting started](../getting-started.md)
- [Architecture](../internals/architecture.md)
- [Plugin contract](../plugin-contract.md)
- [Deployment and privacy](../deployment.md)
- [Changelog](../../CHANGELOG.md)
- [Type declarations](../../src/index.ts)
