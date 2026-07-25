# Changelog

All notable changes to MemoWeft are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses semantic versioning while public APIs remain pre-1.0.

## [Unreleased]

### Added

- `core.expire({ subjectId })` wires the natural-expiry background operator into the facade so a host can actually run it. The `expire` algorithm — marking transient cognitions (`state`, `hypothesis`, `trend`) `invalidAt` once they age past `config.background.expireAfterDays`, while stable classes (`preference`, `fact`, …) never auto-expire — already existed and was covered by tests, but had no production call site: nothing on `MemoWeftCore` invoked it, so transient memory accumulated indefinitely and kept participating in recall. The new method is a deliberate standalone maintenance entry rather than a side effect of `updateProfile`, so expiry stays decoupled from profile writes, is idempotent (a re-run finds nothing new, since `active()` already excludes invalidated rows), and lets the host choose its own cadence (daily, or after a profile update). It reuses the same `subjectId` normalization and injected clock as every other facade method — failing to thread `subjectId` through would silently expire nothing or the wrong subject. Expiry marks `invalidAt` and retains the row for provenance: it is invalidation, not deletion. Purely additive to the public API with no behavior change on any existing path, so existing evaluations are unaffected; the `expire` operator and its parity fixture (`shared/parity/expire.json`) are unchanged, and the Python package continues to expose the operator directly with no facade layer.
- `CognitionExplanation.expiredCount` reports how many of a cognition's evidence pieces have been retracted (soft-deleted). Retracting a piece of evidence still clears its active provenance link, so the cognition's confidence drops and the item leaves `provenance` exactly as before; the retraction is additionally recorded in a separate `evidence_retraction` ledger, so `explainCognition` can surface a count of retracted evidence without touching the confidence path or `sourcesOf`. `recall({ explain: true })` provenance is unchanged. This completes the soft-delete work and aligns with the BMB `expired_count` field.
- `core.explainCognition({ cognitionId })` returns one cognition together with its full provenance chain, addressed by id rather than by similarity. `recall({ explain: true })` only attaches provenance to whatever a query happens to retrieve, so asking why a _specific_ remembered item is held — what a confirmation prompt or a memory-management page needs — was not expressible. Provenance shape, authorization flags, and dangling-link handling are identical to `recall({ explain: true })`; both paths now share one enrichment routine. Reads only, writes no audit entry, returns `null` for an unknown id or a subject mismatch. Cognitions that are invalidated, archived, or muted are still explained, with their state reported — recall gating would make the API return `null` in precisely the case a user is most likely to ask about.
- A `contested` credibility status for cognitions that carry opposing evidence but remain supported by a majority of their evidence. Previously any single piece of opposing evidence produced `conflicted`, so a cognition with six supporting and one opposing item was indistinguishable from one split evenly. Confidence already reflected the difference; the status flattened it, and the periodic review flow kept surfacing well-supported cognitions to the user. `deriveCredStatus` takes an optional `supportCount`: `contested` when support outnumbers opposition, `conflicted` otherwise. Omitting it preserves the previous conservative behavior, since a caller that does not know the support count cannot assume support prevails. The threshold is deliberately a count comparison rather than a confidence cutoff — support scoring caps at 200, so a `stated` cognition with six supporting and one opposing item reaches only 680 and can never clear the 750 `stable` threshold.
- `MemoryGraphStats.contestedCount`, counted separately from `conflictedCount` so the two are not conflated. `onlyConflicts` returns both, because these cognitions were `conflicted` before this change and would otherwise vanish from that view.
- A confidence cap for hedged self-reports. "I might not be very good at cooking" and "I am a vegetarian" previously scored identically — both `stated`, both 600 — while nodding along to an AI's guess about the same hedged claim scored 280 as `confirmed`. That inverted the intended conservatism: a hedged self-report has not even fixed the boundaries of its own proposition, yet outranked a proposition the assistant had already pinned down. The model was in fact labelling these correctly all along (`assertion_strength=weak`); `deriveFormedBy` simply could not see the field, because its `CarrierInput` carries only the two dimensions the provenance carrier needs. Rather than widening that type, hedging is treated as what it is — orthogonal to provenance. `deriveFormedBy` is untouched and still answers "whose words is this"; the new `isHedgedStated` answers "how firmly was it said"; and the capping itself lives in `computeConfidence` as `min(hedgeCap)`, alongside the existing transient cap. Both caps use `min`, so they only ever lower a score and compose freely — a hedged `state` lands at 280, and an `inferred` cognition stays at 200 rather than being lifted to the cap. A cognition counts as hedged when it is `stated` and, among the evidence the user volunteered, none is `explicit` and at least one is `weak`; `none` deliberately does not trigger the cap, since its measured occurrence rate is zero and capping on it would only produce false positives. `ConfidenceInputs.hedged` is optional, so every existing call site and every pre-existing parity fixture behaves bit-for-bit as before. All eight `computeConfidence` call sites are wired, which matters because the flag is never persisted: it is re-derived from the evidence chain on every recomputation, so missing even one would let a capped cognition silently rebound to 600 the next time a user edits its evidence. `hedgeCap` is configurable and defaults to 280, matching the `confirmed` base so the two paths to the same hedged claim now agree.
- `ConsolidateResult.contentTypeFallback` counts how often a cognition's `content_type` was decided by the fallback rather than by the model, split by cause: `missing`, `invalid`, and `outOfScope`. Consolidation silently rewrites any unrecognized `content_type` to `fact`, and `fact` happens to be the most durable class there is — absent from both `halfLifeDays` and `expireAfterDays`, and exempt from `transientCap` — so the fallback always resolves toward permanence. The three causes are not equally serious and were previously indistinguishable. `outOfScope` covers `hypothesis` and `trend`, which are valid `ContentType` values that consolidation does not accept; a model may still emit them, since the existing profile is fed back into the prompt carrying those very labels. That case is a semantic downgrade rather than a typo: a guess that should have been capped at `hypothesisCap`, decayed on a two-day half-life, and queued for `proposeAsk` verification instead becomes a permanent fact and leaves the queue for good. Only fallbacks on cognitions that are actually persisted are counted, so the number reads as "how many stored cognitions had their type decided by the fallback". Measurement only — the fallback behavior is unchanged, and this is deliberately a prerequisite for deciding whether it should change.
- `core.memory.reinforceCognition({ cognitionId, evidenceId, relation, reason })` attaches an existing piece of evidence to a cognition and recomputes its confidence and credibility status in the same transaction. Until now every path that altered a provenance chain ran inside the library, leaving a host no way to record that a user had just confirmed or rejected a remembered item — which is what a confirmation prompt is for. `relation` defaults to `support` and accepts `contradict`, so a rejection lands as counter-evidence rather than as a deletion. Re-attaching the same evidence and relation is idempotent: no link, no recompute, no audit entry, so repeated clicks cannot inflate confidence. The call refuses an unknown cognition or evidence, a subject mismatch, and cognitions that are invalidated or archived, whose confidence is a historical snapshot. It deliberately does not ingest new evidence; that remains the job of the ingest paths, so no second write path bypasses perception.
- `createMemoWeftCore({ contradictionGuard })` (optional, default off) enables a similarity-plus-polarity backstop against contradictory profiles silently coexisting (A5); pass `true` or `{ minSimilarity?, topK? }` and the core reuses its configured embedder, threading the guard through `updateProfile` into consolidation (if no embedder is available it logs a warning and stays off — the guard needs embeddings). The underlying `ConsolidateDeps.contradictionGuard` is also accepted by `consolidate` directly. Consolidation's `new` branch wrote a cognition after checking only that its evidence was traceable, never comparing it against the existing profile for a semantically similar but opposite claim; when the model routed a stance reversal to `new` instead of `conflict`/`correct`, the store ended up holding two opposite active cognitions, both unflagged and invisible to every "conflict stays visible" surface (`credStatus`, the memory graph, `revisitConflicts`). Dogfood on gpt-4o measured this at roughly 25–40% of seeded stance reversals, so it is not a weak-model artifact. When a host injects `{ embedder }`, consolidation now — before inserting each `new` candidate — embeds it against the in-memory active profile, shortlists same-topic cognitions by cosine (at or above `minSimilarity`, default `0.5`, top `topK` default `3`), and asks the write model a focused yes/no polarity question on the few survivors; a candidate judged to reverse an existing cognition is not inserted as an opposite row but attached to that cognition as `contradict` evidence, with confidence recomputed and `credStatus` derived to `conflicted`/`contested` — the same landing a model-flagged `conflict` takes, now shared through one `attachContradiction` helper so the earlier "recompute, don't just rewrite `credStatus`" fix covers both paths. A purely deterministic guard was not achievable: cosine measures topical relatedness and is blind to negation, so the polarity half needs one model call, and similarity alone would misfire on same-topic reinforcement. The dependency is optional and defaults off, so every existing call site, parity fixture, and evaluation behaves bit-for-bit as before; an embedder failure logs and skips the guard for that round rather than blocking consolidation. Implemented identically in the TypeScript and Python `consolidate` paths, sharing one `attachContradiction` helper per language. Because the dependency defaults off, guard-off behavior stays byte-for-byte identical across the two languages under the existing shared `consolidate` parity fixture; guard-on routing is pinned by mirrored behavioral tests in both languages that assert the same fire/no-fire outcome on the same toy-embedder inputs (reversal fires, different-topic and same-topic-non-contradiction do not). Cosine alone does not separate contradiction from compatibility (compatible mean 0.73 vs contradictory 0.71 on labeled pairs), confirming the polarity check carries the discrimination and the similarity threshold is only a cost gate; `0.6` clipped genuine contradictions with cosine as low as 0.571, so the default is `0.5`. The polarity prompt was tuned against real consolidation output, not hand-written pairs: an initial version scored 93.8% recall / 0% false-positive on 95 crisp labeled pairs but, when a full guard-on dogfood was run, caught only ~32% of the elaborated contradictions the pipeline actually produces (e.g. "dislikes running but chose it as an affordable option" vs "has developed a genuine love for running") — the clean pairs were unrepresentative. The prompt was rewritten to name preference/attitude/goal/factual reversals explicitly and to look past justifying clauses and evolution wording; re-measured on 31 real coexisting contradictions drawn from pipeline output plus 47 compatible pairs, it reaches 93.5% recall [79.3, 98.2] at 0% false-positive [0.0, 7.6], while still not flagging refinements or different-facet statements.

