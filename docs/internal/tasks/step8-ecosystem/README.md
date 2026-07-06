# 第 8 步 · 生态获客（0.5.0）——MCP 服务器 + Vercel AI SDK 适配器 + token 用量观测 · 任务书

> **状态：施工完成 ✅（2026-07-06·分支 `step8/ecosystem-0.5.0`·两提交 `6d9d9a6` Part A + `82208b3` Part B/C+横切）。** 三件事全出 `0.5.0`：token 观测进 Core（`core.usage()` + Host `GET /api/usage`）、`@memoweft/mcp-server`（6 tool 白名单）、`@memoweft/adapter-ai-sdk`（读 middleware + 写 onEnd）。**五包三绿**（Core 222 + Host 33 + Collector 10 + mcp-server 5 + adapter 15）+ lint 0 error；**红线自证**（`confidence`/`cognition`/`consolidation`/`conversation`/`ingest` 全零改动、根 `dependencies` 仍 `{}`）。**未合 main**（待作者拍板）；**待作者手动**：npm 发布（`npm publish --workspaces`）+ MCP registry 收录 + `v0.5.0` tag（见下节）。
> 依据：`后续批次总纲.md` 第 8 步（第 31–32 行）+ 四路现状勘察（token / MCP / 适配器 / 横切发布，2026-07-06）+ 四路对抗校对（关键外部 SDK 声称联网/官方 tarball 核实）。体例照 `tasks/step7-plugin-contract-v2/`。
> 执行者：任何 AI 会话。开工前必读 `AGENTS.md`，然后只读本目录 + 自己领的那卡点名的源码文件。碰 Core `LLMClient` 接口（Part A）是 experimental 公开面、且与认知红线擦边——主线亲做。

## 批次目标

把 MemoWeft 从"能用的库"推到"生态里能被接入的库"，落三件事（版本 bump `0.4.0 → 0.5.0`）：

1. **token 用量观测（进 Core）**：让宿主"能算钱"——把 `OpenAICompatClient` / `Embedder` 响应里**当前被丢弃的 usage** 接回来，沿现有 `callCount → llmCalls` 的成熟路子透出原始 token 计数。**只给计数、库不内置价目表**。
2. **MCP 服务器**：新建**可发布**独立包 `@memoweft/mcp-server`，用官方 SDK 把 Core 门面包成 MCP tool 给外部 AI 调；**只读 + 一个"存用户原话"的写入口**，破坏性面锁死。**"本地能跑不算完"——要提交官方 registry 收录**（平台侧，归作者手动）。
3. **Vercel AI SDK 适配器**：新建可发布包 `@memoweft/adapter-ai-sdk`，读=`recall` 经 middleware 注入上下文、写=`onFinish/onEnd` 沉淀用户原话。

这三样都是**包在 Core 公开门面之外的"外部集成"**，消费 `createMemoWeftCore` 门面，**不消费第 7 步的插件 v2 hook**（别把两者搞混）。

## 红线（本批任何卡都不许破）

- **不动认知纪律判定算法**（#1 校对焦点）：`confidence.ts` / `cognition/` / `consolidation/` / `conversation.ts` / `ingest.ts` **git diff 零改动**。
  - **token 观测粗粒度、只在方法/结果对象层取差值**：`TurnOutcome`/`UpdateProfileResult` 加 `tokenUsage` 只在 `conversation.ts`/`updateProfile.ts` 的**方法出入口**取 `client.usage` 差值（复刻现有 `callCount` 差值路子），**纯逻辑体一行不改**。一旦发现要动逻辑体才能带 token，**退回更粗**，别硬塞（细粒度=触红线①）。
  - **usage 绝不流入置信度自算**：token 只做观测/计费，绝不喂进 `confidence`（照 `temperature`/`tier` 已有的"不流入 confidence"先例注释）。
