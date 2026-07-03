# apps/memoweft-host 迁移设计（架构归位·批次4）

> 本文是批次5 搬代码的施工蓝图。只写设计、不落代码。综合三份并行调研（A workspaces 形态 / B 迁移映射 / C Core 缺口），并已逐条核对到实际代码（`src/core/createCore.ts`、`src/index.ts`、`testbench/server.mjs`、`package.json`、`tsconfig*.json`、`.github/workflows/ci.yml`）。
> 一处关键校正：调研B 称"`Conversation` 未挂 facade，是批次5 前置缺口"——**与代码不符**。实际 `createCore.ts:120/193` 已挂 `core.handleConversationTurn`，且支持 `conversationId`/`systemPrompt`/`seedTurns`。**聊天回合不是缺口**（调研C 判定正确）。真正要补 Core 的只有三项：列表读、health、factory-reset。全文以此为准。

---

## 0. 目标与定案

**一句话形态**：`apps/memoweft-host` 是从零起壳的干净【用户产品】运行壳（HTTP 服务 + 用户界面），经公开入口 `import 'memoweft'` 调 Core，把 testbench 里的用户功能逐个搬过来；testbench 原样保留作开发调试。

**用户已拍板、按此设计、不推翻的两条方向**：

1. **Host 形态 = 新建干净骨架、渐进迁移**。`apps/memoweft-host` 从零起壳，把 testbench 的【用户产品功能】逐个搬进来；testbench 原样保留作【开发调试】。产品壳干净、调试代码不混入。
2. **仓库结构 = npm workspaces monorepo**。根仓 = Core 包 `memoweft`，`apps/memoweft-host` 作独立 workspace 包，经公开入口 `import 'memoweft'`（`createMemoWeftCore` 等）调 Core，**不直接相对 import `../../src`**。

**贯穿全程的红线**（每步验收都要对）：

- 零 runtime 依赖：Host 的 `dependencies` 里除 `memoweft`（workspace-link）外不得有任何 registry 运行时包；HTTP/存储/测试全走 Node 内置（`node:http`/`node:sqlite`/`node:test`）。
- Node ≥ 24：延续 testbench 的"Node 原生跑 `.ts` + `node:sqlite`"能力，**不引 tsx/ts-node**。
- 三绿：typecheck / test / build 全过（含 Host 子包）。
- observed 不上云：摄入观察默认不上云的纪律不变。
- 命名遵 `docs/naming.md`：不说"真正理解你"、MemoWeft 不用"她"。

---

## 1. 最终仓库结构

### 1.1 目录树（扁平：Core 在根，Host 在 apps/）

```txt
DLA_rebuild/                         ← 根仓 = Core 包 memoweft，同时是 workspaces 根
├─ package.json                      ← name:"memoweft"，加 "workspaces":["apps/*"]，加显式 exports
├─ tsconfig.json                     ← Core base（noEmit 类型检查档，被 Host extends）
├─ tsconfig.build.json               ← 只管 Core 的 src → dist（不纳入 apps）
├─ src/                              ← Core 源码（记忆引擎）
├─ dist/                             ← Core 构建产物（Host import 'memoweft' 解析到这里）
├─ tests/                            ← Core 测试
├─ testbench/                        ← 保留：开发调试台（分家后回归调试，见 §5）
├─ docs/
│   ├─ boundaries.md
│   └─ host-migration.md            ← 本文
└─ apps/
    └─ memoweft-host/                ← 独立 workspace 包（用户产品壳）
        ├─ package.json              ← name:"@memoweft/host"，private:true，deps:{"memoweft":"*"}
        ├─ tsconfig.json             ← extends ../../tsconfig.json，重写 include 指自己的 src/tests
        ├─ src/
        │   ├─ server.ts             ← 入口（对标 testbench/server.mjs 的用户功能子集）
        │   ├─ routes/              ← 各端点 handler（按 §2 表分模块）
        │   └─ web/                 ← 干净 index.html（只含用户模式）+ 前端资源
        └─ tests/
            └─ *.test.ts
```

**为什么扁平、不挪 `packages/core/`**：根仓 `name` 已是 `memoweft`，根既是 Core 包本体、又是 workspaces 根，这是合法的。挪进 `packages/` 要改一大堆路径，收益为零。遵"好实现优先"，保持扁平、改动最小。

### 1.2 根 package.json 要点（Core 包 + workspaces 根）

- **加 workspaces 字段**：`"workspaces": ["apps/*"]`。`npm install` 会把 Host 里写的 `"memoweft":"*"` 软链回根仓自己，不去 registry 下载——这是"经公开入口 import 却不打真依赖"的机制底座。
- **补显式 `exports`**（现状只有 `main`/`types`，无 `exports`）。**采用路线甲：Host 引 dist**：
  ```jsonc
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "default": "./dist/index.js"
    }
  }
  ```
  这样 `import 'memoweft'` 解析到编译产物 `dist/index.js`，和"未来真发 npm 后外部宿主引 memoweft"是同一条路径——开发期就 dogfood 了真实发布形态，exports 配错/漏导出当场暴露。
