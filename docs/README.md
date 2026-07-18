# MemoWeft documentation

**English** | [简体中文](./README.zh-CN.md)

MemoWeft is a library for portable, traceable user memory. Start with the offline proof, then follow the integration path that matches your application.

## Start here

- **[Getting started](./getting-started.md)** — install the package, store evidence, form a profile, and recall it. [中文](./getting-started.zh-CN.md)
- **[Offline demo](../examples/no-key-demo.ts)** — stated memory, inference, and conflicts with no API key or network.
- **[Concepts](./concepts/)** — six memory-discipline rules, one page each. [中文](./concepts/README.zh-CN.md)
- **[Examples](../examples/)** — Core, management, plugins, and portable bundles.

## Integrations

- [Vercel AI SDK](../packages/adapter-ai-sdk/)
- [Model Context Protocol](../packages/mcp-server/)
- [Claude Agent SDK](../packages/adapter-claude-agent-sdk/)
- [OpenAI Agents SDK](../packages/adapter-openai-agents/)
- [LangChain](../packages/adapter-langchain/)
- [Mastra](../packages/adapter-mastra/)
- [LlamaIndex.TS](../packages/adapter-llamaindex/) — legacy; upstream archived

The [recipes](./recipes/) provide shorter task-oriented paths for published integrations.

## Reference

- **[Memory Surface Contract](./reference/memory-surface-contract.md)** — public methods, data shapes, and stability levels. [中文](./reference/memory-surface-contract.zh-CN.md)
- **[Glossary](./glossary.md)** — code terms, definitions, and user-facing language. [中文](./glossary.zh-CN.md)
- **[Demo walkthrough](./demo-script.md)** — the deterministic four-scene demo.
- **[Plugin contract](./plugin-contract.md)** — plugin hooks and permission boundaries. [中文](./plugin-contract.zh-CN.md)

## Architecture and operations

- **[Architecture](./internals/architecture.md)** — evidence → event → cognition, write path, recall path, and provenance.
- **[Deployment and privacy](./deployment.md)** — production checklist, model routing, cloud-read controls, and data-at-rest boundaries. [中文](./deployment.zh-CN.md)
- **[Installation details](./INSTALL.md)** — Node versions and SQLite drivers. [中文](./INSTALL.zh-CN.md)
- **[Reference host](./reference-host.md)** — capabilities and production boundaries. [中文](./reference-host.zh-CN.md)
- **[Performance](./internals/perf.md)** — measured results and methodology.
- **[Publishing](./PUBLISHING.md)** — package and release process.

## Project

- [Roadmap](../ROADMAP.md)
- [Changelog](../CHANGELOG.md)
- [Contributing](../CONTRIBUTING.md)
- [Support](../SUPPORT.md)
- [Security](../.github/SECURITY.md)
