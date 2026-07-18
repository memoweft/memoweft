# Changelog

All notable changes to MemoWeft are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses semantic versioning while public APIs remain pre-1.0.

## [Unreleased]

### Changed

- Simplified the public repository surface, documentation, contribution flow, and release presentation.
- Prepared unreleased `@memoweft/adapter-ai-sdk` and `@memoweft/mcp-server` `0.2.0` packages for Core `^0.5.1 || ^0.6.0`; documented the published `0.1.0` / Core `0.5.1` installation pair and the MCP tool and registry contracts.

## [0.6.0] — 2026-07-18

### Added

- Interaction context and semantic resolution records for context-dependent replies such as “yes”, “no”, and “the latter”.
- Code-derived formation modes for user-stated, user-confirmed, observed, ruled, and inferred cognitions.
- Conversation-context support in the OpenAI Agents and LangChain integrations.
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
