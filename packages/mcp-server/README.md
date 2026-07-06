# @memoweft/mcp-server

[English](./README.md) · [简体中文](./README.zh-CN.md)

An [MCP](https://modelcontextprotocol.io) server that exposes a [MemoWeft](https://github.com/memoweft/memoweft) memory database as Model Context Protocol tools, so an external AI client (Claude Desktop, Cursor, or any MCP-capable host) can recall stored knowledge and record verbatim user messages.

This is an **external integration package**. It wraps MemoWeft's public Core facade (`createMemoWeftCore`) and does **not** re-implement any memory logic. It surfaces a deliberately narrow, mostly read-only tool surface.

## Tools

### Read (5)

| Tool | Core method | What it does |
|------|-------------|--------------|
| `memoweft_recall` | `core.recall` | Recall cognitions relevant to a query (with confidence + credibility). |
| `memoweft_list_cognitions` | `core.memory.listCognitions` | List all cognitions for a subject, with evidence links and effective confidence. |
| `memoweft_list_evidence` | `core.memory.listEvidence` | List all raw evidence (sources) for a subject. |
| `memoweft_list_events` | `core.memory.listEvents` | List all events for a subject, with the evidence ids each covers. |
| `memoweft_graph` | `core.graph.buildMemoryGraph` | Build a memory graph payload (nodes, edges, stats). |

### Write (1, light)

| Tool | Core method | What it does |
|------|-------------|--------------|
| `memoweft_ingest_user_message` | `core.ingestUserMessage` | Store a **single verbatim user message** as spoken evidence. Does not update the profile, run consolidation, or grant any cloud-read authorization. |

## SECURITY — this is an autonomous external call surface

MCP tools can be invoked **autonomously by an external AI client**, without a human approving each call. The tool surface here is chosen accordingly.

- **5 tools are read-only.** They never mutate memory.
- **1 tool is a light write** (`memoweft_ingest_user_message`): it records one raw user message as `spoken` evidence. It does **not** touch the profile, run consolidation, or change any authorization bit. `observed` evidence and cloud-read defaults are untouched.
- **Destructive and authorization-changing operations are intentionally NOT exposed as tools.** The following MemoWeft Core methods are **never** registered:
  - `invalidateCognition`, `removeEvidenceSafely`, `removeCognitionSafely`, `mergeCognition`, `archiveCognition`, `resetSubject` — destructive / data loss.
  - `updateEvidenceAuthorization` — changes the cloud-read authorization bit (privacy-sensitive).
  - `handleConversationTurn`, `updateProfile` — run the full ingestion/consolidation pipeline and rewrite the profile.
  - `ingestObservation`, `portable.*` (export/import) — bulk data movement / observation authorization surface.

  Handing any of these to an autonomous caller carries too much risk. Managing memory (deleting, merging, changing authorization, resetting) stays a human-supervised operation in the host app, not an autonomous tool.
- **Tool descriptions use neutral protocol wording.** MemoWeft Core is headless — no persona, no anthropomorphized "I remember things about you" copy.

## Install

```sh
npm install @memoweft/mcp-server
```

`memoweft` is a peer dependency (`^0.5.0`); install it alongside if it is not already present:

```sh
npm install memoweft@^0.5.0
```

## Run

The server talks MCP over stdio. It is normally launched as a subprocess by an MCP client. It requires `MEMOWEFT_DB_PATH` — the path to your MemoWeft database file (there is **no** default path; the server refuses to guess one). Use `:memory:` for an ephemeral database.

Example MCP client config (e.g. `claude_desktop_config.json`):

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

Model and embedding configuration follow MemoWeft's standard `.env` loading (see the main package). If they are missing, the server still starts; tools that need a model degrade gracefully (`core.health()` reports readiness) rather than crashing.

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

`server.json` holds the [MCP registry](https://github.com/modelcontextprotocol/registry) metadata. The name `io.github.memoweft/memoweft` is a **placeholder** GitHub namespace. The registry namespace ownership (which GitHub account/org backs `io.github.memoweft/*`) and the actual publish step are done **manually by the maintainer** (this machine has no `gh` installed). Publishing order: publish `memoweft@0.5.0`, then `@memoweft/mcp-server` to npm, then submit to the registry with the `mcp-publisher` CLI. `package.json`'s `mcpName` must stay byte-for-byte equal to `server.json`'s `name`.

## License

MIT
