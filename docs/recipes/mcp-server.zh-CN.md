# 五分钟起一个 MCP 服务器（MCP server）

[English](./mcp-server.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./mcp-server.md) 为准。

把一个 MemoWeft 数据库暴露给 Claude Desktop、Cursor 或任意 MCP 客户端——形式是一组严格限定范围的 Model Context Protocol 工具。当前接口面有 5 个读、3 个受控写；破坏性操作和授权变更不注册成工具。

## 安装

npm 已发布的服务器是 `0.1.0`；请安装这个固定且兼容的组合：

```bash
npm install memoweft@0.5.1 @memoweft/mcp-server@0.1.0
```

`main` 上的 `0.2.0` workspace 版本尚未发布。要配合 Core `0.5.1` 或 `0.6` 时，请从本检出构建；它的 peer 范围是 `^0.5.1 || ^0.6.0`。不要用 `--legacy-peer-deps` 强行把已发布的 `0.1.0` 服务器配到 Core `0.6`。需要 Node 20+。

## 让客户端连上它

这个包自带一个 `memoweft-mcp-server` 可执行文件，所以你基本不用写代码。把下面这段加进客户端配置（比如 `claude_desktop_config.json`），然后重启客户端：

```json
{
  "mcpServers": {
    "memoweft": {
      "command": "npx",
      "args": ["-y", "@memoweft/mcp-server@0.1.0"],
      "env": { "MEMOWEFT_DB_PATH": "/absolute/path/to/memoweft.db" }
    }
  }
}
```

`MEMOWEFT_DB_PATH` 是**必填**的——没有默认值，服务器不会替你猜。想要个用完即弃的数据库就填 `:memory:`。模型和 embedder 的配置沿用 MemoWeft 标准的 `.env`（见 [快速上手](../getting-started.zh-CN.md)）；就算缺了，服务器照样能启动，依赖模型的工具会降级为返回空，而不是崩溃。

## 自己起服务（自定义宿主）

如果要做自定义宿主，整个服务器就是走 stdio 的三次调用：

<!-- snippet:skip (long-running stdio server; needs an MCP client to drive it) -->

```ts
import { createCoreFromEnv, createMcpServer } from '@memoweft/mcp-server';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

const core = createCoreFromEnv(); // reads MEMOWEFT_DB_PATH; throws if unset
const server = createMcpServer(core);
await server.connect(new StdioServerTransport());
```

## 这 8 个工具

`createMcpServer` 注册的就这几个——一个白名单，别无其他。

| 工具                           | 包装的                        | 种类       |
| ------------------------------ | ----------------------------- | ---------- |
| `memoweft_recall`              | `core.recall`                 | 读         |
| `memoweft_list_cognitions`     | `core.memory.listCognitions`  | 读         |
| `memoweft_list_evidence`       | `core.memory.listEvidence`    | 读         |
| `memoweft_list_events`         | `core.memory.listEvents`      | 读         |
| `memoweft_graph`               | `core.graph.buildMemoryGraph` | 读         |
| `memoweft_ingest_user_message` | `core.ingestUserMessage`      | 写（轻量） |
| `memoweft_ingest_tool_result`  | `core.ingestToolResult`       | 写（轻量） |
| `memoweft_mute_cognition`      | `core.memory.muteCognition`   | 写（轻量） |

这三个写工具都是**轻写**。两个 ingest 工具各自只记录**一条原始证据**、别无其他；`memoweft_mute_cognition` 不记录任何证据——它只**翻转某条认知是否从召回排除**（召回负反馈），即仅从召回雪藏、认知仍 active 且继续演化画像，与置信度正交，不删、也不改任何云端读取授权。这三个都不会更新画像、不会跑固化、也不会授予任何云端读取授权。所以 `memoweft_recall` 反映的是你的宿主在别处经 `updateProfile`（配 embedder）构建出的**画像**——不是你刚通过这些工具 ingest 的原始证据。纯 MCP、没有画像步骤时，召回始终为空，这是预期行为。

连接 MCP 客户端就会授予它读取读工具返回的数据库内容，包括 evidence、cognition、event 和 graph 输出。连接前请在宿主侧落实同意与访问控制。`allowCloudRead` 只决定证据能否进入 MemoWeft 内建写路径的云模型提示词，不限制 list、cognition、event、graph 或 MCP 输出。`recall` 使用 `explain` 时会隐藏受限 provenance 的 summary，但这不等同于对 cognition 或其他 MCP 结果的端到端披露控制。

**`memoweft_recall` 的 v2 入参（可选）。** 除了 `query` / `subjectId`，召回还接受一个可选的 `contentTypes` 允许名单（只召回某些认知类型，比如 `["preference", "goal"]`）和一个可选的 `explain` 开关。每条结果都带 `contentType`；`explain: true` 时每条结果还带一条 `provenance` 链（某条记忆所依据的证据）。由于 MCP 客户端往往就是云模型，provenance 在 **tool 内按 tier 预筛**：云端可读的证据保留 `summary`，而云受限的证据只回 `{ evidenceId, relation, sourceKind }` 加授权位——summary 被隐去。

## 为什么只有这 8 个

MCP 客户端是**自主**调用工具的，没人给每次调用把关。所以这个接口面故意收得很窄。下面这些 Core 方法**永远不会**被注册成工具：

- `invalidateCognition`、`removeEvidenceSafely`、`removeCognitionSafely`、`mergeCognition`、`archiveCognition`、`resetSubject`——破坏性操作，会丢数据。
- `updateEvidenceAuthorization`——翻转云端读取开关，涉及隐私。
- `handleConversationTurn`、`updateProfile`——会跑完整的固化流水线并重写画像。
- `ingestObservation`、`portable.*`（导出/导入）——批量搬数据。

`muteCognition` 是白名单中的受控写工具（`memoweft_mute_cognition`）：它可逆、不删东西、不改云端读取授权，并与置信度正交。上面那份破坏性 / 改授权 / 整套消化的清单，其余一律不进 tool 面。

管理记忆——删除、合并、改授权、重置——始终是你宿主应用里由人监督的动作，而不是一个自主工具。

## 看看写工具到底存了什么（无需 key）

每个写工具都只是对某个 Core 调用的薄封装。下面这段无需 key、无需联网就能跑，看看这两次写入到底存了什么：

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

// memoweft_ingest_user_message wraps this → spoken evidence.
await core.ingestUserMessage({ subjectId: 'alice', content: 'I own a red bicycle.' });

// memoweft_ingest_tool_result wraps this → tool evidence (local-only by default).
await core.ingestToolResult({ subjectId: 'alice', content: 'weather: 28C, sunny' });

for (const e of core.memory.listEvidence({ subjectId: 'alice' })) {
  console.log(e.sourceKind, '·', e.rawContent);
}
// → spoken · I own a red bicycle.
// → tool   · weather: 28C, sunny

core.close();
```

`sourceKind` 记录的是谁说的、怎么来的——用户亲口说的（`spoken`）和工具的输出（`tool`）是不同种类的事实（见 [Concepts](../concepts/README.zh-CN.md)）。

## 下一步

- **[Getting started](../getting-started.zh-CN.md)** —— Core 基础和 `.env` 模型配置。
- **[API reference](../reference/memory-surface-contract.zh-CN.md)** —— 每个工具返回的载荷结构。
- **[Run the demo](../demo-script.md)** —— `npm run demo` 端到端演示召回、纠正和冲突。
