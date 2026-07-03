# MemoWeft 阶段 4-A 多源感知(档 1)· 给 Coder 的交接文档

> **来源**：产品经理基于与项目所有者的方向讨论（2026-07-01）整理。供 coder 接手 4-A 档 1 实现。
> **阅读顺序**：先读 `docs/项目现状交接.md` + `STATE.md` 摸全局，再读本文档看 4-A 这一版的边界、影响面、待定项。
> **纪律**（沿用 `AGENTS.md` / 开发者交接文档第九节）：本文档给**方向和边界**，不写死实现；碰核心机制处已标 ⚠️**需所有者确认**；带前提的判断已标明前提。遇文档与代码冲突，以"意图"为准、实现由你结合现有代码判断、拿不准问所有者。**先出方案再写、列影响面清单、碰核心机制先问。**

---

## 0. 一句话

把"用户的**活动窗口行为**"作为对话之外的第二条证据来源接进 MemoWeft：做一个**通用、可扩展的观察摄入 API**，让行为记录能进证据层、并**验证它真能变成归因和画像**。这一版只做到"**摄入口闭环 + 验证**"，真正的后台采集器**留骨架、下一版做**。

---

## 1. 背景与目标

- **为什么做**：MemoWeft 现在只有"对话"一条证据来源。加"行为"这条，是为了让 MemoWeft 不只靠用户**说**的，还能从用户**做**的（开了啥软件、什么时候、待多久）去印证或补充对用户的理解。这是开发者交接文档第八节列的 MemoWeft 差异化之一（"行为感知→画像，且与对话证据融合"）。
- **这一版的真正产出（outcome）**：**不是**"接了个数据源"，而是"**验证『行为记录 → 归因 / 画像』这条路真能走通**"。
- **一举两得**：验证这条路的过程，本身就把现有的认知闭环（distill→consolidate→attribute）用一种**新的、可控的证据**跑了一遍——等于顺手 dogfood 了地基（回应现状交接 §7.1"5 个模块只过离线单测、未系统 dogfood"）。

> **验收是主观的**（项目一贯标准）：所有者觉得"行为数据让它更懂我了"，而不是某个跑分。

---

## 2. 范围：做什么 / 不做什么

**这一版做（In）：**
1. 通用可扩展的 `ingestObservations` 摄入 API（**库级**，`src/index.ts` 导出，不再只活在测试台）。
2. "活动窗口"这一种观察 → 标准化成 `observed` 证据 的映射。
3. `observed` 证据的**保守默认授权**（默认不上云）+ **让"不上云"真生效所需的下游过滤**（见 §6，这是关键）。
4. 测试台手动注入 + 验证 `observed → 归因 → 画像` 闭环（顺手 dogfood）。

**这一版不做 / 留骨架（Out）：**
- ❌ **真后台采集器**（`active-win` 长驻进程、定时/事件触发、停留时长计算）——只留**接口契约 / 骨架**，不实现采集。
- ❌ **读窗口正文**（"看看我现在这个页面"）——另一条技术路，以后再说。
- ❌ 设备 / 手机 / 手表等其它源。
- ❌ 把"能承载的信号字段"写死——**口子要可扩展**（所有者明确要求：不锁死，以后能加接口）。

---

## 3. 四步边界定案（所有者已拍板 · 2026-07-01）

| # | 边界问题 | 定案 |
|---|---|---|
| 1 | **采集归谁** | **独立可选采集器**。MemoWeft 核心只管"认知 + 一个收数据的通用口子"；抓窗口的活儿是个外挂小模块，可有可无、可弃，维护风险隔离在它里面。 |
| 2 | **这版完成到哪** | **档 1：摄入口闭环**。交付 = 摄入 API 做扎实 + 测试台手动注入 + 验证"行为记录真能变成归因/画像"。真采集器留骨架。 |
| 3 | **抓哪些信号** | ① 收数据的口子做成**通用可扩展**（加新数据源不返工）；② 这版只抓**活动窗口**（app 名 / 窗口标题 / 时间 / 停留时长）；③ **不读页面正文**。 |
| 4 | **隐私默认** | 行为记录**默认只在本地用、不上云**（`allowCloudRead=false`）；变画像靠本地模型（3090）。只是**默认值**，每条证据授权可单独改。 |