- **加一个 Core 侧 watch 脚本**抹平"改 Core 要重 build"的内循环：`"dev:core": "tsc -p tsconfig.build.json --watch"`。Host 开发时后台挂着，改完 Core 自动出 dist。
- `dependencies` 保持空、`engines.node >=24` 不变。

**为什么选路线甲（引 dist）而非路线乙（引 src）**：① boundaries.md §2.2 要求 Host 经公开入口、不相对 import `../../src`；路线甲让"公开入口 = 已 build 的 dist"，边界最硬、最不容易被绕过。② 终态是发 npm，开发路径 = 发布路径，提前暴露 exports 问题。③ 路线乙要给 exports 配指向 `.ts` 的条件，会污染"发 npm"语义（`files` 只打包 `dist/`，真发布拿不到 `src/`），等于开发态/发布态两套 exports 容易漂移。代价（Host 开发前要先 build Core）用 `dev:core` watch 抹平即可。

> **待作者拍板①**：是否给 exports 加 `development` 条件双轨（平时指 src 免 build、发布指 dist）。**建议先不上**——双轨有"两套导出漂移"和"dev 要带 `--conditions=development` 隐性魔法"两个成本。纯路线甲跑顺再评估。

### 1.3 Host package.json 要点

```jsonc
{
  "name": "@memoweft/host",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "engines": { "node": ">=24" },
  "scripts": {
    "dev": "node --watch src/server.ts",
    "start": "node src/server.ts",
    "typecheck": "tsc -p tsconfig.json",
    "test": "node --test \"tests/**/*.test.ts\""
  },
  "dependencies": { "memoweft": "*" },
  "devDependencies": {}
}
```

- `name` 建议带 scope `@memoweft/host`（和未来 `@memoweft/plugin-*` 成体系；boundaries.md §2.3 里 Plugin 也是 `@memoweft/` 风格）。无 scope 的 `memoweft-host` 功能等价。**这是命名口径，最终归作者，遵 `docs/naming.md`**。
- `private:true` **关键**：Host 永不发 npm（只 Core 包发），从机制上挡误发。
- `"memoweft":"*"` 而非 `"workspace:*"`：**本仓用 npm（package-lock.json + npm ci），npm 不认 `workspace:` 前缀**。调研B 步0 里写的 `"memoweft":"workspace:*"` 是笔误，落码时以 `"*"` 为准。
- `dependencies` 只允许 `memoweft` 一个键——守零 runtime 依赖红线。
- 入口 `src/server.ts`（`.ts` 跟上 Core 的 TS 化，Node 24 原生能跑）。初期纯源码跑，**不配 `build`**（Host `private`、不分发，无需产物化）。

> **待作者拍板②**：Host 是否要 `build`（产物化壳）。初期建议纯源码跑、省略 `build`；未来若要可分发打包再加。

### 1.4 Host tsconfig 要点

```jsonc
{
  "extends": "../../tsconfig.json",
  "compilerOptions": {
    "rootDir": ".",
    "noEmit": true
    // allowImportingTsExtensions / rewriteRelativeImportExtensions 从根 base 继承
    // → Host 内部相对 import 也带 .ts 后缀，和 Core 源码风格一致
  },
  "include": ["src", "tests"]
}
```

- 类型解析**靠 workspace 自然解析**：根 `exports.types` 指 `dist/index.d.ts`，Host 经 `dependencies:{"memoweft":"*"}` 软链到根仓，`moduleResolution:NodeNext` 会像解析普通 npm 包一样找到 `node_modules/memoweft` 软链 → 读 `exports.types`。**不用 project references、不用 paths**（paths 直连源码 = 绕包边界，精神违红线，排除）。
- **前提**：`dist/` 得先 build 出来。build 一次后 `.d.ts` 就在，类型就通。
- **落码盯死的坑**：`extends` 只继承 `compilerOptions`，**`include`/`exclude` 不继承**。Host 必须重写 `include` 指自己的 `src`/`tests`，否则会继承根的 include 去编译根的 src（错乱）。

### 1.5 构建与 CI 改动

- **`tsconfig.build.json` 不纳入 apps**：它只管 Core 的 `src → dist`（现 `exclude` 已排 testbench）。Host 是 `private` 独立包、不发布，其构建各管各的。守住"Core 包发 npm 时 dist 绝不掺 Host"。
- **CI 顺序必调**（这是硬约束）：路线甲下 Host 的 typecheck/test 依赖 Core 的 `dist/*.d.ts`，所以 **Core build 必须前置**。现 CI 是 typecheck→test→build，加 Host 后调成：
  ```txt
  npm ci
  → npm run typecheck                        (Core 自身，noEmit)
  → npm test                                 (Core 测试)
  → npm run build                            (出 Core dist —— Host 的依赖前置)
  → npm run typecheck -w @memoweft/host      (Host 类型，此时能解析到 dist 类型)
  → npm test -w @memoweft/host               (Host 测试)
  ```
  也可用 `--workspaces --if-present` 自动覆盖未来的插件包（推荐，免维护清单），但**顺序上 Core build 仍须在 Host 步骤之前**。
