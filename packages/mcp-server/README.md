# @memoweft/mcp-server

[English](./README.md) · [简体中文](./README.zh-CN.md)

An [MCP](https://modelcontextprotocol.io) server that exposes a [MemoWeft](https://github.com/memoweft/memoweft) database to an external AI client. It uses MemoWeft's public Core facade and registers exactly eight tools: five reads and three controlled writes.

## Install

The npm-published server is `@memoweft/mcp-server@0.1.0`. Install it with its compatible Core release:

```sh
npm install memoweft@0.5.1 @memoweft/mcp-server@0.1.0
```

The `0.2.0` package on `main` is an unreleased workspace version. Build it from this checkout when using Core `0.5.1` or `0.6`; its peer range is `^0.5.1 || ^0.6.0`. Do not use `--legacy-peer-deps` to force npm's `0.1.0` package onto Core `0.6`.

## Tools and data access

| Tool                           | Core method                   | Kind             |
| ------------------------------ | ----------------------------- | ---------------- |
| `memoweft_recall`              | `core.recall`                 | read             |
| `memoweft_list_cognitions`     | `core.memory.listCognitions`  | read             |
| `memoweft_list_evidence`       | `core.memory.listEvidence`    | read             |
| `memoweft_list_events`         | `core.memory.listEvents`      | read             |
| `memoweft_graph`               | `core.graph.buildMemoryGraph` | read             |
| `memoweft_ingest_user_message` | `core.ingestUserMessage`      | controlled write |
| `memoweft_ingest_tool_result`  | `core.ingestToolResult`       | controlled write |
| `memoweft_mute_cognition`      | `core.memory.muteCognition`   | controlled write |

The two ingest tools record one raw evidence item each; they do not update the profile, run consolidation, or change cloud-read authorization. `mute_cognition` only toggles whether an existing cognition participates in recall; it does not delete it, alter its confidence, or change authorization. Destructive operations, authorization changes, profile updates, observations, and portable import/export are not registered.

Connecting an MCP client grants that client access to database content returned by these read tools, including evidence, cognitions, events, and graph output. Treat the client as a database reader and apply host-level consent and access controls before configuring it. `allowCloudRead` only qualifies evidence for MemoWeft's built-in cloud-model write prompts; it does not constrain the list, cognition, event, graph, or MCP output surfaces.

`memoweft_recall` accepts optional `contentTypes` and `explain`. With `explain`, restricted provenance omits its summary, but this is not end-to-end disclosure control for cognitions or other MCP results.

## Run

The server talks MCP over stdio. It requires `MEMOWEFT_DB_PATH`; use `:memory:` for an ephemeral database.

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

## Programmatic use

```ts
import { createMemoWeftCore } from 'memoweft';
import { createMcpServer } from '@memoweft/mcp-server';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

const core = createMemoWeftCore({ dbPath: process.env.MEMOWEFT_DB_PATH! });
const server = createMcpServer(core);
await server.connect(new StdioServerTransport());
```

## Registry

`io.github.memoweft/memoweft` version `0.1.0` is active in the official MCP registry and points to the published npm package. This repository's `server.json` is the candidate metadata for the unreleased `0.2.0` server; update and validate it only when that npm package is ready to publish. `package.json`'s `mcpName` must remain identical to `server.json`'s `name`.

## License

MIT
