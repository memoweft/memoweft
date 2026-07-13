# MemoWeft 记忆面契约 v1（Memory Surface Contract）

[English](./memory-surface-contract.md) | **简体中文**

> 本文档**以英文版为准**；中文为尽力同步，如有出入以 [英文版](./memory-surface-contract.md) 为准。


> 面向宿主（`import 'memoweft'` 的那一方）。这是对宿主的**承诺书**：哪些能靠、哪些别碰、破了怎么办。
> 单一事实源。与 `INSTALL` / `integration.md` 同级，宿主直接读。
> 归属：总纲第 2 步；第 7 步插件契约、第 10 步 1.0 API 收口的共同地基。

## 怎么读这份契约（三档 + 破坏政策）

**稳定性三档**：

- **stable（稳定面）**：宿主日常靠它做事、已由门面收口、形状定型。承诺"不随手改"。
- **experimental（试验面）**：导出了、宿主可能碰，但**明说会变**；改了不算爽约（CHANGELOG 提一句即可）。
- **internal（内部件）**：门面已收口、宿主没理由碰的散装实现件。导出还在（删属第 10 步），但**别依赖**。

**破坏 stable 的政策（pre-1.0，中间偏松）**：

- **什么算破坏**：改字段名 / 删字段 / 改可空性 / 改语义（例：`confidence` 量纲）。
- **代价**：允许在 minor 版破，但必须 ① CHANGELOG 明确标注 ② 给一句迁移说明（旧→新怎么改）③ 能保旧名的走 `@deprecated` 别名（照 `DLA_VERSION` / `DlaConfig` 样板）。**不强制"保留整一个版本再删"**。
- **枚举加值不算破坏**：给 `SourceKind` / `ContentType` / `CredStatus` 等**加新取值**不算破坏；**收窄（删取值）算破坏**。宿主对这些枚举**必须留 `default` 兜底分支**（漏分支的责任在宿主，见隐性契约第 10 条）。
- **experimental 面**：minor 版随便改，CHANGELOG 提一句即可，不欠迁移说明。

---

## 一、门面方法专章（25 个宿主接触方法）

宿主主入口是 `createMemoWeftCore(options)`，拿到 `MemoWeftCore` 门面后经它的方法与三个子命名空间（`memory` / `portable` / `graph`）做事。**不要绕过门面直接拼底层 `Sqlite*Store` / 算子**。

计数：`createMemoWeftCore`(1) + 门面顶层 9 + `core.memory` 11 + `core.portable` 3 + `core.graph` 1 = **25**。全部 **stable**。

### 1.0 工厂

#### `createMemoWeftCore(options: CreateCoreOptions): MemoWeftCore` — **stable**
- **入参** `CreateCoreOptions`：`dbPath`（必填）、`llm?`（`LLMPool | LLMClient`）、`embedder?`、`retriever?`、`config?`、`vectorDbPath?`。
- **返回**：`MemoWeftCore` 门面（下述 9 顶层方法 + `memory`/`portable`/`graph`/`health`/`close`）。
- **隐性契约**：**无 `.env` 也能建 core**——缺模型配置不崩，只有真调模型的路径才降级/报错（见隐性契约第 9 条）。`vectorDbPath` 缺省与 `dbPath` 同库；一个 subject 一个向量实例的既有契约不变。
- 依据：`src/core/createCore.ts:39-52`（入参）、`:155-174`（装配降级）。

### 1.1 门面顶层 9 方法（`MemoWeftCore.*`）

| 方法 | 入参 | 返回 | 级 | 隐性行为契约 |
|---|---|---|---|---|
| `ingestUserMessage(input)` | `UserMessageInput` | `Promise<Evidence>` | stable | 存 `spoken` 证据，只存不答（"先存后答"里"存"的那半）。 |
| `ingestObservation(input)` | `ObservationInput` | `Promise<Evidence[]>` | stable | 存 `observed` 证据，**默认不上云**；带 `originId` 幂等；返回本次**新落库**的（幂等命中的不在内）。 |
| `ingestToolResult(input)` | `ToolResultInput` | `Promise<Evidence>` | stable | 存一条工具执行的**返回结果** payload 为 `tool` 证据（AD-3/D-0013），**默认不上云**（`config.toolDefaults`）；带 `originId` 幂等。只摄入工具返回结果，绝不摄入 LLM 的工具调用意图/入参（铁律 3a）。要给某条 `tool` 证据开上云走 `memory.updateEvidenceAuthorization`（带审计），不在摄入口开口子。 |
| `recall(input)` | `RecallInput` | `Promise<RecalledCognition[]>` | stable | 与 `Conversation` 同一段共享召回语义（invalid/archived/越界/衰减门控全走）。 |
| `handleConversationTurn(input)` | `ConversationInput` | `Promise<TurnOutcome>` | stable | 存证据→召回→回话；同 `conversationId` 复用实例、窗口连续；`systemPrompt`/`seedTurns` 仅首次建实例生效（见隐性契约第 4 条）。 |
| `dropConversation(conversationId)` | `string` | `void` | stable | 丢内存里的活跃会话实例（不碰库）；下次同 id 会重建（届时新 `systemPrompt`/`seedTurns` 才生效）；不存在的 id 静默略过。 |
| `updateProfile(input?)` | `UpdateProfileInput` | `Promise<UpdateProfileResult>` | stable | 一键 distill→consolidate→attribute→重建召回索引。索引重建失败不回滚画像（`indexError` 报因）。 |
| `health()` | — | `HealthReport` | stable | 基于本 core **实际持有的部件**判断，不重查 env：`llmReady`=持有真对话客户端；`embedReady`=持有向量召回器。注入的 stub/空召回器判 false。 |
| `close()` | — | `void` | stable | 关共享连接 + 自建向量库连接；**注入的 retriever 归调用方管、不动**。 |

