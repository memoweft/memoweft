# 第 6 步 · 本地模型档 2（cloud/local tier 路由）· 任务书**草稿**

> **状态：施工完成 ✅（2026-07-05·分支 `step6/local-model-tier`）——T1–T6 六卡全落地、逐卡三绿 + 提交；根 209/209 + Host 32/32 + lint 0 错 + 零依赖；红线自证（`confidence.ts` / `cognition/*` 零改动，`consolidate` 只改过滤不改判定）。preview 真起验向导渲染 + gen-env 端到端。待作者亲核 diff → 合 main（推 main 前 PM 亲核）。范围与拍板 D1–D8、4 路对抗校对纪要见下。**
> 依据：`后续批次总纲.md` 第 6 步（第 25–26 行）+ 五路只读现状勘察 + 4 路对抗校对（2026-07-05，全带 file:line，关键断言已主线亲验）。体例照 `tasks/0.4.0/`。
> 执行者：任何 AI 会话。开工前必读 `AGENTS.md`，然后只读本目录里自己领的那份分卡 + 它点名的源码文件。

## 批次目标

让 **observed（行为观察）证据能被本地模型消化成画像**，把"行为观察"卖点从"只有手动授权上云才真跑通"补成**真闭环**（总纲完成标志）。落成三件事：

1. **私密支（Core）**：给模型加 `cloud`/`local` tier；写路径的隐私关从"假设喂云端、按 `allowCloudRead` 筛"升级为**按当前写模型的 tier 决定筛哪个授权位**（cloud 筛 `allowCloudRead`、local 筛 `allowLocalRead`）。配了本地写模型 → observed 本地私密消化。
2. **知情同意（Host 向导）**：生成 `.env` 的配置向导里，没配本地写模型时**告知**「observed 不会被消化，除非配本地模型或授权上云（⚠️ 行为观察会离开本机）」，把选择与风险交给用户。
3. **授权门补齐（Core）**：`allowInference` 门在写路径三处口径一致（现只有 `attribute` 查了，`consolidate`/`distill` 漏了）。

**这不是 A2（双模型同一次分流合并）**——作者已定只做单写模型带 tier（A1）+ 知情同意；A2 留作后续可选子里程碑，本步只把 `LLMPool` 的 tier 维度缝铺好（`pool.ts:9` 预留的 `forEvidence(ev)` 方向），不实现分流合并。

## 红线（本批任何卡都不许破）

- **不动认知纪律判定算法**：`consolidation/consolidate.ts` 的合并/冲突暴露、`confidence.ts` 置信度自算、`cognition/` 分型过期、记≠信。**只改「哪些证据进 prompt / 进合法支撑集」这道过滤，`confidence.ts` 一行不改。**（#1 校对焦点）
  - 澄清（对抗校对追问）：tier=cloud（缺省）时支撑集**逐字节不变**→ 置信度不变；tier=local 时本地模型合法多看到 observed → observed 得以支撑画像、置信度输入随之变化——**这是本步的功能，不是"改判定"**（算法没动，只是喂进算法的合法证据变多了）。
- **零运行时依赖**：本地模型 = 宿主把 `WRITE_LLM_BASE_URL` 指向本地 OpenAI 兼容端点（llama.cpp / ollama 等），Core 一行新依赖都不加。`package.json` `dependencies` 保持 `{}`。
- **隐私不倒退**：tier **缺省 = `cloud`**（不配 → observed 绝不因本步误泄）。**"零行为变更"有一处已批准的例外**：D4 的 `allowInference` 门补齐后，`inference=false && cloud=true` 的证据（需用户显式撤销推理授权才出现，缺省不存在）在 cloud 路径下也不再进画像——属"授权位真生效"的正确化，CHANGELOG 明记。除此之外 cloud 路径逐字节不变。
- **公开 API 非破坏**：`LLMClient.tier` 必须**可选**（宿主自注入的 client 不带 tier 也照跑，缺省当 cloud）；tier 落 `LLMConfig`（[experimental]，加字段无契约负担），**不碰** `MemoWeftConfig`（沿 temperature 的 B8 先例：走 client 配置 + env，不进核心 config 形状）。
- **兼容**：`WRITE_LLM_TIER` 走双前缀（`MEMOWEFT_*` 主名 + `DLA_*` 回退）；`./dla.db` 默认路径不动。
- **推 main 前 PM 亲核。**

