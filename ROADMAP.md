# Roadmap

MemoWeft is a library-first memory layer with embedded SQLite storage. The roadmap prioritizes a small, dependable public API, inspectable memory behavior, and portable data over hosted features.

Last updated: 2026-07-18

## Now

- Harden the pre-1.0 Core API around provenance, corrections, conflicts, and portable bundles.
- Keep Node 20, 22, and 24 compatibility covered by CI, with Node 24 as the zero-dependency path.
- Expand maintained integrations from runnable examples into versioned, installable packages.
- Publish reproducible benchmark artifacts with commit, model, configuration, and dataset checksums.
- Complete Python interoperability for portable bundles and cross-language rule parity.

## Next

- Add Windows and macOS package smoke tests alongside the existing Linux matrix.
- Improve observability for host-scheduled profile updates and indexing failures without logging user content.
- Expand maintained integration examples around real application flows rather than adding framework logos alone.

## Later

These items are demand-driven and are not commitments for the current release line:

- Pluggable production storage backends beyond embedded SQLite.
- Multi-device synchronization and merge policies.
- Multimodal evidence.
- A hosted administration service or web console.
- Additional language SDKs after the TypeScript and Python contracts are stable.

## Non-goals

MemoWeft Core will not become:

- a hosted multi-tenant memory service;
- a general document-RAG platform;
- a chat product, persona, or consent interface;
- a system that silently resolves contradictions;
- a pipeline that treats assistant output as user evidence.

The bundled host remains a reference implementation. Production applications own deployment, authentication, consent, encryption at rest, and user-facing memory controls.

## Feedback

Feature requests and concrete integration needs are welcome through [GitHub Issues](https://github.com/memoweft/memoweft/issues). Usage questions belong in [GitHub Discussions](https://github.com/memoweft/memoweft/discussions).
