# MemoWeft · STATE（白板 · 只反映此刻 · 开工先读这个）

> 规则：本文件**极短（目标 ≤40 行）**。完工时【改写】对应行，历史【追加】到 `LOG.md`，不在此堆流水账。
> 设计细节在 `docs/项目地图.md`（**按 cell 随用随读，别通读**）；协作契约在 `AGENTS.md`。

**阶段**：阶段 0 ✅ · 1 画像+召回 ✅ · 阶段 2 纠正闭环 ✅ · 阶段 3 归因+主动询问 ✅ · **阶段 4-B 周期后台 ✅（衰减/过期/召回门控/冲突复看/趋势，离线绿）** · 阶段 4-A 多源感知 · **档1 摄入口闭环 ✅**（`ingestObservations` + 活动窗口→observed 证据；真采集器留骨架）· 档2 真采集器 ⬜
**最后更新**：2026-07-01
**写路径**：原话 → 整理事件(distill) → 画像(consolidate) → 索引(retriever.indexAll)　**读路径**：消息 → 召回相关认知 → 注入回话
**嵌入器**：本地 Ollama `bge-m3`（1024维，中文好），已配 `.env` 的 `DLA_EMBED_*`（`http://localhost:11434/v1`）。换模型只改 .env。

## 当前可用接口（手上有什么 · 下个任务直接用，不必读实现）

- **证据层** `src/evidence/store.ts` → `SqliteEvidenceStore(dbPath?)`
  - `put(input)→Evidence`（带 originId 幂等）｜ `get(id)` ｜ `all()` ｜ `byTimeRange(fromIso,toIso)` ｜ `update(id,{rawContent?,summary?})` ｜ `remove(id)` ｜ `close()`
  - **隐私过滤** `src/evidence/privacy.ts` → `filterCloudReadable(items)`：写路径三处（distill/consolidate/attribute）喂【云端】LLM 前筛掉 `allowCloudRead=false` 的证据。**前提=云端模型**，上本地模型时需改（注释在案）。当前证据皆 cloud=true → 行为零变化；为 4-A 的 observed（默认不上云）兜底。
- **观察摄入（4-A 档1）** `src/perception/ingest.ts` → `ingestObservations(subjectId,observations,{evidenceStore,hostId?})→{stored,skipped}`：通用观察口（开放 `Observation{kind,occurredAt,content,originId?,meta?,授权位?}`）→ `observed` 证据；授权 **显式 > `config.observedDefaults`**(local✓/cloud✗/infer✓)，幂等用 `originId`（`EvidenceStore.findByOrigin` 已公开）。骨架 `collectors/activeWindow.ts`：`activeWindowToObservation(sample)` 纯映射 + `ActiveWindowCollector` 契约（真采集器下一版）。observed 默认 cloud=false → 经 `filterCloudReadable` 不上云；验收走"测试数据手动勾上云"（路线 A）。
- **证据类型** `src/evidence/model.ts` → `Evidence`(13 字段) ｜ `EvidenceInput` ｜ `SourceKind`=`'spoken'|'inferred'|'observed'`
- **认知层** `src/cognition/store.ts` → `SqliteCognitionStore`：`put` ｜ `get` ｜ `all(subjectId?)` ｜ `sourcesOf(id)` ｜ `update(id,patch)` ｜ `remove(id)` ｜ `removeBySubject(id)` ｜ `close()`
  - 类型 `src/cognition/model.ts`：`Cognition`(多维) ｜ `ContentType` ｜ `FormedBy` ｜ `CredStatus` ｜ `EvidenceLink`