## 现状底座（已亲验 · 2026-07-05）

- **题眼一行**：`filterCloudReadable(items)` = `filter(e => e.allowCloudRead)`（`src/evidence/privacy.ts:14`），注释自陈"假设 deps.llm 是云端模型"（`:7-10`），并已写好升级方向"给 LLMClient 加 'cloud'|'local' 标识，按 tier 决定"。
- **设计骨架文档/代码里早埋好**：`LLMPool` 是"按维度选 client、不写死俩固定 client"（`src/llm/pool.ts:7-9`、`docs/architecture.md:261`）；规则已拍板 `ModelTier='cloud'|'local'`——cloud 读 `allowCloudRead`、local 读 `allowLocalRead`、能否进画像还看 `allowInference`（`docs/internal/架构归位路线.md:574-585`）。
- **写模型早已单独配**：`loadLLMConfig('WRITE_LLM')`（`client.ts:59`），`updateProfile` 只取**一个** `pool.for('write')` client（`createCore.ts:242`）——distill→consolidate→attribute→trends 一整趟共用它。**这决定了本步是"给这一个 client 打 tier 标"，不是分流。**
- **6 处过滤调用点**（改 `filterCloudReadable` 会红 `tests/privacy.test.ts:27-211` 的 7 条）：
  - 写路径三处：`distillation/distill.ts:55`、`consolidation/consolidate.ts:181`、`attribution/attribute.ts:159`。
  - 另三处：`background/trends.ts:106`、`asking/proposeAsk.ts:148`、`asking/revisitConflicts.ts:123`。
  - 6 处都能拿到 client：写路径三处走 `updateProfile` 注入的 `pool.for('write')`（`createCore.ts:242`）；**另三处（`proposeAsk`/`revisitConflicts`/`aggregateTrends`）是 `index.ts` 导出、由宿主自注入 `llm` 调用（不走 `updateProfile`），`deps.llm` 可选（`deps.llm?`）**。→ **各处按"自己那个 client 的 tier"筛**（`deps.llm?.tier ?? 'cloud'`）正好自洽：写路径用写模型 tier、asking/trends 用宿主给的 client 的 tier（对抗校对确认）。
- **闭环缺口①（早退）**：`distill` 遇"本批全 cloud=false"直接早退、不建 event、证据留 pending（`distill.ts:60`）。写模型 tier=local 时，`local` 版过滤放行 observed（`allowLocalRead=true`），此处不再早退，observed 建 event 进消化。
- **闭环缺口②（覆盖记账 · 对抗校对亲验挖出 · 更要命）** 🔴：`distill` 建 event 时 `evidenceIds: pending.map`（**全部** pending，`distill.ts:78`），但 summary 只含 `cloudSafe`（`:63`）。→ **observed 一旦和 cloud=true 证据同批，就被标"已覆盖"却从未进 summary 消化，且下轮 `filter(!covered)`（`:48`）再也扫不到**。后果：cloud tier 时期采集的 observed（混批场景=常态）被**静默消耗**、**换本地模型/授权上云也补不回来**——"行为观察"卖点从根上被架空。**修法（作者已拍板纳入本步）**：event 只覆盖当前 tier 真读了的证据（`readable.map`），被挡的留 pending 可再扫，与 `:57-59` 那条"全 cloud=false 留 pending"的既有设计对齐。
- **`allowInference` 门三缺二（已亲验 grep）**：只有 `attribute.ts:162` 有 `.filter(e => e.allowInference)`；`distill`/`consolidate` **无**。今天不出事是因 observed 默认 `inference:true` 且 `cloud:false` 先被云端关挡住；开本地路径后此缺口暴露。
- **"授权上云"分支已现成**：`memory/managementApi.ts:224` `updateEvidenceAuthorization` 翻 `allowCloudRead`/`allowInference` 授权位、落库、带改前改后隐私审计。用户知情同意后翻位，下轮云端模型即消化——**此分支零新核心代码**。
- **"observed 挂账"信号：现状不干净，须补明确字段（对抗校对纠正）**：`distilled.pendingCount` 三处 return 都回 `pending.length`（`:51`=0、`:60`=全量、**`:81` 建成 event 后也=全量**），是"起始未覆盖数"、**不是"被 tier 挡住数"**，向导据它会误判。→ T3 要给 `DistillResult` 加显式"当前 tier 挡住的证据数 / 原因"字段（配合缺口②的覆盖修复，被挡的留 pending，此数天然=挡住数）。
- **向导已成熟**：`POST /api/gen-env` 拼 `.env`（`apps/memoweft-host/src/server.ts:189` `buildEnvResponse`，已含"写模型组" `:221-228`，空组自动写回退注释）；前端首启门向导 `web/index.html:490`。部署口径本就是 **Cloud-first onboarding + evidence 级授权 + Hybrid/local 作高级选项**（`docs/internal/STATE.md:10`）——与本步同调。
- **三绿基线**：`npm run typecheck && npm test && npm run build`（`package.json:28-37`）；Core 202/202、Host 27/27、lint 0 错（存量 6 警）。