- `npm ci` 本身不改：workspaces 根跑 `npm ci` 一次装齐根 + 所有子包并建软链；子包零外部依赖，秒级。

> **待作者拍板③**：CI 是否加 lockfile 机检卡"无新增 runtime 依赖"（扫 `package-lock.json` 出现非 dev 的 registry 包就 fail）。**建议先靠 review + boundaries.md 口径守**，机检作后续强化。

---

## 2. testbench → Host 功能迁移映射表

> testbench 前端 `index.html` 已"预分好家"——三模式共壳（`mode-user` / `mode-wizard` / `mode-memory` 进 Host，`mode-dev` 留 testbench）。搬的时候按模式整块拎走即可。归属口径钉死于 boundaries.md §4.2。
> "调 Core 能力"列 = 搬到 Host 后应经 `import 'memoweft'` 调哪个能力。**⚠缺口**标记的是 facade 现无、需批次5 步0 先补（详见 §3）。

### A. 进 Host 的端点（用户产品，共 19 个）

| # | 端点 | 方法 | 调 Core 能力 | 前端 UI |
|---|---|---|---|---|
| 1 | `/api/chat` | POST | `core.handleConversationTurn`（**已在 facade ✓**，含 `conversationId`/`systemPrompt`/`seedTurns`） | ✅ `#chat` + `send()` |
| 2 | `/api/health` | GET | **⚠缺口C** `core.health()`（现 testbench 直调 `loadLLMConfig`/`loadEmbedConfig`） | ✅ 首启门（进向导 or 聊天） |
| 3 | `/api/bg-status` | GET | 无（Host 自建调度器的状态面：`profileUpdating`/`bgTimer`/`bgLast`） | ✅ 顶栏 `#bgstatus` |
| 4 | `/api/chat-history` | GET | 无（Host 读 `run-*.jsonl` 会话日志） | ✅ 历史轮渲染 |
| 5 | `/api/cognition` | GET | **⚠缺口A** `core.memory.listCognitions`（现 `cogStore.all()+sourcesOf+effectiveConfidence`） | ✅ 记忆管理页 + 友好版抽屉 |
| 6 | `/api/cognition/update`（标失效支） | POST | `core.memory.invalidateCognition`（**已在 facade ✓**）；**内容编辑支不搬**（留 testbench） | ✅ 记忆管理页 + S1 气泡"改" |
| 7 | `/api/cognition/delete` | POST | `core.memory.removeCognitionSafely`（**已在 facade ✓**） | ✅ 记忆管理页 + S1 气泡"删" |
| 8 | `/api/evidence` | GET | **⚠缺口A** `core.memory.listEvidence`（现 `store.all()`） | ✅ 记忆管理页证据视图 |
| 9 | `/api/evidence/update`（授权支） | POST | `core.memory.updateEvidenceAuthorization`（**已在 facade ✓**）；**内容编辑支不搬** | ✅ 证据授权开关 |
| 10 | `/api/evidence/delete` | POST | `core.memory.removeEvidenceSafely({force:true})`（**已在 facade ✓**） | ✅ 证据删除 |
| 11 | `/api/refresh`（用户版"立即整理"） | POST | `core.updateProfile`（**已在 facade ✓**）；**开发者版 `genProfile` 留 testbench**（分歧点1） | ⚠ 用户版"整理记忆"按钮 |
| 12 | `/api/reset` | POST | 无（Host 多会话编排：新开会话） | ✅ 侧栏"＋新会话" |
| 13 | `/api/sessions` | GET | 无（Host 扫 `run-s-*.jsonl` 列会话） | ✅ 侧栏会话列表 |
| 14 | `/api/session/open` | POST | 无（Host 切会话 + 种子重建，`seedTurns` 由 Host 从日志读回） | ✅ 点条续聊 |
| 15 | `/api/session/archive` | POST | 无（Host 给日志文件加 `.archived` 后缀） | ✅ 会话条归档 |
| 16 | `/api/export-bundle` | GET | `core.portable.exportBundle`（**已在 facade ✓**） | ✅ 备份块"导出记忆包"（**需从设置抽屉拆出**） |
| 17 | `/api/import-bundle` | POST | `core.portable.importBundle`（**已在 facade ✓**） | ✅ 备份块"导入记忆包"（**需拆出**） |
| 18 | `/api/factory-reset` | POST | **⚠缺口D** `core.memory.resetSubject`（现逐条 `store.remove`+`removeBySubject`+`managementLog.clear()`+`indexAll([])`） | ✅ S0 记忆抽屉底部软文案"重新开始"（**用户已可达**；批次5 只收口后端逻辑） |
| 19 | `/api/gen-env` | POST | 无（Host 纯拼 `.env` 文本，apiKey 绝不落盘，不碰记忆） | ✅ 配置向导第4步 |

