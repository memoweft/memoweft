# DLA · LOG（磁带 · 只增不改 · 平时不读，仅追溯"当初为何"）

> 开工**不读本文件**，只读 `STATE.md`。仅当需要追溯某个决定的来由时才翻这里。

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

---

## 2026-06-23 · 修 Bug B（分型置信策略 v1）

**起因（dogfood 暴露）**：用户两次说"没睡好"→ 该临时情绪涨到置信 800/"稳定"、成画像第一名，排在偏好前。正是项目最防的"随口情绪当长期画像"（cell 2 自检②）。根因：state 类无时间策略 + 置信只看支持数（重复=越稳定）。

**修法（cell 8 规则 8 落 v1）**：`computeConfidence`/`deriveCredStatus` 加 `contentType`；`config.consolidation.transientTypes=['state']` + `transientCap=300`。临时类置信封顶 300、永不进"稳定/有限"。

**效果（用户真实数据）**：「没睡好」800/稳定 → 300/低置信、垫底；偏好（剑来/喝茶 680）排到临时情绪之上，排序倒挂修正。

**诚实边界**：v1 用"封顶"近似"临时不持久"；完整版按真实时间衰减/有效期窗口，留后续。

**验证**：typecheck ✅ / 16 测试 ✅（加临时类封顶测试）/ 真模型端到端 ✅。

---

## 2026-06-23 · 修 UX 缺口（一键更新画像）

**起因**：distill 与 consolidate 两个手动按钮，dogfood 时整理了新事件却忘了重新生成画像 → 画像滞后（剑来/喝茶没进画像）。

**修法**：`src/consolidation/updateProfile.ts`（distill 未整理对话 → consolidate 全部事件）。测试台「生成画像」改成「更新画像」→ `/api/refresh`，一键搞定 + 同时刷新事件/画像面板。「整理事件」按钮保留作单独查看。

**验证**：typecheck ✅ / 17 测试 ✅ / 真模型端到端（聊"我最近在学钢琴"→一键 → 自动进第3事件 + 画像"正在学习钢琴 project/600"）✅。Bug B 仍稳（临时状态 300/低置信垫底）。

---

## 2026-06-23 · 阶段 1b 召回（代码就绪，待配嵌入端点）

**目标**：回话时召回相关认知注入，DLA 在对话里用上长期画像（不只看最近几轮）。

**做了**
- 嵌入器 `src/retrieval/embedder.ts`：`Embedder` 接口 + `OpenAICompatEmbedder`（打 /embeddings）+ `loadEmbedConfig`（读 `DLA_EMBED_*`，缺则 null）。云端优先、可替换。
- 召回 `Retriever` 泛化为 `indexAll(items)`/`search(query,topK)`；`VectorRetriever`（SQLite 存向量 + JS 余弦，**零依赖**，不上 sqlite-vec 原生扩展——避开 better-sqlite3 那种坑）；`NullRetriever` 降级。
- 接路径：`updateProfile` 重算画像后 `retriever.indexAll(认知)`；`Conversation` 回话前 `search` 相关认知 → `action.reply` 注入（带把握度/可信状态，cell 8 规则 7 透明）。`config.retrieval.topK`。
- 测试台：配了 `DLA_EMBED_*` 用 `VectorRetriever`，否则降级 `NullRetriever`；透视区 recall 显示召回认知。

**决策（确认）**：嵌入器云端（`DLA_EMBED_*`）；向量存 SQLite + JS 余弦（零依赖，规模大才换 sqlite-vec）；召回相关认知注入；v1 每轮 top-k（门控/意图补全推迟）。

**验证**：typecheck ✅ / 21 测试 ✅（VectorRetriever 余弦排序、替换式重建、降级，用 stub 嵌入器）。

**真端到端激活（本地 Ollama bge-m3）**：用户本地有 `bge-m3`（1024维，多语言/中文）。配 `.env`：`DLA_EMBED_BASE_URL=http://localhost:11434/v1` / `DLA_EMBED_MODEL=bge-m3` / `DLA_EMBED_API_KEY=ollama`（dummy）。重启 → 更新画像（索引 7 条）→ 聊"有点渴，喝点什么好呢"（没提茶）→ **召回"用户喜欢喝茶"排第一（相似度603）**，回话"你平时不是挺喜欢喝茶的嘛"——**"越用越懂"闭环成立、本地嵌入零外传**。顺带验难点 2：bge-m3 中文这例分得清。
观察：top-k=5 小画像时会带出不相关认知（相似度阈值待加，模型暂未被带偏）。