- **画像生成** `src/consolidation/`：
  - `updateProfile(subjectId,{evidenceStore,eventStore,cognitionStore,retriever,llm})` ← **一键写路径**：distill→consolidate→**attribute(M4 归因自动并进)**→重建索引。返回含 `attributed`/`indexError`/**`timings`各步耗时**（索引失败不回滚画像）。宿主/测试台用这个，画像永不滞后。
  - `consolidate(subjectId,{eventStore,cognitionStore,llm})`（读事件）；`computeConfidence`/`deriveCredStatus(contentType...)`（**MemoWeft 自算，非 LLM 自报**；临时类封顶；参数在 `config.consolidation`）
- **事件层** `src/event/store.ts` → `SqliteEventStore`：`put` ｜ `get` ｜ `all(subjectId?)` ｜ `evidenceOf(id)` ｜ `coveredEvidenceIds(subjectId)` ｜ `remove` ｜ `removeBySubject` ｜ `close`
  - 事件化 `src/distillation/distill.ts` → `distill(subjectId,{evidenceStore,eventStore,llm})→{event,pendingCount,llmCalls}`（未整理证据→带情境事件；只总结用户话，禁止自证）
  - ⚠️ `consolidate` 现在是**增量更新**（阶段 2）：处理未消化事件 + 现有画像 → `new/reinforce/correct/conflict`。`correct`=旧失效保留(invalidAt)+新采纳；`conflict`=标记暴露。`eventStore` 加 `unconsolidated/markConsolidated`；`cognitionStore` 加 `active/addEvidence` + `update` 支持 `invalidAt`。
  - ⚠️ `consolidate` 已升【证据级引用】（地基债）：`ConsolidateDeps` 加 `evidenceStore`；LLM 看事件下逐条原话、引 `support_evidence_ids`（具体原话 id，非事件级全包）；没引到有效原话的认知【跳过】。支撑不再被污染、置信不虚高。
- **召回** `src/retrieval/`：`Retriever` 接口（`indexAll(items)`/`search(query,topK)→{id,score}[]`）；`VectorRetriever(dbPath,embedder)`（SQLite 存向量 + JS 余弦，零依赖）/ `NullRetriever`（降级）。`Embedder` 接口 + `OpenAICompatEmbedder`（`DLA_EMBED_*`）+ `loadEmbedConfig()`（缺配返回 null）。
  - 回话注入：`Conversation` 现需 `{store,retriever,cognitionStore,llm}`；search 相关认知 → 注入（带有效置信把握度）。`config.retrieval.topK=5`。**召回门控（4-B）**：跳过失效认知 + 有效置信 < `minEffectiveConfidence`(80) 的不注入。
- **归因 M4（阶段3）** `src/attribution/attribute.ts` → `attribute(subjectId,{evidenceStore,cognitionStore,llm})→{hypotheses,consideredPhenomena,llmCalls}`：现象(active `state` 认知)+时间窗证据(`byTimeRange`,回看 `config.attribution.windowHours`)→LLM 产**可解释假设**(`contentType:'hypothesis'`,inferred,封顶 `hypothesisCap`,挂因果两端证据)。手动触发(测试台 `/api/attribute`)，跟在 refresh 后。**④治脑补**：现象需 ≥`config.attribution.minPhenomenonSupport`(2) 条支撑才归因（偶发一次不推、反复才推；dogfood 后调）。
- **主动询问 M5（阶段3）** `src/asking/proposeAsk.ts` → `proposeAsk(subjectId,{cognitionStore,evidenceStore,llm?},opts?)→{proposals,llmCalls}`：挑低置信假设(`askedAt==null`+状态可问+把握度在带内,`config.asking`)→`AskProposal{cognitionId,hypothesis,question,evidence,confidence,credStatus}`；标 `askedAt` 去重。**只给"问什么"，宿主定开口/措辞**(cell 9)。回答走普通 chat→refresh，由阶段 2 correct 闭环否定假设。
- **周期后台 4-B** `src/background/`：`effectiveConfidence(cog,now?)`=confidence×衰减因子（**读时算不持久化**，锚=updatedAt，分型半衰期 `config.background.halfLifeDays`，fact/preference 不衰减）；`expire(subjectId,{cognitionStore},now?)→{expired}` 把临时类(state/hypothesis)久未印证的标 invalidAt、稳定类不失效。召回门控已用有效置信（跳过失效 + 有效置信<80）。
- **冲突复看 4-B** `src/asking/revisitConflicts.ts`：挑 active `conflicted` 认知→带【正反两面证据】的 `AskProposal`(kind='conflict')、标 askedAt 去重；`AskProposal` 加 `kind`+`contradictEvidence`；测试台 `/api/ask` 合并假设求证+冲突复看。
- **跨会话趋势 4-B** `src/background/trends.ts` → `aggregateTrends(subjectId,{evidenceStore,cognitionStore,llm},now?)→{trends,consideredCount,llmCalls}`：近 `trendWindowDays`(14) 内 state 支撑证据，规则筛 ≥`trendMinCount`(3) → LLM 归纳 → `contentType='trend'`/`formed_by='ruled'`、挂证据、dedup。趋势半衰 7 天、过期 30 天。挂进测试台后台维护。**ContentType 加 `'trend'`**。
  - **阶段 4-B 正题全完成**（衰减+过期+召回门控+冲突复看+趋势）。余：召回相似度阈值门控（独立）；**4-A 多源感知未动**。
  - cognition 加 `'hypothesis'` 类型 + `asked_at` 列（幂等迁移）；`CognitionPatch` 支持 `askedAt`。
- **会话编排** `src/pipeline/conversation.ts` → `new Conversation({store,retriever,llm})`，`handle(msg,opts)→TurnOutcome{reply,storedEvidence,recall,llmCalls,error}`
  - 配套：`perceive(raw,opts)` ｜ `WorkingMemory` ｜ `action.reply(msg,recent,llm)`
- **env 前缀（改名后双认）**：代码每个键先读 `MEMOWEFT_*` 主名、回退旧名 `DLA_*`；本机现有 `.env` 仍是 `DLA_*` 键，零改动继续可用。以下沿用 `.env` 现名书写。
- **LLM** `src/llm/client.ts` → `OpenAICompatClient`（`.env`：`DLA_LLM_*`，兼容主名 `MEMOWEFT_LLM_*`）｜ **模型池（治慢·可切换模型第一块）** `src/llm/pool.ts` → `loadLLMPool()`/`LLMPool.for('chat'|'write')`：写路径用小快模型（`.env DLA_WRITE_LLM_*`，缺则回退对话大模型、行为同旧）；档2「按 allowCloudRead 路由本地/云端」在此加 tier 维度。
- **运行日志** `src/obs/runLog.ts` → `createRunLogger({dir,sessionId})`→`appendTurn`(对话轮 `TurnRecord`) ｜ `appendProfileUpdate`(更新画像 `ProfileUpdateRecord`,kind='profile_update',含各步耗时)（落 `logs/run-*.jsonl`）
- **测试台** `testbench/`：聊天 + 透视 + 落盘 + 事件面板 + 画像面板 + 归因/主动询问面板 + 活动窗口注入(4-A) + 开发者抽屉（`npm run testbench` → :7888）
  - **画像后台自动更新**（阶段4-B 起步）：**攒批触发（治勤·核心①）：攒够 `config.profileUpdate.batchSize`(5) 条新对话 / 空闲 `idleMinutes`(30min) 才后台 `updateProfile`**（不挡聊天、别太勤，替代旧"停手7s就更新"）；`/api/bg-status` 轮询、header 状态条、跑完自动刷新画像。手动"立即更新画像"按钮保留（**治慢①：触发后台即返回、不阻塞**，靠状态条看进度；与后台共用锁，忙时 busy）。
  - **更新画像治慢（2026-07-01）**：①手动更新不阻塞（`/api/refresh` 触发后台即返回、靠状态条）；②落盘 `profile_update` 记录含各步耗时（实测暴露 attributeMs~30s，慢在归因）；③写路径可配独立小模型（`DLA_WRITE_LLM_*`，缺则回退大模型）。根治=配上小快模型（模型生成慢，非代码 bug）。

## 命令
`npm run typecheck` ｜ `npm test`（仅 tests/，66 过）｜ `npm run build`（出 dist/）｜ `npm run testbench`
> ⚠️ 嵌入器：MemoWeft 专用 Ollama 跑在 **11435**（避开 codex 占用的 11434）；`.env` 的 `DLA_EMBED_BASE_URL=http://localhost:11435/v1`。起服务前先 `OLLAMA_HOST=127.0.0.1:11435 ollama serve`。

## 进行中任务断点
（无）

## 待办（dogfood 暴露 + 计划）
- ~~Bug B 临时状态~~ **v1 已修**：state 类置信封顶 300、永不"稳定"（`config.consolidation.transientTypes/transientCap`）。**完整版**（按真实时间衰减/有效期窗口）留后续。
- ~~**置信度粒度**：1 个事件覆盖多条原话 → 每条认知吃下全部原话当支持，置信偏高~~ **已修**（证据级引用：consolidate 让 LLM 引具体原话 id，只挂被引那几条）。
- ~~UX 缺口~~ **已修**：「更新画像」按钮 = `updateProfile`（自动先 distill 再 consolidate），画像永不滞后。「整理事件」按钮保留作单独查看。
- **consolidate 慢**：真模型 ~47s（小米 MiMo 这 prompt 慢），非代码 bug。
- **召回相似度阈值**：top-k 会把不相关的也注入（小画像时尤其）。加阈值/门控更干净（已观察到，模型暂未被带偏）。
- 召回精化（推迟）：门控（该不该召回）、意图补全（用窗口补全当前句再召回）。
- ~~**阶段 3**：M4 归因 + M5 带证据主动询问~~ **主干真模型跑通**（dogfood 2 轮）。已定：归因只取最近 1 现象、禁 state→state、支撑 ≤2 原因+1 锚点；闭环明确否定时 conflict/correct 由 LLM 判（维持）；M4 已自动并进 updateProfile、observed 注入已幂等。**留**：①state 现象支撑被污染（置信度粒度老问题，独立于阶段3）；②问法措辞质量；③参数运行后校准。
- 冲突(conflict)路径真模型还没 dogfood 过（纠正 correct 已验）；置信度粒度；consolidate 慢 ~47s。
- 也召回相关证据/事件（阶段 3 归因现用纯时间窗粗筛；语义相关性召回可叠加，推迟）。