### B. 留 testbench 的端点（开发调试，共 13 个 + 静态）

| # | 端点 | 方法 | 说明 |
|---|---|---|---|
| 20 | `/api/distill` | POST | 手动整理事件（§4.2 明列留调试） |
| 21 | `/api/consolidate` | POST | 手动增量消化（UI 无独立按钮，端点归调试） |
| 22 | `/api/attribute` | POST | 手动归因 |
| 23 | `/api/ask` | POST | 手动主动询问 |
| 24 | `/api/event` | GET | 看事件+覆盖证据 id（内部结构透视） |
| 25 | `/api/observe` | POST | 观察注入 |
| 26 | `/api/observe-window` | POST | 活动窗口观察注入 |
| 27–28 | `/api/config` | GET/POST | config 热调读写（47 旋钮） |
| 29 | `/api/config/reset` | POST | 恢复默认参数（旋钮还原，非清数据） |
| 30 | `/api/logs` | GET | 日志透视（renderXray 源） |
| 31–32 | `/api/seed-progress` | POST/GET | dogfood 灌数据进度 |
| 33 | `/config-meta.js` | GET | 47 项参数元数据（仅旋钮用） |

### C. 静态资源/服务端非 /api 路由

| 路由 | 归属 | 说明 |
|---|---|---|
| `/` `/index.html` | 两边各建 | Host 建干净 index.html（只含用户模式）；testbench 保留现有整份 |
| `MEMOWEFT_EXPERIENCE_UI=off` 拦 listen | 进 Host | 部署选项"纯库/带界面"开关，搬进 Host 启动逻辑 |

### 两处需 PM 拍板的归属分歧（诚实标出）

- **分歧点1 · `/api/refresh`（#11）用户版 vs 开发者版并存**。§4.2 只把手动 distill/consolidate/attribute/ask 明列留调试，`updateProfile`（整套画像更新）不在那份清单里，且用户版聊天后有"整理记忆"按钮。**建议**：用户版"立即整理记忆"进 Host（正常用户动作），开发者版 `genProfile` 留 testbench，两边各建自己的端点。**请作者确认**。
- **分歧点2 · 导出/导入记忆包当前只在"仅开发者可见"的设置抽屉里**（`index.html:1080-1122`；抽屉入口按钮 `#btnSettings` 只在 `body.mode-dev` 显示，被隐藏的是入口按钮、不是面板 DOM 本身）。§4.2 明确备份是用户产品。搬 Host 时**必须把导出/导入这两块从开发者设置抽屉摘出**、放到用户可达位置（建议：记忆管理页顶部"数据"入口或独立一页）。**注意区分**：恢复出厂（factory-reset）**已有用户可达入口**——S0 记忆抽屉底部有软文案版"重新开始"（`index.html:822/1633/1852`，`doFactoryReset` 被两个入口复用）。批次5 对恢复出厂只需把后端逻辑经 `core.memory.resetSubject` 收口，UI 沿用现有软入口即可、无需新造。

---

## 3. Core 公开面缺口与补法

判定口径：boundaries.md 一句话定稿——**Core 管"记忆怎么正确存在"，Host 管"用户怎么使用和管理"（HTTP/会话/UI/调度/配置）**。

### 3.1 要补进 Core 的缺口（批次5 步0 必做，不补 Host 就够不着 store、只能退回直穿底层，违反方向②）

| 缺口 | 现状（testbench 怎么调） | 影响哪步 | 建议签名 | 优先级 |
|---|---|---|---|---|
| **A · 只读列取**（列证据/认知/事件，带溯源+有效置信） | `store.all()`；`cogStore.all()+sourcesOf(id)+effectiveConfidence(c)`；`eventStore.all()+evidenceOf(id)`。三者都不在 facade——`core.memory` 只有写/管理，没有列表读 | 步3（记忆管理页）、步6（友好版抽屉） | 见下 | **必补，最硬** |
| **C · 健康检查** | `try{ !!loadLLMConfig() }catch{}` + 同款 embed | 步1（首启决定进向导 or 聊天） | `core.health(): { llmReady: boolean; embedReady: boolean }` | 必补，轻量 |
| **D · 恢复出厂**（整库/整 subject 擦除） | 逐条 `store.remove` + `eventStore.removeBySubject` + `cogStore.removeBySubject` + `managementLog.clear()` + `retriever.indexAll([])` | 步5 | 见下 | 必补 |

