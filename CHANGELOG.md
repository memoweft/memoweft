# Changelog

All notable changes to MemoWeft are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> This file is the user-facing summary of notable changes; the full commit history has the fine detail.
> While the API is pre-1.0, minor versions may include breaking changes.

## [Unreleased]

### Added

- **Schema versioning & migrations** — the database now carries a `PRAGMA user_version`; `openStores` runs an ordered migration runner on open. New databases are stamped to the latest version; existing ones (e.g. a `0.1.0` database) are migrated forward, each migration in its own transaction (rolls back on failure), with an automatic pre-migration backup for schema-changing migrations and a dry-run mode. Exposed as `runMigrations` / `getSchemaVersion` / `LATEST_SCHEMA_VERSION`. A `0.1.0` database opens losslessly under the new version (covered by tests).

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

[Unreleased]: https://github.com/memoweft/memoweft/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/memoweft/memoweft/releases/tag/v0.1.0
