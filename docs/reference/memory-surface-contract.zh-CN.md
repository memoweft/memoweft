# MemoWeft 公共 API 契约

适用于 `memoweft` 0.6.x。本页描述 `createMemoWeftCore()` 返回的应用层接口，以及调用方可以依赖的可观察行为。

MemoWeft 尚未到 1.0。次版本可能增加字段或枚举值；破坏性变更会在[变更日志](../../CHANGELOG.md)中给出迁移说明。根包导出的底层符号会为兼容性继续保留，但下面的 Core facade 才是推荐并受支持的集成入口。

## 稳定性标签

- **stable**：有兼容性快照保护，计划在 0.6 版本线内保持兼容。
- **experimental**：可以使用，但可能在 1.0 前的次版本中调整，并在变更日志说明。
- **internal**：实现细节，不应成为宿主应用的契约。

除非特别标注，本文方法均为 stable。`clock`、插件以及底层模型/检索实现属于 experimental 扩展点。

## 最小生命周期

```ts
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

await core.ingestUserMessage({
  subjectId: 'user-42',
  content: 'I prefer aisle seats.',
  originId: 'message-1001',
});

const evidence = core.memory.listEvidence({ subjectId: 'user-42' });
console.log(evidence[0]?.sourceKind); // spoken

core.close();
```

创建 Core 不要求先配置模型。不依赖模型的证据存储和记忆管理可直接使用；真正调用模型相关方法时，缺失配置才会报错。

## 创建 Core

```text
createMemoWeftCore(options: CreateCoreOptions): MemoWeftCore
```

| 选项           | 稳定性       | 含义                                                                |
| -------------- | ------------ | ------------------------------------------------------------------- |
| `dbPath`       | stable       | 必填 SQLite 路径；传 `:memory:` 得到一次性内存库。                  |
| `llm`          | experimental | `LLMClient` 或 `LLMPool`；缺省时读取 OpenAI-compatible 环境配置。   |
| `embedder`     | experimental | 未同时传 `retriever` 时，用它创建向量检索器。                       |
| `retriever`    | experimental | 优先级最高的自定义检索实现；归调用方所有，`core.close()` 不关闭它。 |
| `config`       | experimental | MemoWeft 配置对象；不传时使用包默认值。                             |
| `vectorDbPath` | experimental | 向量索引数据库路径；缺省与 `dbPath` 相同。                          |
| `clock`        | experimental | 可注入的 `() => Date`，用于落库时间戳和时间相关规则。               |
| `plugins`      | experimental | 插件契约与受限 hook，详见[插件契约](../plugin-contract.md)。        |

未注入检索器时：有 embedder 配置则选择向量检索；否则使用本地 FTS5 关键词检索。若 FTS5 不可用，再降级为空检索器，但不会阻止 Core 创建。

Core 会关闭自己创建的 SQLite store、向量检索器或关键词检索器；注入的 retriever 由调用方管理。

## Core facade

### 摄入

| 方法                       | 持久化影响                                                | 模型或网络 | 说明                                                                                          |
| -------------------------- | --------------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------- |
| `ingestUserMessage(input)` | 写一条 evidence；传 `conversationId` 时也维护交互上下文。 | 无。       | `sourceKind` 缺省为 `spoken`；`originId` 提供幂等摄入。                                       |
| `ingestObservation(input)` | 写零到多条 `observed` evidence。                          | 无。       | 单条 observation 可覆盖授权默认值。`kind` 是开放字符串；`kind` 和 `meta` 当前接受但不持久化。 |
| `ingestToolResult(input)`  | 写一条 `tool` evidence。                                  | 无。       | 只存工具返回结果，不存工具调用意图或参数；建议用 `originId` 保证幂等。                        |

`observed` 与 `tool` evidence 缺省可进入内建本地写模型提示词、不可进入内建云端写模型提示词，并允许推断。Observation 可在摄入时覆盖这些值；`ToolResultInput` 没有授权覆盖字段，需要随后调用 `core.memory.updateEvidenceAuthorization()`。这些标记不限制召回、list/read API、MCP 工具、适配器注入、派生记录、导出、日志或宿主自写代码。

### 召回与画像整理