- **零运行时依赖**：Core 主包 `package.json` `dependencies` **永远 `{}`**。第三方（`@modelcontextprotocol/sdk`、`ai`、`zod`）**只进各自独立包的 dependencies**，绝不进 Core、绝不 hoist 后写进根 deps。token 观测只用 Node 内置。
- **Core 无头**：Core 不画界面、不做人设。
  - **MCP tool 的 description/annotations 保持中性协议措辞**，不复活人设（别把 `recall` 写成"回忆起关于你的事"这类拟人）。
  - **适配器注入文案只搬 Core 现成的 `knowledgeBlock` 中性串**（`action.ts:29-43`），适配器里不自造人格化 prompt。
  - **token 观测在 Core 侧只做纯数据字段**，Core 不 `console`/不格式化/不"算钱展示"——算钱是宿主/新包的事。
- **MCP 权限面（安全硬点）**：破坏性面（`reset`/`remove*`/`invalidate`/`merge`/`archive`）+ `updateEvidenceAuthorization`（改上云授权位）**一律不注册成 tool**；`handleConversationTurn`（外部可触发整套消化改画像）**v1 也不暴露**；写只给 `ingestUserMessage`（只落一条 spoken 证据、不改画像）。适配器写路径**只存用户原话、不存助手回话**（`conversation.ts` 纪律），`observed` 默认不上云不放宽。
- **合并前机器可查自证**（不靠口头）：① `git diff` 上述 5 个认知文件为空；② 根 `package.json` `dependencies` 仍 `{}`。
- **合并/推送已授权 AI 自主**（记忆 `merge-to-main-delegated`）；但 **npm 发布 / MCP registry 收录仍先问作者**（供应链动作 + 需平台凭据）。

## 现状底座（已亲验 · file:line · 2026-07-06 四路勘察 + 对抗校对复核）

### Part A — token 观测
- `LLMClient` 接口 = `chat(messages):Promise<string>` + 只读 `callCount` + 可选只读 `tier`（`src/llm/client.ts:24-32`），标 **experimental**（`src/index.ts:118-125`：「接口签名 pre-1.0 可能演进」——扩它不破约的免死金牌）。
- `OpenAICompatClient.chat` 已 `res.json()` 但 as 类型**只声明 `choices`、把 `usage` 整个丢了**（`src/llm/client.ts:158-161`，请求体 `:136` 非流式）。→ token 是"到手又扔"，不是"没有"。OpenAI 非流式 usage 结构 = `{prompt_tokens, completion_tokens, total_tokens}`（**snake_case**，已 WebSearch 官方核实）。
- **embed 走独立 `Embedder` 接口**（`src/retrieval/embedder.ts:11-14`），也丢 `usage`（`:83-84`），且**连 `callCount` 都没有**——接它要从零加"次数 + usage"计数骨架（改动面比对话侧大，校对官纠正）。
- `callCount` 差值路子：`distill.ts:78-80` / `consolidate.ts:199-205` / `attribute.ts:141,217` / `trends.ts:127-133` / `action.ts:59-61`；`jsonRepair.ts:91,101` 重试会额外 +1（对"算钱"是对的）。汇到 `TurnOutcome.llmCalls`（`pipeline/conversation.ts:48/88/100`）+ `UpdateProfileResult.llmCalls`。
- `RunLogger`（`src/obs/runLog.ts`，纯 `node:fs` 零依赖）已记 `llmCalls`（`:58/:99`），有"旧日志无字段兼容读"的 optional 先例（`:93-96`）。Host 侧**零 token 概念**（grep 整个 `apps/memoweft-host` 无 token/usage）。
- 根 `package.json:43` `dependencies:{}`（`:59` `_comment` 明写零运行时依赖）。

