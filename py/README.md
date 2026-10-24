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

## Recall language and dates

Python recall accepts an explicit `lang` (`zh` or `en`) on shared snapshots and
Trust Query services. Hermes and DSH initialization pass that setting to both
direct recall and RPC previews. Omitting it preserves the existing Chinese
rendering byte for byte, including its existing truncation behavior; explicit
Chinese keeps the same contract and does not add dates or escaping.

Default and Chinese calls also retain their original argument shape: they do
not send a `lang` keyword to existing readers or service constructors. English
is passed only when the callable explicitly declares a keyword-capable `lang`
parameter; legacy, opaque and `**kwargs`-only adapters safely use their original
Chinese call instead. Signature inspection happens before invocation, and
exceptions raised inside an implementation are neither hidden nor retried.

New English recall uses the producer/consumer contract from
[WeftMate #474](https://github.com/memoweft/weftmate/pull/474): `Memory:` or
`Memory (past):`, LF entry separators, and one leading backslash added to body
lines starting with either language's current/past header or a backslash.
Body whitespace, line endings and source wording remain exact after decoding.
The shared JSON in `tests/fixtures/recall-escape-contract.json` is copied from
WeftMate commit `aa83af9a002ad4d2511e2d2fc3edded4ff55154d` and fixes the wire bytes.

Generated confirmation explanations and target-name connectors use English in
English recall; a separate rendering projection keeps the existing matching
text, scores and candidate order unchanged. Original user and assistant wording
is not translated or searched/replaced, even when it contains the same Chinese
phrase as a generated explanation. DSH history context likewise uses the
configured language for its review notice, roles, conversation labels and
suggestion/commitment headings. Default and explicit Chinese keep their exact
prior output; opaque older history renderers retain their old call shape.

English entries show source dates without borrowing formation timestamps:
spoken support Evidence uses `said at`, or `recorded at` when only recording
time can be established; events use `event at` / `event time` from their own
`occurred_at` / `time_expression`. Equal Evidence occurrence/recording values
are conservatively labeled recording time because the existing store fills a
missing occurrence time with that value; pending source timestamps also remain
recording time. Missing or invalid calendar dates are not guessed, and relative
event expressions stay literal. These labels describe provenance, not an
inferred date for the claim itself; `cognition.created_at` is never a source date.

English rendering keeps a prefix of the existing candidate order, at most five
complete entries and 600 characters including headers, dates, escapes and LF
separators. An entry that cannot fit is not truncated or assigned an ID; later
candidates do not move ahead of it. Historical explanations share the same
per-entry budget and escaping. The snapshot token commits to the complete
actual rendered text. This changes no stored content, schema, formation rules,
source permission gates or retrieval ranking.

## Development

From this directory:

```bash
uv sync --extra dev
uv run pytest -q
uv run mypy --strict src tests
uv run python scripts/smoke_distribution.py
```

Python 3.11 or newer is required. Development verifies the sibling `../shared` contract; wheel and sdist installs carry the generated runtime JSON and do not require a monorepo checkout.

The optional `semantic` extra adds local CPU semantic recall to the production
Python integrations. It ranks eligible semantic candidates before literal matches,
adds bounded original-source windows and a sourced owner profile, and falls back
to literal recall during loading or failure. Source permissions and deletion apply
to every output. See [configuration, status, and derived
storage lifecycle](../docs/semantic-recall.md). The Python primary schema remains
21; the rebuildable vector tables are an optional extension.