---

## 2026-06-23 · 阶段 2 · M6 纠正闭环（增量更新）

**目标**：对话里纠正 → 画像真的改（旧失效保留、新采纳）；模糊冲突先暴露。

**做了**
- consolidate 从"重算替换"改成**增量更新**：处理未消化事件(`eventStore.unconsolidated/markConsolidated`)+现有画像(`cognitionStore.active`)→ LLM 输出 `new/reinforce/correct/conflict`。
- 应用：new→新增；reinforce→`addEvidence`+置信重算升；**correct→旧标 `invalidAt`(失效保留可溯源)+新采纳**；conflict→标 `conflicted` 暴露。
- cognition 加 `active()`/`addEvidence()`，`update` 支持 `invalidAt`；event 加 `consolidated` 列(带迁移 ALTER)。
- 召回只索引 active 认知；测试台画像面板标"已失效·被纠正"。
- 决策（确认）：增量更新(真M6)；对话自动识别纠正；旧判断标失效保留。

**真模型 dogfood 抓的 bug**：小米 MiMo 正确识别了纠正，但用 `new_content` 字段名+漏 content_type/formed_by → 严格解析丢弃。修：prompt 给确切 JSON 示例 + 解析容错(`pickCognition`：content/new_content/cognition 都接，缺类型给保守默认)。

**验证**：typecheck ✅ / 22 测试 ✅（new/correct/reinforce/标已消化）/ 真模型端到端：建画像含"喜欢喝茶"→说"不喝茶改咖啡"→**纠正1，"喜欢喝茶"失效保留、"不喝茶了/喝咖啡"采纳**。
**留**：conflict 路径真模型未 dogfood；置信度粒度。

---

## 2026-06-24 · 阶段 3 · M4 归因 + M5 带证据主动询问（代码就绪 + 离线绿）

**目标**：补"主动"那一环。感知"游戏到3:30"(observed) → 用户"没睡好" → 推"可能玩太晚"(低置信假设) → 带证据问 → 用户"没有，只是挂机和女友打电话" → 否定旧假设、修正画像。

**拍板的三个 fork（出方案 → 用户确认）**
- 假设存储：cognition 表 `contentType` 加 `'hypothesis'` + 加 `asked_at` 列（**动表结构红线，用户签字**；幂等 ALTER 迁移旧库）。
- 询问时机（cell 12 开放问题）：**保守**——只问 `candidate/low`、把握度落"将信将疑"带(`100~400`)、一轮最多 1 个、问过(`askedAt`)不再问。
- 接回闭环：**轻量复用**阶段 2 的 correct/conflict（假设是 active 认知，consolidate 看到"画像里的假设 + 用户澄清事件"→correct），零新增纠正逻辑；consolidate prompt 加一句"假设被否定也归 correct"。

**做了**
- `src/attribution/attribute.ts`：现象(active `state` 认知)→时间窗证据(`byTimeRange`，回看 `windowHours=24`，`allowInference` 过滤)→LLM 产假设→落库。假设挂因果**两端**证据（现象证据+原因证据），既可溯源也作"已归因"锚（避免重复产假设）。把握度 inferred 起步、**封顶 `hypothesisCap=250`**（规则 6 低声说）。防幻觉：LLM 没引到真实候选证据 id 的假设丢弃（防自证）。
- `src/asking/proposeAsk.ts`：挑 active 假设里 `askedAt==null` + 状态可问 + 把握度在带内 → `AskProposal{question, evidence(observed 优先), confidence, credStatus}`。问法 v1 模板拼（可选 LLM 润色）；标 `askedAt` 去重。**只产建议，不替宿主开口**（cell 9）。
- `config`：加 `attribution{windowHours,hypothesisCap}` + `asking{maxAsks,confidenceBand,askableStatuses}`。
- 测试台：`/api/observe`(注入 observed) + `/api/attribute` + `/api/ask`；透视区"归因+主动询问"面板，"替宿主发问→"把问法作为 DLA 消息显示（提问不入证据库，回答走普通聊天→更新画像闭环）。

**纪律守点**：规则 4（假设 support 只挂证据、提问不入库、否定靠用户回答）；规则 6（假设低置信封顶）；规则 7（把握度随 AskProposal 透明）；cell 9（DLA 给"问什么"、宿主定表达）。

