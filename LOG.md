# DLA · LOG（磁带 · 只增不改 · 平时不读，仅追溯"当初为何"）

> 开工**不读本文件**，只读 `STATE.md`。仅当需要追溯某个决定的来由时才翻这里。

---

## 2026-07-02 · Phase 5-B 测试台导入导出（备份/迁移入口）

**起因**
- 5-A 的便携记忆包只有库层函数，用户点不到。5-B 把它接成测试台的 API + 按钮，让"导出备份 / 导入迁移"能真用、能 dogfood。

**做了什么**
- `testbench/server.mjs`（纯接线，不碰 `src/`）：
  - `GET /api/export-bundle?subjectId=` → `exportBundle(...)` → `{ bundle }`（前端 Blob 下载成 `.bundle.json`）。
  - `POST /api/import-bundle?mode=dryRun|merge` → `importBundle(...)` → `{ plan, needsReindex? }`。mode 缺省 `dryRun`（安全）；merge 走 `transaction` 原子化；非法包由 `importBundle` 内部拦下、不写库。
- `testbench/index.html`：设置面板加「备份/迁移 · 便携记忆包」区——「导出记忆包」下载 `memoweft-<subjectId>-<日期>.bundle.json`；「导入记忆包」选 JSON → 先 dryRun 展示计划（合法性/将写入/重复跳过/错误/警告）→ **仅合法才给「确认合并导入」按钮**、非法显眼报错不给合并 → merge 后刷新各面板 + 提示重建召回。

**决策/取舍**
- 导入默认 dryRun：先让用户看清"会写多少、重复多少、有没有错"再决定 merge，避免糊里糊涂灌库。
- 向量索引不入包 → merge 回 `needsReindex`，前端提示点「更新画像」重建（不自动重建：可能没配嵌入器）。

**验证**：typecheck ✅ / test **87 过**（后端纯接线未加单测；导入导出逻辑已由 5-A 的 16 个测试覆盖）/ build ✅。分支 `feat/testbench-bundle-io`（基于 5-A）。前端真机点击待 dogfood。（本阶段由后台 Agent 实现，主控 AI 审后端接线 + 前端辅助函数存在性、补 docs-sync 与提交。）

---

## 2026-07-02 · Phase 5-A 便携记忆包（导入/导出/备份/恢复）

**起因**
- 总设计任务书把「可迁移」列为框架闭环第一优先：没有导入导出，用户记忆只是当前数据库里的数据，不是能搬家的资产。先让它能搬家，再让它变漂亮（管理页/图谱靠后）。

**做了什么**
- 新增 `src/portable/`：`model.ts`（`MemoryBundle` / `ImportPlan` 类型）、`exportBundle.ts`、`validateBundle.ts`、`importBundle.ts`、`index.ts`。
- Bundle = 某 subject 的三层数据（evidence/events/cognitions）+ 两张溯源关系（event_evidence / cognition_evidence）+ 格式/版本/计数。**不含**向量索引（派生物，导入后 `retriever.indexAll` 重建）、logs、`.env`。
- 三个 store 各加 `insert()`：按【原 id 与全部时间戳】原样落库（`put()` 的保真对偶）。**不改表结构，仅加方法**。
- `src/version.ts` 抽出 `MEMOWEFT_VERSION` 单一真源（`index` 与 `portable` 共用，避免循环依赖）；`src/index.ts` 改为 re-export，公共 API 只增不改。

**定下的决策（作者拍板）**
- 保真度 = 保留原 id + 全部时间戳（含 `invalidAt`/`askedAt`/`createdAt`），而非 merge-remap。→ 因此需要 `store.insert`。
- 导入模式 V1 = `dryRun` + `merge`（按 id/originId 去重）；`replace` 留 V2。
- 导入的 event 一律标 `consolidated=true`：派生 cognition 已随包带入，防下一轮 `updateProfile` 重复消化（代价：源包里本未消化的事件导入后不再消化——V1 可接受）。
- 引用完整性优先：`originId` 跨血缘撞车时，跳过该证据 + 丢弃指向它的 join 行 + 告警，**绝不写出悬空引用**。
- 认知层红线未破：导入/导出是数据搬运，不产新判断、不自动消解冲突、不删历史（invalid 认知如实保留）。

**对抗式审查加固（同日）**：独立 Agent 读全部实现 + 真库脚本验证，挖出并修掉 4 个真缺陷——① 悬空 `correctsEvidenceId` 落库前置空；② `validateBundle` 补元素级 id + 包内重复 id 校验（防 `Set(undefined)` 蒙混放行非法包 / merge 撞主键）；③ merge 写入 try/catch 收异常，不把裸错抛给调用方；④ `consolidated` 改为随包 `unconsolidatedEventIds` 保真（防"源包未消化事件导入后漏消化"）。

**验证**：typecheck ✅ / test **87 过**（+16）/ build ✅（`dist/portable` 产物）。分支 `feat/portable-bundle`。测试台按钮/API（Phase 5-B）与前端未接，属下一步。

---

## 2026-07-02 · Phase 6-B G1 图谱 payload 后端

