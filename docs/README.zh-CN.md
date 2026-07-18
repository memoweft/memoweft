# MemoWeft 文档

[English](./README.md) | **简体中文**

MemoWeft 是一套可移植、可追溯的用户记忆库。建议先运行离线演示，再选择与你的应用匹配的集成路径。

## 从这里开始

- **[快速上手](./getting-started.zh-CN.md)** — 安装、保存证据、形成画像并完成召回。[English](./getting-started.md)
- **[离线演示](../examples/no-key-demo.ts)** — 无需 API key 或网络即可看到事实、猜测与冲突。
- **[概念](./concepts/README.zh-CN.md)** — 六条记忆纪律，每条一页。[English](./concepts/README.md)
- **[示例](../examples/)** — Core、管理 API、插件与便携包。

## 生态集成

- [Vercel AI SDK](../packages/adapter-ai-sdk/)
- [Model Context Protocol](../packages/mcp-server/)
- [Claude Agent SDK](../packages/adapter-claude-agent-sdk/)
- [OpenAI Agents SDK](../packages/adapter-openai-agents/)
- [LangChain](../packages/adapter-langchain/)
- [Mastra](../packages/adapter-mastra/)
- [LlamaIndex.TS](../packages/adapter-llamaindex/) — 仅维护存量；上游已归档

[配方](./recipes/)提供已经发布集成的短路径说明。

## 参考

- **[Memory Surface Contract](./reference/memory-surface-contract.zh-CN.md)** — 公共方法、数据形状和稳定性等级。[English](./reference/memory-surface-contract.md)
- **[术语表](./glossary.zh-CN.md)** — 代码术语、定义与面向用户的表达。[English](./glossary.md)
- **[Demo 演练](./demo-script.zh-CN.md)** — 确定性的四幕演示。[English](./demo-script.md)
- **[插件契约](./plugin-contract.zh-CN.md)** — 插件钩子与权限边界。[English](./plugin-contract.md)

## 架构与运行

- **[架构](./internals/architecture.zh-CN.md)** — 证据 → 事件 → 认知、写入、召回与来源追溯。
- **[部署与隐私](./deployment.zh-CN.md)** — 生产清单、模型路由、云端读取控制和静态数据边界。[English](./deployment.md)
- **[安装细节](./INSTALL.zh-CN.md)** — Node 版本与 SQLite 驱动。[English](./INSTALL.md)
- **[参考宿主](./reference-host.zh-CN.md)** — 能力与生产边界。[English](./reference-host.md)
- **[性能](./internals/perf.md)** — 实测结果与方法。
- **[发布](./PUBLISHING.md)** — 包与 Release 流程。

## 项目

- [路线图](../ROADMAP.md)
- [更新日志](../CHANGELOG.md)
- [参与贡献](../CONTRIBUTING.zh-CN.md)
- [支持](../SUPPORT.md)
- [安全](../.github/SECURITY.md)
