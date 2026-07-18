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

- [`no-key-demo.ts`](./no-key-demo.ts) — **zero-config · no model endpoint · no network.** The fastest way in: an offline stub LLM (defined in the file) runs the full write path so you can see the differentiators in ~30s — a conflict is exposed and the old belief is kept (never silently overwritten), and an inferred item stays a low-confidence guess instead of a fact.
- [`minimal.ts`](./minimal.ts) — minimal Core setup and conversation flow.
- [`memory-management.ts`](./memory-management.ts) — controlled memory-management APIs.
- [`portable-bundle.ts`](./portable-bundle.ts) — export, validate, and import a memory bundle.
- [`plugin-hook.ts`](./plugin-hook.ts) — plugin hooks and restricted `PluginContext` capabilities.

Run an example from the repository root with Node.js, for example:

```bash
node examples/minimal.ts
```

The prerequisites for model configuration and temporary database files are documented at the top of each example.