### Part B — MCP 服务器
- Core 门面 `MemoWeftCore` 接口（`src/core/createCore.ts:124-149`，11 成员）+ 工厂 `createMemoWeftCore`（`:159`，缺 `.env` 也能建、真调用才报错，文件头 `:1-13`）。
- 破坏性/隐私操作全挂 `core.memory`（`MemoryManagementAPI`·`src/memory/managementApi.ts:143-178`）：`invalidate`/`updateEvidenceAuthorization`（改上云授权位·`:148`）/`removeEvidenceSafely`/`removeCognitionSafely`/`mergeCognition`/`archiveCognition`/`resetSubject`（恢复出厂·`:173-177`）；只读列取 `listEvidence`/`listCognitions`/`listEvents`/`checkIntegrity`（`:163-171`）。→ 破坏性面集中、好隔离。
- 可发布包模板 = `plugins/collector-active-window/`（`@memoweft/collector-active-window` + `private:true` + `dependencies:{memoweft:'*'}` + 独立 tsconfig `extends ../../tsconfig.json`）。**关键差异**：collector 是 `private` 内部包 + `noEmit` tsconfig；MCP 包要发 npm + 进 registry，**必须非 private + 能 emit dist + bin/shebang**。
- 外部 SDK（对抗校对联网核）：`@modelcontextprotocol/sdk` latest **1.29.0**（稳定线；`exports` 已含 `./server`/`./client`/`./*` 通配——**`./server` 不是 v2 才有**，原草稿认知已订正）；peer `zod ^3.25||^4.0`；`engines node>=18`。registry **只收元数据、包体先上 npm**；收录需 `server.json` + `mcp-publisher` CLI + `mcpName`（package.json）与 `server.json` `name` **逐字一致** + GitHub 认证下命名空间须 `io.github.<user>/` 开头（官方 quickstart 逐条核实）。registry 处 **preview 阶段**。

### Part C — Vercel AI SDK 适配器
- 读路径：`core.recall({query,subjectId?})`（`createCore.ts:278`）返回 `RecalledCognitionItem[]`（`{id,content,confidence,credStatus,score}`·`recall.ts:22-30`，已走完相似度/失效/归档/越界/衰减五道门控 `:44-56`）；注入范式照 Core 现成 `knowledgeBlock`（`pipeline/action.ts:29-43`，低置信标"only guesses—do not treat as established facts"）。
- 写路径：`core.ingestUserMessage({content,subjectId?,...})`（`createCore.ts:249`，只存 spoken 证据、**不改画像**）；`Observation` 含三授权位（`allowLocalRead/allowCloudRead/allowInference`），适配器写 observed **不要显式传 `allowCloudRead:true`**（否则"显式>默认"绕过不上云·`ingest.ts:79-82`）；`originId` 幂等（`:68`）。
- 外部 SDK（对抗校对用 `npm pack` 官方 tarball 逐行核）：`ai` latest **7.0.15**，`engines node>=22`，peer `zod ^3.25.76||^4.1.8`。读适配器用 `wrapLanguageModel({model,middleware})` + `transformParams`（RAG-as-middleware 范式）；类型选 **ai re-export 的宽松 `LanguageModelMiddleware`**（`ai/dist/index.d.ts:155`，`specificationVersion` 可选，抗大版本漂移），别直绑 `@ai-sdk/provider` 的强版 `LanguageModelV4Middleware`。写落点 `onFinish`——**ai@7 里 `onFinish` 已是 `onEnd` 的 `@deprecated` 别名**，helper 接 `onEnd`、`onFinish` 当兼容。`getLastUserMessageText`/`addToLastUserMessage` **非 SDK 自带**（官方文档明示），取/塞 last user message 属自研。

### 横切
- `workspaces`（`package.json:26`）= `[".","apps/*","plugins/*"]`——**无 `packages/`**。两非 Core 包 `private:true`+`version 0.0.0`+`memoweft:*` 软链。scope 惯例 `@memoweft/*`。
- 三绿脚本挂根（`typecheck/test/build/lint`·`:27-42`）；Host/collector 各有 `typecheck`+`test`（无 build）。CI（`.github/workflows/ci.yml`）三 job：guardrails（Node24·删 `better-sqlite3` 验零依赖·逐包 `-w` 点名）+ Node22/20 触达矩阵 + on-tag publish（**裸 `npm publish` 只发根单包**·`:175-211`）。

## 决策（作者已拍板 2026-07-06）

