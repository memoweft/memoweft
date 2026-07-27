# API stability & deprecation policy

MemoWeft sorts every public export into one of three support tiers and follows an
explicit versioning and deprecation policy, so integrators know exactly what they can
build on. This document is the authoritative statement of that policy. The per-symbol
tier assignments live in [api-surface.md](./reference/api-surface.md), and the
behavioural contract of the supported facade lives in
[memory-surface-contract.md](./reference/memory-surface-contract.md).

## Support tiers

- **Stable** — covered by the compatibility snapshot and intended for application use.
  After 1.0, a breaking change to a stable symbol requires a new major version and a prior
  deprecation notice (see [Deprecation process](#deprecation-process)).
- **Experimental** — usable, but the shape may still change. Injectable extension points
  (custom retriever / embedder / LLM client / clock), the plugin contract, the
  background-maintenance operators (`expire`, `aggregateTrends`, `reconcileContradictions`),
  diagnostics, and low-level assembly live here. An experimental symbol may change in a
  minor release with a changelog notice.
- **Internal** — an implementation detail, exported only for composition or diagnostics.
  Do not build application contracts on it. It may change in any release, and the cleanest
  internal building blocks are not re-exported from the package root at all (reach them via
  deep import if you must).

**Exporting a symbol is not a support promise.** The root package re-exports low-level
building blocks so advanced hosts _can_ compose them, but the supported integration path is
the `createMemoWeftCore()` facade. When in doubt, integrate through the facade — the
low-level pieces are behind it for a reason.

A stable shape may enclose experimental parts. Where that happens it is called out at the
point of use — for example, `UpdateProfileResult` is a stable envelope whose per-stage
diagnostic payloads are experimental, and `Observation` is stable except for its
reserved `kind`/`meta` fields. Freeze only what a call-out marks as stable.

## Versioning

MemoWeft follows semantic versioning.

- **Pre-1.0 (`0.x`)** — the public API is not yet frozen. A minor release may contain a
  documented breaking change with migration notes in [CHANGELOG.md](../CHANGELOG.md). Stable
  symbols are held steady within a minor line but not guaranteed across minors.
- **1.0 and after** —
  - Breaking a **stable** symbol requires a **major** version bump and a prior deprecation
    notice.
  - **Experimental** symbols may change in a **minor** release with a changelog notice.
    Promoting an experimental symbol to stable is additive and non-breaking, so it can
    happen in any minor; it is demoting or reshaping that carries the notice. This is why,
    when promotion is not clearly earned, the conservative choice is to keep a symbol
    experimental — there is no cost to promoting it later and a permanent cost to freezing
    it early.
  - **Internal** symbols may change in any release.

Additive changes — a new optional field, a new enum value, a new method — are not breaking
and may appear in a minor release at any tier. Consumers must tolerate unknown enum values
and additional object fields.

## Deprecation process

When a stable symbol must change or be removed after 1.0:

1. Mark it `@deprecated` in the type declarations, pointing to the replacement, and record
   the deprecation in [CHANGELOG.md](../CHANGELOG.md).
2. Keep the deprecated symbol working for at least one subsequent minor line.
3. Remove it only in a major release, noting the removal in the changelog.

The deprecated compatibility aliases already in the tree — `DLA_VERSION` (use
`MEMOWEFT_VERSION`), `DlaConfig` (use `MemoWeftConfig`), and the `DLA_*` environment-variable
prefixes — follow exactly this policy: retained for compatibility, removed only on a major
with notice.

## How the surface is enforced

- `tests/api/api-surface.snapshot` freezes the full set of exported symbols and their
  shapes; `npm run api:check` fails CI on any accidental addition or removal. Regenerate it
  deliberately with `npm run api:update` and review the diff before committing.
- [api-surface.md](./reference/api-surface.md) records the human-facing support tier for
  each symbol on top of that mechanical freeze.
- Together they make a surface change a reviewed decision, not an accident.

## Python package

The Python package (`memoweft`) is an experimental parity implementation, not a
feature-complete SDK. Through 1.0 it carries no stability promise: treat its whole surface
as experimental, even though the deterministic rule kernel is held bit-exact to the
TypeScript implementation via `shared/parity/*.json`. Bit-exact parity is a guarantee about
cross-language computation _sameness_, not an API-freeze promise — the two are independent.

## Subpackages

Each subpackage (`@memoweft/mcp-server`, `@memoweft/adapter-ai-sdk`, …) versions
independently of Core and declares its own supported surface in its own README. The
unreleased adapters are source previews and are not frozen.

## Related

- [API surface & tiers](./reference/api-surface.md)
- [Memory surface contract](./reference/memory-surface-contract.md)
- [Changelog](../CHANGELOG.md)