**验证**：typecheck ✅ / **27 测试 ✅**（M4 产假设/无证据不硬编/已归因不重复/防幻觉丢弃 + M5 带证据问/去重 + 接回 correct 闭环）/ build ✅ / 旧 schema 库 `asked_at` 迁移 ✅。
**留（真模型 dogfood 待验）**：①轻量复用闭环依赖 consolidate LLM 把"用户回答↔画像里假设"联系起来——孤立回答可能联系不上，dogfood 看效果，不行再上显式 `respondingTo`。②归因/问法的真模型质量。③M4 现状手动触发（测试台按钮），未自动并进 updateProfile。④置信带阈值、windowHours 等参数运行后校准。

### 同日 · 真模型 dogfood 第 1 轮 → 暴露归因膨胀，修

**dogfood 结果（验收没过）**：核心假设"玩游戏→没睡好"没生成；M4 反而扫全部 5 个 state、爆炸出 **10 条噪声假设**（"没睡好→烦""烦→渴""喝茶咖啡因利尿→渴"…），被问的还方向反了。

**三个根因**
- 时间窗没对上（录入）：observed 注入时间默认=现在，比"没睡好"晚 1 分钟，落在回看窗外。
- **dedup bug（代码）**：旧逻辑"现象证据被任一假设引用过=已归因"——但"没睡好"被当成"烦"的原因引用后，轮到它当现象时被误判已归因、永远跳过。
- 归因没边界：一次扫全部 state + LLM 乱挂 5-7 条不相关证据。

**修（用户拍板两个 fork）**
- 归因范围：**只归因最近一条未归因现象**（`maxPhenomenaPerRun=1`，按 `updatedAt` 降序取——刚抱怨的那条被 consolidate 触碰、最新）。
- 因果方向：**禁 state→state**——候选原因排除"支撑任何 state 现象的证据"，只用行为/观察类当原因；prompt 收紧（原因须客观、至多 1 条、引证 1~2）。
- dedup 改**按现象**判定（因 state 证据只出现在现象 side，"现象证据被假设引用 ⇔ 已归因"重新可靠）。
- 时间窗上界放到"此刻"，吸收"抱怨后才注入观察"的录入时差。
- 清掉测试台库里那 10 条 junk 假设（51 条溯源链），现象复位可重新归因。

**验证**：typecheck ✅ / **29 测试 ✅**（+禁 state→state 不硬编、+单现象上限）。**真模型重 dogfood 待跑**（修后第 2 轮）。

### 同日 · 真模型 dogfood 第 2 轮 → 主干跑通，再修证据过载

**结果（主干通过 🎉）**：感知 observed「游戏到3:30」→「没睡好」→ M4 产出**方向正确**的假设「可能因为昨晚游戏开到凌晨3:30，导致没睡好」（候选/置信250/已问）→ M5 带证据问 → 答「没有，只是挂机，和女友打电话」→ 画像被修：新增 fact「挂机到3:30」+ state「用户有女友」、旧 fact「玩游戏」失效，假设挂 2 条 contradict、标 `conflicted`。**"感知→归因→带证据问→否定→修画像"整条链路首次完整跑通。**

**两个发现**
- **闭环停在 conflicted 而非 correct**（假设仍活跃）。**用户判定：维持现状**——这次否定的是"玩游戏"而非"熬太晚"（人确实熬到3:30），判 conflict 合理；保留"冲突暴露不自动消解"（规则 5），反证已挂、已不被信任。只靠 prompt，不上 `respondingTo` 结构。
- **假设支撑爆炸**：把现象积累的一堆（被污染的）证据全挂上了（26岁/喝茶/剑来…+「没睡好」重复多次）。根因 = 旧代码"强制挂现象全部证据" + state 现象支撑本就被污染（事件覆盖多原话老毛病）。

**修（证据精简）**：①只喂【候选原因】给 LLM（现象写在 prompt，不再塞现象证据当噪声）；②引的原因【硬封顶】`maxCausesPerHypothesis=2`；③现象 side 只挂 **1 个锚点**（最晚那条现象证据，兼作"已归因"判定锚），不再全挂。支撑从十几条降到 ≤3 条。

