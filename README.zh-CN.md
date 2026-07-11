<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="assets/hero-light.svg">
  <img alt="MemoWeft —— 面向 AI 应用的长期记忆，区分事实与猜测" src="assets/hero-dark.svg" width="100%">
</picture>

# MemoWeft

**面向 AI 应用的可迁移长期记忆：分清事实、猜测、冲突与过期状态。**

[![npm](https://img.shields.io/npm/v/memoweft?style=flat-square&labelColor=14110B&color=E2A75E)](https://www.npmjs.com/package/memoweft)
[![CI](https://img.shields.io/github/actions/workflow/status/memoweft/memoweft/ci.yml?style=flat-square&labelColor=14110B&label=CI)](https://github.com/memoweft/memoweft/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/badge/coverage-97.42%25-4A4438?style=flat-square&labelColor=14110B)](#项目状态)
[![runtime deps](https://img.shields.io/badge/运行时依赖-零-4A4438?style=flat-square&labelColor=14110B)](#项目状态)
[![license](https://img.shields.io/badge/license-MIT-4A4438?style=flat-square&labelColor=14110B)](LICENSE)

[为什么不同](#为什么不是又一个记忆库) · [安装](#60-秒安装与首次调用) · [Reference host](#试试-reference-host) · [文档](#接下来去哪)

[English](./README.md) · **简体中文**

</div>

![MemoWeft reference host 演示——聊天、记忆实时成形、以及连接证据/事件/认知的记忆图谱](assets/reference-host-demo.gif)

MemoWeft 是一个供 AI 应用 `import` 的库。它为用户保存可迁移、可追溯的长期记忆——分清事实与猜测、暴露冲突而非静默覆盖，并让不同宿主复用同一份记忆。

## 为什么不是又一个记忆库

- **事实和猜测分开。** 模型推断先作为低置信度假设、绝不是事实——用户真正说的，和模型猜的，是两种不同的记录。
- **冲突暴露，不覆盖。** 矛盾信息被并排暴露、保留；MemoWeft 绝不静默选出赢家。
- **置信度由规则算，不靠自报。** 每条认知按证据强度与重复印证打分——模型永远不能自定可信度。

另有三条纪律（分类衰减、可追溯、不自我印证）由 [`tests/eval/`](./tests/eval/) 里的编号 eval 用例背书——跑 `npm test`。

## 60 秒安装与首次调用

```bash
npm install memoweft   # Node 24 用内置 node:sqlite；Node 20/22 还需 `npm i better-sqlite3`
```

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: './memoweft.db' });
await core.ingestUserMessage({ subjectId: 'user-42', content: '我下午三点后只喝无咖啡因的，咖啡因会影响睡眠。' });
await core.updateProfile({ subjectId: 'user-42' });   // 需要一个 OpenAI 兼容模型（.env）

const turn = await core.handleConversationTurn({ subjectId: 'user-42', message: '推荐一种下午喝的饮料。' });
console.log(turn.reply);
core.close();
```

没有 API key？[`examples/no-key-demo.ts`](./examples/no-key-demo.ts) 用离线 stub 跑同一条写路径——约 30 秒看一个冲突被暴露（而不是被覆盖）。

## 试试 reference host

仓库自带的 reference host 是**演示，不是产品**。它展示应用如何使用 Core——带召回的聊天、看记忆实时成形、查看 证据 → 事件 → 认知 的图谱。需要 Node 24+。

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft && npm install && npm run build
npm start -w @memoweft/host    # 然后打开 http://localhost:7788
```

更多：[reference host 的定位与边界](./docs/reference-host.md)。

## 接下来去哪

- **[快速上手](./docs/getting-started.zh-CN.md)** —— 装好、存一条证据、读回来。五分钟。
- **[核心概念](./docs/concepts/README.zh-CN.md)** —— 六条认知纪律，每条一屏。
- **[接入配方](./docs/recipes/)** —— 五分钟把 MemoWeft 接进 [Vercel AI SDK](./packages/adapter-ai-sdk) 或 [MCP server](./packages/mcp-server)。

完整文档导航：[`docs/README.md`](./docs/README.md)。

## 项目状态

仍处于 1.0 之前、坚持 library-first。Core 已实现并有测试覆盖，但 minor 版本之间的接口仍可能调整——稳定、实验性与内部接口的边界见[记忆面契约](./docs/reference/memory-surface-contract.zh-CN.md)。**零运行时依赖。**

另见[路线图](./ROADMAP.md)、[贡献指南](./CONTRIBUTING.md)和[更新记录](./CHANGELOG.md)。

## 许可证

[MIT](./LICENSE) © 2026 MemoWeft contributors.
