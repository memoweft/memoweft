# MemoWeft 三层边界（Core / Host / Plugin）

> 本文是三层归位的**边界口径**：每层负责什么、不负责什么、层与层怎么交互、现状离目标差多少。
> 定稿依据：[架构归位路线.md](./架构归位路线.md)（2026-07 用户拍板）。架构总览见 [architecture.md](./architecture.md)；用词遵循 [naming.md](./naming.md)。
> 改动任何一层的职责前，先改本文，再动代码。

---

## 1. 一句话定稿

> **MemoWeft Core 负责记忆怎么正确存在，Host 负责用户怎么使用和管理，Plugin 负责扩展能力。**

```txt
MemoWeft Core：记忆/认知内核（被 import 的库）
Host App：应用壳 —— 权限、配置、网页端、记忆管理、插件管理
Plugins：角色、工具、感知采集等外接能力
```

三层归位不是加功能，而是把已有能力放回该在的层：让 MemoWeft 从"开发现场"整理成清晰、稳定、可被宿主接入的记忆/认知框架。

---

## 2. 三层职责

### 2.1 MemoWeft Core —— 记忆引擎，不是完整应用

**负责**：

```txt
evidence / event / cognition 三层数据模型
updateProfile（distill → consolidate → attribute → index）
Conversation / Retriever / LLMClient 抽象
ingestObservations（观察摄入口）
召回、置信度、纠正、冲突、失效
导入导出 bundle、图谱 payload
受控记忆管理 API
schema migration、integrity check
```

**不负责**：

```txt
网页 UI、用户配置页面、记忆管理页面
真实窗口采集（Win32 sampler、采样循环、系统调用）
星瑶人格等任何角色/语气
插件系统、插件市场
完整宿主应用
```

核心原则：

> **Core 不长眼睛，也不长脸。** 它只接收标准输入，沉淀记忆，提供召回、更新和受控管理能力。

### 2.2 Host App —— 应用主体 / 运行壳

**负责**：

```txt
网页端、聊天入口、多会话
配置向导、模型配置、数据目录
权限管理、插件管理
记忆管理页面、备份迁移页面
后台画像更新调度与状态展示
调试面板
调用 MemoWeft Core
```

**不负责 / 不允许**：

```txt
绕过 Core 的记忆规则直接改底层数据
  （不直接写 SQL，不直接调 Sqlite*Store 完成关键管理行为）
定义记忆的可信规则（那是 Core 的纪律）
```

核心原则：

> **Host 负责调度、权限、用户体验和插件管理，但不能绕过 Core 的记忆规则直接修改底层数据。**

当前 testbench（网页端 + 记忆管理页）已是 Host 雏形，后续逐步迁移到 `apps/memoweft-host/`（见 §5）。

### 2.3 Plugins —— Host 的扩展能力，不是 Core 的一部分

插件分三类：

| 类别 | 举例 |
|---|---|
| **Experience Plugin**（体验/角色） | 星瑶、普通助手（experience-plain）、工作助理、宠物助手 |
| **Tool Plugin**（工具） | GitHub、文件、日历、浏览器、Shell |
| **Collector Plugin**（感知采集） | 窗口采集、睡眠、心率、手机使用记录 |

星瑶定位为 Experience Plugin——不进 Core，也不写死在 Host。同时要有 `experience-plain`（普通长期记忆助手）作为第二个体验插件，证明 MemoWeft 是通用用户认知框架，不是某个角色的专用记忆库。

**插件可以做**：

```txt
提交观察、请求读取认知
申请工具权限、请求用户确认一个假设
建议某条记忆过期
提供角色 prompt / 工具能力 / 采集能力
```

**插件不能做**：

```txt
直接删除 evidence、直接修改 cognition
直接修改 allowCloudRead
直接把 cognition 标记为 stable
绕过 Host 写入敏感观察
直接管理记忆库
```

核心原则：

> **插件只能请求，Host 审核，Core 执行记忆规则。**

---

## 3. 标准交互流