**验证**：typecheck ✅ / **31 测试 ✅**（+支撑精简护栏）。配置：`attribution.maxCausesPerHypothesis=2`。
**留**：state 现象支撑被污染（置信度粒度老问题，独立于阶段3）；observed 注入无幂等（dogfood 重复注入会有两条）。

### 同日 · 打磨阶段 3（用户选"打磨"方向）

**做了**
- **M4 归因自动并进 `updateProfile`**（用户拍板"自动并进"）：点"更新画像"= distill→consolidate→**attribute**→索引。假设直接进画像；M5"是否开口"仍手动（cell 9）。成本可控：attribute 内部只挑最近一条未归因现象、无现象/无原因时不调模型。`UpdateProfileResult` 加 `attributed`。
- **观察证据注入幂等**：`/api/observe` 给 `originId='observed:<内容>:<时间>'`，同内容+时间只落一条（修 dogfood 重复注入两条）。
- 测试台：更新画像状态条显示"归因出假设N条"；归因面板标注"已自动跑、按钮=手动再触发"。

**验证**：typecheck ✅ / **32 测试 ✅**（+updateProfile 自动归因 wiring）。
**留**：问法措辞质量、参数校准、假设是否进召回索引（现在进了，低置信排序靠后）——边 dogfood 边看。

---

## 2026-06-30 · 阶段 4 整体方案 + 先清地基债（证据级引用）

**阶段 4 整体方案**（出方案，用户选"先出整体方案"再选"先清地基债"）：扩展 = 4-A 多源感知 M1（窗口/设备→观察证据）+ 4-B 周期后台（分型衰减/有效期/冲突复看/跨会话趋势）+ 地基债（证据级引用）。forks 摊在地图 cell 12 一线（衰减×confidence 关系、失效语义、多源范围、跨会话趋势、地基债时机）。详见当时方案。

**地基债 · 证据级引用（已落地）**
- **问题**：consolidate 旧逻辑事件级全包——LLM 引"支撑事件 id"→ `resolve()` 展开成该事件覆盖的【全部原话】挂上。一条认知吞下同事件无关原话（"没睡好"挂上 26岁/喝茶）→ 支撑污染、置信虚高。归因这轮被它坑过。
- **改（只动 `consolidate.ts`，不动表结构）**：①`buildMessages` 把每个新事件的【原话逐条列出、各带证据 id】喂给 LLM；②prompt 改 `support_event_ids`→`support_evidence_ids`，要求"只引真正支撑的原话、别带同事件无关的、引不出就别给"；③删 `resolve()`，support = LLM 引的原话 id ∩ 合法集合（防幻觉）；④new/reinforce/correct/conflict 四路同步证据级；⑤**没引到有效原话 → 跳过/无操作**（用户拍板"宁缺毋滥"）。
- **连带**：`ConsolidateDeps` 加 `evidenceStore`（要读原话喂 LLM）；updateProfile/测试台/测试桩同步。
- **正向副作用**：`supportCount` 变准 → `computeConfidence` 不再虚高 → 置信/状态更可信。

**验证**：typecheck ✅ / build ✅ / **34 测试 ✅**（+一事件多原话只挂被引那条·去污染、+没引到原话跳过）。**真模型 dogfood 待验**（置信普遍略降、支撑变干净）。
**留**：4-A 多源 / 4-B 周期后台未动（地基夯完再回阶段 4）。

### 同日 · 阶段4-B 起步 · 画像后台自动更新（空闲防抖）

**起因**：用户问"更新画像很慢"。实测小米 MiMo：短请求固定开销 3-5s（网络+排队+首字），回 118 字 9s，**类 consolidate 长请求（读 15 画像+6 事件、吐 635 字）85s**。结论：**慢在模型生成（输入越长、输出越多越慢），DLA 本地处理毫秒级、非瓶颈，也不是单纯网络**。一次"更新画像"串 2-3 次这种调用 → 累加几分钟。

**用户洞察 + 决策**：画像更新可放后台、不用每句都更新（正是读写解耦本意）。触发时机拍板=**聊完空闲防抖**（停手 7s 才跑）。

**做了（仅测试台层，不动 src/DLA 库）**：`runProfileUpdate` 共用锁（手动 + 后台不并发，防同一用户重复消化）；`/api/chat` 后 `scheduleBackgroundUpdate` 防抖触发；`/api/bg-status` 端点；前端 header 后台状态条 + 每 3s 轮询 + 跑完自动刷新画像面板；手动按钮改"立即更新画像"、忙时返回 busy。

