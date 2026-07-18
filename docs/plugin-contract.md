# MemoWeft Plugin Contract v2

**English** | [简体中文](./plugin-contract.zh-CN.md)

> Stability: **experimental** (pre-1.0, signatures may evolve — especially hook parameters may gain fields later, so plugin authors should leave room for extension).
> Type definitions: `src/plugin/contract.ts`, exported from the package main entry `memoweft`. Companion: [memory surface contract](./reference/memory-surface-contract.md).

## In one sentence

Plugins add a "face / tools / perception" to the same MemoWeft memory substrate. The public plugin interface supplies observation and request capabilities; it does not offer a pipeline-mutation API or direct store access.

## The three plugin types

| type         | what it does                                                           | how                                                                                                                      |
| ------------ | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `experience` | swaps the conversational persona / tone (ordinary assistant / Xingyao) | `systemPrompt` (the Host picks it per session, and passes it every turn to `handleConversationTurn`)                     |
| `tool`       | host-defined tools and capabilities                                    | hooks plus request-style `PluginContext` capabilities                                                                    |
| `collector`  | perceptual collection (e.g. active window)                             | collectors are mostly standalone processes producing observations via `/api/observe`; can also react via `onObservation` |

## `MemoWeftPlugin`

```ts
interface MemoWeftPlugin {
  id: string; // stable machine identifier (registry key)
  name: string; // user-facing name
  type: 'experience' | 'tool' | 'collector';
  systemPrompt?: string; // used by experience
  permissions?: PluginPermissions; // declarative: which ctx capabilities it needs
  onLoad?(ctx: PluginContext): void | Promise<void>;
  onUserMessage?(msg: PluginUserMessage, ctx: PluginContext): void | Promise<void>;
  onObservation?(obs: Observation, ctx: PluginContext): void | Promise<void>;
}
```

Register plugins with `createMemoWeftCore({ ..., plugins: [...] })`. Omitting `plugins` leaves Core behavior unchanged.

## Hook behavior

- **`onLoad`**: invoked once after stores and the retriever are ready. It is not awaited, so `createMemoWeftCore` remains synchronous; an asynchronous handler may still be running when the first Core method is called.
- **`onUserMessage`**: invoked **after** each conversation turn, once the reply has been generated. It receives `{ content, subjectId, reply }` for observation only.
- **`onObservation`**: invoked after an observation submitted through `core.ingestObservation` has been stored.

Hook invariants:

- **Return values are discarded** — even if a hook returns a "modified reply/message", it is not fed back into the pipeline.
- **Do not modify** the user message, do not modify the reply text.
- Each hook is wrapped in `try/catch`: a plugin exception is logged and the Core method continues with its normal result path; hook handling does not set host reply-latency or availability expectations.
- Hooks run at Core's **method layer** (`createCore.ts`); the pure pipeline functions in `conversation.ts` and `ingest.ts` remain independent of plugin dispatch.

## `PluginContext`: restricted capabilities

```ts
interface PluginContext {
  submitObservation(input: PluginObservationInput): Promise<void>; // needs permissions.submitObservation
  requestMemory(query: string): Promise<RecalledCognitionItem[]>; // needs permissions.requestMemory
}
```

- **Provided by closure, with no store API exposed**: the context contains two pre-bound methods and does not include `store` or `cognitionStore`.
- **Bound to the current subject** (`config.identity.subjectId` in the current single-user host model); the methods do not accept a `subjectId`.
- **`submitObservation`**:
  - The input `PluginObservationInput` = `Observation` **minus the three authorization fields**. Core reconstructs the accepted fields through a whitelist, so a runtime-injected `allowCloudRead:true` is discarded and the `observed` defaults apply (**locally readable / not uploaded to cloud / profile inference allowed**). These flags govern MemoWeft prompt selection; they are not access control or encryption. This is the Core-side equivalent of the Host's `sanitizeObservation`.
  - Uses the pure `ingestObservations` function rather than the hook-dispatching method. The observation is stored, but **does not trigger `onObservation` recursively**, preventing an "observe → submit → observe again" loop.
  - Idempotent: dedup only happens if a stable `originId` is provided (the plugin is responsible for this).
- **`requestMemory`**: reads the recalled cognition "relevant to query" (goes through the existing recall gating topK / minSimilarity). v2 does not subdivide by `contentType` — **the declarative permission only gates "whether this capability can be called"**; a plugin granted the `requestMemory` permission is trusted not to abuse it (a trust model; the host bears responsibility when choosing which plugins to install).

## Declarative permissions

`permissions` declares which ctx capabilities the plugin needs (`submitObservation?` / `requestMemory?`). **Not declared → calling it throws, and it's blocked**. The Host's plugin management UI **displays + enables/disables** based on the declaration.

## The UI is in the Host, not in Core

Core is a **headless library** and provides no interface. The reference Host owns plugin listing, declared-permission display, enable/disable controls, and persona selection; see its memory-management "Plugins" tab and `GET /api/plugins`. Dynamic permission prompts and UI events are not part of Core's `PluginContext`.

## Current consumers and unsupported capabilities

- The bundled experience plugins use `systemPrompt`, while the active-window collector submits observations through `/api/observe`; neither depends on a hook. [`examples/plugin-hook.ts`](../examples/plugin-hook.ts) is the executable reference for hook behavior.
- **Not supported:** runtime installation or removal of external plugin packages, module sandboxing, pipeline-mutating hooks, or dynamic permission prompts.

Related: [memory surface contract](./reference/memory-surface-contract.md) · [three-layer boundaries](./internals/boundaries.md) · [plugin example](../examples/plugin-hook.ts).