| 方法                                                     | 持久化影响                                                                | 模型或网络                                      | 失败语义                                                                   |
| -------------------------------------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------- |
| `recall({ query, subjectId?, explain?, contentTypes? })` | 无。                                                                      | 向量检索可能调用 embedder；关键词检索保持本地。 | 没有合格结果时返回空数组；检索失败按所用 retriever 的降级语义处理。        |
| `updateProfile({ subjectId? })`                          | 整理待处理证据、更新 cognition 与 semantic resolution，随后重建召回索引。 | 使用 write model；配置 embedder 时也会调用它。  | 索引失败写入 `indexError`，已经提交的画像变化不会回滚；其他失败会 reject。 |

召回会排除已失效、归档、静音以及有效置信低于门槛的 cognition。`contentTypes` 在 top-K 之后过滤，因此返回数可能小于 top-K。`explain: true` 时，结果可附带 evidence 提示词资格标记。召回不会因为某条来源证据不适合云端写模型提示词，就自动隐藏派生 cognition；宿主必须在转发整个结果前执行自己的披露策略。

`UpdateProfileResult` 包含：

- `distilled`、`consolidated`、`attributed` 三阶段结果；
- `indexed` 与 `indexError`；
- 毫秒级 `timings`；
- `metrics.profileSize` 与 `metrics.promptChars`。

### 会话辅助方法