**缺口A 建议签名**（挂 `core.memory` 读侧，把"读+组装"收进 facade，不新写记忆逻辑；`effectiveConfidence`/`sourcesOf`/`all`/`evidenceOf` 都已存在）：
```ts
core.memory.listEvidence(input?: { subjectId?: string }): Evidence[]
core.memory.listCognitions(input?: { subjectId?: string }):
  Array<Cognition & { sources: EvidenceLink[]; effectiveConfidence: number }>
core.memory.listEvents(input?: { subjectId?: string }):
  Array<Event & { evidenceIds: string[] }>   // 对应 /api/event（供友好版/透视）
```

**缺口D 建议签名**（整库擦除危险且要保证"清数据+清审计+清索引"顺序一致，交 Host 逐个直调容易漏一步——尤其 `indexAll([])` 和 `managementLog.clear()`；收进 facade 收口成单个方法更稳。**两点实现约束**：① evidence 层无 `removeBySubject`，内部仍是 `all()`+逐条 `remove(id)`（单 subject 全清）——或顺带给 `EvidenceStore` 补个 `removeBySubject`；② 现 `/api/factory-reset` 未包事务，收口时可考虑加事务包裹，但是否真"原子"以实现为准，契约上别过度承诺）：
```ts
core.memory.resetSubject(input: { subjectId?: string; reason?: string }):
  { evidenceRemoved: number; eventRemoved: number; cognitionRemoved: number; auditRemoved: number }
// 内部：清三层 + 清索引(retriever.indexAll([])) + 清审计(managementLog.clear())。
// 会话窗口的 newSession() 是 Host 的事，不进此方法。
```
> **红线提醒**：`resetSubject` 破坏性极强，MEMORY 有"危险操作用临时库验证"的教训（曾误删）。批次5 落这段时**必须用副本库验证**，dogfood 前备份 db。

### 3.2 待作者拍板才决定补不补的缺口

| 缺口 | 说明 | 建议 |
|---|---|---|
| **B · 内容编辑**（改 summary/rawContent/content/confidence 等） | boundaries.md §4.3 已登记为"允许的直调例外"（内容编辑属开发调试）。若 Host 产品壳**保留**"用户手动改记忆文案/改置信度"这个功能，facade 需给入口；若产品壳只做删/标失效，则**不需要补** | **先问作者**：Host 记忆管理页要不要带"编辑文案"。别默认补 |

若拍板要补，建议签名：
```ts
core.memory.editEvidenceContent(input: { evidenceId: string; summary?: string; rawContent?: string }): Evidence | null
core.memory.editCognition(input: { cognitionId: string; content?: string; confidence?: number; credStatus?: string; scope?: string }): Cognition | null
```

### 3.3 判定为 Host 自实现的项（附理由，防批次5 误塞进 Core）

| 能力 | 为什么属 Host |
|---|---|
| **gen-env（配置向导）** | 输入 9 个 env 值 + 一个布尔 → 输出一段 `.env` 文本，全程字符串拼接、不碰 evidence/cognition。"apiKey 绝不落盘"是这个 HTTP handler 的纪律，Host 照搬即可；Core 不该知道 baseUrl/apiKey 这些宿主部署配置。boundaries §2.2 明列"配置向导、模型配置"是 Host 职责 |
| **多会话（列表/新开/续聊/归档）** | boundaries §2.2 明列"多会话"是 Host 职责。列表 = 扫 `run-s-*.jsonl` 文件，归档 = 加 `.archived` 后缀，纯文件系统编排。续聊的 `seedTurns` 由 Host 从自己的运行日志读回、拼好传给 Core。**Core 内部那个 `conversations = new Map()` 故意不暴露枚举/清除**（`createCore.ts:164`）——会话册是 Host 的权威数据源，Core 的 Map 只是活跃实例窗口缓存。**批次5 别想着从 Core 掏会话列表** |
| **后台画像更新调度（攒批/防抖/单飞锁/fire-and-forget）** | `updateProfile` 本体已在 Core（`core.updateProfile`），但"什么时候调、攒够几条调、空闲多久调"是调度策略。boundaries §2.2 明列"后台画像更新调度与状态展示"是 Host 职责。Host 自建 scheduler（持 `profileUpdating`/`bgTimer`/`bgLast`/`pendingSinceUpdate`），内部调 `core.updateProfile()`。`/api/bg-status`（#3）是这个 scheduler 的状态面 |
| **chat-history / 运行日志落盘编排** | `createRunLogger` 已从 `index.ts` 公开导出，但"读 `run-*.jsonl` 渲染历史轮"是 Host 的持久化编排 |

### 3.4 已确认够用、无需补的项（澄清三方分歧）

- **聊天回合**：`core.handleConversationTurn` 已在 facade（`createCore.ts:120/193`），签名覆盖 testbench 用法——`message`/`originId`/`conversationId`/`systemPrompt`/`seedTurns` 全有，返回 `TurnOutcome{reply,storedEvidence,recall,llmCalls,error}` 满足落盘 `appendTurn`。**调研B "Conversation 未挂 facade" 的说法作废**。
- **观察注入**：`/api/observe`（手动一句话）可走 `core.ingestUserMessage({content, sourceKind:'observed', originId})`（facade 支持 `sourceKind`，`createCore.ts:62`）——但这属留 testbench 的调试端点，不搬 Host，仅记录够用。
- **备份/图谱**：`core.portable.*` / `core.graph.*` 已在 facade。

