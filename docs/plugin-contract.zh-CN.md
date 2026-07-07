# MemoWeft 插件契约 v2

[English](./plugin-contract.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./plugin-contract.md) 为准。


> 稳定性：**experimental**（pre-1.0，签名可能演进——尤其 hook 参数以后可能增字段，插件作者留扩展余地）。
> 类型定义：`src/plugin/contract.ts`，从包主入口 `memoweft` 导出。配套：[记忆面契约 `memory-surface-contract.md`](./memory-surface-contract.md)。

## 一句话

插件给同一套 MemoWeft 记忆底座加"脸 / 工具 / 感知"，**只能观察 + 经受限接口请求，绝不能改管线或绕过记忆规则**。

## 三类插件

| type | 干什么 | 靠什么 |
|---|---|---|
| `experience` | 换回话人设 / 语气（普通助手 / 星瑶） | `systemPrompt`（Host 按会话选、每轮传给 `handleConversationTurn`） |
| `tool` | 工具（如将来的 GitHub / 文件） | hook + `PluginContext` 请求式能力 |
| `collector` | 感知采集（如活动窗口） | 采集器多是独立进程经 `/api/observe` 产观察；也可用 `onObservation` 反应 |

## `MemoWeftPlugin`

```ts
interface MemoWeftPlugin {
  id: string;            // 稳定机器标识（注册表键）
  name: string;          // 给用户看的名字
  type: 'experience' | 'tool' | 'collector';
  systemPrompt?: string; // experience 用
  permissions?: PluginPermissions;                 // 声明式：要用 ctx 的哪些能力
  onLoad?(ctx: PluginContext): void | Promise<void>;
  onUserMessage?(msg: PluginUserMessage, ctx: PluginContext): void | Promise<void>;
  onObservation?(obs: Observation, ctx: PluginContext): void | Promise<void>;
}
```

注册：`createMemoWeftCore({ ..., plugins: [...] })`。不传 = 无插件，行为同旧。

## Hook：只观察，不改管线（红线）

- **`onLoad`**：建 core 时烧一次（stores/retriever 已就绪）。**fire-and-forget**（不 await，保 `createMemoWeftCore` 同步返回）——插件的异步 onLoad 在后台跑、不保证在首次调用前完成。
- **`onUserMessage`**：每轮对话**之后**烧（回话已生成）。拿到 `{ content, subjectId, reply }`——观察这轮说了啥 / 回了啥。
- **`onObservation`**：每条经 `core.ingestObservation` 摄入的观察**落库后**烧。

铁律：
- **返回值一律丢弃**——hook 返回"改过的回话/消息"也不回灌管线。
- **不改**用户消息、不改回话文本。
- 每个 hook `try/catch` 包裹：**插件抛错记日志、不崩会话 / 不崩摄入**（呼应"召回失败不挡回话"）。
- hook 烧在 Core 的**方法层**（`createCore.ts`）——`conversation.ts` / `ingest.ts` 的纯逻辑一行不碰。

## `PluginContext`：受限能力壳

```ts
interface PluginContext {
  submitObservation(input: PluginObservationInput): Promise<void>;  // 需 permissions.submitObservation
  requestMemory(query: string): Promise<RecalledCognitionItem[]>;   // 需 permissions.requestMemory
}
```

- **闭包给、绝不交 store**：ctx 只是两个绑好的方法，插件够不到 `store` / `cognitionStore`。
- **绑当次 subject**（v1 单人单宿主 = `config.identity.subjectId`）；方法不收 subjectId。
- **`submitObservation`**：
  - 入参 `PluginObservationInput` = `Observation` **去掉三个授权位**——插件**不能设** `allowCloudRead/Local/Inference`；Core 侧还会白名单重构一遍，就算插件运行时硬塞 `allowCloudRead:true` 也丢弃 → 一律走 `observed` 保守默认（**本地可读 / 不上云 / 可推画像**）。这是 Host `sanitizeObservation` 的 Core 侧等价。
  - 走**纯函数 `ingestObservations`、不走烧 hook 的方法** → 插件提交的观察照常落库，但**不级联触发 `onObservation`**，杜绝"观察→提交→再观察"的重入死循环。
  - 幂等：带稳定 `originId` 才去重（由插件负责）。
- **`requestMemory`**：读"与 query 相关"的召回认知（走既有召回门控 topK / minSimilarity）。v2 不按 `contentType` 细分——**声明式权限只门控"能不能调"这个能力**；给了 `requestMemory` 权限的插件即信任它不滥用（信任模型，宿主选择装哪些插件时自负）。

## 声明式权限

`permissions` 声明插件要用 ctx 的哪些能力（`submitObservation?` / `requestMemory?`）。**没声明 → 调它抛错、被挡**。Host 的插件管理 UI 据声明**展示 + 启停**。

## UI 在 Host，不在 Core

Core 是**无头库**、不画界面。插件管理界面（列已注册插件 / 类型 / 权限 / 换人设）在 Host——参考实现见 `apps/memoweft-host` 的记忆管理页「插件」tab + `GET /api/plugins`。草案 §7.1 的 `requestPermission` / `emitUIEvent` **不进 Core 的 `PluginContext`**（那是 Host/UI 的事）。

## 现状与边界（v2 诚实交底）

- v2 铺的是**基础设施**：现有 experience 插件靠 `systemPrompt`（无 hook）、活动窗口采集器走 `/api/observe`（不消费 `onObservation`）——**hook 目前没有生产消费者**，真实的 tool / hook 型采集器待后续。活体 demo 见 `examples/plugin-hook.ts`。
- **不做**：运行时动态装卸外部插件包（模块加载 / 插件市场 / 沙箱）；会改管线的 hook；动态权限弹窗。

关联：[记忆面契约](./memory-surface-contract.md) · [三层边界](./internal/boundaries.md) · 示例 `examples/plugin-hook.ts`。
