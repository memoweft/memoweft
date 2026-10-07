# MemoWeft Python 2.0 Core

This directory contains the MemoWeft 2.0 Python Core candidate. Its production mainline lives under `memoweft.integrations` and provides the Hermes and WeftMate/DSH host boundaries for the Personal Memory World. The directory also retains a parity-insurance layer for cross-language behavior and portable-data verification; that layer is not a second production World engine.

The top-level public facade remains intentionally narrow and is not a feature-complete, stable general-purpose Python SDK. The Memory Experience product form has not yet formed or received its own version. This 2.0 Core candidate identity does not claim Beta, GA, release, or publication.

## Current scope

The repository tests these Python layers against fixtures generated from the TypeScript source:

| Layer            | Covered behavior                                                                                                                        |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Rule kernel      | confidence, formation mode, typed decay, identifier resolution, hashing, and shared configuration                                       |
| SQLite           | schema, migrations, transactions, evidence/event/cognition stores, interaction context, semantic resolutions, and FTS5 keyword behavior |
| Portable bundles | schema validation, import behavior, and TypeScript-to-Python interoperability                                                           |
| Write pipeline   | distillation, consolidation, profile updates, asking, attribution, trends, expiration, and privacy filtering                            |
| Model boundary   | OpenAI-compatible HTTP client, prompt loading, response extraction, and JSON repair                                                     |

The top-level `memoweft` exports remain intentionally limited to the rule kernel. Production integrations, storage, portable, and write-path modules are exercised through their explicit module contracts but do not yet form a broad stable facade equivalent to TypeScript's `createMemoWeftCore`.

## Shared contract

Configuration and `{input, expected}` fixtures live in [`../shared`](../shared). They are generated from TypeScript with `npm run shared:update`; that command also synchronizes the generated package resource `src/memoweft/_shared_data/`. The installed package reads this resource through `importlib.resources`, while `npm run shared:check` verifies neither copy has drifted from the TypeScript source.

Parity helpers also reproduce JavaScript-specific behavior where it affects the contract, including `Math.round`, 32-bit `Math.imul`, and UTF-16 `charCodeAt` semantics.

## Development

From this directory:

```bash
uv sync --extra dev
uv run pytest -q
uv run mypy --strict src tests
uv run python scripts/smoke_distribution.py
```

Python 3.11 or newer is required. Development verifies the sibling `../shared` contract; wheel and sdist installs carry the generated runtime JSON and do not require a monorepo checkout.
