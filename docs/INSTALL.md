# Install & Quick Start · MemoWeft

**English** | [简体中文](./INSTALL.zh-CN.md)

> MemoWeft is a "user cognition layer" that wraps **around** an LLM / agent. It is a **library** the host `import`s — it does not do chat, personas, or UI itself.
>
> This guide takes you from **install → configure env → see it working in 15 minutes**. The recommended default is a cloud OpenAI-compatible endpoint for the fastest start; local / hybrid models are an advanced option.

---

## 0. Prerequisites

| Requirement | Notes |
| --- | --- |
| **Node ≥ 24 (works out of the box) or Node 20/22 + `better-sqlite3` (fallback)** | Storage is backed by SQLite. The driver **prefers the built-in `node:sqlite`** and only falls back to the optional `better-sqlite3` if that cannot be loaded. On Node ≥ 24 `node:sqlite` is stable and needs zero extra deps; on Node 20 it is absent, so you **must** install `better-sqlite3` (`npm i better-sqlite3`, see §1.2); on Node 22 it depends on whether your version / flags already expose `node:sqlite` — install `better-sqlite3` only when it is not available. |
| **One OpenAI-compatible chat model endpoint** | The recommended default is a cloud endpoint: least setup, easiest for a developer to get running. Anything compatible with `/chat/completions` works. |
| **Optional: a small/fast write-path model endpoint** | Used for `distill → consolidate → attribute`. If unset, it falls back to the chat model. |
| **Optional: an embedding endpoint** | Used for semantic recall. If unset, recall degrades to empty — the profile is still written, only long-term cognition is not injected. |
| **Zero runtime dependencies** | Runtime `dependencies` is empty; storage / HTTP / vector math all use Node built-ins. `better-sqlite3` is only an **optional peer dependency** — Node ≥ 24 users never need to install it. |

> ⚙️ **Install the optional `better-sqlite3` driver when `node:sqlite` cannot be loaded.** MemoWeft **prefers the built-in `node:sqlite`** and only falls back to `better-sqlite3` (a native module, see §1.2) when it cannot be loaded. `node:sqlite` only became stable in Node 24; on Node 20 it is absent, so you **must** install `better-sqlite3`; on Node 22 availability depends on your Node version / flags — install `better-sqlite3` only when it is not available (once installed it is picked only when `node:sqlite` cannot be loaded, and never overrides an already-usable built-in driver). Developing the library itself (running the `.ts` examples / testbench from the repo) has a stricter bar: Node 22 needs 22.18+ to strip `.ts` types natively and Node 20 cannot do it at all — use Node ≥ 24 to run `.ts`. **Using it as a library** (`import 'memoweft'`, which consumes the compiled `.js`) only needs a usable driver.

> ℹ️ **Cloud-first, not cloud-blind.** MemoWeft recommends starting with a cloud endpoint for speed, but every piece of evidence still carries authorization bits such as `allowCloudRead`. The host owns the privacy policy and consent UI; MemoWeft keeps the model-swap and filtering hooks. See the full modes in [`deployment.md`](./deployment.md).

---

## 1. Install

### 1.1 As a library (`npm install`)

MemoWeft is published on npm. Host developers install it directly:

```bash
npm install memoweft
```

Then `import { createMemoWeftCore } from 'memoweft'` (usage in the README "As a library" section / [`integration.md`](./integration.md)). The install has **zero runtime dependencies** (Node ≥ 24 uses the built-in `node:sqlite`). TypeScript projects just add `@types/node` as usual.

### 1.2 Node 20/22: install the optional `better-sqlite3` driver

On Node ≥ 24 you are done. On **Node 20/22** the built-in `node:sqlite` may be unavailable, so install the optional driver:

```bash
npm i better-sqlite3
```

Once installed, MemoWeft picks it as the backing driver automatically when it opens the database; everything else is identical. A few notes:

- `better-sqlite3` is a **native module**. It usually installs a prebuilt binary in seconds; if there is no matching prebuilt for your platform / Node version, it falls back to compiling with `node-gyp` (which needs Python + a C++ toolchain). So a successful install is **not guaranteed** — the most reliable path when it fails is upgrading Node to ≥ 24 (built-in driver, zero deps).
- If it is not installed and you are not on Node ≥ 24, `import 'memoweft'` throws a plain-language error listing the two ways out (upgrade to Node ≥ 24 / install `better-sqlite3`).

