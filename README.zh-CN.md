<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/memoweft/memoweft/main/assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/memoweft/memoweft/main/assets/hero-light.svg">
  <img alt="MemoWeft — 面向 AI 应用的长期记忆" src="https://raw.githubusercontent.com/memoweft/memoweft/main/assets/hero-dark.svg" width="100%">
</picture>

# MemoWeft

**把证据、推断与冲突明确分开的长期记忆。**

面向 TypeScript AI 应用的可移植、可追溯用户记忆。MemoWeft 把来源记录与模型推断分开，保留矛盾，并导出带版本、可由宿主校验和导入的记忆包。

[![npm](https://img.shields.io/npm/v/memoweft?style=flat-square&labelColor=14110B&color=E2A75E)](https://www.npmjs.com/package/memoweft)
[![CI](https://img.shields.io/github/actions/workflow/status/memoweft/memoweft/ci.yml?style=flat-square&labelColor=14110B&label=CI)](https://github.com/memoweft/memoweft/actions/workflows/ci.yml)
[![Node](https://img.shields.io/badge/Node-20%20%7C%2022%20%7C%2024-4A4438?style=flat-square&labelColor=14110B)](https://github.com/memoweft/memoweft/blob/main/docs/INSTALL.zh-CN.md)
[![license](https://img.shields.io/badge/license-MIT-4A4438?style=flat-square&labelColor=14110B)](https://github.com/memoweft/memoweft/blob/main/LICENSE)

[离线演示](#30-秒看出差别) · [安装](#安装并完成第一次调用) · [生态集成](#生态集成) · [参考宿主](#在本地运行参考宿主) · [文档](#文档)

[English](https://github.com/memoweft/memoweft/blob/main/README.md) · **简体中文**

</div>

MemoWeft 是宿主应用直接导入的库——不是托管服务、聊天 UI、人设、向量数据库或 Agent 框架。

## 30 秒看出差别

准备好 Node 24 后运行：

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
node examples/no-key-demo.ts
```

演示使用内存数据库和确定性的 stub 模型。依赖安装完成后，它不需要 API key、不访问网络，也不会写入磁盘。

```text
[limited   ] conf  600/1000  The user lives in Osaka  — stated memory
[conflicted] conf  600/1000  The user lives in Tokyo  — conflict kept, not overwritten
[candidate ] conf  200/1000  The user probably works somewhere central  — guess (low confidence)

Summary: 3 cognitions, 1 in conflict-exposed state; inference remains labeled and rule-scored separately from stated memory.
Done. (in-memory database — nothing written to disk)
```

这段演示调用真实的 MemoWeft Core 公共 API，验证的是记忆规则，而不是模型质量。要继续查看纠正历史和分型衰减，运行 `npm run demo`。

[阅读四幕演示说明](https://github.com/memoweft/memoweft/blob/main/docs/demo-script.zh-CN.md) · [查看离线演示源码](https://github.com/memoweft/memoweft/blob/main/examples/no-key-demo.ts)

同一组行为还由离线回归和 API 表面检查覆盖。CI 在 Node 24 上运行完整门禁，在 Node 22 上做 Core 兼容测试，并在 Node 20 上做已构建包的 SQLite smoke test。详见[实验与复现协议](https://github.com/memoweft/memoweft/blob/main/BENCHMARKS.md)。

## 为什么是 MemoWeft

- **证据不等于认知。** 用户原话、外部观察、工具结果与模型推断保留不同来源。
- **冲突会暴露，不会被静默覆盖。** 显式纠正保留历史；未决矛盾并列存在。
- **置信度由规则计算。** 模型不会直接设置最终的数值置信度。
- **记忆可检查、可迁移。** 认知能够回溯证据，宿主可以导出、校验并导入带版本的记忆包。
- **助手不能自我印证。** 内建摄入路径可以用回复解释用户下一句话，但不会仅因为助手说过就把回复持久化为证据。
- **不同信息按类型衰减。** 临时状态比稳定事实和明确偏好更快失效。

数据流保持显式：

```text
证据  →  事件  →  认知  →  召回
 ↑                  │
 └──── 可追溯来源 ───┘
```

[了解六条记忆纪律](https://github.com/memoweft/memoweft/tree/main/docs/concepts) · [查看架构如何落实这些约束](https://github.com/memoweft/memoweft/blob/main/docs/internals/architecture.zh-CN.md)

## 安装并完成第一次调用

**推荐 Node 24+。** Node 20 和 22 使用可选的 `better-sqlite3` 驱动。

```bash
npm install memoweft

# 仅 Node 20 / 22
npm install better-sqlite3
```

保存为 `quickstart.mjs`：

```js
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

await core.ingestUserMessage({
  subjectId: 'user-42',
  content: '下午三点以后我只喝低因咖啡，咖啡因会让我睡不着。',
});

for (const evidence of core.memory.listEvidence({ subjectId: 'user-42' })) {
  console.log(evidence.sourceKind, '·', evidence.rawContent);
}

core.close();
```

运行：

```bash
node quickstart.mjs
```

预期输出：

```text
spoken · 下午三点以后我只喝低因咖啡，咖啡因会让我睡不着。
```

第一次调用只负责保存和读取原始证据，并不会把普通存储包装成用户画像。把证据整理成认知并召回到上下文需要聊天模型。继续阅读[五分钟上手指南](https://github.com/memoweft/memoweft/blob/main/docs/getting-started.zh-CN.md)。

## MemoWeft 适合什么

适合：

- 需要跨对话、跨模型或跨宿主保存用户长期记忆；
- 需要来源追溯、纠正历史、冲突可见和可控召回；
- 希望将 SQLite 记忆层直接嵌入 TypeScript 应用；
- 希望宿主能够检查、管理、导入和导出记忆；
- 需要嵌入式 SQLite Core，并明确控制内建模型路径能够读取哪些内容。

不适合：

- 只需要短期聊天记录或文档 RAG；
- 需要现成的多租户托管记忆 API 或托管同步服务；
- 开箱即用地要求 PostgreSQL 或可替换生产存储后端；
- 需要完整的人设、聊天产品、同意界面或管理后台。

聊天体验、用户同意、身份认证、静态加密、画像更新调度和部署始终由宿主负责。

## 生态集成

| 生态                                                                                                 | 接入方式                            | 可用状态                                                            |
| ---------------------------------------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------- |
| [Vercel AI SDK](https://github.com/memoweft/memoweft/tree/main/packages/adapter-ai-sdk)              | 中间件召回与受控持久化              | npm `0.1.0` 配 Core `0.5.1`；源码 `0.2.0` 支持 Core `0.5.1` / `0.6` |
| [Model Context Protocol](https://github.com/memoweft/memoweft/tree/main/packages/mcp-server)         | stdio：5 读、3 个受控写             | npm `0.1.0` 配 Core `0.5.1`；源码 `0.2.0` 支持 Core `0.5.1` / `0.6` |
| [Claude Agent SDK](https://github.com/memoweft/memoweft/tree/main/packages/adapter-claude-agent-sdk) | 用户输入与工具结果 hooks            | 源码预览                                                            |
| [OpenAI Agents SDK](https://github.com/memoweft/memoweft/tree/main/packages/adapter-openai-agents)   | Run wrapper 与模型输入过滤          | 源码预览                                                            |
| [LangChain](https://github.com/memoweft/memoweft/tree/main/packages/adapter-langchain)               | v1 middleware 或 retriever/callback | 源码预览                                                            |
| [Mastra](https://github.com/memoweft/memoweft/tree/main/packages/adapter-mastra)                     | Processor 读写接入                  | 源码预览                                                            |
| [LlamaIndex.TS](https://github.com/memoweft/memoweft/tree/main/packages/adapter-llamaindex)          | Memory block 与 stream tap          | 仅维护存量；上游已归档                                              |

两个已发布集成当前均为 `0.1.0`，应与 `memoweft@0.5.1` 搭配安装。`main` 上的 `0.2.0` 源码支持 Core `0.5.1` 和 `0.6`，但尚未发布到 npm。尚未发布的集成只作为仓库源码预览，不会被描述成可直接 npm 安装。

## 在本地运行参考宿主

参考宿主展示带召回的聊天、记忆形成过程、证据图、记忆管理以及便携包导入导出。

![MemoWeft 参考宿主——聊天、记忆形成和证据图](https://raw.githubusercontent.com/memoweft/memoweft/main/assets/reference-host-demo.gif)

要求：

- Node 24+
- OpenAI-compatible 聊天模型端点
- 可用于 SQLite 数据的本地目录

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm start -w @memoweft/host
```

打开 <http://localhost:7788>。

首次运行时，配置向导会把模型配置保存到 `apps/memoweft-host/.env`；记忆保存在 `apps/memoweft-host/data/host.db`。两处均已被 Git 忽略。保存配置后请重启宿主。

参考宿主不是生产部署模板。接入真实应用前请阅读[它是什么、又不是什么](https://github.com/memoweft/memoweft/blob/main/docs/reference-host.zh-CN.md)和[部署与隐私模型](https://github.com/memoweft/memoweft/blob/main/docs/deployment.zh-CN.md)。

## 可信度、隐私与证据

- **离线回归覆盖：**认知规则由[离线评测用例](https://github.com/memoweft/memoweft/tree/main/tests/eval)固定。
- **持续验证：**CI 运行 lint、类型检查、测试、构建、API 表面检查、可运行文档片段以及 Node 兼容性任务。
- **可复现实验：**[BENCHMARKS.md](https://github.com/memoweft/memoweft/blob/main/BENCHMARKS.md)说明仓库自带的回归夹具、外部数据集协议、公开结果门槛和当前限制。
- **明确的 API 稳定性：**[Memory Surface Contract](https://github.com/memoweft/memoweft/blob/main/docs/reference/memory-surface-contract.zh-CN.md)区分稳定、实验性和内部表面。
- **小依赖边界：**Node 24 使用内建 SQLite，不要求第三方运行时依赖；Node 20 和 22 使用可选的 `better-sqlite3` peer 驱动。
- **可移植数据：**导出、校验、dry-run 导入和版本检查属于公共管理表面。

隐私边界：MemoWeft 将记忆存入标准、未加密的 SQLite 数据库。内建写路径在组装云模型提示词时遵守 `allowCloudRead`；这个标志不是访问控制、磁盘加密，也不约束自定义集成。宿主负责用户同意、角色边界、删除界面、访问控制、备份、日志策略以及操作系统或应用层加密。

## 使用 MemoWeft 构建

[WeftMate](https://www.weftmate.com/) 使用 MemoWeft 作为可移植记忆层，展示了完整桌面产品如何建立在 Core 之上，同时把产品体验留在宿主层。

## 文档

- [快速上手](https://github.com/memoweft/memoweft/blob/main/docs/getting-started.zh-CN.md)
- [概念](https://github.com/memoweft/memoweft/tree/main/docs/concepts)
- [示例](https://github.com/memoweft/memoweft/tree/main/examples)
- [API 表面契约](https://github.com/memoweft/memoweft/blob/main/docs/reference/memory-surface-contract.zh-CN.md)
- [术语表](https://github.com/memoweft/memoweft/blob/main/docs/glossary.zh-CN.md)
- [参考宿主](https://github.com/memoweft/memoweft/blob/main/docs/reference-host.zh-CN.md)
- [部署与隐私](https://github.com/memoweft/memoweft/blob/main/docs/deployment.zh-CN.md)
- [完整文档索引](https://github.com/memoweft/memoweft/blob/main/docs/README.zh-CN.md)

## 项目状态

MemoWeft 目前处于 pre-1.0、library-first 阶段。Core 已实现并经过测试，但实验性接口仍可能在 minor 版本之间变化。

[更新日志](https://github.com/memoweft/memoweft/blob/main/CHANGELOG.md) · [路线图](https://github.com/memoweft/memoweft/blob/main/ROADMAP.md) · [参与贡献](https://github.com/memoweft/memoweft/blob/main/CONTRIBUTING.zh-CN.md) · [支持](https://github.com/memoweft/memoweft/blob/main/SUPPORT.md) · [安全](https://github.com/memoweft/memoweft/blob/main/.github/SECURITY.md)

如果 MemoWeft 的记忆模型对你的工作有帮助，欢迎 Star 仓库，或把离线演示分享给另一位开发者。

## License

[MIT](https://github.com/memoweft/memoweft/blob/main/LICENSE) © 2026 MemoWeft contributors.
