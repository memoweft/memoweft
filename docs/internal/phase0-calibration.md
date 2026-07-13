# Phase 0.2 校准报告(代码为准 · 铁律 7)

> 生成于 2026-07-10,Phase 0(tag `phase-0-start` @ `5a66dcb`)。三路只读 scout 核实,结论均带 `文件:行号`。
> 本报告是 Phase 1(召回)与 Phase 3(适配器)的事实地基。**与 PROJECT_PLAN.md 描述不符处以本报告为准**。

## A. 置信度与衰减(实际数值)

**结论:PROJECT_PLAN 的两条精神在代码里都成立** —— 置信度只由规则算(不采信 LLM 自报)、分类型衰减。数值如下:

| 项 | 实际值 | 出处 |
|---|---|---|
| 底分(按 **FormedBy**,非来源类型) | stated=600 / observed=350 / ruled=450 / inferred=200 | `src/config.ts:104`; `src/consolidation/confidence.ts:27` |
| 佐证加分 | `clamp(supportCount-1, 0..5) × 40`,首条不加,最多 +200 | `confidence.ts:28`; `config.ts:105-106` |
| 反证扣分 | `contradictCount × 120` | `confidence.ts:29`; `config.ts:107` |
| 最终裁剪 | `max(50, min(1000, round(base+support-penalty)))` 恒在 [50,1000] | `confidence.ts:30`; `config.ts:108` |
| 临时类封顶 | `transientTypes=['state']` → `min(result, 300)` | `confidence.ts:20-22,32`; `config.ts:110-111` |
| CredStatus 取值 | `candidate / low / limited / stable / conflicted` | `src/cognition/model.ts:29` |
| 状态阈值 | 反证>0→conflicted;state:≥300 low 否则 candidate;非临时:≥750 stable / ≥500 limited / ≥300 low / 否则 candidate | `confidence.ts:43-52`; `config.ts:109` |
| 半衰期(天) | state=1.5 / hypothesis=2 / trend=7 / goal=14 / project=14 / trait=60;**fact、preference 不衰减** | `config.ts:131`; `src/background/decay.ts:23-25` |
| 衰减公式 | `2^(-ageDays/halfLife)`,读时算、不持久化 | `decay.ts:13-20` |
| 有效置信门控 | 召回处 `effectiveConfidence < 80` 跳过(`minEffectiveConfidence=80`) | `decay.ts:27-38`; `config.ts:102`; `src/retrieval/recall.ts:56` |
| 过期(独立机制) | `invalidAt` 由 `expireAfterDays`: state7 / hypothesis14 / trend30 | `config.ts:133`; `src/background/expire.ts` |
| 归因防伪 | LLM 引用证据 id 先按真实 `candidateIds` 白名单过滤(防编造/自证),封顶条数;`hypothesisCap=250` | `src/attribution/attribute.ts:189-203`; `config.ts:115` |

**认知纪律核对(铁律 3)**:3b(置信度只由规则算)✓ `ConfidenceInputs` 只含 contentType/formedBy/support/contradict 计数,无分数入参;3d(证据 ID 白名单)✓ 见 attribute 的 candidateIds 过滤。所有落库路径(consolidate/attribute/trends/managementApi)都用 `computeConfidence` 重算。

> **已知定义局限(2026-07-13 补,D-0019):ContentType 缺「事件」型。** `fact` = 确定 + 永久不衰减 + 不封顶;`state` = 临时 + 半衰期 1.5 天/7 天过期 + 置信封顶 300 + credStatus 只 {candidate,low}(state 的封顶是为**情绪**的记≠信设计)。**一次性已完成事件**(如"今天没吃早饭/周六加班/删了聊天记录")是「确定发生 + 无需长留」——标 fact 会永久污染画像,标 state 会给确定事件上"低置信",两个格子各对一半。故固化评测 no-over-inference 盘的 `created类型⊆{types}` 不符(模型多标 fact、语料期望 state,及 CC-029→goal / CC-032→preference)是**定义灰区噪声,非过度推断**(该盘 `overInferRate=0.00`,真靶心达标)。正解是将来加 `event` 型(不封顶、中等衰减),现按 D-0019 记档不改。

