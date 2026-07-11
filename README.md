<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="assets/hero-light.svg">
  <img alt="MemoWeft — long-term memory for AI apps, with facts and guesses kept apart" src="assets/hero-dark.svg" width="100%">
</picture>

# MemoWeft

**Portable memory for AI apps — facts, guesses, conflicts, and stale states kept apart.**

[![npm](https://img.shields.io/npm/v/memoweft?style=flat-square&labelColor=14110B&color=E2A75E)](https://www.npmjs.com/package/memoweft)
[![CI](https://img.shields.io/github/actions/workflow/status/memoweft/memoweft/ci.yml?style=flat-square&labelColor=14110B&label=CI)](https://github.com/memoweft/memoweft/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/badge/coverage-97.42%25-4A4438?style=flat-square&labelColor=14110B)](#project-status)
[![runtime deps](https://img.shields.io/badge/runtime%20deps-zero-4A4438?style=flat-square&labelColor=14110B)](#project-status)
[![license](https://img.shields.io/badge/license-MIT-4A4438?style=flat-square&labelColor=14110B)](LICENSE)

[Why](#why-not-just-another-memory-library) · [Install](#60-second-install-and-first-call) · [Reference host](#try-the-reference-host) · [Docs](#where-to-go-next)

**English** · [简体中文](./README.zh-CN.md)

</div>

![MemoWeft reference host demo — chat, watch memory form, and open the memory graph of evidence, events, and cognitions](assets/reference-host-demo.gif)

MemoWeft is a library you `import` into an AI app. It keeps portable, traceable long-term memory about a user — separating facts from guesses, exposing conflicts instead of silently overwriting them, and letting different hosts reuse the same memory.

## Why not just another memory library?

- **Facts and guesses stay apart.** Model inferences begin as low-confidence hypotheses, never facts — what the user actually said and what a model guessed are different kinds of record.
- **Conflicts are surfaced, not overwritten.** Contradictory information is exposed and kept side by side; MemoWeft never silently picks a winner.
- **Confidence is computed by rule, not self-reported.** Each cognition is scored from evidence strength and corroboration — a model never sets its own credibility.

Three more disciplines (typed decay, traceability, and no self-corroboration) are backed by numbered eval cases in [`tests/eval/`](./tests/eval/) — run `npm test`.

## 60-second install and first call

```bash
npm install memoweft   # Node 24: built-in node:sqlite. Node 20/22: also `npm i better-sqlite3`
```

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: './memoweft.db' });
await core.ingestUserMessage({ subjectId: 'user-42', content: 'I only drink decaf after 3pm — caffeine wrecks my sleep.' });
await core.updateProfile({ subjectId: 'user-42' });   // needs an OpenAI-compatible model (.env)

const turn = await core.handleConversationTurn({ subjectId: 'user-42', message: 'Recommend an afternoon drink.' });
console.log(turn.reply);
core.close();
```

No API key? [`examples/no-key-demo.ts`](./examples/no-key-demo.ts) runs the same write path against an offline stub — watch a conflict get exposed (not overwritten) in about 30 seconds.

## Try the reference host

The bundled reference host is a **demo, not the product**. It shows how an app uses Core — chat with recall, watch memory form, and inspect the evidence → event → cognition graph. Needs Node 24+.

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft && npm install && npm run build
npm start -w @memoweft/host    # then open http://localhost:7788
```

More: [what the reference host is and is not](./docs/reference-host.md).

## Where to go next

- **[Getting started](./docs/getting-started.md)** — install, store one piece of evidence, read it back. Five minutes.
- **[Concepts](./docs/concepts/)** — the six cognitive-discipline rules, one screen each.
- **[Recipes](./docs/recipes/)** — drop MemoWeft into the [Vercel AI SDK](./packages/adapter-ai-sdk) or an [MCP server](./packages/mcp-server).

Full documentation index: [`docs/README.md`](./docs/README.md).

## Project status

Pre-1.0 and library-first. Core is implemented and tested, but interfaces may still change between minor releases — stable, experimental, and internal surfaces are documented in the [Memory Surface Contract](./docs/reference/memory-surface-contract.md). **Zero runtime dependencies.**

See the [roadmap](./ROADMAP.md), [contribution guide](./CONTRIBUTING.md), and [changelog](./CHANGELOG.md).

## License

[MIT](./LICENSE) © 2026 MemoWeft contributors.
