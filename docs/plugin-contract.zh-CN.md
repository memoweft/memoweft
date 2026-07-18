# MemoWeft 插件契约 v2

[English](./plugin-contract.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./plugin-contract.md) 为准。

> 稳定性：**experimental**（pre-1.0，签名可能演进——尤其 hook 参数以后可能增字段，插件作者留扩展余地）。
> 类型定义：`src/plugin/contract.ts`，从包主入口 `memoweft` 导出。配套：[记忆面契约](./reference/memory-surface-contract.zh-CN.md)。

## 一句话

插件给同一套 MemoWeft 记忆底座加"脸 / 工具 / 感知"。公开插件接口提供观察与请求能力，但不提供改写管线的 API 或直接访问 store 的 API。

## 三类插件

| type         | 干什么                                 | 靠什么                                                                  |
| ------------ | -------------------------------------- | ----------------------------------------------------------------------- |
| `experience` | 切换会话人设 / 语气（普通助手 / 星瑶） | `systemPrompt`（Host 按会话选、每轮传给 `handleConversationTurn`）      |
| `tool`       | 宿主定义的工具与能力                   | hook + `PluginContext` 请求式能力                                       |
| `collector`  | 感知采集（如活动窗口）                 | 采集器多是独立进程经 `/api/observe` 产观察；也可用 `onObservation` 反应 |

## `MemoWeftPlugin`

```ts
interface MemoWeftPlugin {
  id: string; // 稳定机器标识（注册表键）
  name: string; // 给用户看的名字
  type: 'experience' | 'tool' | 'collector';
  systemPrompt?: string; // experience 用
  permissions?: PluginPermissions; // 声明式：要用 ctx 的哪些能力
  onLoad?(ctx: PluginContext): void | Promise<void>;
  onUserMessage?(msg: PluginUserMessage, ctx: PluginContext): void | Promise<void>;
  onObservation?(obs: Observation, ctx: PluginContext): void | Promise<void>;
}
```

通过 `createMemoWeftCore({ ..., plugins: [...] })` 注册插件；省略 `plugins` 时，Core 行为不变。

## Hook：只观察，不改管线

- **`onLoad`**：在 stores 与 retriever 就绪后调用一次。该 hook 不会被等待，以保持 `createMemoWeftCore` 同步返回；异步处理器可能在第一次 Core 调用时仍在运行。
- **`onUserMessage`**：每轮对话完成、回复已经生成后调用。它只接收 `{ content, subjectId, reply }` 供观察。
- **`onObservation`**：经 `core.ingestObservation` 摄入的观察落库后调用。

不变式：

- **返回值会被丢弃**——hook 返回的“修改后回复/消息”不会进入管线。
- **不改**用户消息、不改回复文本。
- 每个 hook 由 `try/catch` 包裹：插件异常会记录日志，Core 方法按其正常结果路径继续；hook 处理不为宿主设定回复时延或可用性预期。
- hook 在 Core 的**方法层**（`createCore.ts`）运行；`conversation.ts` 与 `ingest.ts` 中的纯管线函数不依赖插件分发。

## `PluginContext`：受限能力

```ts
interface PluginContext {
  submitObservation(input: PluginObservationInput): Promise<void>; // 需 permissions.submitObservation
  requestMemory(query: string): Promise<RecalledCognitionItem[]>; // 需 permissions.requestMemory
}
```

- **通过闭包提供，不暴露 store API**：context 只包含两个预绑定方法，不包含 `store` 或 `cognitionStore`。
- **绑定当前 subject**（当前单用户宿主模型中为 `config.identity.subjectId`）；方法不接收 `subjectId`。
- **`submitObservation`**：
  - 入参 `PluginObservationInput` = `Observation` **去掉三个授权位**。Core 会按白名单重构可接受字段，因此运行时额外传入的 `allowCloudRead:true` 会被丢弃，并应用 `observed` 的保守默认（**本地可读 / 不上云 / 可推画像**）。这些标记只影响 MemoWeft 的 prompt 选择，不是访问控制或加密。这是 Host `sanitizeObservation` 的 Core 侧等价。
  - 直接调用纯函数 `ingestObservations`，而不经过 hook 分发方法。观察会正常落库，但**不会递归触发 `onObservation`**，从而避免“观察→提交→再次观察”的循环。
  - 幂等：带稳定 `originId` 才去重（由插件负责）。
- **`requestMemory`**：读取与 query 相关的召回认知（遵循既有 topK / minSimilarity 门控）。v2 不按 `contentType` 细分；声明式权限只控制插件能否调用该能力。宿主必须评估获准插件，因为该权限不会进一步限制其查询范围。

## 声明式权限

`permissions` 声明插件要用 ctx 的哪些能力（`submitObservation?` / `requestMemory?`）。**没声明 → 调它抛错、被挡**。Host 的插件管理 UI 据声明**展示 + 启停**。

## UI 在 Host，不在 Core

Core 是**无头库**，不提供界面。参考 Host 负责列出插件、显示声明权限、启停控制与人格选择；可查看记忆管理页的“插件”标签和 `GET /api/plugins`。动态权限提示与 UI 事件不属于 Core 的 `PluginContext`。

## 当前使用者与不支持的能力

- 内置 experience 插件使用 `systemPrompt`，活动窗口采集器通过 `/api/observe` 提交观察；二者都不依赖 hook。[`examples/plugin-hook.ts`](../examples/plugin-hook.ts) 是 hook 行为的可执行参考。
- **不支持：**运行时安装或卸载外部插件包、模块沙箱、修改管线的 hook，以及动态权限提示。

关联：[记忆面契约](./reference/memory-surface-contract.zh-CN.md) · [三层边界](./internals/boundaries.zh-CN.md) · [插件示例](../examples/plugin-hook.ts)。
