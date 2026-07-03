# MemoWeft 4-A 多源感知 · 档1 · 实现清单 · 给 Coder

> **来源**：产品经理与所有者讨论定案（2026-07-01）。本清单是 4-A 档1 的**可执行实现指引，决策已定**。
> **背景/分析细节**见同文件夹 `DLA_4-A_交接_给coder.md`（尤其 §5.1 结构草案、§6 隐私、§9 待定项）。
> **前置**：问题1 隐私修复已完成（`src/evidence/privacy.ts` 的 `filterCloudReadable`），本任务建立在其上。
> **纪律**（AGENTS.md）：碰核心先出方案问所有者、列影响面、小步；改完跑测试台落盘 + 同步 STATE/LOG。

---

## 1. 一句话

做一个**通用观察摄入口**，让"活动窗口行为"能进证据层，并**验证"行为→画像"全链路跑通**。这版只做**摄入口闭环 + 验证**，真采集器**留骨架**。

## 2. 所有者已拍板的方向（2026-07-01）

| 项 | 定案 |
|---|---|
| **验证路线** | **A**：用"手动授权上云"的**测试数据**走现有**云端**模型验证全链路。**隐私默认不破**（默认仍 cloud=false，仅测试这几条单条开） |
| **采集归属** | 独立可选采集器（本版**留骨架**）；核心库只做"认知 + 通用摄入口" |
| **信号范围** | 通用可扩展盒子，这版只装**活动窗口**（app / 标题 / 停留时长 / 时间）；**不读正文** |
| **observed 默认授权** | 本地可读 = true / 上云 = **false** / 可推画像(inference) = **true** |
| **停留时长** | **采集器**算好再喂，MemoWeft 不碰"几点进/出窗口"这种平台细节 |
| **过度归因** | 先用**现有**归因刹车兜（一次 1 现象 / ≤2 原因 / 假设低置信封顶），dogfood 后再调 |

## 3. 做什么（In）

1. **`ingestObservations` 库级 API**（`src/index.ts` 导出）。通用可扩展 `Observation` 结构见交接文档 §5.1。
2. **observed 默认授权落地**：`{ allowLocalRead:true, allowCloudRead:false, allowInference:true }`。
3. **活动窗口 → Observation → `observed` 证据** 的映射。
4. **测试台活动窗口注入面板** + 复用现有面板验证"行为→画像"。

## 4. 不做 / 留骨架（Out）

- **真后台采集器**（`active-win` 长驻进程）→ 留骨架/契约（`src/perception/collectors/activeWindow.ts`）。
- 读窗口正文、设备/手机等其它源。
- "完整版"给 `LLMClient` 标 cloud/local → 留给"上本地模型"任务。
- ⚠️ **distill 覆盖语义雷**（交接文档 §9）：本版走路线 A（测试数据 cloud=true）**不触发**，**本版不处理**；但实现者**必须知道它在**（见 §7），别在本版顺手去跑 cloud=false 数据。

## 5. 关键实现点

### 5.1 摄入 API + 默认授权
- 签名 / 结构按交接文档 §5.1 草案（`kind` + `occurredAt` + `content` + `originId` + `meta`）。
- **默认授权落点**（⚠️ 碰核心，先方案问所有者）：observation 没显式给授权位时，套 observed 默认 `{local:true, cloud:false, inference:true}`；**显式给了就用给的**（测试台"允许上云"勾选 = 显式传 `allowCloudRead:true`）。建议落在**摄入层 / `config.observedDefaults`**，别改 `evidence.put` 的通用默认（参考问题1 的"选法 b"取向）。
- 幂等：用 `originId`（`evidence.put` 已支持）。

### 5.2 活动窗口映射
`{app, title, durationSec, time}` → `Observation{ kind:'active_window', content:"在 <app>（<标题>）停留约 <N> 分钟", meta:{app,title,durationSec}, occurredAt:time }` → `ingestObservations` → 落 `sourceKind='observed'` 证据。

### 5.3 测试台注入面板
- 分字段填 `app名 / 窗口标题 / 待了多久 / 什么时候` + 一个 **"这条测试允许上云"** 勾选（勾 = `allowCloudRead:true`，默认不勾 = false）。
- 提交 → 调 `ingestObservations`（可复用 / 改造现有 `/api/observe`）。

### 5.4 验证
注入 → `updateProfile`（distill→consolidate→attribute）→ 现有面板看：证据抽屉（标 `observed` 来源）、事件、画像、归因。

## 6. 验收标准

- [ ] `ingestObservations` 导出 + 单测：批量、`originId` 幂等、observed 默认授权正确（local=true / cloud=false / inference=true）；**显式传 cloud=true 时尊重**。
- [ ] 测试台能注入活动窗口观察（含"允许上云"勾选），`observed` 证据进证据层、面板标来源。
- [ ] **验收场景跑通**（走勾了上云的测试数据，所有者主观验收）：
  > 注入两三条"**凌晨 2–3 点在玩某游戏**"的活动窗口观察 + 跟 MemoWeft 说"**没睡好 / 好累**" → 触发更新 → MemoWeft 把"凌晨游戏"和"累"**联起来**、产出低置信假设"**可能熬夜导致**"，并在画像/归因面板体现。
- [ ] 真采集器**只留骨架**。
- [ ] `typecheck` / `test` / `build` 全绿；**不破坏现有**（对话证据行为不变）。
- [ ] 改完跑测试台落盘 `logs/run-*.jsonl` + `docs-sync`（STATE/LOG）。

## 7. ⚠️ 必看：distill 覆盖语义雷（本版不碰，但要知道）

见交接文档 §9 那条。一句话：`distill.ts:60-65` 现在把"被隐私过滤掉的 cloud=false 证据"也标"已覆盖、不重捞"。
- 本版走**路线 A**（测试数据 cloud=true）→ **不触发**。
- **别在本版顺手让 cloud=false 的 observed 走云端 distill** —— 那会踩雷（行为证据静默丢失）。
- 这颗雷留给"真采集器 / 上本地模型"那一步，连同 §6 折中 A/B 一起处理。

## 8. 影响面清单

| 动作 | 文件 |
|---|---|
| 🆕 新增 | `src/perception/ingest.ts`（+ 单测）、`src/perception/collectors/activeWindow.ts`（骨架） |
| ✏️ 改 | `src/index.ts`（导出）、`config.ts`（`observedDefaults`?）、测试台 `server.mjs` + `index.html`（注入面板） |
| ♻️ 复用 | `evidence.put`（已支持 observed + 授权位）、`updateProfile`、现有面板、`privacy.ts`（问题1） |
| 🚫 不碰 | 认知层算法、隐私过滤逻辑（问题1 已做）、distill 覆盖语义（§7 雷） |

## 9. 协作纪律

1. 碰核心（摄入层默认授权、`index` 导出）→ 先出方案 + 影响面 → 问所有者 → 再写。
2. 只做档1：真采集器留骨架；不碰 distill 雷（§7）；不做"完整版"。
3. 守路线 A：走"测试数据手动授权上云"，别让 cloud=false 的 observed 走云端（会踩雷）。
4. 改完跑 `regression-check` 落盘 + `docs-sync`。
5. 拿不准 / 方案与代码冲突 → 停下问所有者，别擅自决定。

---
*本清单由产品经理生成（2026-07-01），决策已与所有者定案。背景见同文件夹《DLA_4-A_交接_给coder.md》。*