**起因**
- 总设计任务书 Phase 6-B「图谱化记忆视图」。先做后端 payload（G1），前端力导向图（G2/G3）后接——先让"看关系/看来源/看冲突"有据可依，后端统一产出、前端不直接读库拼图。

**做了什么**
- 新增 `src/graph/`：`model.ts`（节点/边/payload 类型）、`buildMemoryGraph.ts`（三层数据 + 溯源 → `{nodes,edges,stats}`）、`index.ts`。
- 边严格按【库里存了的】来：`belongs_to_subject`（subject→cognition）、`distilled_into`（evidence→event，源自 event_evidence）、`supports`/`contradicts`（evidence→cognition，源自 cognition_evidence.relation）。事件与认知不直接连，只经共享证据间接（真数据结构）。
- 筛选：`includeEvidence`（默认展开，可关成高层视图防毛线球）、`includeInvalid`、`contentType`/`credStatus`/`sourceKind`、`onlyCloudBlocked`/`onlyConflicts`/`onlyHypotheses`、`q` 关键词。渲染提示 `val`（认知按 confidence/150）+ `colorKey`。

**定下的边界（诚实）**
- `conflicts_with` / `corrects`（认知↔认知）当前**数据没存**——cognition 表无"和谁冲突/被谁纠正"字段，只有 `credStatus='conflicted'` 和 `invalidAt`。故 V1 **不生成**这两种边（枚举保留待数据模型补）；冲突/失效靠节点属性体现。
- 真 `credStatus` = candidate/low/limited/stable/conflicted（早先那份图谱参考文档写的 low/medium/high 是错的，已纠正）。

**验证**：typecheck ✅ / test **77 过**（+6，rebase 到含 5-A/5-B 的 main 后合计 93）/ build ✅。分支 `feat/graph-payload`。API `/api/memory-graph` + 前端力导向图属 G2/G3，未做。

---

## 2026-07-02 · 文档口径改为 Cloud-first，但不无脑上云

**起因**
- 讨论到如果面向用户 / 其他开发者，默认接入云端 OpenAI-compatible 模型更省事；如果继续把本地模型当主路径，会抬高试用门槛。
- 同时不能把“云端模型省事”误写成“所有原始证据都默认发云端”，尤其是桌面、设备、剪贴板、屏幕、健康类 observed 数据。

**做了什么**
- 新增 `docs/deployment.md`，明确三种部署模式：Cloud-first / Cloud-guarded / Hybrid-local-sensitive。
- 改 `README.md` 与 `README.zh-CN.md`：新增“云端优先，但不是无脑上云”章节，把云端作为默认接入路径，把 `allowCloudRead` 作为安全阀。
- 改 `docs/INSTALL.md`：把最小配置改成云端优先；本地 / 混合作为高级配置。
- 改 `docs/integration.md`：统一 Node ≥24、源码阶段接入方式、Cloud-first 接入口径。

**定下的决策**
- 默认 onboarding：云端 OpenAI-compatible endpoint，先让开发者快速跑通。
- evidence 级授权仍是红线：云端 LLM 调用前必须尊重 `allowCloudRead`。
- observed 行为数据默认保守：桌面窗口 / 设备 / 屏幕 / 剪贴板 / 文件 / 健康数据默认不应上云，除非宿主显式征得用户同意。
- MemoWeft 不替宿主做隐私合规；它只提供授权位、过滤机制和模型切换能力。

**验证**
- 文档改动，无代码改动；未跑 typecheck/test/build。

---

## 2026-06-23 · 阶段 0 地基完成

**做了什么**
- 重构仓 `DLA_rebuild/` 从零起；旧机制冻结进 `reference/migrated-baseline/`（只读参考，不在其上改）。旧 `../DLA_project` 也原样保留。
- 修：包构建（dev 用 Node 原生 TS + `build` 出 `dist/`，TS 5.7 `rewriteRelativeImportExtensions`）、日志（`runLog` 落盘）、测试目录（仅扫 tests/）、测试台骨架。
- 阶段 0 实现：证据层（`evidence` 13 字段）+ 存储/召回接口（`NullRetriever`）+ 对话源 `perceive` + 回话编排 `Conversation`（带最近几轮窗口）。
- 加 `store.update/remove`（用户主动改/删，cell 8 规则 10 + cell 6 条件性真删；非系统自动删）。

**定下的决策（细节见地图对应 cell）**
- evidence schema 13 字段定版（来源强度 / 双时态 occurred+recorded / 授权位 / 幂等 origin / 纠正指向）。
- `summary` v1 = 原文，阶段 1 再 LLM 抽。
- 回话带"最近几轮"上下文。
- `allow_cloud_read` 默认**跟随 `privacyMode` 配置**。
- 底料：**严格参考 Mem0/Graphiti 自研 + 接口隔离**，不拿 Mem0 作基座。
- 依赖取向：**能参考借鉴就用，不盲目造轮子**；核心自有、重依赖慎入。
- 助手回话**不落证据**（禁止系统自证）。

**验证**：typecheck ✅ / 测试 8/8 ✅ / build ✅ / 真模型端到端 ✅ / 禁止自证 ✅。