**效果**：模型还是慢，但**慢在后台、不挡聊天**——你聊你的（存证据+召回回话照常快），画像停手后自己长。
**留**：DLA 库级"周期后台"（分型衰减/有效期/冲突复看/趋势）仍属阶段 4-B 正题，未做；此处只落地"自动沉淀的触发"。慢的根治仍需换快模型 / 写路径配小模型。

---

## 2026-07-01 · 阶段4-B 正题 · 分型衰减 + 自然过期（落地规则 8）

**目标**：情绪该忘的忘、项目结束降权、**明确偏好不自动失效**、不一刀切"越久越不信"。现状只有 v1 简化（临时类封顶、不按时间）→ 这次上**按真实时间**。

**fork 拍板**：①衰减【读时算、不持久化】（confidence 字段保持"证据强度"语义不动、不破坏静态算法、不动 updatedAt 衰减锚）；②失效语义 = 临时类超阈值标 invalidAt（保留可溯源）、稳定类永不自动失效（cell 12 那条开放分叉就此定下）。

**做了（新增 `src/background/`，不动表结构）**
- `decay.ts`：`decayFactor(半衰期, age)=2^(-age/半衰期)` + `effectiveConfidence(cog, now)`＝confidence×因子，**锚 = updatedAt**（多久没被印证；被 reinforce/correct 一碰就恢复新鲜）。
- `expire.ts`：`expire(subjectId, deps, now)` 把临时类里久未印证的标 invalidAt；稳定类跳过。
- `config.background`：半衰期草案 state1.5 / hypothesis2 / goal·project14 / trait60 天，fact·preference 不列=不衰减；过期 state7 / hypothesis14 天。
- 回话注入把握度改用 `effectiveConfidence`（conversation.ts）。
- 测试台：后台维护顺带跑 `expire`；画像面板显示"置信 X → Y"（Y=衰减后）；后台状态条带"过期 N"。

**验证**：typecheck ✅ / build ✅ / **37 测试 ✅**（衰减因子/有效置信分型/自然过期临时类 vs 稳定类）。
**注意**：衰减按真实天数，当场聊看不到（age≈0、因子≈1），**隔天才显现**；想当场看可注入时间戳（单测已覆盖）。

**追加 · 召回衰减门控（4-B 第①子项，已做）**：把有效置信用进召回——`conversation` 召回时**跳过失效的**（invalidAt，即便索引没重建）+ **跳过有效置信 < `config.retrieval.minEffectiveConfidence`(80) 的**（淡了的情绪/过气假设不硬塞回话）。+1 测试（38）。

**追加 · 冲突定期复看（4-B 第②子项，已做）**：`src/asking/revisitConflicts.ts`——挑 active 的 `conflicted` 认知（`askedAt==null`、限 maxAsks），**并排亮支撑/反对两面证据**问用户到底哪样（规则 5"暴露不自动消解"→主动求证），复用 M5 `AskProposal`（加 `kind:'hypothesis'|'conflict'` + `contradictEvidence`）。用户回答→走阶段 2 correct/conflict 闭环消解。测试台 `/api/ask` 现合并"假设求证 + 冲突复看"，前端按 kind 标签 + 反证展示。+1 测试（39）。

**追加 · 跨会话趋势（4-B 第③子项，已做）→ 4-B 正题全完成 🎉**：`src/background/trends.ts`。fork 拍板：①趋势存 `contentType='trend'`（动联合类型，非表结构）；②**规则筛频率 + LLM 归纳**。机制：取近 `trendWindowDays`(14) 内 state 类认知的支撑证据，**规则筛 ≥ `trendMinCount`(3) 才够"趋势"**（保证真有重复，不是一次情绪）→ LLM 归纳命名 → 落库 `formed_by='ruled'`（基于客观频率，比 inferred/特质可信，呼应难点 1"行为类用规则算准"）、挂证据、dedup（同批证据聚过不重复）。趋势半衰期 7 天、过期 30 天。挂进测试台后台维护（规则筛不够频不调模型）。+3 测试（42）。
**4-B 余下**：仅剩召回【相似度阈值】门控（独立老问题，挡"不相关"，非 4-B 正题）。
**阶段 4 余下**：4-A 多源感知（窗口/设备→观察证据）。

---

