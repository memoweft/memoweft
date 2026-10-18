# Changelog

All notable changes to MemoWeft are recorded here. Release publication remains a separate, explicitly verified action.

## Unreleased

- Action confirmations now bind to the selected action proposal rather than a nearby factual guess. Confirmation explanations quote that proposal while provenance keeps the complete original assistant turn.

- Python no longer treats incidental overlap with an assistant suggestion as user confirmation. Explicit short confirmations remain supported, and selecting only “yes” cannot discard a denial in the same reply.

- Python temporal memory supports returning to a previous state and attaches a reworded relationship restatement to the current occurrence. Goal safeguards cover clear source attribution, direct goal-verb negation without an affirmative goal clause, and explicit third-party goal subjects; conditional goals and long-term plans starting today remain eligible, while other semantics belong to the interpretation model.

- Python remembers explicit long-term goals as intentions and distinguishes later completion, abandonment and other state changes from corrections. Current recall uses the latest state; historical questions retain past states without describing a natural change as a mistake.

- Python DSH bridge: bound response replay retention so repeated source and memory reads do not accumulate full response copies for the process lifetime. Replay-window limits are exposed in capabilities; durable operation identities continue to govern retries after a cached response expires.

- Python English recall now uses English memory headings and generated confirmation/history explanations, preserves multiline quotations with reversible escaping, and shows reliable source or event dates with recording-time fallbacks labeled explicitly; default Chinese output remains unchanged.

- Python correction commands can bind to the actual message and a verbatim fact quote, preserving the complete original wording and reusing it when that turn is later ingested. Correcting and forming the same preference no longer create separate records from a model paraphrase and the real source.
- A completed source-bound correction no longer appears failed when later formation returns no change. Unfinished corrections and other facts in the same message remain visible until their own scope is fulfilled.
- Correction status supports databases containing ordinary MemoryLoop ledger records, preserving their original prose or JSON without treating it as a formal support link.
- Source corrections preserve existing optional metadata when omitted and accept the same real message arriving later with its actual timestamp and assistant context. Explicit conflicting metadata remains rejected.
- Forgetting a World item atomically removes its complete correction and replacement chain, including sources of older revisions. The existing confirmation preview lists the same cascade; independent memories stay available.
- Python memory recall can supplement existing literal matches with up to three local CPU semantic candidates; original ordering and permission/lifecycle gates stay authoritative, and the feature can be switched off. See [configuration and derived-data lifecycle](docs/semantic-recall.md).
- Add synthetic recall quality evaluations, frozen baselines and independent review regression probes; this change does not alter recall behavior.

## 2.0.0 — 2026-10-09

### Added

- Add typed observed source upsert, permission update and true withdrawal to DSH RPC v2 and the TypeScript Core, with version/replay fences and valid time.
- Filter local/cloud recall through source provenance, including mixed-source World items and assistant interaction dependencies, while preserving unrelated memory.
- Erase replacement/withdrawal content from derivations, ledgers, indexes, cached snapshots and source-dependent assistant prose; old Portable imports cannot restore withdrawn observations.
- Add shared lifecycle parity and real Core privacy/withdrawal coverage.

- Durable Core terminal outcomes for `applied`, `no_change`, `clarification_required`, `out_of_scope`, and `failed`.
- Durable Hermes outcome consumption, exact-session host events, and typed presentation across CLI, TUI, gateway, ACP, and Desktop surfaces.
- Trust Query and Trust Command services with provenance, coherent revisions, user controls, and durable command receipts.
- A complete clarification answer loop with new Evidence and follow-up Job identity.
- Portable v4 export, deterministic dry-run planning, subject remapping, conflict preview, and atomic import.
- DSH RPC v2 for Trust, Command, Clarification, Portable, health, and replay operations.
- A source-tree Memory Experience for World inspection, user controls, clarification, Recall preview, and Portable workflows.

### Changed

- M2b–M2c: preserve selected user facts and exact evidence spans, rewrite invalid formation JSON once, and stream slow local formation responses.
- M2d–M2g: improve natural correction recall, exact correction fields, formation consistency, and source-linked understanding of people and correction chains.
- FG-1 / FG-2: preview forgetting scope before applying it, erase scoped conversation context, and include dependent interaction commitments in true deletion.
- UP-3: allow the DSH bridge to open and migrate supported legacy memory stores (schema v18–20) through the existing migration path.
- MW-3: honor the host's configured cloud memory model route.

- The Python database schema advances to version 21 (observed source lifecycle; Trust true deletion was added in version 20).
- The formal Portable bundle schema advances to version 4 while retaining v2/v3 reader compatibility.
- Portable v4 now carries entity support provenance through `entityEvidence`, so imported entities retain their Trust/currentness visibility across hosts.
- Hermes memory integration now distinguishes Core acceptance, business terminal, durable host event, user presentation, and Recall indicators.

### Evidence boundary

- Source tests, package hashes, canonical repository automation, fresh installation, live host behavior, dogfood, publication, and post-publication checks remain separate evidence layers.
- Release publication is verified separately.
