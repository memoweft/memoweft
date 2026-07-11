# MemoWeft docs

MemoWeft is a library that gives AI apps portable long-term memory — it keeps facts, guesses, conflicts, and stale states apart.

## Start here

- **[Getting started](./getting-started.md)** — install, store one piece of evidence, read it back. Five minutes, no API key for the first step. [中文](./getting-started.zh-CN.md)
- **[Concepts](./concepts/)** — the six cognitive-discipline rules that make MemoWeft trustworthy, one screen each. [中文](./concepts/README.zh-CN.md)
- **Recipes** — add MemoWeft to your stack in five minutes: [Vercel AI SDK](./recipes/vercel-ai-sdk.md) [中文](./recipes/vercel-ai-sdk.zh-CN.md) · [MCP server](./recipes/mcp-server.md) [中文](./recipes/mcp-server.zh-CN.md).

## Reference

- **[Memory surface contract](./reference/memory-surface-contract.md)** — every host-facing method and shape; the API source of truth. [中文](./reference/memory-surface-contract.zh-CN.md)
- **[Glossary](./glossary.md)** — every core term: code name, definition, and plain user-facing wording. [中文](./glossary.zh-CN.md)
- **[Demo walkthrough](./demo-script.md)** — `npm run demo` shows the four differentiators in 90 seconds.
- **[Plugin contract](./plugin-contract.md)** — plugin hooks and permission boundaries. [中文](./plugin-contract.zh-CN.md)

## How it is built

- **[Architecture](./internals/architecture.md)** — evidence → event → cognition, read and write paths.
- **[Deployment](./deployment.md)** — cloud-first, cloud-guarded, local, and hybrid.
- **[Install details](./INSTALL.md)** — drivers, Node versions, environment. [中文](./INSTALL.zh-CN.md)
- **[Performance](./internals/perf.md)** — measured numbers, reproducible.

## For contributors

- [CONTRIBUTING](../CONTRIBUTING.md) · [AGENTS](../AGENTS.md) · [CURRENT](../CURRENT.md) · [ROADMAP](../ROADMAP.md) · [PUBLISHING](./PUBLISHING.md)
- Maintainer-only notes live under `docs/internal/`.
