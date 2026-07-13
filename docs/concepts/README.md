# Cognitive discipline: six rules

**English** | [简体中文](./README.zh-CN.md)

MemoWeft keeps **facts and guesses apart** — what a user actually said versus what a model inferred — and never lets one quietly become the other. That distinction is the whole point.

Six disciplines enforce the separation. Every write and read path obeys all six; the mechanism lives in [architecture](../internals/architecture.md), and each page below explains one rule with a runnable check where one fits.

- **[Read/write split](./read-write.md)** — reads stay synchronous and light; the heavy digest into a profile runs batched in the background, so chat never blocks.
- **[Sourcing](./sourcing.md)** — every fact is tagged with how it arrived (`spoken`, `observed`, `inferred`, `tool`), and every judgment traces back to the exact user words it rests on.
- **[No self-evidence](./no-self-evidence.md)** — the assistant's own replies never become evidence, and a judgment that cannot cite real user words is dropped.
- **[Confidence by rule](./confidence.md)** — confidence is computed from evidence by a fixed formula, never taken from the model's self-report.
- **[Correct vs conflict](./correct-conflict.md)** — an explicit user correction retires the old belief; a plain contradiction is exposed and kept side by side, never auto-resolved.
- **[Typed decay](./decay.md)** — cognitions fade at different speeds: a passing mood is forgotten fast, an explicit preference is never auto-forgotten.

New to MemoWeft? Read [Getting started](../getting-started.md) first, then come back here. Unsure about a term (evidence, cognition, confidence…)? The [Glossary](../glossary.md) defines them all.