| # | 决策 | 定案 |
|---|---|---|
| **D1** | token 观测挂哪层 | **进 Core**：`LLMClient` 加**只读可选** usage 累计器（复刻 `callCount` 形态）；`OpenAICompatClient.chat` 防御式接 usage；**粗粒度**——只在 `conversation`/`updateProfile` 方法边界取差值透出 `TurnOutcome.tokenUsage`/`UpdateProfileResult.tokenUsage`。usage **不流入 confidence**。**failStub/asPool 因可选字段无需补**（校对官纠原草稿假必做项）。 |
| **D2** | embed token 算不算 | **算**。`Embedder` 从零加 `callCount` + usage 计数骨架，`OpenAICompatEmbedder.embed` 接 usage。理由：漏 embedding 会系统性低估、"算钱"不准。 |
| **D3** | MCP 写入面 | **只读 + 写·轻**：暴露 `recall`/`listCognitions`/`listEvidence`/`listEvents`/`graph`（读）+ `ingestUserMessage`（只存用户原话、不改画像）。**不暴露** `handleConversationTurn`（外部可触发整套消化改画像）；破坏性面 + `updateEvidenceAuthorization` **一律不注册**。 |
| **D4** | 适配器 | **Vercel AI SDK**（`ai ^7`）。读走 `wrapLanguageModel`+`transformParams`（宽松 `LanguageModelMiddleware`）；写走 `onEnd`（`onFinish` 别名）存用户原话；不存助手回话。 |
| **D5** | 包结构 / 命名 | 新开 **`packages/`** 目录专放可发布集成包（不塞 `plugins/*`——语义会乱）。`@memoweft/mcp-server` + `@memoweft/adapter-ai-sdk`，**非 private 可发布**。第三方依赖进各包 `dependencies`；对 `memoweft` 用 **`peerDependency`**（`^0.5.0`）避免双装，`devDependency` 放一份供本包测试。 |
| **D6** | 版本 | 主包 `0.4.0 → 0.5.0`（token 进 Core）；新包**独立起始 `0.1.0`**（不跟 Core 统一）。**发布顺序：先发 `memoweft@0.5.0` 再发新包**（peer 指向已存在版本）。 |
| **D7** | "算钱"程度 | **只给原始 token 计数**（按 tier/purpose/model 分桶），**库不内置价目表**（定价随厂商/时间漂移，且"库不替宿主做定价假设"）。宿主一行乘法算钱。 |
| **D8** | 落盘 | 第一版**内存累计 + 沿 `runLog` 落每轮 `tokenUsage`**（jsonl）。跨会话总账聚合留后续（Host 侧读 jsonl 手算，Core 不背持久聚合、不为此引依赖）。 |
| **D9** | MCP SDK 版本线 | 锁 `@modelcontextprotocol/sdk ^1`（1.29.0 稳定线，用 `./server` 子路径），**不追 v2 beta**（求稳、别押 beta）。 |
| **D10** | 发布口径 | `npm publish --workspaces`（自动跳过 `private` 的 host/collector）；各新包 `prepublishOnly` 走自己三绿；`--workspaces`+`--provenance` 组合**开工必实测**。npm 发布作者手动/确认。 |
| **D11** | CI 覆盖 | 新包进 guardrails job 三绿（`--workspaces --if-present`，**Core build 仍最先**）；Node20/22 触达矩阵**不覆盖新包**（不碰 SQLite 驱动，省 CI 额度）。 |

## 任务清单（按施工顺序 · 已吸收对抗校对修正）

### Part A · token 观测（进 Core · **主线亲做**）
| 序 | 卡 | 一句话 | 碰核心? |
|---|---|---|---|
| **A0** | 测试改动面勘察 | `grep tests/` 里 `callCount`/`failStub`/假 client，锁定加接口字段后要同步补的 mock（校对官加的前置） | 否 |
| **A1** | `LLMClient` 加 usage 累计器 | 接口加**只读可选** `usage?`（`promptTokens/completionTokens/totalTokens/callsWithUsage`），仿 `callCount`；`index.ts` 导出类型（experimental） | 邻近·experimental 公开面 |
| **A2** | `chat` 接 usage | `OpenAICompatClient.chat` 给 `res.json()` 补 `usage?`（snake_case）、**读到才加、读不到静默跳过**（本地模型常不回，不能崩） | 否 |
| **A3** | `Embedder` 加计数（D2） | `Embedder` 从零加 `callCount` + usage 骨架；`OpenAICompatEmbedder.embed` 接 usage | 否 |
| **A4** | 结果对象透出 | `TurnOutcome`/`UpdateProfileResult` 加可选 `tokenUsage`（**方法边界取差值**，含 `callsWithUsage` 否则宿主算均值会错）；`runLog` 加可选字段 + 兼容旧日志读 | **邻近红线**·只方法层取差值、逻辑体零改 |
| **A5** | Host 呈现 | `server.ts` 加只读 `GET /api/usage`（仿 `/api/health`/`/api/memory-graph` 只读门面） | 否（Host） |
| **A6** | 测试 + 文档 | 假 client 带 usage 的断言；`CHANGELOG` + experimental 接口注释说明 `usage?` 语义 | 否 |

