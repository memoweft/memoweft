# Changelog

All notable changes to MemoWeft are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and semantic versioning. The public API surface is frozen as of the 1.0 release candidate — see [docs/STABILITY.md](docs/STABILITY.md) for the support tiers, the versioning rules, and the deprecation process.

## [Unreleased]

### Changed

- `@memoweft/adapter-ai-sdk@0.2.2` and `@memoweft/mcp-server@0.2.2` publish the already-reviewed Core peer range through 1.x, so the official integrations install cleanly with the 1.0 release candidates and final 1.x. Dedicated workspace tags now publish these packages through the same gated GitHub Actions provenance path as Core.

## [1.0.0-rc.2] — 2026-07-31

### Fixed

- Subject isolation now applies to the attribution time-window scan and active conversation/session caches. Attribution cannot place another subject's evidence in a prompt or hypothesis. A `conversationId` is permanently bound to one subject for the lifetime of a Core and cross-subject reuse is rejected, preventing a late subject-A assistant reply from entering subject B after an id reuse.
- Stable Core write methods now reject empty identity/conversation identifiers and impossible or timezone-less `occurredAt` values before persistence, so every accepted write remains portable through the stricter restore validator. Empty message content remains accepted.
- Stable `ConversationInput.episodeId` is now honored by `handleConversationTurn`, which records a visible interaction-context snapshot on every turn. Context deduplication is scoped to subject, conversation, episode, and content, so identical text in another identity or episode is not silently swallowed.
- Trend aggregation now requires every source of a derived state to belong to the subject, permit inference, and be readable by the selected model tier. Asking and conflict-review phrasing similarly falls back to a local template unless the complete provenance chain authorizes model processing, preventing a filtered evidence list from leaking the derived cognition text itself.
- Portable import now validates dates, enums, link relations, interaction records (including a recomputed canonical context hash), and semantic-resolution cardinality before writing. Same-id evidence, events, and cognitions count as idempotent only when their complete exported payload and owned provenance relationships match the target; any collision is fatal instead of allowing target authorization to legitimize unrelated bundle-derived content. Older backups cannot revive tombstoned evidence or derived records that depended on it; an existing semantic resolution remains the single result for its evidence.
- Forced evidence removal now deletes every event and cognition that references the evidence in the same transaction. Event summaries and cognition content are indivisible derived text, so clearing only links could leave deleted content visible through listing, graph output, portable export, or later built-in model paths. Other evidence from a removed multi-evidence event or cognition remains stored and pending for later distillation; an unforced removal remains a zero-change refusal. This retracts active memory and tombstones the source evidence for audit; it does not claim per-row physical erasure. `interaction_context` has no evidence id and is intentionally outside this targeted operation; use `resetSubject` for the complete subject-level privacy clear. Schema v2 applies the same fail-closed rule to rc.1 `evidence_retraction` records, deleting their affected cognitions and relation rows transactionally before the old content can be listed, graphed, or exported.
- Transaction depth is entered only after SQLite `BEGIN` succeeds in both the TypeScript and Python store helpers. A rejected `BEGIN` (for example, while an external transaction is open) no longer leaves the helper poisoned so a later callback executes outside a rollback boundary.
- `core.close()` now clears all in-memory conversation windows and permanent subject bindings before closing owned resources, rejects later conversation ingestion/reply capture, and remains idempotent.

## [1.0.0-rc.1] — 2026-07-28

First release candidate for 1.0. Nothing new is added here beyond the surface work below: 0.9 was the last feature version, and this candidate exists so the frozen API can be exercised before 1.0 is tagged. The supported integration path — `createMemoWeftCore()` and the facade around it — is unchanged from 0.7.0, and no schema migration is involved.

What "frozen" means concretely: every public export now carries a documented support tier ([api-surface.md](docs/reference/api-surface.md)), the policy governing those tiers is written down ([STABILITY.md](docs/STABILITY.md)), and `tests/api/api-surface.snapshot` fails CI on any unreviewed change to the export set. Experimental symbols stay experimental at 1.0 — including both A5 contradiction passes — because promoting one later is additive and non-breaking, while freezing it early is a commitment that can only be undone in a major.

