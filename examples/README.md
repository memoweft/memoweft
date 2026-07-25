# MemoWeft Examples

**Looking for the fastest proof?** After dependencies are installed, run the deterministic offline demo in about 30 seconds:

```bash
npm run build
node examples/no-key-demo.ts
```

It uses an in-memory database and an in-file stub LLM: no API key, network connection, or files written. See the [walkthrough](../docs/demo-script.md) (or [简体中文](../docs/demo-script.zh-CN.md)).

Build the package before running examples that import `memoweft` by package name:

```bash
npm run build
```

## Examples

**Offline & deterministic** — run right after `npm run build`, no API key or network:

- [`no-key-demo.ts`](./no-key-demo.ts) — **the fastest way in (~30s).** An offline stub LLM (defined in the file) runs the full write path so you can see the differentiators: a conflict is exposed and the old belief is kept (never silently overwritten), and an inferred item stays a low-confidence guess instead of a fact.
- [`demo.ts`](./demo.ts) — the four-act story: **remember → correct → conflict → time-decay**. Run it with `npm run demo` (offline stub model + injectable clock). Flags: `-- --act N` runs one act, `-- --fast-forward 30d` sets act 4's horizon.
- [`portable-bundle.ts`](./portable-bundle.ts) — export, validate, and import a memory bundle (evidence only, so it needs no model).
- [`plugin-hook.ts`](./plugin-hook.ts) — plugin hooks and the restricted `PluginContext` capabilities (ships its own stub model).

**Needs a chat model** — set `MEMOWEFT_LLM_*` in a `.env` first (see the [getting-started guide](../docs/getting-started.md)):

- [`minimal.ts`](./minimal.ts) — minimal Core setup and the full write→read loop (`updateProfile` + `handleConversationTurn`).
- [`memory-management.ts`](./memory-management.ts) — controlled memory-management APIs (invalidate / merge / safe-remove, each with an audit reason).

Run an offline example from the repository root with Node.js, for example:

```bash
node examples/no-key-demo.ts
```

Model-configuration prerequisites and the temporary database files each example uses are documented at the top of every file.