### Part B · MCP 服务器（新建 `packages/mcp-server/` · 机械量大可派 agent 后亲核）
| 序 | 卡 | 一句话 | 碰核心? |
|---|---|---|---|
| **B1** | 可发布包骨架 | `package.json`（**非 private** + `@memoweft/mcp-server` + `bin` + `files:[dist]` + `license/repository/publishConfig:{access:public}` + deps `@modelcontextprotocol/sdk ^1` + `memoweft` peer）；**能 emit dist 的 build tsconfig**（非 noEmit）；根 `workspaces` 加 `packages/*` | 否 |
| **B2** | server 入口 | stdio server（`McpServer`+`StdioServerTransport`·`./server` 子路径）；进程内 `createMemoWeftCore`；注册白名单 tool；**tool 描述中性、不复活人设** | 否（壳层） |
| **B3** | tool 白名单（D3） | 读 `recall`/`list*`/`graph` + 写 `ingestUserMessage`；破坏性 + `updateEvidenceAuthorization` + `handleConversationTurn` **不注册** | 否 |
| **B4** | 配置传递 | `dbPath` + 模型 env 经环境变量传子进程；缺库/缺模型走 `health()` 降级提示、不崩 | 否 |
| **B5** | 测试 + 三绿接线 | 离线测试（`:memory:` 库，断言白名单 tool 返回 + 破坏性方法**未注册**）；纳入根三绿 | 否 |
| **B6** | registry 元数据 + 文档 | `server.json`（`mcp-publisher init` 生成）；README（读/写 tool 表 + **SECURITY 外部自主调用风险声明** + `.zh-CN` 双语互链） | 否 |
| **B7** | 发布收尾（**作者手动**） | `npm publish --access public` → `mcp-publisher login github` → `publish`；验收 = registry API 搜得到 | — |

### Part C · Vercel AI SDK 适配器（新建 `packages/adapter-ai-sdk/` · 机械量大可派 agent 后亲核）
| 序 | 卡 | 一句话 | 碰核心? |
|---|---|---|---|
| **C1** | 可发布包骨架 | 同 B1 骨架；deps `ai ^7` + `zod` peer + `memoweft` peer；`engines node>=22` | 否 |
| **C2** | 读适配器 | `recallMiddleware`：`transformParams` 取 last user text → `core.recall` → 按 `knowledgeBlock` 口径拼 → 注入回；`wrapLanguageModel` 包宿主模型。last-user helper 自研 | 否（只读门面） |
| **C3** | 写适配器 | `persistOnEnd`：接 `onEnd`（`onFinish` 兼容别名）→ `core.ingestUserMessage` 存用户原话；稳定 `originId` 幂等；**不存助手回话**；usage 口径与 Part A 对齐 | 否 |
| **C4** | 测试 + 冒烟 + 三绿 | 离线测试（假 `LanguageModel`/假 core，验注入 + `onEnd` 调 ingest）；dist 冒烟（装完能 `import wrapLanguageModel`）；纳入根三绿 | 否 |
| **C5** | 文档 | README（读/写两路 + 最小可跑 example + `.zh-CN` 双语互链） | 否 |

