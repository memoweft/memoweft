# MCP server in five minutes

**English** | [简体中文](./mcp-server.zh-CN.md)

Expose a MemoWeft database to Claude Desktop, Cursor, or any MCP client through a tightly scoped set of Model Context Protocol tools. The current surface contains five reads and three controlled writes; destructive operations and authorization changes are not registered.

## Install

The npm-published server is `0.1.0`; install this fixed, compatible pair:

```bash
npm install memoweft@0.5.1 @memoweft/mcp-server@0.1.0
```

The `0.2.0` workspace version on `main` is unreleased. Build it from this checkout for Core `0.5.1` or `0.6`; its peer range is `^0.5.1 || ^0.6.0`. Do not use `--legacy-peer-deps` to force the published `0.1.0` server onto Core `0.6`. Needs Node 20+.

## Point a client at it

The package ships a `memoweft-mcp-server` bin, so you rarely write code. Add this to your client config (e.g. `claude_desktop_config.json`) and restart the client:

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

`MEMOWEFT_DB_PATH` is **required** — there is no default; the server refuses to guess. Use `:memory:` for a throwaway database. Model and embedder config follow MemoWeft's standard `.env` (see [Getting started](../getting-started.md)); if missing, the server still starts and model-backed tools degrade to empty instead of crashing.

## Start it yourself (custom host)

For a custom host, the whole server is three calls over stdio:

<!-- snippet:skip (long-running stdio server; needs an MCP client to drive it) -->

```ts
import { createCoreFromEnv, createMcpServer } from '@memoweft/mcp-server';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

const core = createCoreFromEnv(); // reads MEMOWEFT_DB_PATH; throws if unset
const server = createMcpServer(core);
await server.connect(new StdioServerTransport());
```

## The 8 tools

`createMcpServer` registers exactly these — a whitelist, nothing more.

| Tool                           | Wraps                         | Kind          |
| ------------------------------ | ----------------------------- | ------------- |
| `memoweft_recall`              | `core.recall`                 | read          |
| `memoweft_list_cognitions`     | `core.memory.listCognitions`  | read          |
| `memoweft_list_evidence`       | `core.memory.listEvidence`    | read          |
| `memoweft_list_events`         | `core.memory.listEvents`      | read          |
| `memoweft_graph`               | `core.graph.buildMemoryGraph` | read          |
| `memoweft_ingest_user_message` | `core.ingestUserMessage`      | write (light) |
| `memoweft_ingest_tool_result`  | `core.ingestToolResult`       | write (light) |
| `memoweft_mute_cognition`      | `core.memory.muteCognition`   | write (light) |

All three write tools are **light writes**. The two ingest tools each record **one raw piece of evidence** and nothing more; `memoweft_mute_cognition` records no evidence at all — it only **toggles whether one cognition is excluded from recall** (recall negative feedback), which hides it from recall while it stays active and keeps evolving the profile, orthogonal to confidence, without deleting it or changing any cloud-read authorization. None of the three update the profile, run consolidation, or grant any cloud-read authorization. So `memoweft_recall` reflects the **profile** your host builds elsewhere via `updateProfile` (with an embedder) — not the raw evidence you just ingested through these tools. In a pure-MCP setup with no profile step, recall stays empty; that is expected.

Connecting an MCP client grants that client access to database content returned through the read tools, including evidence, cognitions, events, and graph output. Apply host-side consent and access controls before connecting a client. `allowCloudRead` qualifies evidence only for MemoWeft's built-in cloud-model write prompts; it does not restrict list, cognition, event, graph, or MCP output. `recall` with `explain` hides summaries for restricted provenance, but that is not end-to-end disclosure control for cognitions or other MCP results.

**`memoweft_recall` v2 inputs (optional).** Besides `query` / `subjectId`, recall takes an optional `contentTypes` allow-list (recall only certain cognition types, e.g. `["preference", "goal"]`) and an optional `explain` flag. Every result carries its `contentType`; with `explain: true` each result also carries a `provenance` chain (the evidence a memory is built on). Because an MCP client is often a cloud model, provenance is **tier pre-filtered inside the tool**: cloud-readable evidence keeps its `summary`, while cloud-restricted evidence returns only `{ evidenceId, relation, sourceKind }` plus its authorization bits — the summary is withheld.

## Why only these 8

An MCP client invokes tools **autonomously**, with no human approving each call. So the surface is narrow on purpose. These Core methods are **never** registered as tools:

- `invalidateCognition`, `removeEvidenceSafely`, `removeCognitionSafely`, `mergeCognition`, `archiveCognition`, `resetSubject` — destructive, data loss.
- `updateEvidenceAuthorization` — flips the cloud-read bit; privacy-sensitive.
- `handleConversationTurn`, `updateProfile` — run the full consolidation pipeline and rewrite the profile.
- `ingestObservation`, `portable.*` (export/import) — bulk data movement.

`muteCognition` is the whitelisted controlled write (`memoweft_mute_cognition`): it is reversible, deletes nothing, changes no cloud-read authorization, and is orthogonal to confidence. Everything else on the destructive / authorization-changing / full-digestion list above stays off the tool surface.

Managing memory — deleting, merging, changing authorization, resetting — stays a human-supervised action in your host app, not an autonomous tool.

## See what the write tools store (no key)

Each write tool is a thin wrapper over a Core call. Run this with no key and no network to see exactly what the two writes persist:

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

`sourceKind` records who said it and how — a user's words (`spoken`) and a tool's output (`tool`) are different kinds of fact (see [Concepts](../concepts/)).

## Next

- **[Getting started](../getting-started.md)** — Core basics and `.env` model config.
- **[API reference](../reference/memory-surface-contract.md)** — the payload shapes each tool returns.
- **[Run the demo](../demo-script.md)** — `npm run demo` shows recall, correction, and conflict end to end.