### Changed

- Evidence deletion is now a soft delete (tombstone) rather than a physical row removal, so the "corrections and deletion retain an auditable data path" invariant holds. `EvidenceStore.remove` sets a `deleted_at` marker and nulls the row's `origin_id` while keeping the original content; every read (`get`, `all`, `byTimeRange`, `findByOrigin`, `precedingAiContextOf`) excludes tombstoned rows, so retracted evidence no longer reaches recall or consolidation, yet the record survives for audit. Re-ingesting the same `originId` after a soft delete is allowed, because the tombstone's `originId` is cleared and no longer occupies the idempotency unique index. Two explicit hard-erase operations are added for privacy erasure and factory reset — `EvidenceStore.purge(id)` and `EvidenceStore.purgeBySubject(subjectId)` physically remove rows including tombstones — and `resetSubject` now purges rather than soft-deleting so a factory reset leaves no residue. Enforced identically in the TypeScript and Python packages. Confidence recompute is deliberately untouched: `removeEvidenceSafely` still clears the active provenance links, so a cognition's confidence drops exactly as before when its evidence is retracted.
- Consolidating a conflict in the Python package now recomputes confidence from the resulting evidence chain, matching the TypeScript path. The Python port previously only wrote the credibility status, so `contradictPenalty` never took effect there and a refuted cognition kept the confidence it had with no opposition at all.
- Simplified the public repository surface, documentation, contribution flow, and release presentation.
- Prepared unreleased `@memoweft/adapter-ai-sdk` and `@memoweft/mcp-server` `0.2.0` packages for Core `^0.5.1 || ^0.6.0`; documented the published `0.1.0` / Core `0.5.1` installation pair and the MCP tool and registry contracts.

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

[Unreleased]: https://github.com/memoweft/memoweft/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/memoweft/memoweft/tree/v0.6.0
[0.5.1]: https://github.com/memoweft/memoweft/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/memoweft/memoweft/releases/tag/v0.5.0
[0.4.0]: https://github.com/memoweft/memoweft/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/memoweft/memoweft/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/memoweft/memoweft/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/memoweft/memoweft/tree/v0.1.0