### 1.3 From source (developing the library / running the reference host & testbench)

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm install        # dev dependencies only; no runtime deps
npm run typecheck && npm test && npm run build   # all green = environment ready
```

The reference host `npm start -w @memoweft/host` (:7788) and the testbench `npm run testbench` (:7888) both run from source. For the publish flow, see [`PUBLISHING.md`](./PUBLISHING.md).

---

## 2. Configure `.env`

Create a `.env` at the repo root, next to `package.json`. You can also copy `.env.example`:

```bash
cp .env.example .env
```

### 2.1 Env naming: new names primary, old names still work

The code reads the `MEMOWEFT_*` primary names first and falls back to the legacy `DLA_*` names:

- **New installs**: always use `MEMOWEFT_*`.
- **Existing users**: an existing `.env` with `DLA_*` keeps working.
- If neither prefix is set: a real model call errors.

---

## 3. Recommended config: Cloud-first

This is the most recommended path for newcomers / developers: use cloud OpenAI-compatible endpoints for everything and get the pipeline running first.

```ini
# ── Chat model (required): read path / reply quality ─────────────
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# ── Write-path model (optional): distill events / profile / attribution ─
# Falls back to the chat model if unset; a small/fast model keeps profile updates cheap.
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model

# ── Embedder (optional): semantic recall ────────────────────────
# If unset, recall degrades to empty; evidence and profile writes are unaffected.
MEMOWEFT_EMBED_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_EMBED_API_KEY=sk-xxxx
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

This mode has the least setup and is good for:

- quickly trying out the testbench;
- letting other developers integrate with minimal cost;
- validating the main `chat → remember → recall → inject into reply` loop first.

---

## 4. Privacy baseline: Cloud-guarded

For real applications, move from Cloud-first to Cloud-guarded: models can still be cloud-hosted, but you control at the evidence level what content is allowed into cloud prompts.

Recommended defaults:

| Evidence source | Default cloud policy | Rationale |
| --- | --- | --- |
| User chat / explicitly entered memory | Host may default to `allowCloudRead=true` | The user is already interacting with the AI host. |
| User-manually-approved observations | Host decides | Needs a clear consent toggle. |
| Desktop window / device observations | Default `allowCloudRead=false` | May contain app names, window titles, file paths. |
| Screen OCR / clipboard / file contents | Default `allowCloudRead=false` | High-risk private content. |
| Sleep / heart rate / health data | Default `allowCloudRead=false` | Sensitive personal data. |

> MemoWeft only provides the authorization bits and filtering; the host must turn user consent, revocation, review, and correction into an explicit experience.

> ⚠️ **Data at rest is unencrypted; disk encryption is the host/OS responsibility.** MemoWeft stores the three memory layers in a standard SQLite database (e.g. `./dla.db`), and the file itself is **not** encrypted. If the host needs "getting the disk ≠ getting the memory", rely on host- / OS-level disk encryption (BitLocker, FileVault, LUKS). `allowCloudRead` governs *what content may enter a cloud prompt*, which is not the same as local encryption at rest. See [`deployment.md`](./deployment.md) for the full policy.

---

## 5. Advanced config: Hybrid / Local-sensitive

If the host cares more about privacy, you can route in a mixed way:

```ini
# Chat can still be cloud
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# The write path can point at a local OpenAI-compatible endpoint
MEMOWEFT_WRITE_LLM_BASE_URL=http://localhost:1234/v1
MEMOWEFT_WRITE_LLM_API_KEY=local
MEMOWEFT_WRITE_LLM_MODEL=your-local-model

# The embedder can be a local Ollama / LM Studio / other compatible service
MEMOWEFT_EMBED_BASE_URL=http://localhost:11435/v1
MEMOWEFT_EMBED_API_KEY=ollama
MEMOWEFT_EMBED_MODEL=bge-m3
```

Local / hybrid suits desktop assistants, sensitive behavior observations, and long-term personal data. The trade-off is higher install and troubleshooting cost; it is not recommended as the default newcomer path.

---

## 6. The nine env keys at a glance

| Purpose | Primary name | Legacy alias | If unset |
| --- | --- | --- | --- |
| Chat model | `MEMOWEFT_LLM_{BASE_URL,API_KEY,MODEL}` | `DLA_LLM_*` | Errors on a real call |
| Write-path model | `MEMOWEFT_WRITE_LLM_{BASE_URL,API_KEY,MODEL}` | `DLA_WRITE_LLM_*` | Falls back to chat model |
| Embedding recall | `MEMOWEFT_EMBED_{BASE_URL,API_KEY,MODEL}` | `DLA_EMBED_*` | Recall degrades to empty |