Core 不写死 Host，也不知道具体有哪些插件。Core 只认识标准输入输出（`UserMessage / Observation / MemoryCommand / MemoryQuery / RecallInput / UpdateProfileInput`——契约类型，目前仅 `Observation` 已落码，其余属目标态，现状见 §4）。Host 负责加载插件、判断权限、调用 Core。

```txt
Plugin → Host → Core
Core → Host → Plugin / UI / LLM
```

**示例一：感知采集**

```txt
Collector Plugin 采集窗口数据
  ↓
Host 检查权限和用户设置
  ↓
Host 调用 core.ingestObservation()
  ↓
Core 转换为 evidence
  ↓
后续 updateProfile / recall 可使用该 evidence
```

**示例二：记忆管理**

```txt
用户在 Host 记忆管理页面点击「标记失效」
  ↓
Host 做二次确认
  ↓
Host 调用 core.memory.invalidateCognition()
  ↓
Core 标记 invalidAt、记录原因、维护索引
  ↓
后续 recall 自动跳过该 cognition
```

---

## 4. 当前归位现状（2026-07-02 实测）

以下差距都有出处，按现状如实登记。修一条勾一条。

### 4.1 Core 主入口导出过宽 ✅（批次3 剥离导出 · 采集器迁插件收尾，2026-07-03）

主入口 `src/index.ts` 原导出 **139 个符号**，其中 **8 个属真实采集相关导出**（3 个函数 + 5 个纯类型，类型跟实现走）。批次3 先从主入口剥离、标 experimental 暂居库内；**本轮（2026-07-03）真实采集已【整体迁出 Core】到独立采集插件包 `plugins/collector-active-window/`（`@memoweft/collector-active-window` workspace）**，`src/perception/collectors/` 已删除：

| 符号 | 迁后位置 |
|---|---|
| `activeWindowToObservation`、`ActiveWindowSample`、`ActiveWindowCollector`（契约 + 样本→Observation 映射） | `plugins/collector-active-window/src/activeWindow.ts` |
| `createActiveWindowCollector`、`ActiveWindowCollectorOptions`、`RunningActiveWindowCollector`、`ActiveWindowEmit`、`ForegroundWindow`、`ForegroundSampler` | `plugins/collector-active-window/src/activeWindowCollector.ts` |
| `sampleForegroundWindowWin32`、`foregroundSamplerSupported` | `plugins/collector-active-window/src/win32Foreground.ts` |

采集参数（采样间隔 / 碎片阈值）也随之迁出：不再挂 Core `config.activeWindowCollector`，改由插件自持缺省（`DEFAULT_SAMPLE_INTERVAL_SEC` / `DEFAULT_MIN_DURATION_SEC`）。

Core 只保留**通用摄入口**：`ingestObservations`（generic `Observation` + observed 授权规则），照常从主入口导出、facade 挂 `core.ingestObservation`。窗口→Observation 的映射属采集插件知识、不在 Core。

**数据流已按路线 §3 接通（本轮）**：采集器插件 → `POST Host /api/observe`（Host 审核：采集总开关 `MEMOWEFT_HOST_COLLECTOR` + 强制剥 `allowCloudRead` 保 observed 不上云）→ `core.ingestObservation` → Core 落 observed 证据。采集插件不再喂旧 testbench、更不直穿 Core/Store。

### 4.2 testbench 是 Host 雏形，功能要分家

| 归属 | 功能 |
|---|---|
| **未来 Host**（用户功能） | 聊天、记忆透视、证据管理、多会话、备份恢复、恢复出厂、配置向导 |
| **留 testbench**（开发调试） | 手动触发 distill / consolidate / attribute / ask、config 热调、日志透视、观察注入 |

迁移后 testbench 回归开发调试用途，不再代表 Core，也不再承担用户入口。

### 4.3 Host 直接摸底层 ✅（批次3 已切换，2026-07-02）

testbench 原直接调 Store 实例完成管理操作的六处已切到受控 API（`createMemoryManagementAPI(stores)`，全部带 reason 落审计）：