依据：`src/core/createCore.ts:120-145`（接口）、`:181-287`（实现）。

### 1.2 `core.memory`（受控记忆管理 API，11 方法）

写操作都带 `reason`（必填，进审计表）；只读列取不落审计。缺省 `subjectId` = `config.identity.subjectId`（v1 单人单宿主）。

| 方法 | 入参 | 返回 | 级 | 隐性行为契约 |
|---|---|---|---|---|
| `invalidateCognition(input)` | `InvalidateCognitionInput` | `Cognition \| null` | stable | 标失效（`invalidAt=now`）+ 审计；不存在返回 `null`（不审计）。 |
| `updateEvidenceAuthorization(input)` | `UpdateEvidenceAuthorizationInput` | `Evidence \| null` | stable | 改授权位 + 审计（detail 记 before/after）；不存在返回 `null`；**零变更原样返回、不落审计**。 |
| `removeEvidenceSafely(input)` | `RemoveEvidenceSafelyInput` | `RemoveEvidenceResult` | stable | 有引用且未 `force`→拒绝并返回影响面；`force`→事务内删证据+清链+审计。`removed=false && blockers 空 = 不存在`（见隐性契约第 7 条）。 |
| `removeCognitionSafely(input)` | `RemoveCognitionSafelyInput` | `RemoveCognitionResult` | stable | 删认知连溯源链 + 审计；审计 detail **只存元数据、不存内容原文**。 |
| `mergeCognition(input)` | `MergeCognitionInput` | `MergeCognitionResult` | stable | 仅同 subject；source 链搬到 target（去重）、target 置信重算、source 标失效不硬删。source/target 不存在、跨 subject、target 已失效/已归档 → **抛错**（什么都不改）。 |
| `archiveCognition(input)` | `ArchiveCognitionInput` | `Cognition \| null` | stable | 归档（`archivedAt=now`）+ 审计；召回跳过 archived；数据保留可恢复；不存在返回 `null`。 |
| `checkIntegrity()` | — | `IntegrityReport` | stable | 只读不改、不落审计、无 `reason`；报孤儿 join 行。 |
| `listEvidence(input?)` | `ListMemoryInput` | `Evidence[]` | stable | 列某 subject 全部证据；只读、不落审计。 |
| `listCognitions(input?)` | `ListMemoryInput` | `CognitionWithMeta[]` | stable | 列某 subject 全部认知，每条配溯源链 + **读时算**的 `effectiveConfidence`（不持久化，见隐性契约第 5 条）。 |
| `listEvents(input?)` | `ListMemoryInput` | `EventWithEvidence[]` | stable | 列某 subject 全部事件，每条配覆盖的证据 id 列表。 |
| `resetSubject(input)` | `ResetSubjectInput` | `ResetSubjectResult` | stable | 破坏性：清三层 + 清审计 + 清向量索引。库内四张表包在一个事务；`indexAll([])` 在事务外、**清整张 vectors 表**（v1 单人限制，见隐性契约第 8 条）。 |

依据：`src/memory/managementApi.ts:143-177`（接口）、`:214-441`（实现）、`src/core/createCore.ts:249`（门面挂载）。

> 陈旧注释提示：`createCore.ts:135` 与 `managementApi.ts:142` 的 doc 仍写"7 操作"，实际已是 **11**（批次5 步0 加了 4 个只读 list）。属陈旧注释，S2-2 或另立订正，本契约以 11 为准。

### 1.3 `core.portable`（便携记忆包，3 方法）

| 方法 | 入参 | 返回 | 级 | 隐性行为契约 |
|---|---|---|---|---|
| `exportBundle(opts?)` | `ExportOptions & { subjectId? }` | `MemoryBundle` | stable | 导出某 subject 三层 + 溯源链为可版本化 JSON；不含向量索引/logs/.env/UI 状态。 |
| `importBundle(bundle, opts)` | `MemoryBundle, ImportOptions` | `ImportPlan` | stable | `dryRun` 只算不写；`merge` 按 id/originId 去重写入（`ImportMode.replace` 留 V2，见 experimental）。 |
| `validateBundle(bundle)` | `unknown` | `ValidateResult` | stable | 只校验结构，不写。 |