---

## 4. 迁移批次拆解（批次5 拆成 步0 + 6 小步）

> 按"依赖 + 风险"排序。前置缺口（§3.1）必须在**步0**补齐，否则步1 起就撞红线（要么直接 import `../src`、要么够不着 store）。每步给验收口径，并注明 testbench 对应功能去留。

### 步0（前置，不算迁移）：补 Core 公开面 + 起 workspaces 骨架

- **做什么**：① 补 §3.1 三个缺口——`core.memory.listEvidence/listCognitions/listEvents`、`core.health()`、`core.memory.resetSubject()`，从 `index.ts` 走公开导出。② 建 `apps/memoweft-host` 作独立 workspace 包（package.json + tsconfig + 空 `src/server.ts`），根 package.json 加 `"workspaces":["apps/*"]` 和显式 `exports`，加 `dev:core` watch 脚本。③ 调整 CI 顺序（Core build 前置）。
- **依赖**：无。**建议 Core 补全与起骨架各一个 PR、并列为步0**（调研B 的建议，降耦合）。
- **验收**：`npm run build`（出 Core dist）后，`npm run typecheck -w @memoweft/host` 能 `import { createMemoWeftCore } from 'memoweft'` 并拿到上述能力；根仓三绿不破；`dependencies` 仍空；lockfile 无新增 runtime 包。
- **本地内循环硬前置（高频坑）**：路线甲下 Host 类型/运行都靠 Core 的 `dist/`。**clone 后首次、或每次改完 Core，必须先 `npm run build`（或后台挂 `dev:core` watch）再 typecheck/跑 Host**，否则 Host 因缺 `dist/*.d.ts` 报"无法解析 'memoweft'"。这条要写进 Host 的 onboarding 说明，别让人踩。
- **testbench 去留**：不动。testbench 继续照旧跑。`resetSubject` **用副本库验证**（红线）。

### 步1：空壳骨架 + 健康检查 + 最小聊天（单会话）

- **搬**：`/api/health`（#2，用 `core.health()`）、`/api/chat`（#1，用 `core.handleConversationTurn`）、`mode-user` 聊天列（`#chat`+`send()`）、`/api/chat-history`（#4）、`/api/bg-status`（#3）+ 后台调度器。**先单会话**，不搬多会话。
- **依赖**：步0 的 `core.health` + 骨架。
- **验收**：Host 起在自己端口（**非 7888**，避与 testbench 撞）、配好 `.env` 能发一句话得到回复、证据落库、后台状态条转；testbench 聊天照常。
- **testbench 去留**：聊天/health/bg-status/chat-history 在 testbench **保留**（调试仍要用），Host 是新建的干净副本，不删 testbench。

### 步2：配置向导（依赖低，早搬）

- **搬**：`/api/gen-env`（#19，Host 自拼文本）、`mode-wizard` 整块、`MEMOWEFT_EXPERIENCE_UI=off` 部署开关（#35）。gen-env 纯文本拼接、不碰 Core、风险低。
- **依赖**：步1 骨架。
- **验收**：Host 首启（无 `.env`）自动进向导，4 步填完生成 `.env` 文本、**key 不落盘**；`EXPERIENCE_UI=off` 时 Host 不起网页（纯库模式）。
- **testbench 去留**：testbench 保留自己的向导用于调试。

### 步3：记忆管理页（依赖缺口A + `core.memory.*`）

- **搬**：`mode-memory` 整块 + `/api/cognition`（#5）、`/api/evidence`（#8）读取、`/api/cognition/update` 标失效支（#6）、`/api/cognition/delete`（#7）、`/api/evidence/update` 授权支（#9）、`/api/evidence/delete`（#10）。**只搬"标失效/授权变更/删除"这些走受控 API 的支，内容编辑那支不搬**（留 testbench；除非分歧点/缺口B 拍板要）。
- **依赖**：步0 缺口A（列表读）。
- **验收**：Host 记忆管理页能列认知/证据、标失效、改授权、删除，每次删改在 `management_log` 留痕；用户看不到 rawContent/summary 原始编辑框（那是调试）。
- **testbench 去留**：内容编辑支 + 记忆管理调试视图在 testbench **保留**。

### 步4：多会话

- **搬**：`/api/reset`（#12）、`/api/sessions`（#13）、`/api/session/open`（#14）、`/api/session/archive`（#15）、侧栏会话列表 UI。`seedTurns` 由 Host 从运行日志读回拼好传 `core.handleConversationTurn`。
- **依赖**：步1（会话日志已在写）。
- **验收**：Host 能开新会话、列历史会话、点条续聊（种子重建上下文）、归档（文件加后缀不删数据）。
- **testbench 去留**：testbench 保留自己的多会话调试。