## 2026-07-01 · 隐私开关接线（让 allowCloudRead 真生效）· 4-A 前置必修

**问题（产品经理审查发现、所有者确认要改）**：证据上的"准不准上云"授权位 `allowCloudRead` 形同虚设——写路径三处把证据喂给云端模型前都不看它。当前没出事（证据全是对话 `spoken`、默认 cloud=true），但 4-A 一引入默认不上云的 `observed` 行为证据，数据会照样被送上云。是"隐私本地优先"的根基，4-A 前必修。

**方案（最小版，所有者认可）**：新增共用小函数 `filterCloudReadable`，三处"取证据→喂 LLM"之间各插一道，只留 cloud=true 的再喂。**不动** `LLMClient` 抽象、**不动**证据存储层（完整版"给 LLMClient 标 cloud/local、按 tier 决定"留给"上本地模型"任务）。

**做了**
- 新增 `src/evidence/privacy.ts` → `filterCloudReadable(items)`（泛型，只留 allowCloudRead=true）；**带前提注释**：假设 deps.llm 是云端模型，上本地模型（3090）时需改成按模型 cloud/local 决定筛不筛。
- `distillation/distill.ts`：喂 LLM 的原话改用 `filterCloudReadable(pending)`；事件 `evidenceIds` 仍用 pending（cloud=false 照算被覆盖、不每轮重捞）；全批 cloud=false 时跳过不调云端（留 `TODO(4-A)`）。
- `consolidation/consolidate.ts`：事件覆盖的原话过滤后才进 `utterances` + `validEvidence`（cloud=false 既不进 prompt、也当不了云端所生认知的支撑）。
- `attribution/attribute.ts`：候选原因链上加 `filterCloudReadable`（在已有的 allowInference / 禁 state→state 过滤之后）。

**前提/边界（别当永久死规则）**：以上全**假设三处的 deps.llm 是云端**；接本地模型时这道关要改。distill 的"全批 cloud=false 怎么走本地 / 怎么记覆盖"明确留给 4-A 折中 A/B 定。

**一个后果（4-A 要面对）**：接线后 observed（4-A 默认 cloud=false）用云端 consolidate/attribute 会被挡 → 变不成画像，除非走本地模型。这把 4-A 交接 §6 的"折中 A 还是 B"正式摆上台面——但那是 4-A 的事。

**验证**：typecheck ✅ / build ✅ / **测试 46 ✅**（42 旧全绿 = 对现有 spoken 证据零影响；+4 新护栏 `tests/privacy.test.ts`：共用函数单测 + distill/consolidate/attribute 各一例"cloud=false 不进 prompt、cloud=true 照常进"）。测试台启动冒烟 ✅（/api/bg-status 200，三处运行时 import 无误）。真模型 dogfood 落盘留所有者（当前全 cloud=true → 喂模型内容逐字不变，真模型行为必与改前一致）。

**只做这一个修复**：未碰 4-A 多源感知；未做完整版（LLMClient 标 cloud/local）。

---

## 2026-07-01 · 阶段 4-A 多源感知 · 档1（摄入口闭环 + 验证）

**目标（所有者定案）**：把"活动窗口行为"作为对话之外的第二条证据来源接进 DLA——做**通用观察摄入口** `ingestObservations`，让行为记录进证据层、并验证"行为→画像/归因"全链路。这版只做**摄入口闭环 + 验证**，真采集器**留骨架**。走**路线 A**：observed 默认不上云（隐私不破），验收用"手动勾上云"的测试数据走现有云端模型验全链路。

**已拍板（出方案 5 个确认点，所有者回 "A" + 默认采纳推荐）**
- ①幂等计数 → **选 A**：`EvidenceStore.findByOrigin` 由 private 提升为公开只读（不动 put/表结构），ingest 用它精确分流 `{stored,skipped}`。
- ②默认授权落点 → **选法 b**：`config.observedDefaults`(local:t/cloud:f/infer:t)，ingest 摄入时套用、显式传 put——**不动 put 通用默认**，故 spoken/旧 observe 行为不变。
- ③测试台旧 `/api/observe` **原样不动**（它落 cloud=true observed、撑现有验收路径），**另起** `/api/observe-window` + 新面板。
- ④`index.ts` 新增导出 ingest/activeWindow 一套；⑤observed 的 hostId 默认 `config.identity.hostId`。