### 文档需修正
- ❌ 底分**不是**按 `user/tool/observed` 来源类型,而是按 **FormedBy**(stated/observed/ruled/inferred)。
- ❌ CredStatus **不是** `confirmed/tentative/expired`,而是 `candidate/low/limited/stable/conflicted`;"过期"是独立的 `invalidAt` 机制,不是一种 cred 状态。

## B. 读/写路径与现有检索(Phase 1 基线)

### 写路径落库与事务(实际)
`perceive`(纯函数不落库)→ `distill`(LLM→`eventStore.put` 写 event+N 条 event_evidence)→ `consolidate`(LLM→写 cognition + `event.markConsolidated`)→ `attribute` → `indexAll`。

- **只有 consolidate 那一段走事务**(`runTx`,认知写+markConsolidated 原子;崩则整段回滚)。`evidence.put`、`distill` 的 `event.put`、`attribute` 均**无事务**;`indexAll` 刻意放事务外。出处 `src/consolidation/consolidate.ts:211-294`、`src/distillation/distill.ts:87-92`、`src/consolidation/updateProfile.ts:62-99`。
- 四个 store(evidence/event/cognition/managementLog)共用一条 `DatabaseSync` 连接才能跨表原子;transaction 可重入。`src/store/openStores.ts:41-94`。

### 现有检索(§14 基线,已精确核实)
- **向量存储**:独立表 `vectors(id TEXT PK, hash TEXT, vec TEXT)`,**向量以 JSON 字符串**存 `vec` 列(非 BLOB/紧凑 float),`hash=sha256(text)` 作内容指纹。`src/retrieval/vectorRetriever.ts:18,93-97`。
- **相似度**:`SELECT id,vec FROM vectors` 读**全表** → 逐行 `JSON.parse` + 手写 JS `cosine` → sort → slice(topK)。**无 ANN、无 SQL 侧向量运算**。`vectorRetriever.ts:106-116,25-39`。
- **索引更新**:`indexAll` **内部已是 sha256 增量 diff**——只对新增/变更条目调 `embedder.embed`,删除消失条目,嵌入调用量 O(Δ);仅旧库缺 hash 列时才 DROP 全量重嵌。触发点仅 `updateProfile`(用全部 active 认知调 indexAll,事务外,失败仅记 indexError)与 `resetSubject`(清空)。`vectorRetriever.ts:69-104`。
- vectors 表由 VectorRetriever 自建、走**独立第二连接**、**不纳入 `runMigrations` 版本化**(自带 DROP-重建)。缺嵌入配置 → 降级 **`KeywordRetriever`**(FTS5 关键词兜底,**D-0017**;FTS5 不可用再降 `NullRetriever` search 返回 [])。`src/core/createCore.ts`;`src/retrieval/nullRetriever.ts`。
- **召回门控顺序**:`search(query,topK)` → `minSimilarity` → `get(id)` → 跳过 invalidAt → 跳过 archivedAt → `subjectId` 硬过滤 → 衰减门控。`src/retrieval/recall.ts:36-59`。

### 文档需修正 —— ⚠️ 直接改写 Phase 1 打法
- ❗ **「全量重建索引」不准确**:嵌入侧**已是增量**(sha256 diff)。真正的性能瓶颈在**读侧**:每次查询 O(N) 读全表 + JSON.parse + JS 余弦。→ **Phase 1 §14.5「增量索引」大部分已有;优化重心应放检索侧**(BM25/FTS 关键词通道 + RRF + 向量侧 ANN/sqlite-vec),而非重建侧。
- ❗ 向量是 **JSON 文本**且在**迁移体系外**:Phase 1 若改向量存储/schema,需单独处理 vectors 表的 DROP-重建路径,不能靠 runMigrations 统一收口。
- ⚠️ 写路径**并非整体单一事务**(只有 consolidate 那段是):任何"写路径整体原子"的假设要按实收窄。

