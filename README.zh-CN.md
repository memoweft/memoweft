<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="assets/hero-light.svg">
  <img alt="MemoWeft —— 面向 AI 应用的长期记忆，区分事实与猜测" src="assets/hero-dark.svg" width="100%">
</picture>

# MemoWeft

**Portable memory for AI apps — facts, guesses, conflicts, and stale states kept apart.**

面向 AI 应用的可迁移长期记忆层：让助手记住用户，并分清事实、猜测、冲突与过期状态。

给 AI 应用一份带得走的长期记忆：换模型不失忆，记得住，也不乱信。

[![npm](https://img.shields.io/npm/v/memoweft?style=flat-square&labelColor=14110B&color=E2A75E)](https://www.npmjs.com/package/memoweft)
[![CI](https://img.shields.io/github/actions/workflow/status/memoweft/memoweft/ci.yml?style=flat-square&labelColor=14110B&label=CI)](https://github.com/memoweft/memoweft/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/badge/coverage-97.42%25-4A4438?style=flat-square&labelColor=14110B)](#项目状态)
[![runtime deps](https://img.shields.io/badge/运行时依赖-零-4A4438?style=flat-square&labelColor=14110B)](#项目状态)
[![license](https://img.shields.io/badge/license-MIT-4A4438?style=flat-square&labelColor=14110B)](LICENSE)

[运行 demo](#运行-demo) · [为什么不同](#它为什么不同) · [作为库接入](#作为库接入) · [Reference host](#reference-host-demo) · [文档](#文档)

[English](./README.md) · **简体中文**

</div>

## 运行 demo

仓库自带的 reference host 需要 Node.js 24 或更新版本。

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm install
npm run build
npm start -w @memoweft/host
```

打开：

```text
http://localhost:7788
```

这会启动仓库自带的 **reference host demo**。它用于展示 MemoWeft Core 如何被应用使用，不是本仓库的产品本体，也不是库本身。首次启动时，按引导填入一个 OpenAI 兼容的模型端点即可。

## 它是什么

MemoWeft 是一个供 AI 应用 `import` 的库。它负责保证记忆如何正确存在，并在宿主请求时返回相关、可追溯的用户上下文。

| 层                                    | 职责                                               |
| ------------------------------------- | -------------------------------------------------- |
| **Core**（`src/`，npm 包 `memoweft`） | 证据、事件、认知、置信度、冲突、召回与受控记忆 API |
| **Host**（`apps/memoweft-host`）      | 聊天、界面、人设、同意流程，以及何时、如何使用记忆 |
| **Plugin**（`plugins/`）              | 可选的采集器与体验扩展，受 Host 和 Core 边界约束   |

MemoWeft 不提供聊天产品、人设或界面；这些属于宿主。

## 为什么需要它

更换模型或宿主后，助手往往会丢掉此前对用户的了解。把持续增长的完整对话塞进 prompt，成本高、难追溯，也难迁移。

MemoWeft 把“对用户的理解”当作一份持久的数据资产：可以长期积累、回溯到原始证据、导出，并在不同模型和宿主之间复用。

## 它为什么不同

- **记下不等于相信。** 已保存的证据与已采信的认知不是一回事。
- **事实和猜测分开。** 模型推断先作为低置信度假设，而不是事实。
- **冲突明确暴露。** 矛盾信息不会被静默覆盖。
- **置信度由 MemoWeft 计算。** 依据证据强度与重复印证，不接受模型自报分数。
- **临时状态会淡化。** 短期情绪随时间衰减，稳定偏好继续保留。
- **每条认知都能追溯。** 判断会链接回形成它的证据。
- **助手不能自我印证。** 助手自己的输出和用户沉默都不算证据。

这些规则由 [`tests/eval/cognition-discipline.eval.test.ts`](./tests/eval/cognition-discipline.eval.test.ts) 中的编号 eval 用例验证，并随 `npm test` 运行。

## 作为库接入

安装 Core 包：

```bash
npm install memoweft
```

Node.js 24 可直接使用内置 `node:sqlite`。在 Node.js 20 或 22 上，还需要安装可选 peer 依赖 `better-sqlite3`。

配置任意 OpenAI 兼容端点：

```bash
MEMOWEFT_LLM_BASE_URL=https://your-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-...
MEMOWEFT_LLM_MODEL=gpt-4o-mini
```

然后通过公开入口创建并使用 Core：

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: './memoweft.db' });
const subjectId = 'user-42';

await core.ingestUserMessage({
  subjectId,
  content: '我下午三点后只喝无咖啡因的，咖啡因会影响睡眠。',
});

await core.updateProfile({ subjectId });

const turn = await core.handleConversationTurn({
  subjectId,
  message: '推荐一种下午喝的饮料。',
});

console.log(turn.reply);
console.log(turn.recall);

core.close();
```

可运行版本见 [`examples/minimal.ts`](./examples/minimal.ts)。其余公开入口请看 [examples 导航](./examples/README.md)和[中文接入指南](./docs/integration.zh-CN.md)。

## Reference host demo

仓库自带的 Host 是参考实现，展示应用如何只通过 Core 公开面使用 MemoWeft，而不直接访问底层 store。它覆盖带记忆召回的聊天、可见的记忆形成、证据与认知查看、记忆管理、便携记忆包，以及插件和观察数据流。

详见 [reference host 的定位与边界](./docs/reference-host.md)。

![MemoWeft reference host 聊天界面，包含记忆召回与记忆控制](assets/screenshot-chat.png)

![MemoWeft reference host 记忆图谱，连接证据、事件与认知](assets/screenshot-memory-graph.png)

![MemoWeft reference host 认知卡片，展示置信度与来源追溯](assets/screenshot-memory-manage.png)

## 生态适配器

- [`@memoweft/mcp-server`](./packages/mcp-server) 通过 Model Context Protocol 暴露受控的 MemoWeft 读写工具。
- [`@memoweft/adapter-ai-sdk`](./packages/adapter-ai-sdk) 为 Vercel AI SDK 添加召回与证据采集。

它们都是同一套 Core 规则之上的轻适配层，不改变 Core 包零运行时依赖的基线。

## 文档

从[公开文档导航](./docs/README.md)开始。

核心文档包括[接入指南](./docs/integration.zh-CN.md)、[架构说明](./docs/architecture.zh-CN.md)、[部署说明](./docs/deployment.md)、[公开记忆面契约](./docs/memory-surface-contract.zh-CN.md)和[插件契约](./docs/plugin-contract.zh-CN.md)。

## 仓库结构

- `src/` — MemoWeft Core 库。
- `apps/memoweft-host/` — 仓库自带的 reference host demo。
- `packages/` — MCP、AI SDK 等生态适配器。
- `plugins/` — 可选的采集器与体验插件。
- `examples/` — 小型接入示例。
- `docs/` — 公开文档。

## 项目状态

MemoWeft 仍处于 1.0 之前，并坚持 library-first。Core 行为已经实现并有测试覆盖，但 minor 版本之间的接口仍可能调整。稳定、实验性与内部接口的边界见 [Memory Surface Contract](./docs/memory-surface-contract.zh-CN.md)。

最新版本：**0.5.0** — `npm install memoweft`。

另见[路线图](./ROADMAP.md)、[贡献指南](./CONTRIBUTING.md)和[更新记录](./CHANGELOG.md)。

## 许可证

[MIT](./LICENSE) © 2026 MemoWeft contributors.