### 横切收尾
| 序 | 卡 | 一句话 |
|---|---|---|
| **X1** | CI 接线 | guardrails job 加新包三绿 step（`--workspaces --if-present`，Core build 最先） |
| **X2** | publish job | 改多包发布（`--workspaces`+`--provenance` 组合实测） |
| **X3** | 主包 bump + CHANGELOG | `0.4.0 → 0.5.0`；CHANGELOG 汇总三件事 |
| **X4** | 合并前自证 | 5 认知文件 `git diff` 空 + 根 `dependencies:{}` |

## 对抗校对纪要（四路 · 2026-07-06 · 外部 SDK 声称联网/官方 tarball 核实）

**采纳并入（真问题 → 已改任务书）**：
1. **token 线删假必做项**（→ D1）：原草稿称"failStub/asPool 要补 usage 否则 typecheck 炸"与"usage 可选字段"设计自相矛盾（`tier?` 就是可选、failStub 现在没带 tier 也编译通过）——可选字段无需补，删。
2. **细粒度触红线①上升为硬红线**（→ 红线/A4）：每算子带 token 要动 `consolidate.ts`/`conversation.ts` 逻辑体=触红线；只在方法边界取差值。
3. **A0 先 grep 测试假 client**（→ A0）；**embedder 从零补计数**（→ D2/A3，改动面被原草稿低估）；**`callsWithUsage` 必须透出**（→ A4，否则宿主 token÷llmCalls 算均值错）；OpenAI usage snake_case 已 WebSearch 证实。
4. **MCP `./server` 认知订正**（→ D9/现状）：`./server`/`./client` 是 1.29.0 已有、非 v2 beta（原草稿事实反了）；真 v2 beta 是拆成 `@modelcontextprotocol/server`+`@modelcontextprotocol/client` 两个不同包名。
5. **MCP 发布包差异**（→ B1）：必须 emit dist（不能照抄 collector 的 noEmit）+ bin/shebang + 纳入三绿 + 模型 env 子进程传递 + 根 deps={} 机器守卫；tool 描述别复活人设；`handleConversationTurn` 是"外部可写身份记忆"面 v1 不暴露（→ D3）。
6. **适配器出处订正 + onEnd**（→ 现状/C3）：原草稿"读的是本机 `node_modules/ai`"不可复现（此仓库没装 ai），来源改官方 tarball；`onFinish` 已是 `onEnd` `@deprecated` 别名，接 `onEnd`；宽松 `LanguageModelMiddleware` 抗漂移；peer 版本前置发布顺序（先发 `memoweft@0.5.0`·→ D6）。
7. **横切补漏**（→ B1/C1/X2）：新包补 `license/repository/publishConfig`；`files` 白名单升硬约束；`--workspaces`+`--provenance` 组合开工实测；开发期 workspace 软链 vs 发布期 `^0.x` 复核。

**行号微订**（不影响结论）：`checkIntegrity` 实为 `managementApi.ts:163`；`pool.ts` 按用途取 client 在 `:50`（接口 `:20-23`、装配 `:31-51`）。

> 四路独立 Explore agent 读真代码 + 联网核外部 SDK 产出（现状/施工卡/影响面/风险/待拍板）；每路配一个 high-effort 对抗校对官逐条挑刺（声称要有代码据、外部 SDK 要查证、别漏红线）。Part A 碰 Core 接口 + 红线擦边，施工时主线逐行亲验。

## 待作者手动（平台侧尾巴 · AI 做不了）

- **MCP registry 收录**（B7）：`mcp-publisher login github` 需 GitHub 交互式登录（**本机没装 gh**，工具链需作者侧确认）；命名空间归属（`io.github.<memoweft>/*` 组织 vs 个人账号）按官方 authentication 文档确认。
- **npm 发布**：`@memoweft` scope 是否已注册/占用需确认（host/collector 从未发过）；**先发 `memoweft@0.5.0` 再发新包**。
- **本地模型端点 usage 真机验**：第 6 步 local tier 场景（llama.cpp/ollama/vLLM）到底回不回 usage、字段全不全——配好模型的机器实测（同"读到才加"容错逻辑）。
- **（承前）第 4 步真模型 e2e 英文验**、**`v0.4.0` tag + Release**（若未打）——见 `CURRENT.md`。