---

## 4. 现状盘点（好消息：地基已就位大半）

基于通读真实代码（`src/evidence/`、`config.ts`、`attribution/attribute.ts`、测试台）：

| 已就位 ✅ | 位置 |
|---|---|
| `sourceKind = 'observed'` 已是合法来源类型 | `evidence/model.ts:11` |
| `put` 已幂等（带 `originId` 不重复落库）——观察证据天然适合用它去重 | `evidence/store.ts:111` |
| 三个授权位字段都在：`allowLocalRead` / `allowCloudRead` / `allowInference` | `evidence/model.ts:33-37` |
| 授权位"缺省由存储层补默认"机制已在；`cloudReadDefault()` 跟随 `privacyMode` | `evidence/store.ts:129-131`、`config.ts:108` |
| 归因 `attribute` 已按 `allowInference` 过滤、且能吃 `observed` 证据 | `attribution/attribute.ts:141` |
| 测试台已有 `/api/observe` 手动注入口（已幂等） | `testbench/server.mjs` |

| 缺口 / 待建 ⬜ | 说明 |
|---|---|
| `ingestObservations` 库级 API | `src/` 里**未实现**，只在测试台就地处理（grep 仅命中 testbench / docs） |
| `observed` 的**保守默认授权** | 当前 `put` 补默认**不分来源种类**：不管 spoken/observed，`allowCloudRead` 一律跟随 `cloudReadDefault()`（现 `privacyMode=false` → 默认 **true**）。需让 observed 走更严默认。 |
| ⚠️ **`allowCloudRead` 下游无人过滤** | **见 §6——这是"隐私默认本地"能否真生效的核心缺口。** |

---

## 5. 怎么做（设计指引，非写死实现）

### 5.1 通用摄入 API（草案签名，coder 可调）

所有者要"可扩展、不锁死"。建议 `Observation` 用 `kind + 结构化 meta` 的开放形状，新增信号类型不改接口：

```ts
// ⚠️ 草案示意，coder 结合现有 EvidenceInput 定稿
interface Observation {
  kind: string;          // 这版固定 'active_window'；以后可加 'clipboard' / 'device' …（可扩展关键）
  occurredAt: string;    // 精确时间戳，必带（cell 7：每条证据必带精确时间）
  content: string;       // 标准化后的人类可读串，例："在 VS Code（DLA_rebuild）停留约 40 分钟"
  originId?: string;     // 幂等键（同一窗口会话不重复落）
  meta?: Record<string, unknown>; // 原始结构化字段（app/title/durationSec…），开放、不写死
  // 授权位可选；不传则走 observed 的保守默认（见 5.3）
}

function ingestObservations(
  subjectId: string,
  observations: Observation[],
  deps: { evidenceStore: EvidenceStore /* …按需 */ },
): { stored: Evidence[]; skipped: number };
```

> 要点：MemoWeft **只定义"观察怎么进来"**，不在库里写"怎么从操作系统抓"（cell 9 边界 + 四步定案 #1）。

### 5.2 活动窗口 → Observation → Evidence 映射

采集器（骨架/外挂）把 `active-win` 拿到的 `{app, title}` + 进入/离开时间算出的 `durationSec`，组装成上面的 `Observation`（`kind='active_window'`），调 `ingestObservations` → 落成 `sourceKind='observed'` 的 Evidence。

### 5.3 隐私默认：observed 默认不上云（落点 + 选法）

目标：`observed` 证据默认 `{ allowLocalRead:true, allowCloudRead:false, allowInference:true }`。
（`allowInference=true` 是为了它**能变画像**；不上云靠 `allowCloudRead=false` + §6 的过滤来保证。）

⚠️ **碰核心机制（证据层默认授权），实现选法请 coder 提案、所有者确认。** 三种选法：
- **选 a**：`evidence/store.ts` 的 `put` 里按 `sourceKind` 分流默认（observed → cloud 默认 false）。改动集中但动了通用存储层。
- **选 b**：`config.ts` 加 `observedDefaults`，由 `ingestObservations` 摄入时套用、显式传入 `put`。不动 `put` 通用逻辑。
- **选 c**：默认值完全在 `ingestObservations` 层定，`put` 不变。最不碰核心，但默认逻辑分散两处。
- 倾向 **选 b**（集中、可配、不动通用存储层）——但交所有者定。

