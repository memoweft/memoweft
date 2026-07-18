# Cognitive discipline: six rules

**English** | [简体中文](./README.zh-CN.md)

MemoWeft keeps **source records and derived claims apart** — what arrived from a user or tool versus what a model inferred. Built-in paths preserve that distinction instead of presenting an inference as user-provided evidence.

Six disciplines define that separation in the public Core. The mechanism lives in [architecture](../internals/architecture.md), and each page below explains one rule with a runnable check where one fits.

- **[Read/write split](./read-write.md)** — recall and profile updates are separate operations; the host decides when and where to schedule the model-backed write path.
- **[Sourcing](./sourcing.md)** — every evidence record is tagged with how it arrived (`spoken`, `observed`, `inferred`, `tool`), and derived cognitions retain links to their supporting records.
- **[No self-evidence](./no-self-evidence.md)** — built-in ingestion does not persist assistant replies as evidence, and derived cognitions require source links.
- **[Confidence by rule](./confidence.md)** — confidence is a deterministic heuristic score computed from evidence metadata, not a model-reported probability.
- **[Correct vs conflict](./correct-conflict.md)** — an explicit user correction retires the old belief; a plain contradiction is exposed and kept side by side, never auto-resolved.
- **[Typed decay](./decay.md)** — recall weight can fade by content type; the default configuration does not time-decay facts or preferences, while correction and invalidation still apply.

New to MemoWeft? Read [Getting started](../getting-started.md) first, then come back here. Unsure about a term (evidence, cognition, confidence…)? The [Glossary](../glossary.md) defines them all.
