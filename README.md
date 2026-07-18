<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/memoweft/memoweft/main/assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/memoweft/memoweft/main/assets/hero-light.svg">
  <img alt="MemoWeft — long-term memory for AI applications" src="https://raw.githubusercontent.com/memoweft/memoweft/main/assets/hero-dark.svg" width="100%">
</picture>

# MemoWeft

**Long-term memory that keeps evidence, inference, and conflict distinct.**

Portable, traceable user memory for TypeScript AI applications. MemoWeft keeps source records separate from model inference, preserves contradictions, and exports versioned memory bundles that hosts can validate and import.

[![npm](https://img.shields.io/npm/v/memoweft?style=flat-square&labelColor=14110B&color=E2A75E)](https://www.npmjs.com/package/memoweft)
[![CI](https://img.shields.io/github/actions/workflow/status/memoweft/memoweft/ci.yml?style=flat-square&labelColor=14110B&label=CI)](https://github.com/memoweft/memoweft/actions/workflows/ci.yml)
[![Node](https://img.shields.io/badge/Node-20%20%7C%2022%20%7C%2024-4A4438?style=flat-square&labelColor=14110B)](https://github.com/memoweft/memoweft/blob/main/docs/INSTALL.md)
[![license](https://img.shields.io/badge/license-MIT-4A4438?style=flat-square&labelColor=14110B)](https://github.com/memoweft/memoweft/blob/main/LICENSE)

[Demo](#see-the-difference-in-30-seconds) · [Install](#install-and-make-the-first-call) · [Integrations](#integrations) · [Reference host](#run-the-reference-host-locally) · [Docs](#documentation)

**English** · [简体中文](https://github.com/memoweft/memoweft/blob/main/README.zh-CN.md)

</div>

MemoWeft is a library your application imports — not a hosted service, chat UI, persona, vector database, or agent framework.

## See the difference in 30 seconds

With Node 24 installed:

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
node examples/no-key-demo.ts
```

The demo uses an in-memory database and a deterministic stub model. After dependencies are installed, it needs no API key, makes no network calls, and writes nothing to disk.

```text
[limited   ] conf  600/1000  The user lives in Osaka  — stated memory
[conflicted] conf  600/1000  The user lives in Tokyo  — conflict kept, not overwritten
[candidate ] conf  200/1000  The user probably works somewhere central  — guess (low confidence)

Summary: 3 cognitions, 1 in conflict-exposed state; inference remains labeled and rule-scored separately from stated memory.
Done. (in-memory database — nothing written to disk)
```

This exercises MemoWeft's public Core API and memory rules; it is not a model-quality benchmark. For correction history and typed decay as well, run `npm run demo`.

[Read the four-scene walkthrough](https://github.com/memoweft/memoweft/blob/main/docs/demo-script.md) · [Inspect the offline demo source](https://github.com/memoweft/memoweft/blob/main/examples/no-key-demo.ts)

The same behaviors are covered by offline regression cases and API-surface checks. CI runs the full guardrail suite on Node 24, Core compatibility tests on Node 22, and a built-package SQLite smoke test on Node 20. See the [evaluation protocol](https://github.com/memoweft/memoweft/blob/main/BENCHMARKS.md).

## Why MemoWeft

- **Evidence is not belief.** User statements, observations, tool results, and model inferences retain distinct provenance.
- **Conflicts are exposed, not silently overwritten.** Explicit corrections retain history; unresolved contradictions remain visible side by side.
- **Confidence is computed by rule.** The model does not directly set the numeric confidence score.
- **Memory stays inspectable and portable.** Cognitions trace back to evidence, and hosts can export, validate, and import versioned memory bundles.
- **Assistant replies do not self-corroborate.** Built-in ingestion paths may use a reply to interpret the next user turn, but do not persist that reply as evidence merely because the assistant said it.
- **Staleness is typed.** Transient states fade faster than durable facts and explicit preferences.

The data flow stays explicit:

```text
evidence  →  event  →  cognition  →  recall
   ↑                       │
   └────── provenance ─────┘
```

[Explore the six memory-discipline rules](https://github.com/memoweft/memoweft/tree/main/docs/concepts) · [See how the architecture enforces them](https://github.com/memoweft/memoweft/blob/main/docs/internals/architecture.md)

## Install and make the first call

**Node 24+ is recommended.** Node 20 and 22 use the optional `better-sqlite3` driver.

```bash
npm install memoweft

# Node 20 / 22 only
npm install better-sqlite3
```

Save as `quickstart.mjs`:

```js
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

await core.ingestUserMessage({
  subjectId: 'user-42',
  content: 'I only drink decaf after 3pm — caffeine wrecks my sleep.',
});

for (const evidence of core.memory.listEvidence({ subjectId: 'user-42' })) {
  console.log(evidence.sourceKind, '·', evidence.rawContent);
}

core.close();
```

Run it:

```bash
node quickstart.mjs
```

Expected output:

```text
spoken · I only drink decaf after 3pm — caffeine wrecks my sleep.
```

This first call stores and reads raw evidence; it does not pretend that storage alone is a user profile. Turning evidence into cognitions and recalled context uses a chat model. Continue with the [five-minute getting-started guide](https://github.com/memoweft/memoweft/blob/main/docs/getting-started.md).

## Is MemoWeft a fit?

Choose MemoWeft when you need:

- long-term user memory across conversations, models, or hosts;
- provenance, correction history, conflict visibility, and controlled recall;
- an embedded TypeScript library backed by SQLite;
- memory that the host can inspect, manage, export, and import;
- an embedded SQLite core with explicit controls over what built-in model paths may read.

MemoWeft is not the right layer when you need:

- only short-term chat history or document RAG;
- a hosted multi-tenant memory API or managed synchronization service;
- PostgreSQL or a replaceable production storage backend out of the box;
- a ready-made persona, chat product, consent UI, or administration console.

Your host remains responsible for chat UX, consent, authentication, encryption at rest, scheduling profile updates, and deployment.

## Integrations

| Ecosystem                                                                                            | Integration surface                          | Availability                                                               |
| ---------------------------------------------------------------------------------------------------- | -------------------------------------------- | -------------------------------------------------------------------------- |
| [Vercel AI SDK](https://github.com/memoweft/memoweft/tree/main/packages/adapter-ai-sdk)              | Middleware recall and controlled persistence | npm `0.1.0` for Core `0.5.1`; source `0.2.0` supports Core `0.5.1` / `0.6` |
| [Model Context Protocol](https://github.com/memoweft/memoweft/tree/main/packages/mcp-server)         | Stdio: 5 reads and 3 controlled writes       | npm `0.1.0` for Core `0.5.1`; source `0.2.0` supports Core `0.5.1` / `0.6` |
| [Claude Agent SDK](https://github.com/memoweft/memoweft/tree/main/packages/adapter-claude-agent-sdk) | User-prompt and tool-result hooks            | Source preview                                                             |
| [OpenAI Agents SDK](https://github.com/memoweft/memoweft/tree/main/packages/adapter-openai-agents)   | Run wrapper and model-input filter           | Source preview                                                             |
| [LangChain](https://github.com/memoweft/memoweft/tree/main/packages/adapter-langchain)               | v1 middleware or retriever/callback paths    | Source preview                                                             |
| [Mastra](https://github.com/memoweft/memoweft/tree/main/packages/adapter-mastra)                     | Processor-based read/write integration       | Source preview                                                             |
| [LlamaIndex.TS](https://github.com/memoweft/memoweft/tree/main/packages/adapter-llamaindex)          | Memory block and stream tap                  | Legacy; upstream archived                                                  |

The two published integrations are currently `0.1.0`, which install with `memoweft@0.5.1`. Their `0.2.0` source versions on `main` support Core `0.5.1` and `0.6`, but are not yet published to npm. Source-preview integrations are available for evaluation from this repository and are not presented as npm-installable until released.

## Run the reference host locally

The bundled host demonstrates chat with recall, visible memory formation, the evidence graph, memory management, and portable import/export.

![MemoWeft reference host — chat, memory formation, and evidence graph](https://raw.githubusercontent.com/memoweft/memoweft/main/assets/reference-host-demo.gif)

Requirements:

- Node 24+
- an OpenAI-compatible chat-model endpoint
- a local filesystem location for SQLite data

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm start -w @memoweft/host
```

Open <http://localhost:7788>.

On first run, the setup wizard saves model configuration to `apps/memoweft-host/.env`; memory is stored in `apps/memoweft-host/data/host.db`. Both paths are git-ignored. The current setup UI is Chinese. Restart the host after saving configuration.

The host is a reference implementation, not a production deployment template. See [what it is and is not](https://github.com/memoweft/memoweft/blob/main/docs/reference-host.md) and review the [deployment and privacy model](https://github.com/memoweft/memoweft/blob/main/docs/deployment.md) before integrating MemoWeft into an application.

## Trust, privacy, and evidence

- **Offline regression coverage:** cognitive rules are pinned in [evaluation cases](https://github.com/memoweft/memoweft/tree/main/tests/eval).
- **Continuous verification:** CI runs linting, type checking, tests, builds, API-surface checks, runnable documentation snippets, and Node compatibility jobs.
- **Reproducible evaluation:** [BENCHMARKS.md](https://github.com/memoweft/memoweft/blob/main/BENCHMARKS.md) documents the shipped regression fixtures, external-dataset protocols, publication standard, and current limitations.
- **Explicit API stability:** public surfaces are classified as stable, experimental, or internal in the [Memory Surface Contract](https://github.com/memoweft/memoweft/blob/main/docs/reference/memory-surface-contract.md).
- **Small dependency boundary:** Node 24 uses built-in SQLite with no required third-party runtime dependency; Node 20 and 22 use the optional `better-sqlite3` peer driver.
- **Portable data:** export, validation, dry-run import, and version checks are part of the public management surface.

Privacy boundary: MemoWeft stores memory in a standard, unencrypted SQLite database. Built-in write paths honor `allowCloudRead` when assembling cloud-model prompts; the flag is not access control, disk encryption, or a guarantee about custom integrations. Hosts own consent, role boundaries, deletion UX, access control, backups, logging policy, and OS- or host-level encryption.

## Built with MemoWeft

[WeftMate](https://www.weftmate.com/) uses MemoWeft as its portable memory layer, demonstrating how the library sits beneath a complete desktop experience while keeping product UX outside Core.

## Documentation

- [Getting started](https://github.com/memoweft/memoweft/blob/main/docs/getting-started.md) — evidence to first recalled profile
- [Concepts](https://github.com/memoweft/memoweft/tree/main/docs/concepts) — six memory-discipline rules
- [Examples](https://github.com/memoweft/memoweft/tree/main/examples) — Core, management, plugins, and portable bundles
- [API surface contract](https://github.com/memoweft/memoweft/blob/main/docs/reference/memory-surface-contract.md)
- [Glossary](https://github.com/memoweft/memoweft/blob/main/docs/glossary.md)
- [Reference host](https://github.com/memoweft/memoweft/blob/main/docs/reference-host.md)
- [Deployment and privacy](https://github.com/memoweft/memoweft/blob/main/docs/deployment.md)
- [Full documentation index](https://github.com/memoweft/memoweft/blob/main/docs/README.md)

## Project status

MemoWeft is pre-1.0 and library-first. Core is implemented and tested, but experimental interfaces may change between minor releases.

[Changelog](https://github.com/memoweft/memoweft/blob/main/CHANGELOG.md) · [Roadmap](https://github.com/memoweft/memoweft/blob/main/ROADMAP.md) · [Contributing](https://github.com/memoweft/memoweft/blob/main/CONTRIBUTING.md) · [Support](https://github.com/memoweft/memoweft/blob/main/SUPPORT.md) · [Security](https://github.com/memoweft/memoweft/blob/main/.github/SECURITY.md)

If MemoWeft's memory model is useful to your work, consider starring the repository or sharing the offline demo with another builder.

## License

[MIT](https://github.com/memoweft/memoweft/blob/main/LICENSE) © 2026 MemoWeft contributors.