| 端点 | 现状 |
|---|---|
| `/api/evidence/update` 授权位变更 | ✅ `memoryApi.updateEvidenceAuthorization`（零变更不落审计） |
| `/api/evidence/delete` | ✅ `memoryApi.removeEvidenceSafely({force:true})`（UI 已二次确认=用户执意删） |
| `/api/cognition/update` 标失效 | ✅ 请求只带 invalidAt 且非 null → `memoryApi.invalidateCognition` |
| `/api/cognition/delete` | ✅ `memoryApi.removeCognitionSafely`（审计 detail 只存元数据不存原文） |
| `/api/factory-reset` | ✅ 保留 store 直调（见下例外）+ 新增 `managementLog.clear()`（出厂=无历史，用户拍板） |

**登记在案的直调例外（不算越界）**：① evidence/cognition 的**内容编辑**（rawContent/summary/content 等字段）保留 `store.update` 直调——属开发调试编辑，非关键管理行为；② **恢复出厂**的批量清空保留 store 直调——整库擦除不是逐条管理行为，逐条走受控 API 反而会往正要清掉的审计表里再写行。

### 4.4 受控记忆管理 API 现状

| 状态 | 能力 | 现状说明 |
|---|---|---|
| ✅ 已有 | `createMemoWeftCore` 统一入口 | 批次2：`src/core/`，Host 优先经它调 Core |
| ✅ 已有 | `exportBundle` / `importBundle` / `validateBundle` | `src/portable/`，保真 + 幂等 + 校验；facade 挂 `core.portable.*` |
| ✅ 已有 | `buildMemoryGraph` | `src/graph/`，后端 payload；facade 挂 `core.graph.*` |
| ✅ 已有 | `ingestObservations` | `src/perception/ingest.ts`；facade 挂 `core.ingestObservation` |
| ✅ 已有 | `invalidateCognition` | 批次2：`core.memory.*`，带 reason + 审计 |
| ✅ 已有 | `updateEvidenceAuthorization` | 批次2：`core.memory.*`，审计 detail 记授权位 before/after |
| ✅ 已有 | `removeEvidenceSafely` / `removeCognitionSafely` | 批次2：有引用默认拒绝并返回影响面，`force` 才删；审计留痕 |
| ✅ 已有 | `mergeCognition` | 批次2：仅同 subject；链搬家去重、置信度重算、source 标失效不硬删 |
| ✅ 已有 | `archiveCognition` | 批次2：`archived_at` 幂等加列；召回/图谱默认跳过归档（invalid 同款待遇） |
| ✅ 已有 | `checkIntegrity`（库级 v1） | 批次2：孤儿溯源链检查，只报告不修 |
| ✅ 已有 | `management_log` 审计表 | 批次2：管理操作 op/target/reason/detail 留痕，只记真实变更 |
| ⬜ 没有 | 统一 schema migration runner | 现只有零散的幂等补列迁移（asked_at / archived_at 同款） |

---

## 5. 归位路线

```txt
拆边界 → 瘦 Core → 建 Host → 迁旧功能 → 做插件 → 清仓库 → 发 npm
```

| 步骤 | 做什么 |
|---|---|
| 拆边界 | 写清三层边界（本文）+ 登记现状差距 |
| 瘦 Core | `createMemoWeftCore` 统一入口、收瘦主入口导出、建受控记忆管理 API、补 migration / integrity |
| 建 Host | 新建 `apps/memoweft-host/`，承接聊天、配置、记忆管理、备份迁移 |
| 迁旧功能 | testbench 用户功能迁 Host，testbench 回归开发调试 |
| 做插件 | 定义最小插件契约；先 `experience-plain` + `experience-xingyao` 两个体验插件 |
| 清仓库 | 重写 README 第一屏、整理 docs、统一术语、修 examples |
| 发 npm | 满足发布条件后再发（发布是对外承诺，不是保存进度） |

分步细节、优先级与"当前不要做"清单，以 [架构归位路线.md](./架构归位路线.md) 为准。
