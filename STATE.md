# MemoWeft · STATE（白板 · 只反映此刻 · 开工先读这个）

> 规则：本文件**极短（目标 ≤40 行）**。完工时【改写】对应行，历史【追加】到 `LOG.md`，不在此堆流水账。
> 设计细节在 `docs/项目地图.md`（**按 cell 随用随读，别通读**）；协作契约在 `AGENTS.md`。

**阶段**：阶段 0 ✅ · 1 画像+召回 ✅ · 阶段 2 纠正闭环 ✅ · 阶段 3 归因+主动询问 ✅ · **阶段 4-B 周期后台 ✅**（衰减/过期/召回门控/冲突复看/趋势）· 阶段 4-A 多源感知 · **档1 摄入口闭环 ✅** · **档2 真采集器 ✅**（Win32 前台窗口采集循环 + `npm run collector` 运行器）
**框架闭环**（总设计任务书）：**Phase 5-A 便携记忆包 ✅**（导入/导出/备份/恢复，保真）· **5-B 测试台导入导出 ✅**（`/api/export-bundle`、`/api/import-bundle?mode=dryRun|merge` + 备份/迁移面板）· **6-A 记忆管理页 ✅**（筛选/详情/改删/标失效/授权开关）· 6-B 图谱视图（**G1 后端 ✅** · G2 前端 ⬜）· **7-A Cloud Guard ✅**（trends/ask 路径补过滤）· **8-A 真采集器 ✅** · 9-A 星瑶最小宿主 · 10-A 插件契约 · 11-A 稳定性/迁移 · 12-A npm 发布
**最后更新**：2026-07-02
**模型部署口径**：文档已改为 **Cloud-first onboarding**（开发者先用 OpenAI-compatible 云端端点跑通）+ **evidence 级授权**（`allowCloudRead` 控制哪些证据可进云端 prompt）+ **Hybrid/local 作为高级选项**。详见 `docs/deployment.md`。
**写路径**：原话 → 整理事件(distill) → 画像(consolidate) → 归因(attribute) → 索引(retriever.indexAll)　**读路径**：消息 → 召回相关认知 → 注入回话

## 当前可用接口（手上有什么 · 下个任务直接用，不必读实现）

- **证据层** `src/evidence/store.ts` → `SqliteEvidenceStore(dbPath?)`
  - `put(input)→Evidence`（带 originId 幂等）｜ `get(id)` ｜ `all()` ｜ `byTimeRange(fromIso,toIso)` ｜ `update(id,{rawContent?,summary?,allowCloudRead?,allowInference?})`（6-A：授权位可改）｜ `remove(id)` ｜ `close()`
  - **隐私过滤** `src/evidence/privacy.ts` → `filterCloudReadable(items)`：写路径喂【云端】LLM 前筛掉 `allowCloudRead=false` 的证据；observed 默认不上云，作为 Cloud-guarded 的安全阀。
