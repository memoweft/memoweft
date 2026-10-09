# Changelog

All notable changes to MemoWeft are recorded here. Release publication remains a separate, explicitly verified action.

## Unreleased

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
