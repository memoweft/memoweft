# Deployment, privacy, and production operations

**English** | [简体中文](./deployment.zh-CN.md)

MemoWeft is an embedded SQLite library, not a managed service. It can use local or OpenAI-compatible cloud models, while evidence-level routing flags control what its built-in write-model prompts may include.

For a local proof that needs no endpoint, key, network, or persistent database, run the [offline demo](./demo-script.md). The bundled [reference host](./reference-host.md) is a local, single-user example—not a production template.

## Model and retrieval configuration

The minimum model-backed setup uses one chat endpoint. A separate write-path model and an embedder are optional.

```ini
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# Optional: a separate model for distill, consolidate, and attribute
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model
MEMOWEFT_WRITE_LLM_TIER=cloud

# Optional: semantic/vector recall
MEMOWEFT_EMBED_BASE_URL=https://your-embedding-endpoint/v1
MEMOWEFT_EMBED_API_KEY=your-embedding-key
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

The legacy `DLA_*` aliases remain supported, but new deployments should use `MEMOWEFT_*` names. Without an embedder, Core normally uses local FTS5 keyword recall; it falls back to empty recall only when FTS5 is unavailable.

`MEMOWEFT_WRITE_LLM_TIER=cloud|local` selects which evidence-eligibility flags MemoWeft applies to the write endpoint:

- `cloud` reads evidence with `allowCloudRead=true`;
- `local` reads evidence with `allowLocalRead=true`.

The tier is a declaration, not endpoint verification. Labeling a cloud URL as `local` does not keep data on the device. A deployment currently chooses one write model and tier per profile-update run; per-evidence routing across two write models is not built in.

## Privacy boundary

- `allowCloudRead` limits evidence selection for MemoWeft's built-in cloud write-model prompts. It does not restrict recall, list/read APIs, MCP tools, adapter prompt injection, derived cognitions/events/graphs, custom host code, exports, or logs. It is not access control.
- `observed` evidence and tool results default to no cloud read. The host still needs clear consent, review, authorization-change, and deletion flows.
- The SQLite database is not encrypted by MemoWeft. Disk, volume, or application-level encryption is the host's responsibility.
- Core does not provide authentication, authorization, tenant isolation, key management, or a compliance policy.
- Assistant replies can be retained as interaction context by built-in conversation helpers, but built-in ingestion does not turn them into evidence. Custom integrations must preserve role boundaries themselves.

## Production checklist

Complete and test these controls in the application that hosts MemoWeft.

### Process, network, and tenancy

- [ ] Run a supervised process with a restart policy, bounded logs, and graceful shutdown. Call `core.close()` after in-flight work finishes.
- [ ] Bind only permitted network interfaces. Provide TLS, authentication, authorization, rate limits, and request-size limits at the host or edge boundary.
- [ ] Define the tenant boundary before writing data. Authenticate each request, enforce authorization before each memory operation, and pass a tenant-scoped `subjectId`.
- [ ] Do not share a SQLite file, default `subjectId`, conversation cache, or exported bundle across users without a separately enforced isolation design.
- [ ] Do not expose the reference host unchanged. It binds to `127.0.0.1` and intentionally has no authentication or multi-tenant isolation.

### Persistent storage and recovery

- [ ] Put the SQLite database and host-owned session state on a durable, access-controlled volume. Do not rely on an ephemeral container filesystem.
- [ ] Encrypt disks or volumes at rest and restrict database-file permissions.
- [ ] Back up the database and host state on a documented schedule. Encrypt backups, restrict access, and set retention.
- [ ] Test restoration in an isolated environment and verify that representative memory can be read and recalled.
- [ ] Use portable bundles for user export/import. Validate with `dryRun` before `merge`; rebuild derived recall indexes when required.

### Secrets and model routing

- [ ] Inject model credentials through a secret manager or protected runtime environment. Never commit `.env`, databases, exported bundles, or logs containing user memory.
- [ ] Limit secret access, rotate credentials, and redact diagnostics.
- [ ] Document the chosen write tier and verify the endpoint behind it. The tier setting alone is not a security control.
- [ ] Review evidence authorization defaults and every custom path that can send, export, or log memory content.

### Schema and release operations

- [ ] Pin a MemoWeft version and read its changelog before upgrading.
- [ ] Test startup and migrations against a copy of production data, then back up before deployment.
- [ ] Deploy one compatible version at a time unless concurrent access by old and new versions has been tested.
- [ ] Verify the database path, mount, ownership, and permissions after deployment so a path error cannot silently create an empty database.

### Scheduling and observability

- [ ] Schedule `core.updateProfile()` outside the latency-sensitive reply path. Batch by turns, run after idle time, use a periodic job, or expose a user-triggered refresh.
- [ ] Prevent overlapping profile updates for the same subject.
- [ ] Schedule `core.expire()` as a periodic maintenance job (for example daily, or after a profile update). It marks transient cognitions (`state`/`hypothesis`/`trend`) that have passed their `expireAfterDays` window as invalid so they stop being recalled. It is idempotent and rule-only (no LLM or embedder), does not delete (it sets `invalidAt`, kept traceable), and is intentionally decoupled from `updateProfile`—without a scheduled call, transient memory never expires.
- [ ] Treat `core.health()` as a configuration signal. `embedReady: false` means vector recall is unavailable, not necessarily that all recall is unavailable.
- [ ] Use `core.usage()` deltas only for Core-owned clients whose endpoints return usage; it is not a universal billing meter.
- [ ] Alert on failed profile updates, repeated model timeouts, database-open errors, backup failures, restore-test failures, and unexpected empty databases—without logging raw evidence.

## Acceptance exercise

Before launch, rehearse the complete lifecycle:

1. deploy to an empty persistent volume and store representative synthetic memory;
2. restart the process and verify that data survives;
3. restore a backup into an isolated environment;
4. verify that tenant A cannot read, export, or recall tenant B;
5. run a profile update and inspect health and usage signals;
6. rotate a non-production model credential.

Record the result and owner for each control. A checklist that has not been exercised is not a recovery or isolation guarantee.
