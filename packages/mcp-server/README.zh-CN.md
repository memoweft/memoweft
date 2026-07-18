# @memoweft/mcp-server

[English](./README.md) · [简体中文](./README.zh-CN.md)

这是一个 [MCP](https://modelcontextprotocol.io) 服务器，把 [MemoWeft](https://github.com/memoweft/memoweft) 数据库暴露给外部 AI 客户端。它只调用公开 Core 门面，恰好注册 8 个工具：5 个读、3 个受控写。

## 安装

npm 已发布的服务器是 `@memoweft/mcp-server@0.1.0`。请与兼容的 Core 版本一起安装：

```sh
npm install memoweft@0.5.1 @memoweft/mcp-server@0.1.0
```

`main` 上的 `0.2.0` 是尚未发布的 workspace 版本。要配合 Core `0.5.1` 或 `0.6` 使用时，请从本检出构建；它的 peer 范围是 `^0.5.1 || ^0.6.0`。不要用 `--legacy-peer-deps` 强行把 npm 上的 `0.1.0` 配到 Core `0.6`。

## 工具与数据访问

| 工具                           | Core 方法                     | 种类   |
| ------------------------------ | ----------------------------- | ------ |
| `memoweft_recall`              | `core.recall`                 | 读     |
| `memoweft_list_cognitions`     | `core.memory.listCognitions`  | 读     |
| `memoweft_list_evidence`       | `core.memory.listEvidence`    | 读     |
| `memoweft_list_events`         | `core.memory.listEvents`      | 读     |
| `memoweft_graph`               | `core.graph.buildMemoryGraph` | 读     |
| `memoweft_ingest_user_message` | `core.ingestUserMessage`      | 受控写 |
| `memoweft_ingest_tool_result`  | `core.ingestToolResult`       | 受控写 |
| `memoweft_mute_cognition`      | `core.memory.muteCognition`   | 受控写 |

两个 ingest 工具各自只记录一条原始证据，不更新画像、不跑固化、也不改云端读取授权。`mute_cognition` 只切换既有认知是否参与召回，不删除它、不改置信度、也不改授权。破坏性操作、授权变更、画像更新、observation 以及 portable 导入/导出都不会注册成工具。

连接 MCP 客户端就会授予该客户端读取这些读工具返回的数据库内容，包括 evidence、cognition、event 和 graph 输出。配置前应把客户端视为数据库读取方，并在宿主侧落实同意与访问控制。`allowCloudRead` 只决定证据是否可进入 MemoWeft 内建写路径的云模型提示词；它不限制 list、cognition、event、graph 或 MCP 输出面。

`memoweft_recall` 接受可选的 `contentTypes` 和 `explain`。使用 `explain` 时，受限 provenance 会隐藏 summary；这不等同于对 cognition 或其他 MCP 结果的端到端披露控制。

## 运行

服务器通过 stdio 讲 MCP，必须提供 `MEMOWEFT_DB_PATH`；使用 `:memory:` 可获得临时数据库。

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

## 编程方式使用

```ts
import { createMemoWeftCore } from 'memoweft';
import { createMcpServer } from '@memoweft/mcp-server';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

const core = createMemoWeftCore({ dbPath: process.env.MEMOWEFT_DB_PATH! });
const server = createMcpServer(core);
await server.connect(new StdioServerTransport());
```

## Registry（注册表）

`io.github.memoweft/memoweft` 的 `0.1.0` 已在官方 MCP registry 处于 active 状态，并指向已发布的 npm 包。本仓库的 `server.json` 是尚未发布的 `0.2.0` 服务器候选元数据；只有该 npm 包准备发布时才更新并验证它。`package.json` 的 `mcpName` 必须与 `server.json` 的 `name` 完全一致。

## 许可

MIT