> ⚠️ Do not commit `.env`. It contains secrets and should be ignored by `.gitignore`.

---

## 7. Run the testbench

The testbench is a local web page: the left side is a normal chat, the right side is MemoWeft's "x-ray view". You can watch how evidence lands in the store, how it is distilled into events, how it settles into a profile, and what the system wants to proactively ask.

```bash
npm run testbench
# open http://localhost:7888
```

Testbench features:

- Uses a separate `testbench/testbench-evidence.db` so it never pollutes your real database.
- Uses vector recall when `MEMOWEFT_EMBED_*` is configured; otherwise recall is empty.
- Writes each turn's internals to `logs/run-*.jsonl` for diagnosis.
- Supports chatting, inspecting evidence / events / profile, manual profile updates, attribution, proactive asks, and injecting active-window observations.

> Not seeing profile updates? The testbench batches by default: it only auto-runs `updateProfile` after 5 new conversations or 30 minutes idle. To see the effect immediately, click "Update profile now".

---

## 8. Run the minimal code example

The repo ships a runnable minimal example: [`examples/minimal.ts`](../examples/minimal.ts). The example imports by **package name** (`import { createMemoWeftCore } from 'memoweft'`), so run `npm run build` to produce `dist/` first, then run it:

```bash
npm run build
node examples/minimal.ts
```

It demonstrates (all via the unified entry `createMemoWeftCore`):

1. One-line assembly with `createMemoWeftCore({ dbPath })`: the three-layer stores + retriever + model pool at once, all read from `.env`, degrading gracefully instead of crashing when config is missing.
2. `core.ingestUserMessage()` writes one user-spoken piece of evidence.
3. `core.updateProfile()` runs the full write path (distill → consolidate → attribute → reindex) to build the profile.
4. `core.handleConversationTurn()` handles the next message: recall the relevant profile and inject it into the reply.
5. `core.close()` releases connections.

Two further examples are worth a look: [`examples/memory-management.ts`](../examples/memory-management.ts) (controlled memory management) and [`examples/portable-bundle.ts`](../examples/portable-bundle.ts) (portable memory bundle import / export).

---

## 9. Core API at a glance

Prefer going through the unified entry `createMemoWeftCore` for everything, instead of hand-wiring the low-level stores with `new Sqlite*Store`:

```ts
import { createMemoWeftCore, MEMOWEFT_VERSION } from 'memoweft';

const core = createMemoWeftCore({ dbPath: './my-app.db' });
```

| What you want to do | What to use |
| --- | --- |
| Assemble a core in one line (stores + recall + models) | `createMemoWeftCore({ dbPath })` |
| Check whether it can chat / recall | `core.health()` → `{ llmReady, embedReady }` |
| Write a user-spoken piece of evidence | `core.ingestUserMessage({ content, subjectId?, hostId? })` |
| Ingest behavior observations | `core.ingestObservation({ observations })` |
| Write path (update profile) | `core.updateProfile({ subjectId? })` |
| Read path (handle one conversation turn) | `core.handleConversationTurn({ message, subjectId? })` |
| Recall relevant cognitions | `core.recall({ query, subjectId? })` |
| Controlled memory management | `core.memory.*` |
| Portable memory bundle | `core.portable.*` |

For the full export list, see [`src/index.ts`](../src/index.ts) and [`docs/integration.md`](./integration.md).

---

## 10. Command reference

| Command | Purpose |
| --- | --- |
| `npm run typecheck` | Type check |
| `npm test` | Run tests |
| `npm run build` | Produce `dist/` |
| `npm run testbench` | Start the testbench |
| `npm run experience` | Testbench alias |

---

## 11. FAQ

### Can I configure only a cloud model?

Yes, and that is the recommended default path. Get it running first, then decide by data sensitivity which evidence is not allowed into the cloud.

### Can I skip the embedder?

Yes. Recall degrades to empty, but evidence is still stored and the profile is still written. The reply just will not have long-term cognition injected.

### Does observed behavior data go to the cloud by default?

It should not. Desktop / device / health / screen observations should default to `allowCloudRead=false` unless the host has explicit user consent.

### Is the memory database file encrypted?

No. Data at rest is currently unencrypted: the three memory layers live in a standard SQLite database and the file is not encrypted. Disk encryption is the host/OS responsibility (see §4).

### Does MemoWeft handle privacy compliance for me?

No. MemoWeft is a library; it only provides authorization bits and filtering. The host owns the privacy policy, consent UI, and the final data export / deletion experience.