### 5.4 验证闭环（档 1 的核心交付）

测试台手动注入活动窗口观察 → `updateProfile`（distill→consolidate→attribute）→ 观察它是否进证据层、被归因/画像消费。
> 现有验收场景已验过类似最小路（"游戏到 3:30"observed → "没睡好" → 归因），4-A 是把它**正式化 + 系统验**。

---

## 6. ⚠️ 关键发现：「默认不上云」现在是空设，要补一环才真生效

**事实**（grep `allowCloudRead` 全 `src/`）：除证据层自己存读 + config 定义默认外，**没有任何下游使用它**。即 `distill` / `consolidate`（这两步把证据喂**云端 MiMo**）**根本不看 `allowCloudRead`**。

**后果**：把 observed 证据的 `allowCloudRead` 设成 `false` **不会阻止它被送上云**——因为到目前所有证据都是对话（spoken、默认可上云），从没人需要这个过滤，所以一直没实现。

**更深一层的张力**（请 coder 和所有者都注意）：
1. 4-A 要 observed 证据**能变画像** → 需 `allowInference=true`。
2. 但"变画像"的三步（distill / consolidate / attribute）现在都调**云端 MiMo**。
3. 所以 observed 证据一旦进提炼，就会上云 → 与 `allowCloudRead=false` **直接冲突**。
4. （注：`attribute` 已按 `allowInference` 过滤，但它**自己也调云端模型**，所以"过滤了不许推断的"≠"没上云"。）

**结论**：要让"observed 默认不上云"**真生效、同时仍能变画像**，干净解只有一个——**observed 证据的提炼/归因改走本地模型（3090）**。

> 这把"**写路径配本地模型**"从一个"性能优化"（现状交接 §7.2、慢的取舍）**提升为"隐私默认本地"的前提**。两件事是**绑定的，不是独立议题**。→ 已标进 §9 开放问题，需所有者拍。

**档 1 的务实折中**（二选一，所有者定 → §9）：
- **折中 A**：档 1 先**只验证到 `attribute` 归因**（已按 allowInference 过滤），不让 observed 进会上云的 distill/consolidate。隐私默认不破，但验证范围小一点。
- **折中 B**：档 1 **临时借现有云端 consolidate** 把"observed→画像"全链路验通，**明确标注这是临时违反隐私默认、仅供档 1 验证**；真采集器上量前**必须**切本地模型。验证最完整，但需在代码/文档里写死"临时"警示，防止它变永久。

---

## 7. 影响面清单

| 动作 | 文件 | 备注 |
|---|---|---|
| 🆕 新增 | `src/perception/ingest.ts`（或类似）+ 单测 | `ingestObservations` 本体 |
| 🆕 新增（骨架） | `src/perception/collectors/activeWindow.ts` | 仅接口契约/骨架，**不实现长驻采集** |
| ✏️ 改 | `src/index.ts` | 导出 `ingestObservations` + `Observation` 类型 |
| ✏️ 改 | `config.ts` | 加 `observedDefaults`（若选 §5.3 选法 b） |
| ⚠️ 改（碰核心） | `evidence/store.ts` | 若选 a：`put` 按来源分流默认 |
| ⚠️ 改（碰核心） | `distillation/distill.ts`、`consolidation/consolidate.ts` | 上云前按 `allowCloudRead` 过滤证据（§6）——**这是让隐私默认生效的关键改动** |
| ✏️ 改 | 测试台 `server.mjs` / `index.html` | `/api/observe` 对接新 API；可加活动窗口注入面板 |

---

## 8. 验收标准（档 1 = 做完）

- [ ] `ingestObservations` 库级导出 + 单测：批量摄入、`originId` 幂等、observed 默认授权正确（local=true / cloud=false / inference=true）。
- [ ] "默认不上云"**真生效**：有自动化测试证明 `allowCloudRead=false` 的证据不会被喂进云端调用（§6）。
- [ ] 测试台能手动注入活动窗口观察，并能看到它**进证据层 → 被归因/画像消费**（按 §6 选定的折中 A 或 B）。
- [ ] 真采集器：**只留骨架/契约**，不实现后台采集。
- [ ] `npm run typecheck` / `npm test` / `npm run build` 全绿。