## 决策（作者已拍板 2026-07-05）

| # | 决策 | 定案 |
|---|---|---|
| **D1** | 路由档位 | **A1（单写模型带 tier）**，不做 A2（双模型分流合并）。A2 留后续可选子里程碑、只走"双趟顺跑"变体、绝不动 consolidate 合并算法。本步预留 tier 维度缝。 |
| **D2** | tier 落点 | 落 `LLMConfig`（`client.ts`）+ env `WRITE_LLM_TIER`（双前缀），**缺省 `cloud`**。`LLMClient` 加**可选** `tier?`（TS 结构类型下加可选字段对宿主自注入 client 非破坏——旧实现照满足接口）。**tier 绑在 client 实例上**（各按自己 env 前缀读：chat←`LLM_TIER`、write←`WRITE_LLM_TIER`），故 `pool.ts:44` 写模型缺配回退成 chat 时，自然继承 chat 的 cloud tier——**杜绝"标 local 实跑云端"泄漏**（对抗校对补强）。**不碰** `MemoWeftConfig`（沿 temperature B8 先例）。**驳回**校对建议的"tier 只放 LLMPool"——asking/trends 由宿主注入 client 而非 pool，tier 必须在 client 上才够用。 |
| **D3** | 过滤器语义 | `cloud` 筛 `allowCloudRead=true`、`local` 筛 `allowLocalRead=true`。tier=local **不等于"全放行"**——仍按 `allowLocalRead` 筛（贴文档 `架构归位路线:581`）。签名 `filterReadableByTier<T extends {allowCloudRead:boolean; allowLocalRead:boolean}>(items, tier)`（泛型约束补 `allowLocalRead`；6 处传的都是 `Evidence`，两字段都有）。 |
| **D4** | `allowInference` 门 | **补齐三处口径一致**（`consolidate`/`distill` 补 `.filter(allowInference)`，与 `attribute` 已有的**无条件**门对齐）。**distill 也补的理由**（对抗校对追问后确认）：distill 建的 event summary 会喂进 consolidate 画像，若不在 distill 拦，`inference=false` 证据的内容会经 summary **间接**渗进画像——故三处都拦才真生效，且这正是 attribute 已在做的口径。门**与 tier 无关**（推理授权是用户对某条证据的撤销，跟模型云/本地无关）。副作用：cloud 路径下 `inference=false && cloud=true` 证据从此不进画像（缺省数据无此状态，需用户显式撤销才出现）——见红线例外条，须补测试 + CHANGELOG。 |
| **D5** | Host 同意层 | **只做向导 setup 时提醒**（生成 `.env` 时没配本地写模型 → 告知 observed 不消化 + 授权上云的风险）。运行时管理页的完整同意 UI 归后期前端打磨批，本步不做。 |
| **D6** | tier 是否自动探测 | **否**——宿主/用户显式声明（向导选 / env 配）。库不按 baseUrl 猜"是不是本地"（守"库不替宿主做安全策略"，也防误判把敏感数据当本地）。 |
| **D7** | T2/T3 合卡 | **合成一卡原子做**（对抗校对定）。分卡先合 T2（开本地路径）后合 T3（补 inference 门）之间有泄漏窗口；且都改 `distill.ts`/`consolidate.ts` 相邻块。合卡=一次做全：过滤器 tier 化 + 6 处接线 + 早退松开 + 覆盖修复 + inference 门补齐。 |
| **D8** | 覆盖记账修复 | **纳入本步**（作者已拍板）。event 只覆盖当前 tier 真读了的证据；被挡的留 pending、可再扫。这是让"换本地/授权后 observed 真能被补消化"成立的关键（否则闭环②架空卖点）。改的是 distill 覆盖记账、非认知判定算法。 |