Please report anything that looks like it still needs to change before 1.0; that is what the candidate period is for.

### Added

- `core.reconcileContradictions({ subjectId })` (experimental, default off) adds a second-pass A5 backstop that scans the whole stored profile for contradictory cognitions that coexist, complementing the first-pass `contradictionGuard` in consolidation. The first-pass guard only inspects the `new` candidates the model emits in a single `updateProfile` round, so a stance reversal that arrives across rounds — or is squeezed out of a candidate's top-`K` shortlist once the profile grows — still lands two opposite active cognitions in the store, invisible to every "conflict stays visible" surface (`credStatus`, the memory graph, `revisitConflicts`). `reconcileContradictions` takes the already-stored `active` cognitions, clusters same-topic ones by embedding cosine (connected components at or above `minSimilarity`, default `0.5`), runs the same polarity judge on each within-cluster pair, and on a hit attaches the later cognition's support evidence as `contradict` to the earlier one (the anchor = the earlier `updatedAt`, treated as the prior stance), recomputing confidence and deriving `credStatus` to `conflicted`/`contested` — the exact `attachContradiction` landing the first-pass guard and a model-flagged `conflict` already take, now shared through one `contradiction` single-source-of-truth module per language so the two paths cannot drift. It never creates an opposite row, never deletes, and never picks a winner; conflict is surfaced, not resolved. Like `expire`/`aggregateTrends` it is a deliberate standalone maintenance entry decoupled from the write path — a host calls it on its own cadence — and returns `{ scanned, pairsJudged, conflictsAttached, llmCalls }` for cost observability. It needs an embedder to judge same-topic similarity; the facade logs a warning and no-ops when none is available. The mechanism was validated on gpt-4o across 33 bilingual scenarios over three rounds: residual coexisting contradictions dropped from 35% to under 1.7%, with a real net-misjudgement rate of ~1.5% (the polarity judge's floor, not a structural gap). Purely additive to the public API with no behavior change on any existing path, so existing evaluations are unaffected, and it does not touch the schema. Implemented identically in the TypeScript and Python packages, sharing one `contradiction` module and `attachContradiction` helper per language; the current pass is a full-profile scan, with incremental change-set scanning left as a later cost optimization. The deterministic clustering (`clusterByCosine`, connected components) is pinned byte-exact across the two languages by a shared parity fixture (`shared/parity/reconcile.json`); the polarity judge is an LLM call and stays out of the fixture, same as the first-pass guard.

### Changed

- **Root export surface narrowed toward the 1.0 freeze (breaking only for code importing internals from the package root).** The 1.0 API surface review kept every experimental symbol experimental and left the supported facade untouched, but removed a set of purely-internal building blocks from the root re-export to shrink the accidental-dependency surface: the six SQLite store implementation classes (`SqliteEvidenceStore`, `SqliteEventStore`, `SqliteCognitionStore`, `SqliteInteractionContextStore`, `SqliteSemanticResolutionStore`, `SqliteManagementLog`), the JSON-repair helpers (`extractJsonObject`, `parseJsonObject`, `parseJsonObjectWithRepair`, `ParseWithRepairDeps`), and `noopTransaction`. These had no external consumers, appear in no exported stable/experimental signature, and were always documented as unsupported ("export ≠ support"); the facade (`createMemoWeftCore`) and every supported path are unchanged. Note that because the package declares a single `"."` entry point, this removes them outright for installed consumers rather than demoting them to a deep-import path — permitted by the internal tier, but stated plainly here rather than left to be discovered. The store interface _types_ (`EvidenceStore`, `EventStore`, `CognitionStore`, `CognitionPatch`, `InteractionContextStore`, `SemanticResolutionStore`, `ManagementLog`) and `Transaction` stay root-exported because the experimental `StoreBundle` references them.
- Clarified the stable API surface for the 1.0 freeze so no stable symbol depends on a non-frozen shape (documentation/classification only — no runtime behavior or schema change): `BuildGraphOptions` is now stable (it is part of the stable `core.graph.buildMemoryGraph` signature); `createMemoryManagementAPI` is now experimental (its signature takes the experimental `StoreBundle` and internal deps — hosts use `core.memory`); and `UpdateProfileResult`'s stage payloads (`distilled`/`consolidated`/`attributed`/`timings`), `Observation`'s `kind`/`meta`, and the graph's reserved `conflicts_with`/`corrects` edges are now documented as experimental within their otherwise-stable enclosing shapes.

## [0.7.0] — 2026-07-25

### Added

- `core.expire({ subjectId })` wires the natural-expiry background operator into the facade so a host can actually run it. The `expire` algorithm — marking transient cognitions (`state`, `hypothesis`, `trend`) `invalidAt` once they age past `config.background.expireAfterDays`, while stable classes (`preference`, `fact`, …) never auto-expire — already existed and was covered by tests, but had no production call site: nothing on `MemoWeftCore` invoked it, so transient memory accumulated indefinitely and kept participating in recall. The new method is a deliberate standalone maintenance entry rather than a side effect of `updateProfile`, so expiry stays decoupled from profile writes, is idempotent (a re-run finds nothing new, since `active()` already excludes invalidated rows), and lets the host choose its own cadence (daily, or after a profile update). It reuses the same `subjectId` normalization and injected clock as every other facade method — failing to thread `subjectId` through would silently expire nothing or the wrong subject. Expiry marks `invalidAt` and retains the row for provenance: it is invalidation, not deletion. Purely additive to the public API with no behavior change on any existing path, so existing evaluations are unaffected; the `expire` operator and its parity fixture (`shared/parity/expire.json`) are unchanged, and the Python package continues to expose the operator directly with no facade layer.
- `core.aggregateTrends({ subjectId })` wires the cross-session trend operator into the facade, mirroring `core.expire()`. The operator — collecting `state` cognitions whose support evidence falls inside the `trendWindowDays` window, requiring at least `trendMinCount` occurrences (rule-gated on objective repetition, not model guessing) before asking the write model to name the recurring pattern as a `ruled` `trend` cognition — already existed and was tested, but had no production call site: nothing on `MemoWeftCore` invoked it, so `trend` had no producer and `trendWindowDays`/`trendMinCount` were dead configuration. Like `expire`, it is a deliberate standalone maintenance entry decoupled from `updateProfile`, reuses the same `subjectId` normalization, injected clock, and write-path model, and returns `{ trends, consideredCount, llmCalls }`. Purely additive to the public API with no behavior change on any existing path, so existing evaluations are unaffected. After producing new trends it rebuilds the subject's recall index (matching `updateProfile`: active, non-muted cognitions), so a freshly aggregated trend is retrievable through `recall` instead of remaining invisible until the next profile update; index failures are logged and do not fail the maintenance call. The operator takes an optional `retriever` — the facade always passes one, and retrieval is TypeScript-only, so the Python package continues to expose the operator directly with no facade layer.
- `CognitionExplanation.expiredCount` reports how many of a cognition's evidence pieces have been retracted (soft-deleted). Retracting a piece of evidence still clears its active provenance link, so the cognition's confidence drops and the item leaves `provenance` exactly as before; the retraction is additionally recorded in a separate `evidence_retraction` ledger, so `explainCognition` can surface a count of retracted evidence without touching the confidence path or `sourcesOf`. `recall({ explain: true })` provenance is unchanged. This completes the soft-delete work and aligns with the BMB `expired_count` field.
- `core.explainCognition({ cognitionId })` returns one cognition together with its full provenance chain, addressed by id rather than by similarity. `recall({ explain: true })` only attaches provenance to whatever a query happens to retrieve, so asking why a _specific_ remembered item is held — what a confirmation prompt or a memory-management page needs — was not expressible. Provenance shape, authorization flags, and dangling-link handling are identical to `recall({ explain: true })`; both paths now share one enrichment routine. Reads only, writes no audit entry, returns `null` for an unknown id or a subject mismatch. Cognitions that are invalidated, archived, or muted are still explained, with their state reported — recall gating would make the API return `null` in precisely the case a user is most likely to ask about.
- A `contested` credibility status for cognitions that carry opposing evidence but remain supported by a majority of their evidence. Previously any single piece of opposing evidence produced `conflicted`, so a cognition with six supporting and one opposing item was indistinguishable from one split evenly. Confidence already reflected the difference; the status flattened it, and the periodic review flow kept surfacing well-supported cognitions to the user. `deriveCredStatus` takes an optional `supportCount`: `contested` when support outnumbers opposition, `conflicted` otherwise. Omitting it preserves the previous conservative behavior, since a caller that does not know the support count cannot assume support prevails. The threshold is deliberately a count comparison rather than a confidence cutoff — support scoring caps at 200, so a `stated` cognition with six supporting and one opposing item reaches only 680 and can never clear the 750 `stable` threshold.
- `MemoryGraphStats.contestedCount`, counted separately from `conflictedCount` so the two are not conflated. `onlyConflicts` returns both, because these cognitions were `conflicted` before this change and would otherwise vanish from that view.
- A confidence cap for hedged self-reports. "I might not be very good at cooking" and "I am a vegetarian" previously scored identically — both `stated`, both 600 — while nodding along to an AI's guess about the same hedged claim scored 280 as `confirmed`. That inverted the intended conservatism: a hedged self-report has not even fixed the boundaries of its own proposition, yet outranked a proposition the assistant had already pinned down. The model was in fact labelling these correctly all along (`assertion_strength=weak`); `deriveFormedBy` simply could not see the field, because its `CarrierInput` carries only the two dimensions the provenance carrier needs. Rather than widening that type, hedging is treated as what it is — orthogonal to provenance. `deriveFormedBy` is untouched and still answers "whose words is this"; the new `isHedgedStated` answers "how firmly was it said"; and the capping itself lives in `computeConfidence` as `min(hedgeCap)`, alongside the existing transient cap. Both caps use `min`, so they only ever lower a score and compose freely — a hedged `state` lands at 280, and an `inferred` cognition stays at 200 rather than being lifted to the cap. A cognition counts as hedged when it is `stated` and, among the evidence the user volunteered, none is `explicit` and at least one is `weak`; `none` deliberately does not trigger the cap, since its measured occurrence rate is zero and capping on it would only produce false positives. `ConfidenceInputs.hedged` is optional, so every existing call site and every pre-existing parity fixture behaves bit-for-bit as before. All eight `computeConfidence` call sites are wired, which matters because the flag is never persisted: it is re-derived from the evidence chain on every recomputation, so missing even one would let a capped cognition silently rebound to 600 the next time a user edits its evidence. `hedgeCap` is configurable and defaults to 280, matching the `confirmed` base so the two paths to the same hedged claim now agree.
- `ConsolidateResult.contentTypeFallback` counts how often a cognition's `content_type` was decided by the fallback rather than by the model, split by cause: `missing`, `invalid`, and `outOfScope`. Consolidation silently rewrites any unrecognized `content_type` to `fact`, and `fact` happens to be the most durable class there is — absent from both `halfLifeDays` and `expireAfterDays`, and exempt from `transientCap` — so the fallback always resolves toward permanence. The three causes are not equally serious and were previously indistinguishable. `outOfScope` covers `hypothesis` and `trend`, which are valid `ContentType` values that consolidation does not accept; a model may still emit them, since the existing profile is fed back into the prompt carrying those very labels. That case is a semantic downgrade rather than a typo: a guess that should have been capped at `hypothesisCap`, decayed on a two-day half-life, and queued for `proposeAsk` verification instead becomes a permanent fact and leaves the queue for good. Only fallbacks on cognitions that are actually persisted are counted, so the number reads as "how many stored cognitions had their type decided by the fallback". Measurement only — the fallback behavior is unchanged, and this is deliberately a prerequisite for deciding whether it should change.
- `core.memory.reinforceCognition({ cognitionId, evidenceId, relation, reason })` attaches an existing piece of evidence to a cognition and recomputes its confidence and credibility status in the same transaction. Until now every path that altered a provenance chain ran inside the library, leaving a host no way to record that a user had just confirmed or rejected a remembered item — which is what a confirmation prompt is for. `relation` defaults to `support` and accepts `contradict`, so a rejection lands as counter-evidence rather than as a deletion. Re-attaching the same evidence and relation is idempotent: no link, no recompute, no audit entry, so repeated clicks cannot inflate confidence. The call refuses an unknown cognition or evidence, a subject mismatch, and cognitions that are invalidated or archived, whose confidence is a historical snapshot. It deliberately does not ingest new evidence; that remains the job of the ingest paths, so no second write path bypasses perception.
- `createMemoWeftCore({ contradictionGuard })` (optional, default off) enables a similarity-plus-polarity backstop against contradictory profiles silently coexisting (A5); pass `true` or `{ minSimilarity?, topK? }` and the core reuses its configured embedder, threading the guard through `updateProfile` into consolidation (if no embedder is available it logs a warning and stays off — the guard needs embeddings). The underlying `ConsolidateDeps.contradictionGuard` is also accepted by `consolidate` directly. Consolidation's `new` branch wrote a cognition after checking only that its evidence was traceable, never comparing it against the existing profile for a semantically similar but opposite claim; when the model routed a stance reversal to `new` instead of `conflict`/`correct`, the store ended up holding two opposite active cognitions, both unflagged and invisible to every "conflict stays visible" surface (`credStatus`, the memory graph, `revisitConflicts`). Dogfood on gpt-4o measured this at roughly 25–40% of seeded stance reversals, so it is not a weak-model artifact. When a host injects `{ embedder }`, consolidation now — before inserting each `new` candidate — embeds it against the in-memory active profile, shortlists same-topic cognitions by cosine (at or above `minSimilarity`, default `0.5`, top `topK` default `3`), and asks the write model a focused yes/no polarity question on the few survivors; a candidate judged to reverse an existing cognition is not inserted as an opposite row but attached to that cognition as `contradict` evidence, with confidence recomputed and `credStatus` derived to `conflicted`/`contested` — the same landing a model-flagged `conflict` takes, now shared through one `attachContradiction` helper so the earlier "recompute, don't just rewrite `credStatus`" fix covers both paths. A purely deterministic guard was not achievable: cosine measures topical relatedness and is blind to negation, so the polarity half needs one model call, and similarity alone would misfire on same-topic reinforcement. The dependency is optional and defaults off, so every existing call site, parity fixture, and evaluation behaves bit-for-bit as before; an embedder failure logs and skips the guard for that round rather than blocking consolidation. Implemented identically in the TypeScript and Python `consolidate` paths, sharing one `attachContradiction` helper per language. Because the dependency defaults off, guard-off behavior stays byte-for-byte identical across the two languages under the existing shared `consolidate` parity fixture; guard-on routing is pinned by mirrored behavioral tests in both languages that assert the same fire/no-fire outcome on the same toy-embedder inputs (reversal fires, different-topic and same-topic-non-contradiction do not). Cosine alone does not separate contradiction from compatibility (compatible mean 0.73 vs contradictory 0.71 on labeled pairs), confirming the polarity check carries the discrimination and the similarity threshold is only a cost gate; `0.6` clipped genuine contradictions with cosine as low as 0.571, so the default is `0.5`. The polarity prompt was tuned against real consolidation output, not hand-written pairs: an initial version scored 93.8% recall / 0% false-positive on 95 crisp labeled pairs but, when a full guard-on dogfood was run, caught only ~32% of the elaborated contradictions the pipeline actually produces (e.g. "dislikes running but chose it as an affordable option" vs "has developed a genuine love for running") — the clean pairs were unrepresentative. The prompt was rewritten to name preference/attitude/goal/factual reversals explicitly and to look past justifying clauses and evolution wording; re-measured on 31 real coexisting contradictions drawn from pipeline output plus 47 compatible pairs, it reaches 93.5% recall [79.3, 98.2] at 0% false-positive [0.0, 7.6], while still not flagging refinements or different-facet statements. Measured end-to-end on the same seeded dogfood scenarios, enabling the guard roughly halves coexisting A5 contradictions (16/16 → 8–9 of 16, ~40% → ~22%) at 0% false invalidation of compatible cognitions — a partial mitigation rather than a complete fix, since scenarios carrying several contradiction lines still leave derived secondary reversals unflagged once the primary one is caught. The guard's polarity calls (and any JSON-repair retries) are included in the returned `ConsolidateResult.llmCalls`, snapshotted after the guard rather than only after the main consolidation request. Per candidate, the guard compares against at most `topK` similar existing cognitions and `topK` earlier same-batch candidates (the first hit short-circuits the second), so the per-candidate polarity budget is up to `2 × topK`.

### Changed

- Evidence deletion is now a soft delete (tombstone) rather than a physical row removal, so the "corrections and deletion retain an auditable data path" invariant holds. `EvidenceStore.remove` sets a `deleted_at` marker and nulls the row's `origin_id` while keeping the original content; every read (`get`, `all`, `byTimeRange`, `findByOrigin`, `precedingAiContextOf`) excludes tombstoned rows, so retracted evidence no longer reaches recall or consolidation, yet the record survives for audit. Re-ingesting the same `originId` after a soft delete is allowed, because the tombstone's `originId` is cleared and no longer occupies the idempotency unique index. Two explicit hard-erase operations are added for privacy erasure and factory reset — `EvidenceStore.purge(id)` and `EvidenceStore.purgeBySubject(subjectId)` physically remove rows including tombstones — and `resetSubject` now purges rather than soft-deleting so a factory reset leaves no residue. Enforced identically in the TypeScript and Python packages. Confidence recompute is deliberately untouched: `removeEvidenceSafely` still clears the active provenance links, so a cognition's confidence drops exactly as before when its evidence is retracted.
- Consolidating a conflict in the Python package now recomputes confidence from the resulting evidence chain, matching the TypeScript path. The Python port previously only wrote the credibility status, so `contradictPenalty` never took effect there and a refuted cognition kept the confidence it had with no opposition at all.
- Simplified the public repository surface, documentation, contribution flow, and release presentation.
- `@memoweft/mcp-server` and `@memoweft/adapter-ai-sdk` `0.2.1` widen their `memoweft` peer range to `^0.5.1 || ^0.6.0 || ^0.7.0` so they resolve against Core `0.7.0`; the not-yet-published integration adapters declare `^0.7.0` support in-repo as well. The MCP tool and registry contracts are unchanged.

### Fixed

- Recall now over-fetches its candidate pool before gating instead of after, so gate rejections no longer thin the result below `topK`. It fetched exactly `topK` candidates and then applied six gates (similarity, invalidation, archival, muting, subject isolation, decayed confidence), so any candidate a gate removed simply shrank the result — if the top `topK` were all gated out, recall returned nothing even when the store held qualifying cognitions ranked just below. It now fetches `topK × retrieval.overfetchFactor` candidates (default factor `4`) and stops once `topK` survive the gates, refilling emptied slots from further down the ranking. The new `retrieval.overfetchFactor` is configurable; setting it to `1` restores the previous fetch-then-gate behavior. Gate order and conditions are unchanged, and both `recall` and the conversation reply path benefit. Retrieval is a TypeScript-only path, so there is no Python change.
- `validateBundle` now rejects cognitions whose `content_type`, `formed_by`, or `cred_status` is not a known enum value, and whose `confidence` is not an integer in 0–1000. It previously validated only structure, ids, and referential integrity, and `importBundle` trusts a `valid` result and inserts rows directly — so a bundle with an out-of-range value passed straight into the database. The `cognition` table has no `CHECK` constraints on those columns and SQLite's type affinity does not stop a string from landing in the `INTEGER` `confidence` column, so the bad value persisted silently. An out-of-range `formed_by` was the worst case: it imported cleanly but became a delayed fault, because the next `computeConfidence` recomputation looks up `baseByFormedBy[formedBy]`, gets `undefined`, produces `NaN`, and then fails the whole recomputation when `NaN` cannot be written back to the `NOT NULL` column. These values come from an external file and never pass through the consolidation write path, which is the only place that falls back to `fact` for an unrecognized type — so this validator is the sole guard. `content_type` accepts all eight values including `hypothesis` and `trend`, which `attribute` and `trends` can produce and persist, rather than only the six that consolidation accepts. Enforced identically in the TypeScript and Python packages against a shared parity fixture.

## [0.6.0] — 2026-07-18

### Added

- Interaction context and semantic resolution records for context-dependent replies such as “yes”, “no”, and “the latter”.
- Code-derived formation modes for user-stated, user-confirmed, observed, and ruled cognitions. `inferred` remains model-reported by design: it encodes distance from the utterance rather than who carried it, and the risks are asymmetric — overstating a confirmed cognition as stated inflates confidence, whereas self-reporting "I inferred this" only under-reports.
- Conversation-context support, enabled by a `conversationId` parameter, in the OpenAI Agents and LangChain integrations.
- A Mastra processor integration and LangChain v1 middleware.
- A Python parity package covering confidence, formation mode, decay, storage, FTS, and portable-bundle interoperability.
- Language-neutral shared fixtures that verify TypeScript and Python behavior against the same source data.
- Diagnostics for model-produced evidence identifiers that cannot be matched safely.

### Changed

- The LlamaIndex.TS integration is frozen for existing users because the upstream project is archived. New projects should prefer a maintained integration.
- Short replies are resolved into structured context before cognition formation mode is derived.

### Fixed

- Evidence identifiers truncated by a model can be resolved only when the prefix is unique, sufficiently long, and still belongs to the permitted evidence set.
- Deleting evidence or performing a factory reset now clears associated semantic-resolution and interaction-context data.

## [0.5.1] — 2026-07-15

### Added

- Keyword recall as the default no-embedder path.
- Source-aware consolidation and the `confirmed` cognition formation mode.
- Integrations for Claude Agent SDK, OpenAI Agents SDK, LangChain, and LlamaIndex.TS.
- Recall provenance with `explain`, content-type filtering, and cognition muting.
- Injectable clocks for deterministic tests and typed-decay demonstrations.
- Tool-result evidence ingestion with local-only privacy defaults.
- Consistent timeout, retry, and degradation behavior for the AI SDK and MCP integrations.

### Changed

- The default profile-update batch size increased from 5 to 12.
- Documentation was reorganized around concepts, recipes, API stability, and runnable examples.

## [0.5.0] — 2026-07-06

### Added

- Local and cloud model tiers for write-path privacy.
- Plugin contract v2 with lifecycle hooks and controlled context.
- Token-usage accounting for OpenAI-compatible clients.
- `@memoweft/mcp-server` with controlled recall and memory-management tools.
- `@memoweft/adapter-ai-sdk` for Vercel AI SDK middleware and persistence.

### Fixed

- Evidence is marked covered only after it is actually distilled.
- `allowInference` is enforced consistently across distillation, consolidation, and attribution.

## [0.4.0] — 2026-07-05

### Added

- English and Chinese output configuration.
- Configurable model temperatures and reasoning-response compatibility.
- A documented Memory Surface Contract for stable, experimental, and internal APIs.
- Memory-management and portable-bundle examples.

### Changed

- English became the default output language.
- The default host identifier changed from `testbench` to `local`.

## [0.3.0] — 2026-07-05

### Added

- Optional `better-sqlite3` support for Node 20 and 22.
- SQLite busy-timeout handling for concurrent local processes.

### Changed

- The supported Node range expanded from Node 24 to Node 20 and newer.

## [0.2.0] — 2026-07

### Added

- Ordered SQLite schema migrations and `PRAGMA user_version` tracking.
- Downgrade protection for databases created by newer MemoWeft versions.

## [0.1.0] — 2026-07

### Added

- Evidence, event, and cognition layers with profile consolidation and recall.
- The `createMemoWeftCore` facade and controlled memory-management API.
- Versioned portable bundles and memory-graph output.
- Cloud-read filtering for model-facing write paths.
- A local reference host and active-window collector plugin.

[1.0.0-rc.1]: https://github.com/memoweft/memoweft/compare/v0.7.0...v1.0.0-rc.1
[0.7.0]: https://github.com/memoweft/memoweft/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/memoweft/memoweft/tree/v0.6.0
[0.5.1]: https://github.com/memoweft/memoweft/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/memoweft/memoweft/releases/tag/v0.5.0
[0.4.0]: https://github.com/memoweft/memoweft/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/memoweft/memoweft/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/memoweft/memoweft/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/memoweft/memoweft/tree/v0.1.0
