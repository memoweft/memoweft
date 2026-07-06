# @memoweft/mcp-server

[English](./README.md) · [简体中文](./README.zh-CN.md)

一个 [MCP](https://modelcontextprotocol.io) 服务器，把 [MemoWeft](https://github.com/memoweft/memoweft) 的记忆库暴露成 Model Context Protocol 工具，让外部 AI 客户端（Claude Desktop、Cursor，或任何支持 MCP 的宿主）能召回已存的知识、记录用户原话。

这是一个**外部集成包**。它包住 MemoWeft 的公开 Core 门面（`createMemoWeftCore`），**不**重新实现任何记忆逻辑，只暴露一个刻意收窄、以只读为主的工具面。

## 工具

### 读（5 个）

| 工具 | Core 方法 | 作用 |
|------|-----------|------|
| `memoweft_recall` | `core.recall` | 按 query 召回相关认知（带置信度 + 可信状态）。 |
| `memoweft_list_cognitions` | `core.memory.listCognitions` | 列某 subject 的全部认知，含溯源链与有效置信。 |
| `memoweft_list_evidence` | `core.memory.listEvidence` | 列某 subject 的全部原始证据（来源）。 |
| `memoweft_list_events` | `core.memory.listEvents` | 列某 subject 的全部事件，含每条覆盖的证据 id。 |
| `memoweft_graph` | `core.graph.buildMemoryGraph` | 产出记忆图谱 payload（nodes / edges / stats）。 |

### 写（1 个，轻）

| 工具 | Core 方法 | 作用 |
|------|-----------|------|
| `memoweft_ingest_user_message` | `core.ingestUserMessage` | 存**一句用户原话**为 spoken 证据。不改画像、不做消化、不授予任何上云授权。 |

## SECURITY —— 这是一个"外部自主调用面"

MCP 工具会被**外部 AI 客户端自主调用**，不一定有人逐次审批。这里的工具面就是按这个前提挑的。

- **5 个工具只读。** 绝不改动记忆。
- **1 个工具是轻写**（`memoweft_ingest_user_message`）：只把一句用户原话记为 `spoken` 证据。**不**碰画像、不做消化、不改任何授权位。`observed` 证据与"默认不上云"口径不受影响。
- **破坏性 / 改授权 的操作刻意【不】暴露成工具。** 以下 MemoWeft Core 方法**绝不**注册：
  - `invalidateCognition`、`removeEvidenceSafely`、`removeCognitionSafely`、`mergeCognition`、`archiveCognition`、`resetSubject` —— 破坏性 / 会丢数据。
  - `updateEvidenceAuthorization` —— 改"能否上云"的授权位（隐私敏感）。
  - `handleConversationTurn`、`updateProfile` —— 触发整套摄入 / 消化管线、重写画像。
  - `ingestObservation`、`portable.*`（导出 / 导入）—— 批量数据搬运 / 观察授权面。

  把上面任何一个交给自主调用方，风险都过大。管理记忆（删除、合并、改授权、恢复出厂）应留在宿主 App 里由人监督，而不是做成自主工具。
- **工具描述用中性协议措辞。** MemoWeft Core 无头——没有人设，不写"我回忆起关于你的事"这类拟人文案。

## 安装

```sh
npm install @memoweft/mcp-server
```

`memoweft` 是 peer 依赖（`^0.5.0`），若尚未安装请一并装上：

```sh
npm install memoweft@^0.5.0
```

## 运行

服务器经 stdio 讲 MCP，通常由 MCP 客户端拉起为子进程。它需要 `MEMOWEFT_DB_PATH`——你的 MemoWeft 记忆库文件路径（**没有**缺省路径；服务器拒绝擅自猜）。用 `:memory:` 得到一次性内存库。

MCP 客户端配置示例（如 `claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "memoweft": {
      "command": "npx",
      "args": ["-y", "@memoweft/mcp-server"],
      "env": {
        "MEMOWEFT_DB_PATH": "/absolute/path/to/memoweft.db"
      }
    }
  }
}
```

模型 / 嵌入配置沿用 MemoWeft 的标准 `.env` 加载（见主包）。缺配时服务器照常启动；需要模型的工具走优雅降级（`core.health()` 报能力就绪状态），而不是崩溃。

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

`server.json` 存 [MCP registry](https://github.com/modelcontextprotocol/registry) 元数据。名字 `io.github.memoweft/memoweft` 是**占位**的 GitHub 命名空间。registry 命名空间归属（`io.github.memoweft/*` 背后是哪个 GitHub 账号 / 组织）与实际发布步骤都由**作者手动**完成（本机没装 `gh`）。发布顺序：先发 `memoweft@0.5.0`，再发 `@memoweft/mcp-server` 到 npm，最后用 `mcp-publisher` CLI 提交到 registry。`package.json` 的 `mcpName` 必须与 `server.json` 的 `name` 逐字一致。

## 许可

MIT
