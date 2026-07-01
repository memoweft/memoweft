# MemoWeft 接入指南（Integration Guide）

> 面向宿主（Host）开发者：如何把 MemoWeft 这层「用户认知框架」import 进你的应用/Agent，
> 建好三层数据存储、接上模型、跑通读路径（对话回话）和写路径（更新画像）。

MemoWeft 是一个**被宿主 import 的库**——它自己不做聊天界面、不定人设、不管 UI。
宿主负责语气/角色/隐私策略与「是否开口」，MemoWeft 只负责：把用户的证据**编织**成
一块可追溯、带把握度的「认知之布」，需要时提供带边界的用户上下文。

本文所有 API 名称与签名均逐一核对自 `src/` 真实源码（入口 `src/index.ts`）。

---

## 目录

- [1. 前置与安装](#1-前置与安装)
- [2. 30 秒心智模型](#2-30-秒心智模型)
- [3. 端到端最小接入](#3-端到端最小接入)
- [4. 环境变量配置（MEMOWEFT_* / 兼容 DLA_*）](#4-环境变量配置)
- [5. 可替换点（Retriever / Embedder / LLMPool）](#5-可替换点)
- [6. 公共 API 全表](#6-公共-api-全表)
- [7. 宿主该负责什么（边界）](#7-宿主该负责什么边界)

---

## 1. 前置与安装

- **Node ≥ 22.5**（用到内置 `node:sqlite`；本仓在 Node 24 上开发验证）。
  - `node:sqlite` 目前仍是 Node 的实验特性，运行时可能打印一行实验告警——功能正常，不影响使用。
- **零运行时依赖**：存储用 `node:sqlite`、HTTP/日志用 Node 内置，`package.json` 的 `dependencies` 为空。
- **TypeScript ≥ 5.7**（若你用 TS）。库以 ESM 发布（`"type": "module"`）。

安装（包名 `memoweft`）：

```bash
npm install memoweft
```

从包名导入（发布形态 `main = dist/index.js` / `types = dist/index.d.ts`）：

```ts
import {
  SqliteEvidenceStore,
  SqliteEventStore,
  SqliteCognitionStore,
  Conversation,
  updateProfile,
  loadLLMPool,
  VectorRetriever,
  NullRetriever,
  OpenAICompatEmbedder,
  loadEmbedConfig,
  config,
} from 'memoweft';
```

> 注：仓库内部源码用相对路径 + `.ts` 扩展名 import（`allowImportingTsExtensions` + `rewriteRelativeImportExtensions`）。
> 作为宿主，你只从包名 `memoweft` 导入，不碰内部路径。

---

## 2. 30 秒心智模型

三层数据，逐层「情境化」：

```
evidence（证据·原始事实）  →  event（事件·情境化）  →  cognition（认知/画像·判断）
   用户说了/做了什么              一段对话的情境摘要         对用户的判断（带把握度、可溯源）
```

- **读路径**（同步、轻）：一轮对话 → 存证据 → 召回相关认知 → 带上下文回话。走 `Conversation.handle`。
- **写路径**（异步、攒批）：把「没整理」的证据沉淀成事件 → 重算画像 → 归因 → 重建召回索引。走 `updateProfile`。
- **读写解耦**：聊天时只落证据、轻量召回；「更新画像」是重活（要调几次模型），放后台慢慢做，不挡聊天。

三个存储可以**指向同一个 SQLite 文件**（三张各自的表），也可分开。默认路径 `'./dla.db'`（品牌改名，物理默认库名保守不改）。

---

## 3. 端到端最小接入

下面是一段可直接照抄的接入骨架，覆盖：建三层 store → 接模型/召回 → `handle` 对话 → `updateProfile` 更新画像 → 读画像。
它与仓库里 `testbench/server.mjs` 的真实接线一致。

```ts
import {
  SqliteEvidenceStore,
  SqliteEventStore,
  SqliteCognitionStore,
  Conversation,
  updateProfile,
  loadLLMPool,
  loadEmbedConfig,
  OpenAICompatEmbedder,
  VectorRetriever,
  NullRetriever,
  config,
} from 'memoweft';

// ── 1) 三层存储（可共用一个 db 文件；测试用 ':memory:'）──
const DB = './my-app.db';
const evidenceStore  = new SqliteEvidenceStore(DB);
const eventStore     = new SqliteEventStore(DB);
const cognitionStore = new SqliteCognitionStore(DB);

// ── 2) 模型池：对话用大模型、写路径用（可独立配的）小快模型 ──
//    从 .env 读 MEMOWEFT_LLM_*（对话）与 MEMOWEFT_WRITE_LLM_*（写路径，缺配则回退对话模型）。
const llmPool  = loadLLMPool();
const chatLLM  = llmPool.for('chat');   // 读路径（回话质量优先）
const writeLLM = llmPool.for('write');  // 写路径（distill/consolidate/attribute）

// ── 3) 召回底座：配了嵌入器就用向量召回，否则降级为空召回（不报错）──
const embedConfig = loadEmbedConfig();  // 读 MEMOWEFT_EMBED_*（缺配返回 null）
const retriever = embedConfig
  ? new VectorRetriever(DB, new OpenAICompatEmbedder(embedConfig))
  : new NullRetriever();

// ── 4) 会话对象（一个会话一个实例；它内部维护「最近几轮」窗口）──
const convo = new Conversation({
  store: evidenceStore,
  retriever,
  cognitionStore,
  llm: chatLLM,
});

const subjectId = config.identity.subjectId; // 单人单宿主默认 'owner'；多用户时自己传

// ── 5) 读路径：一轮对话（存证据 → 召回 → 回话，先存后答，召回/回话失败不丢证据）──
const outcome = await convo.handle('我最近在赶一个副业项目，天天熬夜');
console.log(outcome.reply);           // 助手回话文本
console.log(outcome.storedEvidence);  // 本轮落库的证据（只存用户的，助手回话不落）
console.log(outcome.recall);          // 本轮召回注入的认知（带相似度 score + 有效置信）

// ……多轮对话后……

// ── 6) 写路径：把攒下的证据沉淀成画像（重活，通常后台/攒批触发，不必每轮调）──
const upd = await updateProfile(subjectId, {
  evidenceStore,
  eventStore,
  cognitionStore,
  retriever,   // 用于重建召回索引，让新画像马上能被召回
  llm: writeLLM,
});
console.log(upd.consolidated.created);  // 新增的认知
console.log(upd.attributed.hypotheses); // 归因产出的可解释假设
console.log(upd.timings);               // 各步耗时(ms)，诊断"慢在哪步"
console.log(upd.indexError);            // 索引重建失败原因；null=成功（失败不回滚画像）

// ── 7) 读画像：直接查认知层（宿主自己决定怎么用）──
const profile = cognitionStore.active(subjectId); // 未失效的认知，按把握度降序
for (const c of profile) {
  console.log(`[${c.contentType}] ${c.content}  把握度=${c.confidence}/1000  ${c.credStatus}`);
}
```

**关键纪律（接入时别绕过）：**

- 只有**用户消息**落成证据；**助手回话不落证据**（禁止系统自证）。`Conversation.handle` 已内建这条。
- 画像里 `confidence` 是 MemoWeft **自算**的把握度（0~1000），不采信模型自报。
- LLM 推断出的条目先当**低置信候选/假设**（`formedBy='inferred'`、封顶）；冲突先标 `conflicted` 暴露，不自动消解。
- 召回注入回话时走**衰减门控**：淡了的情绪、过气的假设（有效置信低于阈值）不注入。

### 直接摄入「行为观察」证据（可选）

除了对话，宿主还能把外部采集器（活动窗口/设备等）标准化好的观察灌进来，落成 `sourceKind='observed'` 证据：

```ts
import { ingestObservations, activeWindowToObservation } from 'memoweft';

const obs = activeWindowToObservation({
  app: 'VS Code',
  title: 'my-app',
  durationSec: 2400,
  occurredAt: new Date().toISOString(),
});
const res = ingestObservations(subjectId, [obs], { evidenceStore });
console.log(res.stored, res.skipped); // observed 默认授权：本地可读、默认不上云、可推画像
```

> `observed` 证据的默认授权保守（`config.observedDefaults` = 本地可读 / **默认不上云** / 可推画像）。
> 写路径喂云端模型前会用隐私关 `filterCloudReadable` 把 `allowCloudRead=false` 的挡在 prompt 外。

---

## 4. 环境变量配置

MemoWeft 读 env 时**双前缀兼容**：每个键先读 `MEMOWEFT_*`，读不到再回退旧名 `DLA_*`。
两者都没配，写路径/嵌入器才会报错或降级。现有只含 `DLA_*` 的 `.env` **零改动继续工作**。

| 用途 | 主名（推荐） | 兼容旧名 | 谁读 / 缺配后果 |
| --- | --- | --- | --- |
| 对话模型 | `MEMOWEFT_LLM_BASE_URL`<br>`MEMOWEFT_LLM_API_KEY`<br>`MEMOWEFT_LLM_MODEL` | `DLA_LLM_*` | `loadLLMPool().for('chat')` / `loadLLMConfig()`。缺配：起服务不崩，真调用时抛错。 |
| 写路径模型 | `MEMOWEFT_WRITE_LLM_BASE_URL`<br>`MEMOWEFT_WRITE_LLM_API_KEY`<br>`MEMOWEFT_WRITE_LLM_MODEL` | `DLA_WRITE_LLM_*` | `loadLLMPool().for('write')`。**缺配则回退对话模型**（行为同旧、不强制）。 |
| 嵌入器（召回） | `MEMOWEFT_EMBED_BASE_URL`<br>`MEMOWEFT_EMBED_API_KEY`<br>`MEMOWEFT_EMBED_MODEL` | `DLA_EMBED_*` | `loadEmbedConfig()`。**缺任一 → 返回 `null`**，召回降级为空（不报错、回话不注入画像）。 |

- 模型客户端打 **OpenAI 兼容** `/chat/completions`；嵌入器打 **OpenAI 兼容** `/embeddings`。任何兼容此协议的云端/本地服务（含本地 Ollama 的 OpenAI 兼容端点）都能接。
- `loadLLMConfig(prefix)` 的 `prefix` 默认 `'LLM'`；写路径传 `'WRITE_LLM'`。历史兼容：也接受传 `'DLA_LLM'` / `'DLA_WRITE_LLM'`（自动剥去 `DLA_` 再走双前缀）。
- `.env` 会由 `loadLLMConfig` / `loadEmbedConfig` 内部通过 `process.loadEnvFile()` 尝试加载（无 `.env` 时静默忽略）。

错误提示文案已同时列两种前缀，例如：
`LLM 配置缺失：请在 .env 设置 MEMOWEFT_LLM_BASE_URL / _API_KEY / _MODEL（或兼容旧名 DLA_LLM_*）`

---

## 5. 可替换点

MemoWeft 把三个「换实现只动一处」的接缝暴露成接口，宿主可自带实现替换。

### 5.1 Retriever（召回底座）

接口 `Retriever`（`src/retrieval/retriever.ts`）：

```ts
interface Retriever {
  indexAll(items: Array<{ id: string; text: string }>): Promise<void>; // 替换式重建索引
  search(query: string, topK: number): Promise<RetrievalHit[]>;        // 返回 { id, score }[]，按分降序
}
```

内置两种实现：

- `NullRetriever`：空召回（`indexAll` 不做事、`search` 返回 `[]`）。没配嵌入器时的降级底座。
- `VectorRetriever(dbPath, embedder)`：SQLite 存向量 + JS 余弦相似度，零原生扩展。`indexAll` 替换式重建，`search` 嵌入 query 后取 top-k。

自带实现（如接 Mem0 / 外部向量库）：实现这两个方法即可，把实例传给 `Conversation` 的 `retriever` 和 `updateProfile` 的 `retriever`。

### 5.2 Embedder（嵌入器）

接口 `Embedder`（`src/retrieval/embedder.ts`）：

```ts
interface Embedder {
  embed(texts: string[]): Promise<number[][]>; // 一组文本 → 一组向量
}
```

- 内置 `OpenAICompatEmbedder(cfg)`：打 OpenAI 兼容 `/embeddings`。`cfg` 由 `loadEmbedConfig()` 从 env 读（缺配返回 `null`）。
- 换本地嵌入模型：自己实现 `embed`，塞进 `new VectorRetriever(dbPath, myEmbedder)`。

### 5.3 LLMPool / LLMClient（模型池）

接口（`src/llm/pool.ts` / `src/llm/client.ts`）：

```ts
type LLMPurpose = 'chat' | 'write';
interface LLMPool { for(purpose: LLMPurpose): LLMClient; }

interface LLMClient {
  chat(messages: ChatMessage[]): Promise<string>;
  readonly callCount: number; // 累计调用次数（用于统计本轮调了几次）
}
interface ChatMessage { role: 'system' | 'user' | 'assistant'; content: string; }
```

- `loadLLMPool()` 从 env 装配：`chat` 用 `MEMOWEFT_LLM_*`、`write` 用 `MEMOWEFT_WRITE_LLM_*`（缺则回退 chat）。
- 内置 `OpenAICompatClient(cfg?)`：打 OpenAI 兼容 `/chat/completions`（`temperature: 0.3`）。`cfg` 缺省时用 `loadLLMConfig()`。
- 换任意模型：实现 `LLMClient`（两个成员：`chat` + `callCount`），把它作为 `llm` 传给 `Conversation` / `updateProfile` / 各写路径函数。
- **留口**：模型池按「维度」选 client，当前维度是用途（chat/write）；未来「按证据 `allowCloudRead` 路由本地/云端」在此之上加 tier 维度即可，不用重构。

---

## 6. 公共 API 全表

以下为 `src/index.ts` 的全部导出（类型 + 函数/类签名 + 一句话用途）。签名均核对自源码。

### 6.1 证据层（evidence）

| 导出 | 形态 | 一句话用途 |
| --- | --- | --- |
| `SourceKind` | 类型 = `'spoken' \| 'inferred' \| 'observed'` | 来源种类：亲口 / AI 推测 / 行为观察（强度分层）。 |
| `Evidence` | 接口 | 一条证据落库后的完整形状（id、subjectId、sourceKind、双时态 occurredAt/recordedAt、rawContent、summary、三个授权位、correctsEvidenceId…）。 |
| `EvidenceInput` | 接口 | 写入证据的入参；id/recordedAt 由存储生成，授权位/summary 缺省时补默认。 |
| `EvidenceStore` | 接口 | 证据存储契约（见下方方法）。 |
| `SqliteEvidenceStore` | 类 | `new SqliteEvidenceStore(dbPath = './dla.db')`。`node:sqlite` 实现。 |

`EvidenceStore` 方法：

```ts
put(input: EvidenceInput): Evidence;        // 写入；带 originId 时幂等（已存在返回原条）
get(id: string): Evidence | null;
all(): Evidence[];
byTimeRange(fromIso: string, toIso: string): Evidence[];      // 按 occurredAt 区间升序
update(id: string, patch: { rawContent?: string; summary?: string }): Evidence | null;
remove(id: string): boolean;                                  // 用户主动删（非系统自动删）
findByOrigin(originId: string): Evidence | null;              // 按幂等键查（摄入判重用）
close(): void;
```

### 6.2 事件层（event）

| 导出 | 形态 | 一句话用途 |
| --- | --- | --- |
| `Event` | 接口 | 一个事件（id、subjectId、summary、occurredAt、createdAt）。 |
| `EventInput` | 接口 | 写入事件入参（subjectId、summary、occurredAt、`evidenceIds: string[]` 覆盖了哪些原话）。 |
| `EventWithEvidence` | 接口 | `Event` + `evidenceIds`。 |
| `EventStore` | 接口 | 事件存储契约。 |
| `SqliteEventStore` | 类 | `new SqliteEventStore(dbPath = './dla.db')`。 |

`EventStore` 方法：`put`、`get`、`all(subjectId?)`、`evidenceOf(eventId)`、`coveredEvidenceIds(subjectId)`、`unconsolidated(subjectId)`、`markConsolidated(ids)`、`remove(id)`、`removeBySubject(subjectId)`、`close()`。

### 6.3 认知层（cognition）

| 导出 | 形态 | 一句话用途 |
| --- | --- | --- |
| `ContentType` | 类型 | `'fact' \| 'preference' \| 'goal' \| 'project' \| 'state' \| 'trait' \| 'hypothesis' \| 'trend'`。 |
| `FormedBy` | 类型 | 形成方式：`'stated' \| 'observed' \| 'ruled' \| 'inferred'`（来源强度）。 |
| `CredStatus` | 类型 | 可信状态：`'candidate' \| 'low' \| 'limited' \| 'stable' \| 'conflicted'`。 |
| `EvidenceRelation` | 类型 | `'support' \| 'contradict'`。 |
| `EvidenceLink` | 接口 | `{ evidenceId: string; relation: EvidenceRelation }`（溯源链一环）。 |
| `Cognition` | 接口 | 一条认知（content、contentType、formedBy、`confidence: 0~1000`、credStatus、scope、validAt/invalidAt、askedAt…）。 |
| `CognitionInput` | 接口 | 写入认知入参（confidence/credStatus 由 consolidate 算好传入，`evidence?: EvidenceLink[]`）。 |
| `CognitionWithSources` | 接口 | `Cognition` + `sources: EvidenceLink[]`。 |
| `CognitionPatch` | 接口 | 更新补丁（content/confidence/credStatus/scope/invalidAt/askedAt，均可选）。 |
| `CognitionStore` | 接口 | 认知存储契约。 |
| `SqliteCognitionStore` | 类 | `new SqliteCognitionStore(dbPath = './dla.db')`。 |

`CognitionStore` 方法：

```ts
put(input: CognitionInput): Cognition;
get(id: string): Cognition | null;
all(subjectId?: string): Cognition[];                // 按 confidence 降序
active(subjectId: string): Cognition[];              // 只取未失效（invalidAt IS NULL）——读画像常用
sourcesOf(cognitionId: string): EvidenceLink[];
update(id: string, patch: CognitionPatch): Cognition | null;
addEvidence(cognitionId: string, links: EvidenceLink[]): void;
remove(id: string): boolean;
removeBySubject(subjectId: string): number;          // consolidate 重算替换用
close(): void;
```

### 6.4 写路径（画像生成）

```ts
// 一键更新：distill → consolidate → attribute → 重建索引。宿主通常只调这一个。
updateProfile(subjectId: string, deps: UpdateProfileDeps): Promise<UpdateProfileResult>;
//   UpdateProfileDeps = { evidenceStore, eventStore, cognitionStore, retriever, llm }
//   UpdateProfileResult = { distilled, consolidated, attributed, indexed, indexError, timings }
//   UpdateProfileTimings = { distillMs, consolidateMs, attributeMs, indexMs, totalMs }

// 分步（若要自己编排）：
distill(subjectId: string, deps: DistillDeps): Promise<DistillResult>;
//   DistillDeps = { evidenceStore, eventStore, llm }；DistillResult = { event, pendingCount, llmCalls }
consolidate(subjectId: string, deps: ConsolidateDeps): Promise<ConsolidateResult>;
//   ConsolidateDeps = { eventStore, evidenceStore, cognitionStore, llm }
//   ConsolidateResult = { created, reinforced, corrected, conflicted, processedEvents, llmCalls }

// 把握度算法（自算，不采信 LLM 自报）：
computeConfidence(i: ConfidenceInputs): number;      // 0~1000
deriveCredStatus(confidence: number, contradictCount: number, contentType: ContentType): CredStatus;
//   ConfidenceInputs = { contentType, formedBy, supportCount, contradictCount }
```

导出的类型：`UpdateProfileDeps`、`UpdateProfileResult`、`UpdateProfileTimings`、`DistillDeps`、`DistillResult`、`ConsolidateDeps`、`ConsolidateResult`、`ConfidenceInputs`。

### 6.5 归因（M4）+ 带证据主动询问（M5）

```ts
// 从「现象」(state 认知) + 时间窗证据推「可解释假设」（低置信、挂证据、可推翻）。
attribute(subjectId: string, deps: AttributeDeps): Promise<AttributeResult>;
//   AttributeDeps = { evidenceStore, cognitionStore, llm }
//   AttributeResult = { hypotheses: AttributedHypothesis[], consideredPhenomena, llmCalls }
//   AttributedHypothesis = { cognition, phenomenon, basedOnEvidenceIds }

// 对低置信假设产出「该不该问 / 问什么 / 附什么证据」建议（不替宿主开口）。
proposeAsk(subjectId: string, deps: ProposeAskDeps, opts?: ProposeAskOptions): Promise<ProposeAskResult>;
//   ProposeAskDeps = { cognitionStore, evidenceStore, llm? }（llm 仅用于润色问法，可省）
//   ProposeAskOptions = { policy?: Partial<AskPolicy>, markAsked?: boolean }
//   ProposeAskResult = { proposals: AskProposal[], llmCalls }

// 把「冲突中」的认知拿出来，带正反两面证据主动问（复用 AskProposal 形态）。
revisitConflicts(subjectId: string, deps: RevisitDeps, opts?): Promise<RevisitResult>;
//   RevisitDeps = { cognitionStore, evidenceStore, llm? }；opts = { maxAsks?, markAsked? }
//   RevisitResult = { proposals: AskProposal[], llmCalls }
```

导出的类型：`AttributeDeps`、`AttributeResult`、`AttributedHypothesis`、`ProposeAskDeps`、`ProposeAskOptions`、`ProposeAskResult`、`AskProposal`、`AskPolicy`、`RevisitDeps`、`RevisitResult`。

`AskProposal` 形状：`{ cognitionId, kind: 'hypothesis' | 'conflict', hypothesis, question, evidence: {id,summary}[], contradictEvidence?, confidence, credStatus }`。

### 6.6 周期后台（阶段 4-B）

```ts
// 分型衰减（读时算、不持久化）：
decayFactor(halfLifeDays: number, ageMs: number): number;   // 0~1；半衰期≤0=不衰减返回1
halfLifeOf(contentType: ContentType): number;               // 类型对应半衰期（天）；没配=0
effectiveConfidence(cog: Pick<Cognition,'confidence'|'contentType'|'updatedAt'>, now?: Date): number;
//   有效置信 = confidence × 衰减因子（按距 updatedAt 的时间）

// 自然过期：临时类（state/hypothesis/trend）久没印证 → 标 invalidAt（保留可溯源、不删）。
expire(subjectId: string, deps: ExpireDeps, now?: Date): ExpireResult;
//   ExpireDeps = { cognitionStore }；ExpireResult = { expired: number }

// 跨会话趋势：反复出现的状态聚成持续模式认知（formed_by=ruled）。
aggregateTrends(subjectId: string, deps: AggregateTrendsDeps, now?: Date): Promise<TrendResult>;
//   AggregateTrendsDeps = { evidenceStore, cognitionStore, llm }
//   TrendResult = { trends: Cognition[], consideredCount, llmCalls }
```

导出的类型：`ExpireDeps`、`ExpireResult`、`AggregateTrendsDeps`、`TrendResult`。

### 6.7 召回 / 嵌入器 / LLM

| 导出 | 形态 | 一句话用途 |
| --- | --- | --- |
| `Retriever` | 接口 | 召回底座契约（`indexAll` / `search`）。 |
| `RetrievalHit` | 接口 | `{ id: string; score: number }`。 |
| `NullRetriever` | 类 | 空召回（降级/占位）。`new NullRetriever()`。 |
| `VectorRetriever` | 类 | `new VectorRetriever(dbPath, embedder)`。SQLite + JS 余弦向量召回；含 `close()`。 |
| `Embedder` | 接口 | `embed(texts): Promise<number[][]>`。 |
| `EmbedConfig` | 接口 | `{ baseUrl, apiKey, model }`。 |
| `OpenAICompatEmbedder` | 类 | `new OpenAICompatEmbedder(cfg)`。打 `/embeddings`。 |
| `loadEmbedConfig` | 函数 | `(): EmbedConfig \| null`。读 `MEMOWEFT_EMBED_*`（兼容 `DLA_EMBED_*`），缺配返回 `null`。 |
| `LLMClient` | 接口 | `chat(messages)` + `callCount`。 |
| `ChatMessage` | 接口 | `{ role: 'system'\|'user'\|'assistant'; content: string }`。 |
| `OpenAICompatClient` | 类 | `new OpenAICompatClient(cfg?)`。打 `/chat/completions`。 |
| `loadLLMConfig` | 函数 | `(prefix = 'LLM'): LLMConfig`。缺关键项抛错。 |
| `LLMPool` | 接口 | `for(purpose: 'chat'\|'write'): LLMClient`。 |
| `LLMPurpose` | 类型 | `'chat' \| 'write'`。 |
| `loadLLMPool` | 函数 | `(): LLMPool`。装配对话/写路径模型（写缺配回退对话）。 |

### 6.8 会话编排 / 管线 / 感知

```ts
// 读路径主入口：一轮对话。
class Conversation {
  constructor(deps: ConversationDeps);  // { store, retriever, cognitionStore, llm }
  handle(userMsg: string, opts?: PerceiveOptions): Promise<TurnOutcome>;
}
//   TurnOutcome = { reply, storedEvidence, recall: RecalledCognition[], llmCalls, error }
//   PerceiveOptions = { subjectId?, hostId?, sourceKind?, originId?, occurredAt? }

perceive(rawContent: string, opts?: PerceiveOptions): EvidenceInput;  // 把原始输入包成证据入参（默认 spoken）

class WorkingMemory {                    // 「最近 N 轮」纯内存窗口（Conversation 内部用）
  constructor(maxTurns?: number);
  push(turn: Turn): void;                // Turn = { role: 'user'|'assistant'; content: string }
  context(): Turn[];
  get size(): number;
}

// 多源观察摄入口（把外部采集器标准化好的观察落成 observed 证据）。
ingestObservations(subjectId: string, observations: Observation[], deps: IngestDeps): IngestResult;
//   IngestDeps = { evidenceStore, hostId? }；IngestResult = { stored: Evidence[], skipped: number }
//   Observation = { kind, occurredAt, content, originId?, meta?, allow*? }

activeWindowToObservation(s: ActiveWindowSample): Observation;  // 活动窗口样本 → 通用 Observation（纯函数）
//   ActiveWindowSample = { app, title, durationSec, occurredAt }
//   ActiveWindowCollector = { start(): void; stop(): void }（真采集器契约，骨架未实现）
```

导出的类型：`ConversationDeps`、`TurnOutcome`、`PerceiveOptions`、`Turn`、`Observation`、`IngestDeps`、`IngestResult`、`ActiveWindowSample`、`ActiveWindowCollector`。

> 注：`TurnOutcome.recall` 元素是 `RecalledCognition`（= `{ content, confidence, credStatus, score }`）。该类型本身未从入口导出，按结构使用即可。

### 6.9 可观测性（日志）

```ts
createRunLogger(opts: RunLoggerOptions): RunLogger;   // opts = { dir, sessionId }
class RunLogger {
  readonly file: string;
  appendTurn(rec: Partial<TurnRecord>): TurnRecord;               // 追加一轮对话内幕（jsonl）
  appendProfileUpdate(rec): ProfileUpdateRecord;                  // 追加一次「更新画像」耗时+摘要
  readRecent(n?: number): TurnRecord[];
}
```

导出的类型：`RunLogger`、`TurnRecord`、`LogRecallItem`（= 内部 `RecallItem`）、`Hypothesis`、`RunLoggerOptions`、`ProfileUpdateRecord`、`ProfileUpdateTimings`。

### 6.10 配置 / 版本

| 导出 | 形态 | 一句话用途 |
| --- | --- | --- |
| `config` | 值 (`MemoWeftConfig`) | 全局可调参数（identity、privacyMode、retrieval.topK、衰减半衰期、attribution、asking、profileUpdate 攒批等）。 |
| `cloudReadDefault` | 函数 | `(c?: MemoWeftConfig): boolean`。`allow_cloud_read` 默认值（跟随 privacyMode）。 |
| `MemoWeftConfig` | 类型 | 配置的类型。 |
| `DlaConfig` | 类型 | **@deprecated** 旧名别名 = `MemoWeftConfig`（保留兼容）。 |
| `MEMOWEFT_VERSION` | 常量 | `'0.0.0-rebuild'`。 |
| `DLA_VERSION` | 常量 | **@deprecated** = `MEMOWEFT_VERSION`（保留兼容已引用旧名的宿主）。 |

> `config` 是**共享单例**：改它会影响全部读到它的调用点（把握度算法、召回门槛、攒批阈值等）。多用户/多宿主场景，通过 `PerceiveOptions.subjectId` / `Conversation` 各传 subjectId 隔离，而非改全局 `config.identity`。

---

## 7. 宿主该负责什么（边界）

MemoWeft 只给「对用户的理解」，**不**替宿主做这些——它们是宿主的活：

- **语气 / 角色 / 人设 / UI**：库内回话用最朴素的 prompt；产品化的措辞、角色扮演、界面全归宿主。
- **是否开口问**：`proposeAsk` / `revisitConflicts` 只产出「该问什么 + 附什么证据 + 把握度」的**建议**；真正是否发问、怎么措辞由宿主决定。
- **隐私 / 安全策略**：库只提供「可切换 + 留好口」——模型口（`llmPool` chat/write 分流）、授权位（证据的 `allowLocalRead/CloudRead/Inference`、`config.privacyMode`、`observedDefaults`）。用云端还是本地、给不给上云，是宿主 + 用户的选择，库不代做。
- **采集器**：从操作系统抓窗口/设备等是**独立可选外挂**；库只定义通用摄入口 `ingestObservations`（`activeWindowToObservation` 只给标准化映射，真采集器 `ActiveWindowCollector` 是骨架）。
- **触发时机**：读路径（`handle`）每轮同步调；写路径（`updateProfile`）是重活，宿主自己决定攒批/空闲触发（可参考 `config.profileUpdate.batchSize` / `idleMinutes`），别每轮都调。

---

## 相关文档

- `AGENTS.md` — AI 工作契约（本项目面向 AI 维护）。
- `STATE.md` — 此刻状态 + 可用接口清单。
- `docs/项目地图.md` — 内部 17 格设计稿（对外读者以本文与架构文档为准）。