依据：`src/core/createCore.ts:100-104`（`PortableAPI`）、`:251-260`（实现）。

### 1.4 `core.graph`（图谱视图，1 方法）

| 方法 | 入参 | 返回 | 级 | 隐性行为契约 |
|---|---|---|---|---|
| `buildMemoryGraph(opts?)` | `BuildGraphOptions & { subjectId? }` | `MemoryGraphPayload` | stable | 后端统一产出力导向图 `{nodes, edges}`；`conflicts_with`/`corrects` 边 v1 不生成（数据未存，见 experimental）。 |

依据：`src/core/createCore.ts:107-109`（`MemoryGraphAPI`）、`:262-267`（实现）。

---

## 二、关键数据形状专章（≥30 项）

每项标 stable/experimental。"落库后完整形状"与"门面入参/返回"是 stable；`*Input`（宿主直接构造的入参）随门面稳定而 stable；只由内部算子产出、宿主不直接构造的中间入参标 experimental。

### 2.1 三层落库形状（stable）

1. **`Evidence`** — stable。证据落库完整形状：`id / subjectId / sourceKind / hostId / originId / occurredAt / recordedAt / rawContent / summary / allowLocalRead / allowCloudRead / allowInference / correctsEvidenceId`。依据 `src/evidence/model.ts:14-40`。
2. **`EvidenceInput`** — stable（宿主经 `ingestUserMessage` 间接产；直接构造 `evidenceStore.put` 属 internal 路径）。`id/recordedAt` 由存储层生成，缺省授权位按 `sourceKind` 分流。依据 `src/evidence/model.ts:48-60`。
3. **`SourceKind`** — stable 枚举：`'spoken' | 'inferred' | 'observed' | 'tool'`（`'tool'` 于 AD-3/D-0013 加入 = 工具执行的返回结果，外部数据点）。加值不算破坏、须留 default。依据 `src/evidence/model.ts:11`。
4. **`Event`** — stable。事件落库形状：`id / subjectId / summary / occurredAt / createdAt`。依据 `src/event/model.ts:10-18`。
5. **`EventInput`** — **experimental**。宿主一般不直接构造（由 `distill` 内部产）；Host 侧无直接构造点（grep `apps/memoweft-host` 无命中）。依据 `src/event/model.ts:20-26`。
6. **`EventWithEvidence`** — stable（`core.memory.listEvents` 返回项）：`Event + evidenceIds: string[]`。依据 `src/event/model.ts:28-30`。
7. **`Cognition`** — stable。认知落库形状：`id / subjectId / content / contentType / formedBy / confidence(0~1000) / credStatus / scope / validAt / invalidAt / askedAt / archivedAt? / createdAt / updatedAt`。`askedAt` 字段本身 stable，其**写入时机**（M5 主动询问）属 experimental 面。依据 `src/cognition/model.ts:40-60`。
8. **`CognitionInput`** — **experimental**。宿主不直接构造（`confidence`/`credStatus` 由 `consolidate` 算好后传入；Host grep 无命中）。依据 `src/cognition/model.ts:63-75`。
9. **`ContentType`** — stable 枚举：`fact | preference | goal | project | state | trait | hypothesis | trend`。加值不算破坏、须留 default。依据 `src/cognition/model.ts:15-23`。
10. **`FormedBy`** — stable 枚举：`stated | observed | ruled | inferred`。依据 `src/cognition/model.ts:26`。
11. **`CredStatus`** — stable 枚举：`candidate | low | limited | stable | conflicted`。依据 `src/cognition/model.ts:29`。
12. **`EvidenceRelation`** — stable 枚举：`support | contradict`。依据 `src/cognition/model.ts:32`。
13. **`EvidenceLink`** — stable：`{ evidenceId, relation: EvidenceRelation }`。依据 `src/cognition/model.ts:34-37`。
14. **`CognitionWithSources`** — stable：`Cognition + sources: EvidenceLink[]`。依据 `src/cognition/model.ts:78-80`。

### 2.2 门面各方法入参形状

