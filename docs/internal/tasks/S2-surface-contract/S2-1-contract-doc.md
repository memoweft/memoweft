# S2-1 · 存疑项定级 + Surface Contract 文档

**对应五关**：给库 1.0 收口打地基，属"库对外契约纪律"，不归某一关功能。
**依赖**：无。先做。产出的定级结论供 S2-2 用。

## 背景

三路清点已把 `src/index.ts` 的 171 个导出符号分成"核心宿主接触 / 边缘内部 / 存疑"。本任务把还悬着的存疑符号回源定级，并写出 **Surface Contract 文档**（单一事实源）——宿主靠它判断"哪些能靠、哪些别碰、破了怎么办"。**纯只读源码 + 写文档，不改任何运行时代码。**

## 改哪里

### 1. 回源确认存疑符号定级（只读判定，不改代码）

- **`AskProposal` / `AskPolicy` / `proposeAsk`（`src/asking/`）**：校对已 grep `src/core/` 确认门面**不暴露**它们 → **倾向 internal**。执行时只需再确认 Host 层（`apps/memoweft-host/`、`testbench/`）有无绕过门面直用；无则定 internal。
- **`Cognition` / `Evidence` 领域形状**：回 `src/cognition/model.ts`、`src/evidence/model.ts` + `src/pipeline/conversation.ts`（recall 返回）确认——`recall` / `list*` 是否把整条 Evidence/Cognition 回吐给宿主。回吐 → 升 **stable**。
- **`Conversation` 类 / `TurnOutcome` / `RecalledCognition`**：确认门面 `handleConversationTurn` 是否内包 `Conversation`。门面已收口 → `Conversation` 类判 **internal**，但 `TurnOutcome` / `RecalledCognition` 作返回形状判 **stable**。
- **`Observation`（`src/perception/ingest.ts`）**："采集插件→Host→Core"跨层契约 → **stable**，但 `meta` 字段标 **experimental**（源码注释"本版仅承载不落库"）。
- **`EventInput` / `CognitionInput`**：确认宿主是否直接构造（一般由 distill/consolidate 内部产）→ 倾向 **experimental** 或不列入宿主主面。
- **`ManagementLogEntry`**：确认宿主是否经 `core.memory` 读审计历史。读 → **experimental**（字段弱类型，`op`/`targetKind` 现为 string）。

### 2. 写 Surface Contract 文档

新建 **`docs/memory-surface-contract.md`**（面向宿主，和 INSTALL/integration 同级）。必须逐项覆盖：

- **门面 24 个宿主接触方法专章**：`createMemoWeftCore`(1) + 门面顶层 8（`ingestUserMessage` / `ingestObservation` / `recall` / `handleConversationTurn` / `dropConversation` / `updateProfile` / `health` / `close`）+ `core.memory` **11**（`invalidateCognition` / `updateEvidenceAuthorization` / `removeEvidenceSafely` / `removeCognitionSafely` / `mergeCognition` / `archiveCognition` / `checkIntegrity` / `listEvidence` / `listCognitions` / `listEvents` / `resetSubject`）+ `core.portable` 3 + `core.graph` 1。每个方法写：入参形状、返回形状、稳定性级、隐性行为契约。
- **关键数据形状专章**（≥30 项）：三层落库 Evidence/Event/Cognition + 各 `*Input` + `MemoryBundle` + 图谱 payload + 管理 API 入出参，每项带明确 stable/experimental 标记。
- **隐性契约专章**（宿主最易踩的坑，逐条一句话写清）：
  1. `confidence` 0~1000 量纲、由 MemoWeft 自算而非 LLM 自报；
  2. 管理写操作的 `reason` 必填是隐私审计契约，不可放松为可选；
  3. observed 证据默认 `allowCloudRead=false`（隐私红线 B）；
  4. `systemPrompt` / `seedTurns` 仅首次建会话实例时生效（换需 `dropConversation`）；
  5. `effectiveConfidence` 是读时算的衍生值、不持久化；
  6. `TurnOutcome.error` 非空 = 回话降级但证据已落（先存后答）；
  7. `RemoveEvidenceResult` 里 `removed=false 且 blockers 空 = 目标不存在` 的二义；
  8. `resetSubject` v1 单人限制（`indexAll([])` 清整张 vectors 表）；
  9. **无 `.env` 也能建 core**：缺模型配置时"存证据 / 管理记忆"这类不碰模型的活仍可用，只有真调模型的读写路径才降级/报错（宿主判断"缺配时哪些能力还在"的关键承诺）；
  10. 枚举取值集合（`SourceKind`/`ContentType`/`FormedBy`/`CredStatus`/`EvidenceRelation`）——**收窄算破坏；加值不算破坏，但宿主须留 default 兜底分支**（作者拍板 ③）。
- **experimental 清单专章**：把"以后要变"的集中列出（`Observation.meta`、`Observation.kind` 开放集、`ImportMode.replace`、图谱 `conflicts_with`/`corrects` 边、`Cognition.askedAt`、`Retriever`/`Embedder`/`LLMClient` 扩展点接口、config 的单例取用方式 [作者拍板 ⑥]）。

## 不许动

- 任何 `src/**` 运行时代码（本任务纯只读源码 + 写文档）。
- 不改导出、不删符号、不重排 `index.ts` 结构（属第 10 步）。
- 不臆测：存疑项没回源确认前不下定级。

## 验收

- [ ] `docs/memory-surface-contract.md` 存在。
- [ ] "门面方法专章"覆盖全部 **24** 个宿主接触方法（`grep -c` 方法名核对：`createMemoWeftCore` + 顶层 8 + memory 11 + portable 3 + graph 1）。
- [ ] "数据形状专章"覆盖 **≥30** 项形状，每项带明确 stable/experimental 标记。
- [ ] "隐性契约专章"覆盖上列 **10** 条坑，每条一句话写清。
- [ ] 存疑符号在文档里都有落定的级 + 一句定级依据（指到源文件）。
- [ ] `npm run typecheck && npm test && npm run build` 三绿（本任务不碰代码，跑一遍确认没误碰）。

## 发现待办（顺手记，别顺手改）

- `src/core/createCore.ts:135` 与 `src/memory/managementApi.ts:142` 的 doc 注释仍写 `core.memory` 是"7 操作"，实际已是 **11**（批次 5 步 0 加了 4 个只读 list）。属陈旧注释，可在 S2-2（碰文档那步）或另立一并订正，本任务只记不改。