## 任务清单（6 卡 · 待终审后拆独立施工卡 · 已并 T2/T3=D7）

| 序 | 卡 | 一句话 | 大小 | 碰核心? | 依赖 / gate |
|---|---|---|---|---|---|
| **T1** | 模型 tier 地基 | `ModelTier='cloud'\|'local'` 类型 + `LLMConfig.tier?`（默认 cloud）+ `loadLLMConfig` 读 `${base}_TIER` 双前缀 + `LLMClient.tier?` 可选 + `OpenAICompatClient`/`loadLLMPool` **按 client 实例**装配 tier（回退安全，D2） | 小-中 | 否 | 无（奠基） |
| **T2** | 写路径隐私关 tier 化（**含 D7 合并的全部核心**） | ①`filterCloudReadable`→`filterReadableByTier(items, tier)`（旧名留薄别名=cloud）；②6 处按 `deps.llm?.tier ?? 'cloud'` 接线；③`distill` 早退按 tier 后为空判定；④**覆盖修复（D8）**：event 只覆盖 `readable`、被挡留 pending；⑤**inference 门（D4）**：`distill`/`consolidate` 补 `.filter(allowInference)` 对齐 `attribute` | 大（**最核心**） | 邻近·**不动判定算法/confidence** | 依赖 T1 |
| **T3** | Core 挂账信号 | `DistillResult` 加显式"当前 tier 挡住数/原因"字段（替代误导的 `pendingCount`），`updateProfile` 透传，供向导/宿主判"有 observed 挂账" | 小 | 否 | 依赖 T2 |
| **T4** | 测试 | `privacy.test.ts` 按 tier 参数化（cloud/local 两版 client）；**(cloud×inference) 二维矩阵**锁 D4 行为变更；**覆盖修复专测**：被挡证据留 pending、换 local 后能被补消化；EVAL-L：local 下 observed 真进画像 / cloud 下仍被挡；Host `buildEnvResponse` 新增用例 | 中-大 | 否 | 依赖 T2/T3 |
| **T5** | Host 向导 tier 字段 + 风险提醒 | `buildEnvResponse` 产出 `MEMOWEFT_WRITE_LLM_TIER`（空写模型组也写=cloud）+ 无本地写模型时按空组注释风格加风险提醒；`web/index.html` 写模型组加 tier 选择 + 本地提示 | 中 | 否（Host） | 软依赖 T1（键名口径） |
| **T6** | 文档 + 契约 + CHANGELOG | `.env.example` 加 `WRITE_LLM_TIER`；`architecture.md`§8 / `deployment.md` / `STATE.md`；**SECURITY**：tier=local 却指向云端端点=宿主责任；`memory-surface-contract.md` 同步（`LLMConfig.tier?`/`LLMClient.tier?` 可选·experimental·缺省 cloud·非破坏）；**CHANGELOG 记 D4 行为变更**（`inference=false&&cloud=true` 不再进画像） | 中 | 否 | 软依赖 T1-T3 |