- **观察摄入（4-A 档1）** `src/perception/ingest.ts` → `ingestObservations(subjectId,observations,{evidenceStore,hostId?})→{stored,skipped}`：`Observation{kind,occurredAt,content,originId?,meta?,授权位?}` → `observed` 证据；授权 **显式 > `config.observedDefaults`**(local✓/cloud✗/infer✓)。骨架 `collectors/activeWindow.ts`：`activeWindowToObservation(sample)` + `ActiveWindowCollector` 契约。
- **认知层** `src/cognition/store.ts` → `SqliteCognitionStore`：`put` ｜ `get` ｜ `all(subjectId?)` ｜ `sourcesOf(id)` ｜ `update(id,patch)` ｜ `remove(id)` ｜ `removeBySubject(id)` ｜ `close()`
- **事件层** `src/event/store.ts` → `SqliteEventStore`：`put` ｜ `get` ｜ `all(subjectId?)` ｜ `evidenceOf(id)` ｜ `coveredEvidenceIds(subjectId)` ｜ `remove` ｜ `removeBySubject` ｜ `close`
- **画像生成** `src/consolidation/updateProfile.ts` → `updateProfile(subjectId,{evidenceStore,eventStore,cognitionStore,retriever,llm,transaction?})`：distill→consolidate→attribute→index；返回 `timings`，索引失败不回滚画像。
- **召回** `src/retrieval/`：`Retriever` 接口；`VectorRetriever(dbPath,embedder)` / `NullRetriever`；`OpenAICompatEmbedder` + `loadEmbedConfig()`（缺配返回 null）。召回门控：跳过失效认知 + 有效置信 < `minEffectiveConfidence`(80) 不注入。
- **归因 / 主动询问**：`attribute` 产低置信 hypothesis；`proposeAsk` 挑低置信假设；`revisitConflicts` 复看冲突认知。宿主决定是否开口和怎么问。
- **周期后台 4-B**：`effectiveConfidence`（读时衰减）、`expire`（临时类过期）、`aggregateTrends`（近 14 天 state 聚趋势）。
- **会话编排** `src/pipeline/conversation.ts` → `new Conversation({store,retriever,cognitionStore,llm,seedTurns?,systemPrompt?})`（`seedTurns`=打开旧会话把最近几轮种回窗口续聊；`systemPrompt`=宿主注入人设，缺省库内最朴素提示，cell 9 语气/角色归宿主），`handle(msg,opts)→TurnOutcome{reply,storedEvidence,recall,llmCalls,error}`。`RunLogger` 重开已存在会话 logger 时轮号接着历史往下。
- **LLM / env**：`loadLLMPool()` / `LLMPool.for('chat'|'write')`；先读 `MEMOWEFT_*`，回退 `DLA_*`。默认文档推荐云端 OpenAI-compatible；本地 Ollama/LM Studio 仍可作为 endpoint。
- **便携记忆包（Phase 5-A）** `src/portable/` → `exportBundle(subjectId,{evidenceStore,eventStore,cognitionStore},opts?)→MemoryBundle` ｜ `validateBundle(bundle)→{valid,errors,warnings}`（结构+引用完整性；致命 vs 软告警分级）｜ `importBundle(bundle,{...三 store,transaction?},{mode:'dryRun'|'merge'})→ImportPlan`。保真（保留原 id 与全部时间戳）、按 id/originId 幂等去重、非法包不写库、可选事务防污染。为此三个 store 各新增 `insert()`（按原 id 原样落库，导出的对偶）。不含向量索引（导入后 `retriever.indexAll` 重建）。
- **图谱视图（Phase 6-B · G1）** `src/graph/` → `buildMemoryGraph(subjectId,{evidenceStore,eventStore,cognitionStore},opts?)→MemoryGraphPayload{nodes,edges,stats}`。节点 subject/evidence/event/cognition；边 belongs_to_subject/distilled_into/supports/contradicts（conflicts_with/corrects 数据未存 → V1 不产，冲突/失效靠节点 credStatus/invalidAt 体现）。筛选 includeEvidence/includeInvalid/contentType/credStatus/sourceKind/onlyCloudBlocked/onlyConflicts/onlyHypotheses/q。**仅 payload；API `/api/memory-graph` + 前端力导向图（G2/G3）待接。**
- **真·活动窗口采集（8-A 档2）** `src/perception/collectors/` → `createActiveWindowCollector({sampler,onEmit,...})→{start,pause,resume,stop,tick}`（连续相同合并、≥`config.activeWindowCollector.minDurationSec` 才产出、时钟/定时器可注入）+ `sampleForegroundWindowWin32()`（PowerShell P/Invoke，双向 base64 防乱码，仅 Windows）。独立运行器 `npm run collector` → 投喂 `/api/observe-window`（observed 不上云默认不动）。
- **测试台** `testbench/`（**体验层 V3 应用壳**）：左侧栏（＋新会话 / 会话列表·点开续聊·hover 可归档 / 设置组[配置·记忆管理]+调试）+ 聊天正门（思考中动效 + 记忆气泡"记住了…"就地织进对话 + 右上「它记住我 N 件事」抽屉）+ 首启门（没配模型先向导）+ 调试区（原透视/47旋钮/证据库/整理记忆/猜测原因，平时收起）+ 备份迁移(5-B) + 记忆管理(6-A)。多会话后端：`/api/sessions`、`/api/session/open`(续聊)、`/api/session/archive`(软移除·加 .archived·数据不删)、`/api/health`。`npm run testbench` → `:7888`。

## 命令
`npm run typecheck` ｜ `npm test`（108 过）｜ `npm run build` ｜ `npm run testbench` ｜ `npm run collector`

## 进行中任务断点
（无）
