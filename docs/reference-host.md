# Reference Host Demo

**English** | [简体中文](./reference-host.zh-CN.md)

The bundled host under `apps/memoweft-host` is a reference implementation for a **local, single-user demo**.

It exists to demonstrate how a host can use MemoWeft Core. It is not the main product of this repository and is **not a production deployment template**.

MemoWeft itself is the library exposed by:

```ts
import { createMemoWeftCore } from 'memoweft';
```

## What the demo shows

The reference host demonstrates:

- chat with memory recall;
- visible memory formation;
- evidence and cognition inspection;
- memory management;
- export and import;
- plugin and observation flows.

## What the host owns

A host owns:

- UI;
- chat experience;
- persona and tone;
- privacy prompts;
- when to trigger `updateProfile()`;
- how to display recalled context;
- how users manage memory.

It also owns authentication, tenant isolation, encryption at rest, backup and restore operations, observability, and the process lifecycle. Those responsibilities are deliberately outside Core.

## What MemoWeft Core owns

MemoWeft Core owns:

- evidence storage;
- event distillation;
- cognition formation;
- confidence computation;
- conflict handling;
- recall;
- controlled memory-management APIs;
- portable memory bundles.

## Run the demo

The reference host requires Node.js 24 or newer.

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm start -w @memoweft/host
```

Open:

```text
http://localhost:7788
```

On first run, the setup UI writes model settings to `apps/memoweft-host/.env`. The default SQLite database is `apps/memoweft-host/data/host.db`; the host's chat-history JSONL files live beside it in `apps/memoweft-host/data/sessions/`. Both are local state and must be treated as private user data. Set `MEMOWEFT_HOST_DB` before startup to use a different database path; session files then follow that path's directory.

The server listens on `127.0.0.1` only and defaults to port `7788` (`PORT` overrides it). State-changing requests are limited to loopback hosts, same-origin JSON, a per-process session token, and a 5 MiB body limit. These are local-demo safeguards, not user authentication or tenant isolation. The host is not externally reachable by default; do not expose it through a reverse proxy or bind it to a public interface unchanged.

## Demo path versus production

Use the reference host to inspect the host boundary and demo the flow locally. For a fresh, offline proof of MemoWeft itself, use the [30-second offline demo](./demo-script.md); it does not start this server or need a model endpoint.

Before shipping a real host, work through the [production deployment checklist](./deployment.md#production-checklist). In particular, replace this demo's single local process and shared default subject with your own authenticated tenant boundary, database placement, operations, and user-facing consent policy.