15. **`CreateCoreOptions`** — stable：`dbPath` 必填 + `llm?/embedder?/retriever?/config?/vectorDbPath?` + **`clock?: Clock`（experimental，Phase 4）**。`clock` 注入 store 落库/更新时间源（recordedAt/created_at/updated_at）以求确定性/时间旅行；缺省真实系统时间（additive，旧调用方不受影响）。只产时间戳、绝不进置信度自算（铁律 3b）。**D-0015 已把 clock 接通整条门面路径(三个 store + consolidate/attribute/管理审计 + 读路径衰减 now)。剩两处非门面路径——主动询问(`ProposeAskDeps`/`RevisitDeps` 的 askedAt)与 dev 运行日志(`RunLoggerOptions` 的 ts)——各自带可选 `clock?`(D-0020),补全「全仓时间源皆可注入」;两者属 internal 档、不经 `CreateCoreOptions.clock`。** 依据 `src/core/createCore.ts`。
15b. **`Clock`** — experimental（Phase 4）：`type Clock = () => Date`;`systemClock` 是缺省(真实系统时间)。经 `CreateCoreOptions.clock` / `openStores(dbPath, cfg, clock)` 注入。依据 `src/clock.ts`。
16. **`UserMessageInput`** — stable：`content` + `subjectId?/hostId?/sourceKind?/originId?/occurredAt?`。依据 `:56-66`。
17. **`ObservationInput`** — stable：`observations: Observation[]` + `subjectId?/hostId?`。依据 `:68-73`。
17a. **`ToolResultInput`** — stable（AD-3/D-0013）：`content`（工具返回结果 payload）+ `subjectId?/hostId?/originId?/occurredAt?`。落成 `tool` 证据，cloud-read 缺省 false（`config.toolDefaults`）。依据 `src/core/createCore.ts`。
18. **`RecallInput`** — stable：`query` + `subjectId?` + **`explain?: boolean`**（D-0021：`true` → 每条召回认知带上支撑证据链 `provenance`；additive、缺省关 = 行为不变）+ **`contentTypes?: ContentType[]`**（D-0022：允许名单；空/不传 = 全类型；对 top-K 的后过滤,可能欠填；additive）。依据 `:75-78`。
19. **`ConversationInput`** — stable：`message` + `conversationId?/subjectId?/hostId?/originId?/occurredAt?/systemPrompt?/seedTurns?`。依据 `:80-93`。
20. **`UpdateProfileInput`** — stable：`subjectId?`。依据 `:95-97`。
21. **`ListMemoryInput`** — stable：`subjectId?`。依据 `src/memory/managementApi.ts:115-117`。

### 2.3 门面各方法返回形状

22. **`TurnOutcome`** — stable：`reply / storedEvidence: Evidence / recall: RecalledCognition[] / llmCalls / error: string | null`。`error` 非空 = 回话降级但证据已落（见隐性契约第 6 条）。依据 `src/pipeline/conversation.ts:44-50`。
23. **`RecalledCognition`** — stable（`recall`/`TurnOutcome.recall` 项）：`RelevantCognition + score + id?` + **`contentType?: ContentType`**（D-0022；底层 `RecalledCognitionItem` 为必填）+ **`provenance?: RecalledEvidence[]`**（D-0021，仅 `recall({ explain: true })` 时带；`RecalledEvidence = { evidenceId; relation; summary; sourceKind; allowCloudRead; allowInference }` 支撑/反证证据简报、**带授权位**（对齐 `buildMemoryGraph`，宿主转发云模型前可按 tier 自筛 `provenance`）、面向宿主、可追溯；additive）。依据 `src/pipeline/conversation.ts:38-42`。
24. **`UpdateProfileResult`** — stable：`distilled / consolidated / attributed / indexed / indexError: string | null / timings`。依据 `src/consolidation/updateProfile.ts:45-55`。
25. **`UpdateProfileTimings`** — stable：`distillMs / consolidateMs / attributeMs / indexMs / totalMs`。依据 `:37-43`。
26. **`HealthReport`** — stable：`llmReady / embedReady`。依据 `src/core/createCore.ts:112-117`。
27. **`CognitionWithMeta`** — stable（`listCognitions` 项）：`Cognition + sources: EvidenceLink[] + effectiveConfidence`（读时算）。依据 `src/memory/managementApi.ts:120-125`。

### 2.4 管理 API 入出参形状

28. **`InvalidateCognitionInput`** — stable：`cognitionId + reason`。依据 `src/memory/managementApi.ts:22-26`。
29. **`UpdateEvidenceAuthorizationInput`** — stable：`evidenceId + allowCloudRead? + allowInference? + reason`。依据 `:28-34`。
30. **`RemoveEvidenceSafelyInput`** — stable：`evidenceId + reason + force?`。依据 `:36-41`。
31. **`RemovalBlocker`** — stable：`kind: 'event'|'cognition' + id + relation?`。依据 `:44-51`。
32. **`RemoveEvidenceResult`** — stable：`removed + blockers: RemovalBlocker[]`。依据 `:53-58`。
33. **`RemoveCognitionSafelyInput`** — stable：`cognitionId + reason`。依据 `:60-63`。
34. **`RemoveCognitionResult`** — stable：`removed + removedLinks: EvidenceLink[]`。依据 `:65-69`。
35. **`MergeCognitionInput`** — stable：`sourceId + targetId + reason`。依据 `:71-77`。
36. **`MergeCognitionResult`** — stable：`merged + movedLinks + duplicateLinks + target: Cognition + source: Cognition`。依据 `:79-89`。
37. **`ArchiveCognitionInput`** — stable：`cognitionId + reason`。依据 `:91-94`。
38. **`IntegrityIssue`** — stable：`kind + eventId? + cognitionId? + evidenceId + missing`。依据 `:97-104`。
39. **`IntegrityReport`** — stable：`ok + issues: IntegrityIssue[] + checkedAt`。依据 `:106-110`。
40. **`ResetSubjectInput`** — stable：`subjectId? + reason?`（`reason` 仅备语义、不落库）。依据 `:129-133`。
41. **`ResetSubjectResult`** — stable：`evidenceRemoved / eventRemoved / cognitionRemoved / auditRemoved`。依据 `:135-140`。
42. **`ManagementLogEntry`** — **experimental**（弱类型：`op`/`targetKind` 现为 `string`；门面**不暴露**读审计路径——Host 走 `core.memory.*` 写但不经门面读审计历史，只有底层 `SqliteManagementLog.list` 能读）：`op / targetKind / targetId / reason / detail: Record<string,unknown>|null / createdAt`。依据 `src/memory/managementLog.ts:23-33`。