## 本批明确不做

- 不暴露破坏性 tool / `handleConversationTurn`（D3）；不追 MCP SDK v2 beta（D9）。
- 不内置价目表 / 计价逻辑（D7）；不做跨会话持久 token 聚合（留 Host 读 jsonl·D8）。
- 适配器不存助手回话、不放宽 observed 不上云（红线）。
- 不动认知纪律判定算法、不引 Core runtime 依赖、不碰 `MemoWeftConfig` 形状。

## 批次验收（草案 · 全批合完跑一遍）

- [ ] **token 通**：真/假端点各跑一次，`TurnOutcome.tokenUsage` 带出 prompt/completion/total + `callsWithUsage`；端点不回 usage 时不崩、字段为 0/缺。
- [ ] **embed 计数通**（D2）：灌一批记忆后 embed 的 token 也进账。
- [ ] **usage 不入 confidence**（红线）：置信度计算 diff 里无 token 参与。
- [ ] **MCP tool 白名单**（D3）：`:memory:` 库起 server，白名单 tool 返回正常；破坏性方法 + `handleConversationTurn` + `updateEvidenceAuthorization` **确未注册**；tool 描述中性无人设。
- [ ] **MCP 可发布**：包能 `build` 出 dist、`bin` 可执行、`files` 白名单只含 dist；`server.json` `name` = package.json `mcpName`。
- [ ] **适配器通**：假模型下 `transformParams` 真把召回注进 params；`onEnd` 真调 `ingestUserMessage` 存用户原话（不存助手话）；dist 冒烟能 import。
- [ ] **零依赖 + 五文件零改**（红线·机器可查）：根 `dependencies:{}`；`confidence.ts`/`cognition/`/`consolidation/`/`conversation.ts`/`ingest.ts` `git diff` 空。
- [ ] **五包三绿**：Core + Host + Collector + mcp-server + adapter-ai-sdk 各 typecheck+test（+ 能 build 的 build）全绿；lint 不新增错。

## 附 · 现状勘察证据索引（file:line · 已主线/对抗校对亲验）

- **token**：`src/llm/client.ts:24-32`（接口）/`:158-161`（丢 usage）/`:136`（非流式）；`src/retrieval/embedder.ts:11-14`/`:83-84`（丢 usage·无 callCount）；`callCount` 差值 `distill.ts:78-80`/`consolidate.ts:199-205`/`attribute.ts:141,217`/`trends.ts:127-133`/`action.ts:59-61`/`jsonRepair.ts:91,101`；`pipeline/conversation.ts:48/88/100`（llmCalls）；`src/obs/runLog.ts:58/99`（llmCalls）/`:93-96`（optional 兼容先例）；`src/index.ts:118-125`（LLMClient experimental 导出）。
- **MCP**：`src/core/createCore.ts:124-149`（门面）/`:159`（工厂）/`:1-13`（缺配不崩）；`src/memory/managementApi.ts:143-178`（破坏性）/`:148`（updateEvidenceAuthorization）/`:163-177`（只读列取 + resetSubject）；`plugins/collector-active-window/package.json`（可发布包模板）。
- **适配器**：`src/core/createCore.ts:278`（recall）/`:249`（ingestUserMessage）/`:262`（ingestObservation）/`:282`（handleConversationTurn）；`src/retrieval/recall.ts:22-30/44-56`（返回 + 门控）；`src/pipeline/action.ts:29-43`（knowledgeBlock）；`src/perception/ingest.ts:19-34/66-85`（Observation + observed 不上云）。
- **横切**：`package.json:26`（workspaces）/`:43,59`（零依赖）；`apps/memoweft-host/package.json`、`plugins/collector-active-window/package.json`（private 模板）；`.github/workflows/ci.yml`（三 job·`:175-211` publish 只发根包）。
- **外部 SDK**（联网/tarball 核）：`@modelcontextprotocol/sdk@1.29.0`（`./server` 子路径·peer zod·node>=18·registry preview）；`ai@7.0.15`（node>=22·peer zod·`dist/index.d.ts:155` 宽松 middleware·`onFinish`=`onEnd` 别名）。