**当时为何重规划**：旧 25 条决策重心错了（全在纠结召回怎么找相关）；v3 把"记≠信"压实成贯穿数据结构的纪律，推翻向量禁令 / topic / 单一权重 / State-Profile 双层。v3 本身也只是方向草稿，非定死。

---

## 2026-06-23 · 加开发期省 token 框架

- 起因：项目地图.md ~600 行，每次开工通读最烧 token；旧项目的白板/磁带纪律重构后没补。
- 加 `STATE.md`（白板·此刻状态+可用接口+下一步，开工先读）+ `LOG.md`（本磁带）。
- 加横切 skill `context-economy`：开工读取顺序（STATE→AGENTS→按 cell Grep 地图→代码靠接口签名），列出烧 token 坏习惯（通读/重读/整文件找符号）。
- 改 `AGENTS.md`（文档分层 + 工作流加横切）、`task-planning`（别通读改 Grep 定位 cell）、`docs-sync`（先改写 STATE + 追加 LOG，决策变了才改地图）、地图 cell 16 + 顶部省 token 指引。
- 文档分层定案：状态在白板、设计在地图、历史在磁带，各取所需互不灌入。

---

## 2026-06-23 · 收尾测试台开发者抽屉

- 补 `SqliteEvidenceStore.update/remove` 实现（接口先加了实现没跟上，typecheck 抓到）。
- testbench 加 `/api/evidence/update`、`/api/evidence/delete` 端点；`index.html` 折叠抽屉做成真面板：证据列表 + 原始 JSON 展开 + 改 summary + 删（用户主动真删）。
- 加 store update/remove 单测。验证：typecheck ✅ / 9 测试 ✅ / 端点冒烟（存→查→改→删）✅。

---

## 2026-06-23 · 阶段 1a 画像完成

**做了什么**
- 认知层 `src/cognition/{model,store}.ts`：cognition + cognition_evidence 两表（判断层，与 evidence 原料层分开 = 记≠改画像）。多维：content_type / formed_by / confidence / cred_status / scope / valid-invalid_at + 溯源链。用户可查改删。
- 把握度 `src/consolidation/confidence.ts`：**DLA 自算**（formedBy 起步分[推测最低] + 支持加分 - 反对扣分），cred_status 阈值映射；有反对证据→conflicted。参数在 `config.consolidation`。
- 画像生成 `src/consolidation/consolidate.ts`：读证据→LLM 提候选→**DLA 自算把握度（忽略 LLM 自报）**→重算替换写库（merge 留阶段 2）。推测类低置信、冲突仅标记不消解。参考 Mem0/Graphiti 抽取逻辑。
- 测试台：`/api/consolidate` + `/api/cognition`(+update/delete) 端点；index.html 加「用户画像」面板（生成按钮 + 认知列表 + 改删）。

**决策（已确认）**：cognition 6 维 schema 定版；手动按钮触发；先 1a 后 1b；授权位归 evidence 不进 cognition；一张表+溯源链不拆实体/边。

**验证**：typecheck ✅ / 13 测试 ✅（含 consolidate 用 stub LLM 验证不采信自报 999、重算替换、无证据不调模型）/ 真模型端到端（聊2句→生成2条合理认知，置信600 DLA 自算）✅。

---

## 2026-06-23 · 加事件化层 + 修 Bug A（来源强度）

**起因（dogfood 暴露）**：阶段 1a 每句直接当证据、consolidate 读孤立原话 → "比较烦"丢上下文；且 LLM 把推测的"单身"误标成"亲口"，把来源强度架空。

**补：事件化层（原话→事件→画像）**
- 在 evidence 与 cognition 之间插 event 层：`src/event/{model,store}.ts`（event + event_evidence 两表）+ `src/distillation/distill.ts`（未整理证据→LLM 总结成带情境事件；只总结用户话，禁止自证）。
- `consolidate` 改成**读事件**（引用事件 id），溯源解析回原话证据。证据/认知表不动。
- 决策（确认）：event schema = id/subject_id/summary/occurred_at + event_evidence；手动「整理事件」按钮触发（自动滑出沉淀 D-024 留后面）；流程 原话→事件→画像。
- 测试台加 `/api/distill`、`/api/event` + 事件面板。
- 真模型验证：你的 4 句 dogfood → 1 个事件（"用户26岁，问怎么找女朋友，反映没睡好且烦"），溯源到 4 条原话。

**修 Bug A：来源强度**
- consolidate prompt 把 stated/inferred 卡死 + 给"单身=inferred"反例。
- 效果：「单身」从 fact/亲口/720 → fact/**推测/320/低置信**；亲口说的仍 720。来源强度生效。

**验证**：typecheck ✅ / 15 测试 ✅（加 event store + distill；consolidate 改读事件）/ 真模型端到端 ✅。

**留下的（待办，见 STATE）**：Bug B 临时状态无时间策略（没睡好/烦 still 720）；置信度粒度（1 事件覆盖多原话 → 支持数虚高 720 一刀切）；consolidate 慢 ~47s。