### 2.5 便携包形状

43. **`MemoryBundle`** — stable：`format / schemaVersion / exportedAt / memoWeftVersion / subjectId / source{hostId,exportMode:'full'} / data{evidence,events,eventEvidence,cognitions,cognitionEvidence,unconsolidatedEventIds} / metadata{counts,notes}`。依据 `src/portable/model.ts:33-60`。
44. **`EventEvidenceLink`** — stable：`{eventId, evidenceId}`。依据 `:20-23`。
45. **`CognitionEvidenceLink`** — stable：`{cognitionId, evidenceId, relation}`。依据 `:26-30`。
46. **`ImportMode`** — stable 类型，但 `'replace'` 取值 **experimental**（留 V2；现仅 `'dryRun' | 'merge'`）。依据 `:63`。
47. **`ValidateResult`** — stable：`valid + errors[] + warnings[]`。依据 `:66-70`。
48. **`ImportPlan`** — stable：`mode + valid + errors[] + warnings[] + counts{...} + duplicates{...}`。依据 `:73-92`。
49. **`BUNDLE_FORMAT` / `BUNDLE_SCHEMA_VERSION`** — stable 常量：`'memoweft-bundle'` / `1`。依据 `:15-17`。

### 2.6 图谱 payload 形状

50. **`MemoryGraphPayload`** — stable：`subjectId / generatedAt / scope / depth / nodes / edges / stats`。依据 `src/graph/model.ts:71-79`。
51. **`MemoryGraphNode`** — stable：`id / kind / label / summary? / (cognition:) contentType?/formedBy?/confidence?/credStatus? / (evidence:) sourceKind?/allowCloudRead?/allowInference? / 时间字段 / archivedAt? / val?/colorKey?`。依据 `:26-50`。
52. **`MemoryGraphEdge`** — stable：`id / source / target / kind / label? / dashed?`。依据 `:52-59`。
53. **`MemoryGraphStats`** — stable：`nodeCount / edgeCount / hiddenCount / activeCognitionCount / conflictedCount / hypothesisCount / observedEvidenceCount / toolEvidenceCount`（`toolEvidenceCount` 于 AD-3/D-0013 加入，additive）。依据 `:61-69`。
54. **`MemoryGraphNodeKind`** — stable 枚举：`subject|evidence|event|cognition`。依据 `:16`。
55. **`MemoryGraphEdgeKind`** — stable 枚举，但 `conflicts_with`/`corrects` 两值 **experimental**（v1 未生成，数据未存）。依据 `:18-24`。

### 2.7 感知输入形状

56. **`Observation`** — stable（跨层契约"采集插件→Host→Core"）：`kind / occurredAt / content / originId? / meta? / allow*?`。**但**：`meta` 字段 **experimental**（本版仅承载不落库）、`kind` 是**开放集** experimental（现固定 `'active_window'`，以后加值）。依据 `src/perception/ingest.ts:19-34`。

### 2.8 版本 / 配置

57. **`MEMOWEFT_VERSION`** — stable 常量。`DLA_VERSION` 是 `@deprecated` 别名（保留、勿删）。依据 `src/index.ts:208-211`。
58. **`MemoWeftConfig`（有哪些配置项）** — stable：identity / privacyMode / observedDefaults / consolidation / retrieval / attribution / background 等字段结构。**0.4.0 加可选 `language: 'zh' | 'en'`（additive 非破坏——旧宿主不传照跑；缺省 `'en'`，env `MEMOWEFT_LANG=zh` 或运行期设 `config.language` 切中文）+ 导出 `type Lang`（stable，供宿主设值）**。**AD-3/D-0013 加 `toolDefaults: { allowLocalRead; allowCloudRead; allowInference }`（additive）——`tool` 证据的保守默认授权（local✓ / cloud✗ / infer✓），由 `put()` 按 `sourceKind` 套用，与 `observedDefaults` 对称。****但“怎么拿到 config”（`config` 单例访问）标 experimental**，pre-1.0 期间可能调整。`DlaConfig` 是 `@deprecated` 别名。`cloudReadDefault()` / `resolveLang()` stable（后者取当前库语言，只决定文本产出、绝不进置信度自算）。依据 `src/config.ts`。