### 步5：备份恢复 + 恢复出厂（从开发者抽屉拆出，放用户可达处）

- **搬**：`/api/export-bundle`（#16）、`/api/import-bundle`（#17）、`/api/factory-reset`（#18，用 `core.memory.resetSubject`，**不让 Host 自己遍历 store**）。**导出/导入 UI 从开发者设置抽屉摘出、放用户可达位置；恢复出厂 UI 沿用 S0 抽屉现有软入口**（分歧点2）。
- **依赖**：步0 缺口D（resetSubject）、步3（记忆管理页作为"数据"入口的宿主）。
- **验收**：Host 用户能导出 `.bundle.json`、dryRun 试算后 merge 导入、恢复出厂清全部数据（含审计表）；二次确认文案齐；导入非法包被拦。**恢复出厂用副本库验证、dogfood 前备份 db**（红线）。
- **testbench 去留**：testbench 可保留一份备份/出厂用于调试，但 Host 侧必须用户可达。

### 步6：S0/S1 用户正门收尾（可并入步1 或独立）

- **搬**：`#memPill`"它记住我的事"胶囊 + S0 记忆抽屉（`openMem`）+ S1 记忆气泡（`weaveMemNote`，后台消化出新认知就地织进聊天流）+ 用户友好版认知渲染（`refreshCognition` 的 user 分支，用缺口A 的 `listCognitions`）+ "立即整理记忆"用户按钮（#11，**若分歧点1 拍板进 Host**，用 `core.updateProfile`）。
- **依赖**：步1（聊天流）、步3（缺口A 读取）。
- **验收**：用户聊天后胶囊数字更新、点开看友好版记忆、后台整理出新认知时聊天流冒出记忆气泡、可就地改/删。
- **testbench 去留**：S0/S1 属用户正门，testbench 不需要，Host 独有；testbench 保留开发者透视（renderXray）。

---

## 5. testbench 与 Host 最终分工

**分家后各自定位**：

| | Host（`apps/memoweft-host`） | testbench |
|---|---|---|
| **面向** | 用户产品 | 开发者调试 |
| **入口** | `src/server.ts`，自己端口 | `server.mjs`，7888 |
| **界面** | 干净 index.html（只 `mode-user`/`mode-wizard`/`mode-memory` + S0/S1 正门） | 现有整份（含 `mode-dev`、47 旋钮、renderXray、seed 进度条） |
| **含端点** | §2 表 A 的 19 个用户端点 | §2 表 B 的 13 个调试端点 + 静态 `config-meta.js` |
| **调 Core 方式** | 只经 `import 'memoweft'`（公开入口） | 保留现状：直接相对 import `../src/*`（调试台特权，不受 Host 边界约束） |
| **手动 distill/consolidate/attribute/ask** | 无 | 保留（开发者工具区） |
| **config 热调 / 日志透视 / 观察注入** | 无 | 保留 |
| **恢复出厂/备份** | 有（用户可达，经 facade） | 可保留一份（调试用，直调） |

**一句话**：testbench 回归"开发现场"——它可以直穿底层、按旋钮、看内幕、手动跑管线各环节，服务于开发调试；Host 是提炼出来的、只经公开入口、只含用户功能的干净产品壳。两者**各连各的库或共享库由作者定**（testbench 现用独立库 `testbench-evidence.db`，Host 应有自己的数据目录，互不污染）。

> **待作者拍板④**：Host 与 testbench 是各用独立库还是共享一个库。建议各用独立库（testbench 现就独立），避免调试数据污染产品库、也守 MEMORY"冒烟数据必清"的卫生。

---

## 6. 风险与开放问题

### 6.1 workspaces 对三条红线的影响与守法

| 红线 | 影响 | 守法 |
|---|---|---|
| **零 runtime 依赖** | workspaces 会在 lockfile 加 workspace-link 条目，但那是软链不是 registry 包 | ① Host `dependencies` 只允许 `memoweft` 一键，人工 review 卡；② lockfile 体检：除 `@types/node`/`typescript`/`undici-types`（全 `dev:true`）+ workspace 软链外，不应出现新的 `resolved: registry.npmjs.org` runtime 包；③ 可选 `npm ci --omit=dev` 冒烟：真零依赖则去掉 devDep 也能起 Host |
| **Node ≥ 24** | Host 延续 testbench 的"Node 原生跑 `.ts` + `node:sqlite`"，`node --watch src/server.ts` 起，**不引 tsx** | CI 继续锁 Node 24；Host `engines.node >=24` |
| **三绿** | Host typecheck/test 依赖 Core dist，**顺序错就红** | CI 顺序调成 Core build 前置（§1.5）；`--workspaces --if-present` 覆盖子包 |

### 6.2 运行时链路的一个确认项（批次5 验证）