| 方法                                                | 行为                                                                                                                                              |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handleConversationTurn(input)`                     | 保存用户消息、召回合格记忆并调用 chat model。同一个 `conversationId` 复用活跃的内存窗口；`systemPrompt` 与 `seedTurns` 只在首次创建该会话时生效。 |
| `recordAssistantReply({ conversationId, content })` | 把助手回复加入已有交互窗口，供后续用户短回答结合上下文解析。它永远不创建 evidence；未知会话 id 会被忽略。                                         |
| `dropConversation(conversationId)`                  | 丢弃活跃的内存会话与交互窗口，不删除持久记忆。下一次调用可建立新的 prompt 与 seed turns。                                                         |

助手文本可以作为 interaction context 保存，但永远拿不到 evidence id，也不能满足 cognition 的溯源要求。

### 诊断与生命周期

| 方法       | 行为                                                                                                                                                                                                                           |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `health()` | 返回内建 OpenAI-compatible client 与向量 retriever 的 `{ llmReady, embedReady }`。自定义注入实现即使可用也可能仍返回 `false`，它不是通用能力探测器。                                                                           |
| `usage()`  | 返回 Core 自持 client 的累计 `{ llm, embed, total }` token 计数。每个桶含 `promptTokens`、`completionTokens`、`totalTokens`、`callsWithUsage`。端点不返回 usage 时不会计入；注入 retriever 的 embed 用量由宿主管理，这里为零。 |
| `close()`  | 关闭 Core 所有的 store 与自建 retriever。关闭后不要继续使用；注入 retriever 仍由调用方负责。                                                                                                                                   |

## 受控记忆管理 API

应用应使用 `core.memory`，不要直接写 SQLite 表。

### 只读操作

| 方法                             | 返回                                                      |
| -------------------------------- | --------------------------------------------------------- |
| `listEvidence({ subjectId? })`   | 该 subject 的全部 evidence。                              |
| `listEvents({ subjectId? })`     | event 及其 evidence id。                                  |
| `listCognitions({ subjectId? })` | cognition、溯源链和读时计算的 `effectiveConfidence`。     |
| `checkIntegrity()`               | event/evidence 与 cognition/evidence 悬空关系的只读报告。 |

### 状态、授权与删除

| 方法                                 | 未找到或拒绝时                                           | 说明                                                               |
| ------------------------------------ | -------------------------------------------------------- | ------------------------------------------------------------------ |
| `invalidateCognition(input)`         | 不存在返回 `null`。                                      | 设置 `invalidAt`；`reason` 必填。                                  |
| `archiveCognition(input)`            | 不存在返回 `null`。                                      | 设置 `archivedAt`；`reason` 必填。                                 |
| `muteCognition(input)`               | 不存在返回 `null`。                                      | 静音或恢复召回，不修改 confidence；`reason` 必填。                 |
| `updateEvidenceAuthorization(input)` | 不存在返回 `null`。                                      | 修改 `allowCloudRead` 和/或 `allowInference`；无变化时不写审计。   |
| `mergeCognition(input)`              | 缺失、跨 subject、失效或归档目标会抛错。                 | 去重迁移溯源链、重算目标 confidence，并让 source 失效。            |
| `removeEvidenceSafely(input)`        | 有引用且未 force 时返回 `{ removed: false, blockers }`。 | `force: true` 会在同一数据库事务内清掉引用关系。                   |
| `removeCognitionSafely(input)`       | 不存在时 `removed: false`。                              | 删除 cognition 与溯源链，不删除 evidence 本体。                    |
| `resetSubject(input)`                | 返回删除计数。                                           | 破坏性重置；`reason` 可选，因为该 subject 的审计历史也会一并清除。 |

成功的管理变更会把 metadata 与 reason 写入 `management_log`，只有 `resetSubject` 例外：它会主动删除该日志。被拒绝和无变化的操作不产生审计行。

`resetSubject` 会在事务内删除该 subject 的 evidence、event、cognition、关系行、interaction context、semantic resolution 与管理审计。返回计数只覆盖 evidence、event、cognition 和 audit。召回索引随后通过 `indexAll([])` 清理，不属于数据库事务；若使用异步外部索引，方法可能在索引完全清空前返回。

MemoWeft 的审计 metadata 不保留已删除 cognition 的原始内容。

## 便携记忆包

```text
core.portable.exportBundle(options?)
core.portable.validateBundle(bundle)
core.portable.importBundle(bundle, { mode: 'dryRun' | 'merge' })
```

一个 bundle 包含单个 subject 的 evidence、event、cognition、溯源关系、待整理 event 状态、interaction context 与 semantic resolution，并保留 id 和时间戳。它不包含向量索引、日志、API key、环境文件或宿主 UI 状态。

- `dryRun` 只校验和统计计划写入，不修改数据库。
- `merge` 经 Core facade 在事务内导入，并按 id 与 evidence `originId` 去重。
- Bundle schema v2 可导入 schema v1；缺失的交互段按空处理。
- 0.6.x 没有 `replace` 导入模式。

导入后，如需从导入画像重建 retriever 索引，请调用 `updateProfile()`。

## 记忆图谱

```text
core.graph.buildMemoryGraph(options?): MemoryGraphPayload
```

payload 包含 subject、evidence、event、cognition 节点，以及实际生成的 `belongs_to_subject`、`distilled_into`、`supports`、`contradicts` 边。`conflicts_with` 与 `corrects` 是预留枚举值，但 0.6.x 不会生成，因为当前没有持久化 cognition-to-cognition 关系。

## 行为保证

- evidence 与 cognition 分层；存下一句话不会自动把它变成可信结论。
- cognition confidence 是 MemoWeft 按规则计算的 0–1000 整数，不采信模型自报置信度。
- 明确纠正会保留已失效历史；未解决矛盾会继续可见，不会静默覆盖。
- 内建摄入路径不会把助手回复转成 evidence；依赖上下文的用户短回答可通过独立 interaction context 解析。
- 受支持摄入路径中的 `originId` 提供 evidence 幂等。
- 默认配置下，事实和偏好不使用与临时状态、假设相同的时间衰减策略。
- MemoWeft 不加密 SQLite；同意、访问控制、删除 UX、备份和静态加密由宿主负责。

## 错误与降级

- 缺模型配置不阻止 Core 创建；真正进入模型相关调用时才 reject。
- 缺 embed 配置会选择本地关键词检索；FTS5 也不可用时再降级为空召回。
- 画像写入与索引重建刻意分离；索引错误不会抹掉已成功的 cognition 更新。
- 管理方法按上表分别使用 `null`、结果标志或异常，调用方不应假设所有“未找到”都采用同一种形式。
- `subjectId` 缺省取 `config.identity.subjectId`。多用户宿主应显式传入，并自行执行授权隔离。

## 相关文档

- [开始使用](../getting-started.zh-CN.md)
- [架构](../internals/architecture.md)
- [插件契约](../plugin-contract.zh-CN.md)
- [部署与隐私](../deployment.md)
- [变更日志](../../CHANGELOG.md)
- [类型声明入口](../../src/index.ts)