**做了**
- 新增 `src/perception/ingest.ts`：`Observation`(开放 kind+meta) + `ingestObservations`→observed 证据；授权 **显式 > observedDefaults**；带 originId 幂等（命中计 skipped）。`meta` 本版仅承载、不落库（Evidence 无 meta 列，不碰表结构）。
- 新增 `src/perception/collectors/activeWindow.ts`（**骨架**）：`activeWindowToObservation(sample)` 纯映射（`{app,title,durationSec,occurredAt}`→Observation，content="在 X（标题）停留约 N 分钟"、originId 去重）+ `ActiveWindowCollector` 契约。**不引入 active-win 依赖、不实现长驻采集**。
- `config.ts` 加 `observedDefaults`；`evidence/store.ts` 公开 `findByOrigin`（+进 `EvidenceStore` 接口）；`index.ts` 导出。
- 测试台：`/api/observe-window` 端点 + index.html"①′ 注入活动窗口"面板（app/标题/时长/时间 + **"允许上云"勾选**）。

**守的雷/边界**：**distill 覆盖语义雷（交接 §9）不碰**——本版数据走路线 A（cloud=true）不触发；observed 默认 cloud=false 由上轮 `filterCloudReadable` 在写路径三处挡住，真不上云。未做"完整版"（LLMClient 标 cloud/local）。

**验证**：typecheck ✅ / build ✅ / **测试 51 ✅**（42 原 + 4 隐私 + **5 新** `tests/perception.test.ts`：observed 默认授权 local✓cloud✗infer✓ / originId 幂等计 skipped / 显式 cloud=true 被尊重 / activeWindow 映射 / 端到端时间窗可捞）。**测试台端点冒烟 ✅**：起服务 → POST `/api/observe-window`(勾上云) → 落 1 条 observed（local/cloud/infer=T/T/T、content 正确）→ 重复 skipped=1（幂等）→ 删除清场，未碰已有 dogfood 数据。
**真模型 dogfood 验收场景**（凌晨游戏 + 没睡好 → 低置信假设"可能熬夜导致"）：要起 Ollama 11435 + 云端模型，**交所有者主观验收**——面板/端点已备好，操作=注入两三条凌晨游戏活动窗口(勾上云) + 聊"没睡好/好累" → 更新画像 → 看归因/画像面板。

**只做档1**：真采集器留骨架；未碰 distill 雷；未做 LLMClient 标 cloud/local 完整版。

---

## 2026-07-01 · 治慢 + 落实三原则（4 件）

**来源**：档1 dogfood 后所有者定案——功能过关、唯一痛点=更新画像慢。放宽"碰核心先问"：coder 自定合理默认、直接做、标注释/LOG，所有者事后看。

**A 组（治慢/等）**
- **①手动"更新画像"不阻塞**：`/api/refresh` 从 `await runProfileUpdate()`（干等几十秒）改为 **fire-and-forget + 立即返回 `{started:true}`**（实测 **12ms 返回**）；前端 `genProfile` 不再等结果、靠 `pollBg` 状态条看进度、跑完自动刷新画像。忙时仍 `{busy:true}`。
- **②补落盘**（AGENTS.md"内幕必落盘"，最慢的写路径以前没日志）：`updateProfile` 返回加 `timings`（distill/consolidate/attribute/index/total ms）；`runLog` 新增 `ProfileUpdateRecord`（kind='profile_update'）+ `appendProfileUpdate`，写进同一 `run-*.jsonl`（对话轮不写 kind、更新画像写，靠 kind 分辨）；`runProfileUpdate(trigger)` 跑完落盘各步耗时 + 摘要 + trigger(manual/background)。**实测落盘**：`attributeMs:30227`——直接暴露"慢在归因 30s"，治慢诊断价值当场兑现。
- **③写路径配独立小快模型 + 为"可切换模型"架构留口**：新增 `src/llm/pool.ts` → `loadLLMPool()`/`LLMPool.for('chat'|'write')`；`loadLLMConfig(prefix)` 支持第二组 `DLA_WRITE_LLM_*`。测试台 `convo` 用 `for('chat')`（对话保大模型）、写路径（updateProfile/trends/手动 distill/consolidate/attribute/ask）用 `for('write')`。**缺 `DLA_WRITE_LLM_*` → write 回退 chat**（不强制、不崩、行为同旧，实测回退跑通）。🧭 **留口**：按"维度选 client"，档2「按证据 allowCloudRead 路由本地/云端」在此加 tier 维度即可，不重构。