### FTS5 / trigram 补充(Phase 0.1 实测)
`node:sqlite` FTS5+trigram 可用(node v24)。**中文 trigram 有 ≥3 字符阈值**:`饮食`(2字)MATCH→0 行,`饮食限`(3字)→命中。→ hybrid 里 2 字中文词须靠向量通道兜底;黄金集中文组按此设计。降级链(better-sqlite3→纯 TS BM25)本轮未触发。

## C. 适配器实际调用的公共 API(Phase 3 输入)

两个适配器都**只从 `memoweft` 包根(barrel)** import,peerDep `^0.5.0`,且只用门面 `MemoWeftCore` 上极少数方法。

**适配器依赖的核心公共 API 最小集(= §13 API 快照必须冻结的重点、§16 adapter-kit 的接触面):**

| 符号 | 类型 | mcp-server | adapter-ai-sdk |
|---|---|---|---|
| `createMemoWeftCore` | 值(工厂) | ✓ `server.ts:22-32` | (仅示例) |
| `core.recall(RecallInput)` | 异步 | ✓ `tools.ts:68` | ✓ `recallMiddleware.ts:119` |
| `core.ingestUserMessage(UserMessageInput)` | 异步 | ✓ `tools.ts:179` | ✓ `persistOnEnd.ts:44` |
| `core.memory.listCognitions/listEvidence/listEvents` | **同步** | ✓ `tools.ts:96,114,132` | — |
| `core.graph.buildMemoryGraph` | 同步 | ✓ `tools.ts:155` | — |
| 类型 `MemoWeftCore` / `RecalledCognition` | 类型 | ✓ | ✓(`Pick<MemoWeftCore,'recall'|'ingestUserMessage'>`) |

未被任一适配器使用:`handleConversationTurn / ingestObservation / updateProfile / portable / health / usage / close`。

- **注入格式(AD-4 输入)**:`buildKnowledgeBlock` → `- {content} (confidence {confidence}/1000, {credStatus})`,低置信明确标 "only guesses—do not treat them as established facts",作为一个 text part 前插到最后一条 user 消息前。文案逐字对齐 Core `src/pipeline/action.ts:18-41`。→ **§16.4 注入格式快照应同时锁 Core action.ts 与适配器 buildKnowledgeBlock 两处**(它们必须一致)。
- **降级语义(AD-6 输入)**:recall 抛错→`return params`(静默不注入);persist 抛错→`onError` 或静默吞;空 query/空消息直接透传。**无适配器层显式超时**(依赖底层)。`recallMiddleware.ts:115-127`;`persistOnEnd.ts:90-103`。

### 文档需修正
- ❌ mcp-server 的 `tools.ts:12` 注释与 README 称"工具层用 `core.health()` 做降级提示",**但实现里没有任何 `core.health()` 调用**,6 个 handler 也无 try/catch;降级实际发生在 Core 内部。→ **Phase 3 adapter-kit 不要假设 health 探针**;这条注释/文档要在 Phase 3 或 Phase 5 修正。
- ⚠️ `memory.list*` / `graph` 是**同步**方法,`recall`/`ingestUserMessage` 是**异步** —— API 快照与 adapter-kit 封装需区分签名。

## 对后续 Phase 的净影响(给 Integrator 的备忘)

1. **Phase 1 重心 = 检索读侧**(加 FTS/BM25 关键词通道 + RRF + 向量侧 ANN),而非"增量索引"(已基本有)。基线报告要单独量出 search 全表扫描的 P50/P95。
2. **Phase 1 向量层改动**要处理 vectors 表在迁移体系外这一事实(D-xxxx)。
3. **Phase 3** 注入格式快照锁两处(Core action.ts + 适配器 buildKnowledgeBlock);修正 mcp-server 的 health 注释;adapter-kit 覆盖同步/异步两类签名。
4. **术语对齐**:后续所有文档用 FormedBy(stated/observed/ruled/inferred)与 CredStatus(candidate/low/limited/stable/conflicted),不用文档里的 user/tool 与 confirmed/tentative。