---

## 三、隐性契约专章（宿主最易踩的坑）

1. **`confidence` 是 0~1000 量纲、由 MemoWeft 自算而非 LLM 自报**。别把它当 0~1 概率、也别信 LLM 回报的分数。依据 `src/cognition/model.ts:46-47`、`src/consolidation/confidence.ts:4`（"不采信 LLM 自报"）、`:24-34`。
2. **管理写操作的 `reason` 必填是隐私审计契约**，不可放松为可选——审计表回答"我的记忆被怎么了"。依据 `src/memory/managementApi.ts:22-94`（各 Input 的 `reason: string` 非可选）、`managementLog.ts` schema `reason TEXT NOT NULL`。
3. **`observed` 与 `tool` 证据均默认 `allowCloudRead=false`（隐私红线 B）**。摄入观察、摄入工具结果默认不上云（工具返回值常含敏感外部数据——网页/文件/API 响应）；只有输入显式给 `allowCloudRead:true` 才上云。最后防线在 `evidenceStore.put()` 按 `sourceKind` 兜底（`observed` → `observedDefaults`、`tool` → `toolDefaults`）。依据 `src/evidence/store.ts`（保守分支）、`src/perception/ingest.ts:7-10`、`:79-82`。
4. **`systemPrompt` / `seedTurns` 仅首次建会话实例时生效**（换人设/重种续聊窗口须先 `dropConversation(id)` 再调，否则命中旧实例、新值被静默忽略）。依据 `src/core/createCore.ts:89-92`（入参注释）、`:207-234`（复用/重建逻辑）。
5. **`effectiveConfidence` 是读时算的衍生值、不持久化**。库里存的是原始 `confidence`；`listCognitions` 返回的 `effectiveConfidence` = `confidence × 衰减因子`，每次读现算。依据 `src/memory/managementApi.ts:123-124`、`:397-405`。
6. **`TurnOutcome.error` 非空 = 回话降级但证据已落（先存后答）**。宿主看到 `error != null` 应知"这轮没正常回话，但用户的话已存进证据库"，不要重试摄入（会重复落库或靠 originId 幂等）。依据 `src/pipeline/conversation.ts:44-50`、`:63-78`（存在前、召回失败当无召回照常）。
7. **`RemoveEvidenceResult`：`removed=false && blockers 为空 = 目标不存在**（二义消歧）。拒绝只发生在有引用时（`blockers` 非空）；`removed=false` 且 `blockers=[]` 是"证据本就不存在"，不是"被拦下"。依据 `src/memory/managementApi.ts:55-57`、`:250`。
8. **`resetSubject` v1 单人限制**：库内清理按 subject，但清向量索引走 `indexAll([])`，**清的是整张 vectors 表（所有 subject 的向量）**，不是只清本 subject。v1 单人单宿主无碍；多 subject 化时须换 subject 粒度。依据 `src/memory/managementApi.ts:435-438`。
9. **无 `.env` 也能建 core**：缺模型配置时，"存证据 / 管理记忆"这类不碰模型的活仍可用；只有真调模型的读写路径（回话、语义召回、画像生成）才降级/报错。`health()` 告诉你哪些能力还在（`llmReady`/`embedReady`）。这是宿主判断"缺配时哪些能力还在"的关键承诺。依据 `src/core/createCore.ts:5-8`（工厂头注）、`:147-174`、`:269-280`（health）。
10. **枚举取值集合的兜底责任在宿主**：`SourceKind` / `ContentType` / `FormedBy` / `CredStatus` / `EvidenceRelation`——**收窄（删取值）算破坏；加值不算破坏，但宿主须留 `default` 兜底分支**。宿主 `switch` 这些枚举时若无 `default`，加值后会漏分支——责任在宿主。依据本契约“破坏政策”节 + `src/evidence/model.ts:11`、`src/cognition/model.ts:15-32`。

---

## 四、experimental 清单专章（"以后要变"的集中列出）

导出了、宿主可能碰，但**明说会变**，minor 版随便改（CHANGELOG 提一句即可，不欠迁移说明）。别把这些当稳定面依赖。

- **`Observation.meta`** — 本版仅承载、不落库（Evidence 无 meta 列）；以后落库形状会变。依据 `src/perception/ingest.ts:28-29`。
- **`Observation.kind`（开放集）** — 现固定 `'active_window'`，以后加 `'clipboard'`/`'device'` 等。依据 `src/perception/ingest.ts:23-24`。
- **`ImportMode.replace`** — 现只支持 `'dryRun'|'merge'`，`'replace'` 留 V2。依据 `src/portable/model.ts:62-63`。
- **图谱 `conflicts_with` / `corrects` 边** — v1 未生成（cognition↔cognition 链数据未存）；枚举保留、等数据模型补齐再产。依据 `src/graph/model.ts:7-12`、`:23-24`。
- **`Cognition.askedAt`（写入时机）** — 字段本身稳定，但"何时写"（M5 主动询问 `proposeAsk` 发问后写）属试验期能力。依据 `src/cognition/model.ts:53`、`src/asking/proposeAsk.ts`。
- **`ManagementLogEntry`（读审计历史）** — 弱类型（`op`/`targetKind` 为 `string`），门面不暴露读路径；宿主经 `core.memory.*` 只写不经门面读审计历史。依据 `src/memory/managementLog.ts:23-33`。
- **扩展点接口 `Retriever` / `Embedder` / `LLMClient`** — 可替换的注入点，接口签名以后可能演进。依据 `src/index.ts:88-105`。**新增（档2·非破坏）**：`LLMClient.tier?` 与 `LLMConfig.tier?`（`ModelTier='cloud'|'local'`，已导出）是可选字段，缺省视为 `cloud`；宿主自注入的 `LLMClient` 不带 tier 也照跑。
- **插件契约 `MemoWeftPlugin` / `PluginContext` / `PluginPermissions` / hook 类型**（第 7 步·v2·**experimental**）— 从 `src/plugin/contract.ts` 导出；`createMemoWeftCore` 加可选 `plugins?`（不传 = 行为同旧）。pre-1.0 hook 签名可能演进（如加字段）。**权威定义与语义见 [`plugin-contract.md`](../plugin-contract.md)**，此处不重复。
- **config 的“取用方式”（单例访问）** — “有哪些配置项”是 stable，“怎么拿到 config（`config` 单例）”是 experimental，pre-1.0 期间可能调整。依据 `src/config.ts`。
- **`EventInput` / `CognitionInput`** — 见"存疑定级"：宿主不直接构造的内部入参。

---

## 五、破坏性变更政策（pre-1.0，中间偏松）

> 契约顶部「怎么读这份契约」已给一句话摘要；本章是**成文的政策全文**，宿主据此判断"某次升级会不会崩我的集成、要不要改代码"。

### 5.1 什么算「破坏 stable」

对 **stable** 面（第一/二章列出的门面方法与数据形状），下列改动算破坏：**改字段名 / 删字段 / 改可空性（可选↔必填、可空↔非空）/ 改语义**（例：`confidence` 从 0~1000 量纲改成 0~1 概率）。

### 5.2 破坏 stable 的三要件

允许在 **minor 版**破坏 stable，但**必须同时**满足三条，缺一不可：

1. **① CHANGELOG 明确标注** —— 在 `CHANGELOG.md` 的 `Changed`（或 `Removed`）段点名写清破了哪个符号 / 哪个字段。
2. **② 一句迁移说明** —— 旧→新怎么改（宿主照着一步能改完），写在 CHANGELOG 同一条里。
3. **③ 能保旧名的走 `@deprecated` 别名** —— 凡是"改名 / 换常量"这类能留旧名的，保留旧名并挂 `@deprecated` 指向新名（**照现成样板**：`DLA_VERSION`（`src/index.ts:210` @deprecated 别名指向 `MEMOWEFT_VERSION`）、`DlaConfig`（`src/config.ts:136` @deprecated 别名指向 `MemoWeftConfig`）——这两处就是"已弃用样板"，别删）。

**不强制"保留整整一个版本再删"**：删除时机不设硬性冷却期，能留别名就留、留不了（如删字段）就按 ①② 标注 + 迁移说明走。

### 5.3 枚举加值口径

对 `SourceKind` / `ContentType` / `FormedBy` / `CredStatus` / `EvidenceRelation` 等枚举：

- **加新取值 ≠ 破坏** —— minor 版可加值。**但宿主必须对这些枚举留 `default` 兜底分支**（`switch` 没 `default`、加值后漏分支——**责任在宿主**，见隐性契约第 10 条）。
- **收窄（删取值）= 破坏** —— 按 5.2 三要件走。

### 5.4 experimental 面（松口径）

对 **experimental** 面（第四章清单：`Observation.meta` / `Observation.kind` 开放集 / `ImportMode.replace` / 图谱 `conflicts_with`·`corrects` 边 / `Cognition.askedAt` 写入时机 / `Retriever`·`Embedder`·`LLMClient` 扩展点 / config 的取用方式 / `ManagementLogEntry` / `EventInput`·`CognitionInput` 等）：

- **minor 版随便改**，改了不算爽约。
- **CHANGELOG 提一句即可**，不欠迁移说明、不欠 `@deprecated` 别名。

### 5.5 internal 面

**别依赖**。导出还在只是本步"只标不删"（删属第 10 步）；一旦收口删除，不走 stable 的三要件，CHANGELOG 提一句即可。

---

## 六、存疑符号定级（回源确认结论）

以下 6 处容易误判的符号，按源码使用方式逐项定级：

| 符号 | 级 | 定级依据（指到源） |
|---|---|---|
| `AskProposal` / `AskPolicy` / `proposeAsk`（`src/asking/`）| **internal** | 门面 `MemoWeftCore`（`createCore.ts:120-145`）**不暴露** proposeAsk；Host（`apps/memoweft-host`）grep **无命中**。唯一直用点是**开发测试台** `testbench/server.mjs:25-26,463-464`——测试台是 dev 调试harness、非产品宿主，不构成对宿主的契约面。故定 internal（AskProposal/AskPolicy 是其入出参，随之 internal）。`revisitConflicts` 同理 internal。 |
| `Cognition` / `Evidence` 领域形状 | **stable** | `recall`/`TurnOutcome.storedEvidence` 回吐整条 `Evidence`（`createCore.ts:122`、`conversation.ts:46`）；`listCognitions`/`listEvidence`/`listEvents` 把整条 `Cognition`/`Evidence`/`Event` 回吐给宿主（`managementApi.ts:167-171`）。回吐→升 stable。 |
| `Conversation` 类 | **internal** | 门面 `handleConversationTurn` **内包** `Conversation`（`createCore.ts:207-228` 建实例、缓存、复用），宿主不直接 new。门面已收口→类判 internal。 |
| `TurnOutcome` / `RecalledCognition` | **stable** | 作 `handleConversationTurn` / `recall` 的返回形状回吐宿主（`createCore.ts:126,128`）。→ stable。 |
| `Observation`（`src/perception/ingest.ts`）| **stable**（`meta` 字段 experimental）| "采集插件→Host→Core"跨层契约，`ingestObservation` 入参（`createCore.ts:124`、`ingest.ts:19-34`）。`meta` 字段源码注明"本版仅承载不落库"→ 该字段 experimental。 |
| `EventInput` / `CognitionInput` | **experimental** | 宿主一般不直接构造（由 distill/consolidate 内部产；Host grep 无直接构造点）。不列入宿主主面。依据 `event/model.ts:20-26`、`cognition/model.ts:63-75`。 |
| `ManagementLogEntry` | **experimental** | 字段弱类型（`op`/`targetKind` 为 `string`，`managementLog.ts:23-33`）；门面不暴露读审计历史路径，Host 只经 `core.memory.*` 写、不经门面读审计。→ experimental。 |

---

## 七、适配器降级语义（§16.2）

> 范围：MemoWeft 官方适配器（`@memoweft/adapter-ai-sdk`、`@memoweft/mcp-server`）。当记忆层（`core.recall` / `core.ingestUserMessage`）失败或超时，适配器**降级而不中断对话**。人类已批准措辞，2026-07-11（见 `DECISIONS.md` D-0012）。本节只约束适配器，不给上面的 Core 门面新增任何义务。

- **recall 超时**：读路径把 `core.recall` 包在 **200ms** 超时里，默认值**可配**（适配器工厂选项 `recallTimeoutMs`）；超时即视为失败。
- **重试**：**读路径（recall）不重试**——失败/超时直接降级；**写路径（ingest）失败重试一次**再放弃。
- **降级行为**：失败/超时 → **注入空上下文（无记忆），对话不中断**；经**注入的 logger** 记一条（默认无 logger = 静默，宿主可注入）。
- **实现边界**：超时用适配器内 `Promise.race` 包裹 `core.recall`；logger 是适配器工厂的可选参。**不碰 Core api-freeze**——Core 的 `src/index.ts` 导出面 / `tests/api/api-surface.snapshot` 不变（`npm run api:check` 仍一致）。
- **logger 只记结构化降级事件**——形状 `{ event: 'memory_degraded', op: 'recall' | 'ingest', reason: 'timeout' | 'error' }`（MCP server 另带一个可选 `tool` 字段）——**绝不记用户内容 / 原话 / 密钥**（认知纪律 + 隐私）。
- **降级 vs 真错**：只有记忆层内部故障/超时（`core.recall` / `core.ingestUserMessage` 抛错或超时）才降级。调用方的错——参数非法、协议层错误——**不**被当降级吞掉，仍以错误上浮。MCP server 里 inputSchema（`zod`）校验在 handler 之前跑，故参数非法仍是协议错误 `isError: true`；只有被包裹的 `core.*` 调用才降级。

依据：`packages/adapter-ai-sdk/src/recallMiddleware.ts`（recall 超时 + 降级）、`packages/adapter-ai-sdk/src/persistOnEnd.ts`（写路径一次重试 + 降级）、`packages/mcp-server/src/tools.ts`（读/写 tool 兜底）、`packages/adapter-ai-sdk/src/degrade.ts` + `packages/mcp-server/src/degrade.ts`（共享 `DEFAULT_RECALL_TIMEOUT_MS = 200`、`withTimeout`、logger 事件类型）。