---

## 9. 待定项 / 开放问题（诚实标注 · 🔵=需所有者拍 / 🔶=假设待验）

> **【2026-07-01 方向更新 · 上本地模型】** 所有者定案：MemoWeft 配**本地 + 云端两个模型，按证据 `allowCloudRead` 自动路由**——不许上云(observed)走**本地模型(3090)**、许上云走**云端**。这解开了本节下面几条：`observed 用什么模型`(→ 本地)、`折中 A/B`(→ 不用选，cloud=false 走本地变画像)、以及那颗 **distill 覆盖语义雷**(→ cloud=false 走本地、不再被云端挡/静默丢，随之化解)。**代价**：配两套模型 + 路由逻辑 + 改写路径三处(distill/consolidate/attribute)认路由；本地模型中文提炼/归因质量**未验**。**顺序**：先 dogfood 档1(云端)摸到云端质量参照，再上。档2 实现。
>
> **【更远期 · 档2 之后】** 此路由之上还有一层**隐私知情同意层**：observed 授权**不预设**，改为「攒着待定 → MemoWeft 主动征询 → 用户开关选本地/云端」（现档1 的 `cloud=false` 是其保守简化版；测试台"允许上云"勾选是手动雏形）。需先有本地模型 + 「问/答」交互通道。详见项目记忆，**授权设计别做死，给这层留余地**。

- 🔵 **observed 的提炼用什么模型**？——这是 §6 的核心，**"隐私默认本地"与"写路径配本地模型"绑定**。可能要把"配本地模型"在优先级上提前。
- 🔵 **档 1 验证走折中 A 还是 B**（§6）？——只验到归因（不破隐私默认）vs 临时借云端验全链路（标"临时"）。
- 🔵 ⚠️ **distill 对"被过滤掉的证据"的覆盖语义**（隐私修复埋的雷，**必须随折中 A/B 一并定**）：现在 `distill.ts:60-65` 把**全部** pending 证据（含 `allowCloudRead=false`、没喂给模型的那些）都记进事件的 `evidenceIds` → 它们被标"已覆盖、不重捞"。当前全 cloud=true 零影响；但 4-A 引入 observed（默认 cloud=false）后，若仍走**云端** distill，这些行为证据会**被标已处理却从没进任何事件 → 静默丢失**（既不进画像、也不再被提炼）。代码留了 `TODO(4-A)`（`distill.ts:47`）。定折中时必须决定：① cloud=false 证据**不标覆盖、留着等本地模型**（不丢数据，推荐方向）；还是 ② 想清后明确接受丢失。
- 🔵 **observed 默认 `allowInference`** 给 true 还是 false？建议 true（否则行为永远变不成画像），但更激进——请所有者确认。
- 🔵 **停留时长归谁算**：采集器算好再喂，还是喂原子"进入/离开"事件让 MemoWeft 算？（摄入口契约细节，可由 coder 提案）
- 🔶 **观察数据更脏 → 放大已知的"过度归因"风险**（现状交接 §6.5 / §7）。档 1 要不要给 observed 证据更保守的归因门控？建议 dogfood 后按真实表现调。
- 参数校准：observed 起步分（现 `baseByFormedBy.observed=350`）、归因窗口（`windowHours=24`）等都是初值，运行后按真实体验调。

---

## 10. 给 Coder 的协作纪律提醒

1. **先出方案再写**：尤其 §5.3（默认授权落点）、§6（上云过滤）这两处碰核心，先把选法 + 影响面给所有者过一遍再动手。
2. **别把档 1 的临时折中写成永久规则**：若走 §6 折中 B（临时借云端），代码注释 + 文档都要写死"临时、本地模型就绪后必切"。
3. **诚实优先于乐观**：可行性以"能跑的代码 + 真实 dogfood"为准；本地模型提炼质量、本地嵌入分不分得清"项目 vs 生活"都还没验（开发者交接文档难点 1/2），别预设乐观结论。
4. **方向/价值判断归所有者**：本文档已把能摊开的权衡摊开；遇到本文档没覆盖、又影响方向的问题，回去问，别自己拍成既定事实。

---

*本文档随讨论演进，非冻结契约。发现某条判断"是被推着定的、不是真想清的"，可与所有者重新确认——包括本文档自己。*