**B 组（治脑补）**
- **④归因加"攒够/反复出现才推"门槛**：`config.attribution.minPhenomenonSupport=2`（可配）；`attribute` 挑现象时要求支撑证据 ≥N，**偶发一次"好累"不推因果、先攒着；反复出现（多条支撑）再解释**。默认 N=2 由 coder 定（所有者授权），dogfood 后调。连带更新受影响测试（现象改挂 ≥2 条）。

**碰核心的默认（所有者授权 coder 自定、事后看）**：②落盘=新增记录类型 `ProfileUpdateRecord`、同文件靠 kind 区分（不扩 `appendTurn`、不单独文件）；③=`LLMPool` 抽象留口（非写死俩 client）；④=N=2。均在代码注释 + 本条标清。

**验证**：typecheck ✅ / build ✅ / **测试 54 ✅**（51→54：+pool smoke、+appendProfileUpdate 落盘、+④偶发不推；attribution/cognition/privacy 里受④影响的现象改挂 ≥2 条）。**测试台端到端**：①`/api/refresh` 12ms 返回 `{started:true}`、后台 updating→跑完；②落盘 `profile_update` 记录含各步耗时（attributeMs=30227 暴露慢点）。
**诚实预期**：③本轮未配 `DLA_WRITE_LLM_*`（写路径回退大模型，故 attribute 仍 30s）——所有者配上小快模型才真变快；**小模型提炼/归因质量可能降，dogfood 后按真实表现调/换**，别预设又快又好。
**只做这 4 件**：未碰档2 按授权路由、distill 雷、画像膨胀。

---

## 2026-07-01 · 治"勤" · 更新时机改攒批（核心①）+ 三原则收口

**背景**：清单《DLA_治慢_落盘与写路径小模型》重排优先级——核心从"让更新变快"改为"**别太勤 + 别挡人 + 别脑补**"。清单五项里 ②归因门槛 / ③不阻塞 / ④落盘 / ⑤小模型 **前几轮已做完**（见上一条治慢记录）；本轮只补真正新增的 **核心①攒批触发**。

**①攒批触发（治"勤"，落实原则一/二）**
- 旧：`server.mjs` 每次聊完停手 7s（`BG_DEBOUNCE_MS`）就更新画像 → 太勤、一批跑 3~4 趟模型、又慢又费。
- 新：`scheduleBackgroundUpdate` 改成【攒批】——每次聊完累加 `pendingSinceUpdate`；**攒够 `config.profileUpdate.batchSize`(5) 条立即排更新**；**否则重置空闲计时、歇够 `idleMinutes`(30min) 没动静再更新一次**，先到先触发；更新成功清零计数；忙时占锁则 10s 后重排（不丢这批）。
- 参数落 `config.profileUpdate`（DlaConfig，可配）。**碰核心默认（所有者授权自定）**：batchSize=5 / idleMinutes=30，标在 config 注释；触发逻辑在 server.mjs。

**②③④⑤ 现状（前几轮已做，本轮未重复）**：②`attribution.minPhenomenonSupport=2`（攒够≥2 条支撑才归因，dogfood 验过"第1次不推、第2次才推"）；③`/api/refresh` 不阻塞（12ms 返回）；④`appendProfileUpdate` 各步耗时落盘；⑤`LLMPool.for('chat'|'write')` 写路径可配小模型（缺配回退大模型）。

**验证**：typecheck ✅ / build ✅ / **测试 54 ✅**（核心①在 server.mjs 不进单测；config 加 `profileUpdate` 字段类型完整）。**运行时**：起测试台聊 1 句（<5）+ 等 10s → `bg-status updating=false, pending=true`——**证明不再"停手7秒就更新"**，改为攒着等（旧逻辑此刻早在跑了）。

**顺带（本轮之前的 dogfood 工具改进，一并补记）**：测试台加了一键灌数据脚本 `testbench/seed-dogfood.ps1` + 灌数据进度条 + 聊天区实时轮询（脚本灌的对话也显示）+ **LLM 网络抖动崩溃兜底**（`process.on('unhandledRejection')` + 后台更新 try/catch，一次 `socket closed` 不再拖垮整个测试台）。

**只做核心①**：未碰档2 按授权路由、distill 雷、画像膨胀。
