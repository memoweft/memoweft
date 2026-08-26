# Changelog

All notable changes to MemoWeft are recorded here. Release publication remains a separate, explicitly verified action.

## Unreleased — MemoWeft 2.0 integration candidate

### Added

- Durable Core terminal outcomes for `applied`, `no_change`, `clarification_required`, `out_of_scope`, and `failed`.
- Durable Hermes outcome consumption, exact-session host events, and typed presentation across CLI, TUI, gateway, ACP, and Desktop surfaces.
- Trust Query and Trust Command services with provenance, coherent revisions, user controls, and durable command receipts.
- A complete clarification answer loop with new Evidence and follow-up Job identity.
- Portable v4 export, deterministic dry-run planning, subject remapping, conflict preview, and atomic import.
- DSH RPC v2 for Trust, Command, Clarification, Portable, health, and replay operations.
- A source-tree Memory Experience for World inspection, user controls, clarification, Recall preview, and Portable workflows.

### Changed

- The Python database schema advances to version 19.
- The formal Portable bundle schema advances to version 4 while retaining v2/v3 reader compatibility.
- Portable v4 now carries entity support provenance through `entityEvidence`, so imported entities retain their Trust/currentness visibility across hosts.
- Hermes memory integration now distinguishes Core acceptance, business terminal, durable host event, user presentation, and Recall indicators.

### Evidence boundary

- Source tests, package hashes, canonical repository automation, fresh installation, live host behavior, dogfood, publication, and post-publication checks remain separate evidence layers.
- This changelog entry describes the integration candidate. It does not claim that MemoWeft 2.0 has been published.