Host 用 `import 'memoweft'` 在 Node 运行时解析到 `dist/index.js` 后，dist 里的相对 import 已被 `rewriteRelativeImportExtensions` 改写成 `.js`，是正常 ESM，Node 直跑没问题。"Node 原生跑 TS"只用在 Host **自己**的 `.ts` 入口那一层，不用它去解析 Core 内部——这是路线甲（引 dist）比路线乙（引 src）运行时更稳的附带好处。**批次5 步1 跑通第一句聊天时即可确认这条链路**。

### 6.3 需作者后续拍板的点（汇总）

| # | 待拍板 | 建议 | 卡哪步 |
|---|---|---|---|
| ① | exports 是否上 `development` 条件双轨 | 先不上，纯路线甲跑顺再评估 | §1.2 / 全局 |
| ② | Host 是否要 `build`（产物化） | 初期纯源码跑，省略 `build` | §1.3 |
| ③ | CI 是否加 lockfile 机检 | 先靠 review + boundaries 口径 | §1.5 |
| ④ | Host 与 testbench 各用独立库还是共享库 | 各用独立库 | §5 |
| ⑤ | Host `name` 用 `@memoweft/host` 还是 `memoweft-host` | 建议带 scope（成体系），归作者、遵 naming.md | §1.3 |
| **分歧点1** | `/api/refresh` 用户版进 Host、开发者版留 testbench？ | 是（用户版"立即整理"进 Host） | 步6 |
| **分歧点2** | 导出/导入从开发者抽屉拆到用户可达处的落点（恢复出厂已有 S0 软入口，只需收口后端） | 记忆管理页顶部"数据"入口或独立一页 | 步5 |
| **缺口B** | Host 记忆管理页要不要带"编辑文案/改置信度" | 先问作者再定补不补 facade（§3.2） | 步3 |

### 6.4 施工纪律（红线复述）

- `resetSubject`（恢复出厂）**必须用副本库验证**，dogfood 前备份 db——MEMORY 有误删事故教训。
- 冒烟数据必清；Host 与 testbench 数据目录隔离。
- Host 只经 `import 'memoweft'` 调 Core，**任何 `import '../../src/*'` 都算越界**，review 卡死。
- 命名遵 `docs/naming.md`：Host 文案不说"真正理解你"，MemoWeft 不用"她"。

### 6.5 已知限制（v1 单人单宿主无碍，多 subject 化时要处理 · 批次5 步0 审查记录）

- **恢复出厂清向量索引是整表粒度**：`core.memory.resetSubject` 内部 `retriever.indexAll([])` 会 `DELETE FROM vectors` 清【所有 subject】的向量，不是只清被 reset 的 subject。v1 单 subject 下"全表 = 单 subject 表"、无误伤；多 subject 化时要换成 subject 粒度清索引（Retriever 需提供按 subject/id 清）。见 `src/memory/managementApi.ts` resetSubject 注释。
- **`core.health().llmReady` 用 `instanceof OpenAICompatClient` 判定**：env 装配的 Host 主路径完全正确；但注入一个非 `OpenAICompatClient` 的自定义真 LLM 客户端会误报 `llmReady=false`。当前 `src/` 里 `LLMClient` 唯一真实现就是 `OpenAICompatClient`，无触发。日后引入第二类真实客户端时，改为在 `LLMClient` 接口上加 `ready`/`kind` 能力标记、用能力判定取代类型判定。
- **进程退出会截断在途后台整理（步1 引入 · 步6/收尾修）**：Host 的后台画像更新是 fire-and-forget，`SIGINT`/`SIGTERM` 直接 `scheduler.dispose()` + `core.close()` + `process.exit()`，不等在途 `core.updateProfile()` 收尾。影响可恢复（未消化事件下次启动会重新消化、原始证据早已落库，非用户内容永久丢失），但那一趟 in-flight 整理作废。收尾时改：退出前判 `scheduler.status().profileUpdating`，在跑则给一小段宽限期等它收尾再 close；或让 scheduler 暴露"当前是否有在途整理"的 Promise 供 await。

---

**依据文件（绝对路径）**：
- Core facade：`D:\A-DLA_project\DLA_rebuild\src\core\createCore.ts`（缺口比对基准；`handleConversationTurn` 在 120/193 行）
- 公开导出：`D:\A-DLA_project\DLA_rebuild\src\index.ts`
- 现有产品功能实现：`D:\A-DLA_project\DLA_rebuild\testbench\server.mjs`（35 端点）
- 前端三模式共壳：`D:\A-DLA_project\DLA_rebuild\testbench\index.html`
- 边界口径：`D:\A-DLA_project\DLA_rebuild\docs\boundaries.md`（§2 职责 / §4.2 分家 / §4.3 直调例外 / §4.4 facade 清单）
- 根包/类型/构建/CI：`D:\A-DLA_project\DLA_rebuild\package.json`、`tsconfig.json`、`tsconfig.build.json`、`.github\workflows\ci.yml`