## 并行冲突图

**热点文件与抢占方**：
- `src/llm/client.ts`：T1 全占（`LLMConfig`+`LLMClient`+`loadLLMConfig`+`OpenAICompatClient`）。→ **T1 单独先合**，其余卡都踩它。
- `src/evidence/privacy.ts`：T2 全占（过滤器签名 tier 化）。
- `src/distillation/distill.ts`、`src/consolidation/consolidate.ts`：**T2 独占**（接线 + 早退 + 覆盖修复 + inference 门一次做全，D7 合并后无 T3 争抢）。
- 其余（`attribute`/`trends`/`proposeAsk`/`revisitConflicts`）：仅 T2 动一行过滤调用，互不冲突。

**建议波次**：波1 = **T1**（奠基，单独合）→ 波2 = **T2**（核心，一卡原子做完过滤/接线/覆盖/inference 门）→ 波3 = **T3**（信号，踩 T2）**+ T5**（Host 独立，可任意波）→ 波4 = **T4**（测试，踩 T2/T3 成品）**+ T6**（文档，踩最终形状）。

## 批次验收（草案 · 全批合完跑一遍）

- [ ] **私密支闭环**：写模型 `tier=local` 时，observed(cloud=false, local=true) 真被 distill 建 event、进 consolidate 画像。
- [ ] **覆盖修复（D8）**：observed 与 cloud=true 证据混批、cloud tier 下，observed **留 pending 不被吞**；事后换 `tier=local`（或授权上云）后，这些 observed **能被补消化**（不是已 covered 消失）。
- [ ] **隐私不倒退**：写模型 `tier=cloud`（含缺省）时，observed(cloud=false) 仍被挡在 prompt + 合法支撑集之外，行为与本步前**逐字节一致**（除下一条 inference 门的已批准例外）。
- [ ] **inference 门生效**：`inference=false` 的证据，两个 tier 下都进不了画像（consolidate/attribute/distill 三处一致）；(cloud×inference) 二维矩阵专测锁住 cloud 路径这一行为变更。
- [ ] **信号可用**：Host 能据 T3 的显式"tier 挡住数"字段判定"有 observed 挂账"并触发向导提示（不再用会误导的 `pendingCount`）。
- [ ] **向导**：`gen-env` 产出含 `MEMOWEFT_WRITE_LLM_TIER`；未配本地写模型时 `.env` 带风险提醒注释；`web` 向导有 tier 入口。
- [ ] **API 非破坏**：不带 tier 的自注入 `LLMClient` 照跑（缺省当 cloud）；`MemoWeftConfig` 形状未变。
- [ ] **三绿 + 零依赖**：`typecheck`/`test`/`build` 全绿，Host 测试全绿，`dependencies` 仍 `{}`，lint 不新增错。
- [ ] **红线自证**：diff 里 `consolidate` 的合并/冲突判定、`confidence.ts`、`cognition/` 分型无改动（只增过滤，不改判定）。

## 本批明确不做

- **不做 A2**（双模型同一次分流 + 画像合并）——留后续可选子里程碑，只铺缝。
- **不做运行时管理页的完整同意 UI**——归后期前端打磨批（作者拍板 D5）。
- **不按 baseUrl 自动探测 tier**（D6）。
- **不改任何认知纪律判定算法**（只改进 prompt/支撑集的过滤）。
- **不引任何运行时依赖**；本地模型的架设是宿主/用户的活。
- **0.4.0 未闭的真模型 e2e 英文验**不在本批（另账，见 CURRENT）。

## 对抗校对纪要（4 路 · 2026-07-05 · 关键项已主线亲验）

评审读真代码挑洞，结论分三类：

