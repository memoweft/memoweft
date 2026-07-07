# MemoWeft Plugin Contract v2

**English** | [简体中文](./plugin-contract.zh-CN.md)

> Stability: **experimental** (pre-1.0, signatures may evolve — especially hook parameters may gain fields later, so plugin authors should leave room for extension).
> Type definitions: `src/plugin/contract.ts`, exported from the package main entry `memoweft`. Companion: [memory surface contract `memory-surface-contract.md`](./memory-surface-contract.md).

## In one sentence

Plugins add a "face / tools / perception" to the same MemoWeft memory substrate, and **can only observe + request through a restricted interface, never modify the pipeline or bypass the memory rules**.

## The three plugin types

| type | what it does | how |
|---|---|---|
| `experience` | swaps the conversational persona / tone (ordinary assistant / Xingyao) | `systemPrompt` (the Host picks it per session, and passes it every turn to `handleConversationTurn`) |
| `tool` | tools (e.g. future GitHub / files) | hook + `PluginContext` request-style capabilities |
| `collector` | perceptual collection (e.g. active window) | collectors are mostly standalone processes producing observations via `/api/observe`; can also react via `onObservation` |

## `MemoWeftPlugin`

```ts
interface MemoWeftPlugin {
  id: string;            // stable machine identifier (registry key)
  name: string;          // user-facing name
  type: 'experience' | 'tool' | 'collector';
  systemPrompt?: string; // used by experience
  permissions?: PluginPermissions;                 // declarative: which ctx capabilities it needs
  onLoad?(ctx: PluginContext): void | Promise<void>;
  onUserMessage?(msg: PluginUserMessage, ctx: PluginContext): void | Promise<void>;
  onObservation?(obs: Observation, ctx: PluginContext): void | Promise<void>;
}
```

Registration: `createMemoWeftCore({ ..., plugins: [...] })`. Not passing it = no plugins, behaves as before.

## Hooks: observe only, don't modify the pipeline (red line)

- **`onLoad`**: fired once when core is built (stores/retriever already ready). **fire-and-forget** (not awaited, to keep `createMemoWeftCore` returning synchronously) — a plugin's async onLoad runs in the background and is not guaranteed to complete before the first call.
- **`onUserMessage`**: fired **after** each conversation turn (the reply has already been generated). Receives `{ content, subjectId, reply }` — observe what was said / what was replied this turn.
- **`onObservation`**: fired **after each observation ingested via `core.ingestObservation` lands in the store**.

Iron rules:
- **Return values are always discarded** — even if a hook returns a "modified reply/message", it is not fed back into the pipeline.
- **Do not modify** the user message, do not modify the reply text.
- Each hook is wrapped in `try/catch`: **a plugin throwing is logged, and does not crash the conversation / does not crash ingestion** (echoing "recall failure does not block the reply").
- Hooks fire at Core's **method layer** (`createCore.ts`) — the pure logic of `conversation.ts` / `ingest.ts` is not touched by a single line.

## `PluginContext`: the restricted-capability shell

```ts
interface PluginContext {
  submitObservation(input: PluginObservationInput): Promise<void>;  // needs permissions.submitObservation
  requestMemory(query: string): Promise<RecalledCognitionItem[]>;   // needs permissions.requestMemory
}
```

- **Given by closure, never hands over the store**: ctx is just two pre-bound methods; the plugin cannot reach `store` / `cognitionStore`.
- **Bound to the current subject** (v1 single-person single-host = `config.identity.subjectId`); the methods do not take a subjectId.
- **`submitObservation`**:
  - The input `PluginObservationInput` = `Observation` **minus the three authorization fields** — the plugin **cannot set** `allowCloudRead/Local/Inference`; the Core side also re-constructs it via a whitelist, so even if the plugin hard-injects `allowCloudRead:true` at runtime it is discarded → it always goes through the `observed` conservative default (**locally readable / not uploaded to cloud / profile inference allowed**). This is the Core-side equivalent of the Host's `sanitizeObservation`.
  - Goes through the **pure function `ingestObservations`, not the hook-firing method** → the plugin-submitted observation lands in the store as usual, but **does not cascade-trigger `onObservation`**, preventing the "observe → submit → observe again" reentrant infinite loop.
  - Idempotent: dedup only happens if a stable `originId` is provided (the plugin is responsible for this).
- **`requestMemory`**: reads the recalled cognition "relevant to query" (goes through the existing recall gating topK / minSimilarity). v2 does not subdivide by `contentType` — **the declarative permission only gates "whether this capability can be called"**; a plugin granted the `requestMemory` permission is trusted not to abuse it (a trust model; the host bears responsibility when choosing which plugins to install).

## Declarative permissions

`permissions` declares which ctx capabilities the plugin needs (`submitObservation?` / `requestMemory?`). **Not declared → calling it throws, and it's blocked**. The Host's plugin management UI **displays + enables/disables** based on the declaration.

## The UI is in the Host, not in Core

Core is a **headless library** and draws no interface. The plugin management interface (list registered plugins / types / permissions / swap persona) is in the Host — for a reference implementation see the "Plugins" tab of the memory management page in `apps/memoweft-host` + `GET /api/plugins`. The draft §7.1 `requestPermission` / `emitUIEvent` **do not enter Core's `PluginContext`** (that's the Host/UI's business).

## Current state and boundaries (v2 honest disclosure)

- What v2 lays down is **infrastructure**: the current experience plugin relies on `systemPrompt` (no hook), the active-window collector goes through `/api/observe` (does not consume `onObservation`) — **hooks currently have no production consumer**, real tool / hook-type collectors are pending later. A live demo is in `examples/plugin-hook.ts`.
- **Not doing**: runtime dynamic install/uninstall of external plugin packages (module loading / plugin marketplace / sandbox); pipeline-modifying hooks; dynamic permission popups.

Related: [memory surface contract](./memory-surface-contract.md) · [three-layer boundaries](./internal/boundaries.md) · example `examples/plugin-hook.ts`.