**采纳并入（真问题 → 已改任务书）**：
1. **T2/T3 合卡**（→ D7）：分卡有"先开路径后补门"的泄漏窗口 → 合成一卡原子做。
2. **tier 绑 client 实例、回退安全**（→ D2 补强）：防"标 local 实跑云端"泄漏；亲验 `pool.ts:44` 回退成 chat 时继承 cloud tier。
3. **6 处各按自己 client 的 tier 筛**（→ 现状底座）：亲验 asking/trends 是宿主自注入 llm（`index.ts` 导出、不走 updateProfile），`deps.llm?.tier ?? 'cloud'` 自洽。
4. **inference 门"三处一致"的理由与副作用**（→ D4/红线例外）：distill 也拦是因 event summary 间接喂画像；副作用是 cloud 路径 `inference=false&&cloud=true` 不再进画像（缺省无此态）→ 修正"零行为变更"措辞 + CHANGELOG。
5. **信号不干净**（→ T3 升级）：亲验 `distill.ts:81` 建成 event 后 `pendingCount` 仍回全量 → 加显式"tier 挡住数"字段。
6. 泛型约束补 `allowLocalRead`（→ D3）；向导/Host 测试/契约同步（→ T4/T5/T6）。

**主线亲验挖出、评审没抓到（最要命 → D8 覆盖修复，作者已拍板纳入）**：
- `distill.ts:78` 建 event 覆盖**全部** pending 而 summary 只含 readable → observed 混批被静默消耗且再扫不到，卖点被架空。修法=只覆盖 readable、被挡留 pending。

**降级（评审用力过猛，不采纳为 blocker）**：
- "T2 改支撑集→改置信度=破红线"：**不成立**。`confidence.ts` 算法零改；cloud tier 支撑集逐字节不变；local tier 让 observed 合法支撑画像=本步功能本身。已在红线条澄清。

> 备注：本轮校对由 4 路独立 Explore agent 读真代码产出（红线/正确性/完整性/可实现性），主线对最高风险的 5 条断言（distill 返回/consolidate 置信度路径/6 处 client 来源/覆盖记账/inference 门现状）逐条亲验核对。

## 附 · 现状勘察证据索引（file:line · 已主线亲验）

- **过滤关 & 6 处调用**：`evidence/privacy.ts:7-16`、`distill.ts:53-60`、`consolidate.ts:174-184`、`attribute.ts:157-162`、`trends.ts:106`、`proposeAsk.ts:148`、`revisitConflicts.ts:123`。
- **证据模型 & 授权默认**：`evidence/model.ts:14-40`（结构，`:35` allowCloudRead、`:37` allowInference）、`config.ts:100` `observedDefaults={local:true,cloud:false,inference:true}`、`config.ts:140-142` `cloudReadDefault` 跟 `privacyMode`、`evidence/store.ts:139-159`（put 按 sourceKind 分流 + `??` 显式优先）、`perception/ingest.ts:7-10/55-88`。
- **LLM 客户端/配置/池**：`llm/client.ts:15-19`（LLMClient 仅 chat+callCount）、`:21-29`（LLMConfig 四字段）、`:62-81` loadLLMConfig 双前缀、`llm/pool.ts:7-9`（tier 维度预留注释）、`:14-20`（LLMPurpose/LLMPool）、`:28-48` loadLLMPool、`core/createCore.ts:148-159`（asPool）、`:242`（write client 注入）。
- **写路径编排 & 信号**：`consolidation/updateProfile.ts:60-114`（distill→consolidate→attribute→index，`:99` 透传 distilled）、`distill.ts:44-60`（pending/cloudSafe/早退）。
- **同意分支现成**：`memory/managementApi.ts:224-246`（updateEvidenceAuthorization + 审计）。
- **向导**：`apps/memoweft-host/src/server.ts:189-248`（buildEnvResponse，`:221-228` 写模型组）、`web/index.html:490/788/801`、`docs/internal/STATE.md:10`（部署口径）。
- **规则/红线出处**：`后续批次总纲.md:25-26`（第 6 步）、`架构归位路线.md:574-585`（ModelTier 规则）、`architecture.md:261/275/282`（LLMPool 留口 + 隐私归宿主 + 前提注释）、`0.4.0/README.md:15/90-92`（红线体例）。
</content>
</invoke>
