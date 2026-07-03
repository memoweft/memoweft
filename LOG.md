# DLA · LOG（磁带 · 只增不改 · 平时不读，仅追溯"当初为何"）

> 开工**不读本文件**，只读 `STATE.md`。仅当需要追溯某个决定的来由时才翻这里。

---

## 2026-07-03 · 架构归位：真采集器迁独立插件包 + 采集流接 Host

**起因**：归位路线走到"做插件"收尾——体验插件（v1）已立，采集器是路线明列的**第二类插件（Collector）**、也是**唯一还赖在 Core 里的归位债**（`src/perception/collectors/` 批次3 只做到"不从主入口导出、标 experimental 暂居库内"，STATE 自记"待迁 plugins/collector-active-window/"）。作者拍板本轮做它，且选**完整版**（归位 + 按路线 §3 把采集流接到 Host，不只物理搬家）。

**做了什么**
- **建采集插件包** `plugins/collector-active-window/`（`@memoweft/collector-active-window` workspace，根 workspaces 加 `plugins/*`）：搬入三个源文件（`activeWindow.ts` 契约+映射 / `activeWindowCollector.ts` 采集循环 / `win32Foreground.ts` Win32 采样）+ 运行器 `run.mjs` + 测试 `tests/collector.test.ts`（8 例）+ README + tsconfig。
- **解耦 Core config**：采集参数（采样间隔 / 碎片阈值）不再挂 `config.activeWindowCollector`（已从 `MemoWeftConfig` 删除），改由插件自持缺省 `DEFAULT_SAMPLE_INTERVAL_SEC/MIN_DURATION_SEC`。插件源文件里 `Observation` 类型从 `memoweft` 引（单一来源，type-only、运行时 elide）。
- **瘦 Core**：`src/index.ts` 删掉采集器 3 个导出（Core 只留 generic `Observation` + `ingestObservations`）；`src/perception/collectors/` 整目录删除；`tests/perception.test.ts` 的映射用例搬去插件、端到端用例改用 plain `obs()` 不再依赖映射。
- **接 Host（路线 §3 数据流）**：Host 加 `POST /api/observe`——收 generic Observation 数组 → 审核（① 采集总开关 `MEMOWEFT_HOST_COLLECTOR`，off 回 403；② `sanitizeObservation` 强制剥掉所有授权位，让 Core 套 observedDefaults 保证 observed 不上云；③ 调 `core.ingestObservation`）。运行器改指 Host `:7788`（不再喂旧 testbench `:7888`）。`npm run collector` 重定向到插件 `run.mjs`；testbench 手动 observe 表单的 import 改指插件路径（保留开发调试用途）。

**为什么这样接**：窗口→Observation 的映射属**采集插件知识**，Host `/api/observe` 只认 generic Observation（任何采集插件——睡眠/心率——都能复用同一入口）。隐私红线在 Host 边界强制执行（剥授权位），不信任插件自报的 `allowCloudRead`（路线 §7「插件只能请求，Host 审核，Core 执行」）。上云是记忆管理页的人工动作，不是采集默认。

**验证**（把关三查）：typecheck 三处全绿；`npm test` Core 144 / Host 25 / 插件 8 全过（采集器 7 + 映射 1 搬到插件、Core 相应减少）。端到端冒烟（**临时库** `MEMOWEFT_HOST_DB` 指 scratchpad，不碰 dogfood 库）：主流程落库 1 / 幂等跳过 / observed 默认（sourceKind=observed·本地可读·不上云·可推）/ **★隐私红线：POST 带 `allowCloudRead:true` 被 Host 强制剥成 false** / 空数组 400 / 采集关闭 403，全过。冒烟后按端口精确杀两个临时 Host + 删临时库/sessions，真库 `host.db` 时间戳未变（未被碰）。

---

## 2026-07-03 · 插件 v1 dogfood 修复：切人设被对话历史带跑

**起因**：作者 dogfood 发现——切「星瑶 → 普通助手」后，当前对话下一句仍在演星瑶。

**诊断**（看会话历史 + 后端状态）：切换本身生效了（`current=plain`、dropConversation 重建了实例、新 systemPrompt 也传了）；真凶是**续聊 seedTurns 把整段旧对话（含旧人设"我是星瑶"的 assistant 回复）种回窗口**，LLM 更信历史里自己演过的角色、被带跑——历史里的自我表述盖过了 systemPrompt。这是"换人设"和"续聊记上下文"的内在冲突，dropConversation 那轮没料到。

**修**：切人设后【第一句】的 seedTurns 只种【用户说过的话】（`seedFor(convId, {onlyUser:true})` filter user turns），不种旧人设的 assistant 回复。切人设端点标记 `switchedExperienceConvs`，chat 用一次即清。普通续聊（open 旧会话、不换人设）仍种完整历史，保持对话连续。

**设计口径**：短期对话窗口（seedTurns）属"当前这个人设"，切人设时旧人设的回复不该被新人设认领；但用户说过的话是跨人设的**事实**、要保留。长期记忆（cognition）本就跨人设（靠 recall 带回）。

**验证**（真模型端到端，dogfood 库临时测试对话已归档）：切星瑶（"你好，云~真好听的名字"温柔）→ 切普通助手问"你是谁"→"我是一个能持续记住你的AI助手"（**不演星瑶**）→ 问"还记得我吗"→"你叫云、26岁"（**记得用户**）。Host test 25、Core test 152 不回归。

---

## 2026-07-03 · 插件 v1：Experience Plugin 契约 + experience-plain/xingyao + 切换

**起因**：架构归位完成，路线（§7）下一步做插件。用户拍板 v1：只 systemPrompt 级契约、Host 内模块、星瑶人设工程师按 naming.md 补写。

**做了什么**
- **apps/memoweft-host/src/experiences/**（Host 内模块，零 Core 依赖）：`plugin.ts`（`MemoWeftPlugin` 契约，v1 只 `{id,name,type:'experience',systemPrompt}`；hooks/permissions/PluginContext 只预留注释、不实现）、`plain.ts`（= 原 REPLY_PERSONA，普通助手）、`xingyao.ts`（星瑶人设，守 naming §6：prompt 里可拟人自称"我"、但禁过度承诺"真正理解你/永远不忘"、落"记≠信"，覆盖记忆唤起/矛盾温和确认/陪伴三场景）、`index.ts`（注册表 + getExperience 回退 + listExperiences 不外泄 systemPrompt，默认 env `MEMOWEFT_EXPERIENCE`）+ tests/experiences.test.ts 6 例。
- **server.ts**：硬编码 REPLY_PERSONA 换成"当前激活体验的 systemPrompt" + GET `/api/experiences` + POST `/api/experience`（切换，白名单 404）+ 前端顶栏体验选择器。
- **Core 加 `core.dropConversation(id)`**（审查抓出的 must-fix，用户拍板破 v1"不改 Core"）：切换体验/重开会话要丢弃 Core 缓存的旧会话实例才真重建换人设——**Host 的 activatedConvs 与 Core 的 conversations Map 是两套独立缓存**，光清 Host 侧、Core 命中旧实例就不重建、新 systemPrompt 被静默忽略。切换端点 + 步4 open 都补调 dropConversation（一并根治步4 那个"Core 覆盖旧实例"的错误假设，虽步4 不换人设没暴露）。

**决策**：v1 只 systemPrompt（用户拍板）；`dropConversation` 加进 Core facade（用户拍板破"不改 Core"，补 Core 本就缺的会话生命周期能力，纯增量方法不改现有）。

**对抗审查（4 维×证伪）抓出 1 must-fix + PM 诚实纠错**：审查读代码证明"切换当前会话不生效"（Core 不重建实例）。**PM 承认：之前"切 xingyao 就温柔了"的行为冒烟是假阳性**——普通助手人设对"今天有点累"本来也会共情，被 LLM 通用温柔骗过；审查的代码逻辑比行为观察靠谱。修复：加 core.dropConversation + core.test 新增用例直接断言 systemPrompt 换没换（不 drop 仍旧人设、drop 后换新人设——把 bug 和假阳性都锁进测试）。

**验证**：Core typecheck ✅ ｜ Core test 152 过（151 + 1 dropConversation）✅ ｜ build ✅ ｜ Host typecheck ✅ ｜ Host test 25（含 experiences 6）✅ ｜ 冒烟切换链路不崩 + 白名单 404 ✅。

**意义**：证明 MemoWeft 是通用框架——同一套记忆底座，普通助手能用、星瑶也能用；插件只管"脸"(systemPrompt)、Core 只管"记忆"，边界清清楚楚。**路线后续**：更多插件（tool/collector）、清仓库/README、npm 发布。

---

## 2026-07-02 · 架构归位批次5 步6：Host S0-S1 用户正门（批次5 收尾）

**起因**：批次5 最后一步——补上用户正门体验（记忆胶囊、记忆气泡、立即整理）。

**做了什么（全在 apps/memoweft-host/）**
- **scheduler.ts**：S1 新理解信号（lastUpdate.newCognitions 从 consolidated.created 取 id/content/credStatus，只新增几条不塞整段画像）+ refreshNow（用户"立即整理"，走同一单飞锁、不与后台并发）。
- **server.ts**：POST `/api/refresh`（core.updateProfile 单飞）+ GET `/api/cognition/count`（胶囊数）。
- **web/index.html**：#memPill 胶囊「它记住我 N 件事」→ 点进记忆抽屉；S1 气泡（整理出新理解就地织进聊天流「记住了：X · 把握度 · 这条不对/删」）；「立即整理记忆」按钮。
- **tests/scheduler.test.ts** 3 例（newCognitions 信号透传 + refreshNow 单飞）。

**决策**：refresh 用户版进 Host（分歧点1）；S0 抽屉复用记忆管理页；气泡把握度用 credStatus（刚生成无衰减）。

**对抗审查（3 维×证伪）修的 4 条（1 must-fix + 3 minor；none 那条设计观察不修）**：
- **must-fix**：气泡「删」直接硬删连溯源链、无确认，和记忆管理页删除口径不一致，且紧挨"这条不对"易误点（团队有误删事故教训）→ 加 memConfirm 二次确认（警示 + 引导用"这条不对"更稳）。
- minor：refreshNow/trigger 成功后无条件清 pendingSinceUpdate，会抹掉 updateProfile 的 await 期间新到的 turn 计数 → 改快照相减保留新增（并只在真攒空才清 idle 兜底）。
- minor：首启 pollBg 追溯织历史气泡的洞（"首次不织"依赖 lastAt、被 doRefresh 改掉）→ 用独立 `_seenFirstBg` flag 解耦。
- minor：weaveMemNote 注释说"向导态不织"但无守卫 → 改注释如实（任何态都织进隐藏 chatInner、切回可见、不丢气泡）。

**验证**：Core 151 不回归 ✅ ｜ Host typecheck ✅ ｜ Host test 19 ✅ ｜ build ✅ ｜ 前端内联 JS `node --check` ✅。冒烟（真 .env 临时库）：胶囊数端点通、**refresh 单飞端到端验到**（后台正忙时 ran:false、不并发）、DOM 齐。诚实标注：S1 气泡"真产认知内容渲染"因本机 updateProfile 慢没端到端浏览器验（信号逻辑 scheduler 单测覆盖 + 前端照搬 testbench 已验证的 weaveMemNote 结构）。

**批次5 收尾**：apps/memoweft-host 六步（步0 骨架 → 步1 聊天 → 步2 配置向导 → 步3 记忆管理 → 步4 多会话 → 步5 备份/恢复出厂 → 步6 S0-S1 正门）全部完成。Host 已能承接 testbench 的全部用户功能，全走 core.* 公开面、零 store 直穿、零 runtime 依赖。testbench 回归开发调试。

---

## 2026-07-02 · 架构归位批次5 步5：Host 备份恢复 + 恢复出厂

**起因**：步4 多会话，步5 让用户能导出/导入记忆包、恢复出厂——批次5 最后一个带破坏性操作的步骤。

**做了什么（全在 apps/memoweft-host/）**
- **server.ts** 加 3 端点，全走 core.portable.* / core.memory.resetSubject：GET `/api/export-bundle`（exportBundle → {bundle}）、POST `/api/import-bundle`（validateBundle 非法 400 → importBundle dryRun/merge，mode 默认安全 dryRun）、POST `/api/factory-reset`（resetSubject 清三层+审计+索引 + 归档 Host 会话软移除 + newSession）。
- **web/index.html** 记忆管理页加「数据·备份」tab：导出（Blob 下载 + revokeObjectURL）、导入（FileReader → dryRun 预览 → 确认 merge）、恢复出厂（强确认要输入「清空」二字 + 劝先备份）。

**决策**：导出/导入/恢复出厂放记忆管理页数据区（用户可达）；全走 core.portable.* / resetSubject；恢复出厂对 Host 会话软移除（不硬删）。

**把关（审查工作流撞平台限制，改 PM 只读亲核补齐）**：本轮对抗审查工作流因撞【会话限制 session limit】大部分 agent 中途失败、只完成 1 维度；PM 改用只读亲核补齐（3 端点 + 前端逐个核：走 core.*/校验/软移除/强确认/textContent 免 XSS 全对）。**亲核发现并加固 1 处（CSRF）**：factory-reset 服务端原是裸端点（强确认全在前端），恶意网页的 CSRF simple-request 能直连清库（CORS 只挡"读响应"不挡"请求到达执行"）→ 加服务端确认词兜底（`body.confirm==='清空'`，带 JSON body 触发 preflight 挡跨源），前端配套带 confirm。

**验证**：Core 151 不回归 ✅ ｜ Host typecheck ✅ ｜ Host test 16 ✅ ｜ build ✅。**临时库冒烟**（⚠ 恢复出厂全程用临时库 `MEMOWEFT_HOST_DB`，绝未碰默认库）：CSRF 无 confirm → 400 ✅、非法包 {} → 400 ✅、恢复出厂带 confirm 清 5 条证据 + 出厂后归零 ✅、导入/导出 dryRun 链路通 ✅、临时库/默认库零污染 ✅。**诚实标注**：cognition 维度因本机 updateProfile 整理慢未造出认知，链路靠 evidence 维度验证（portable 对 cognition 同一套路径）；dogfood 前建议带 .env 聊够、待整理出认知后复验含 cognition 的 bundle。

**下一步**：步6 S0-S1 用户正门（记忆胶囊 + 记忆气泡 + 友好版渲染），批次5 收尾。

---

## 2026-07-02 · 架构归位批次5 步4：Host 多会话

**起因**：步3 能管记忆，步4 让用户能有多条对话、切换续聊、归档。

**做了什么（全在 apps/memoweft-host/）**
- **chatHistory.ts** 从单会话扩成目录级多对话管理器：一对话一 jsonl，append/read/list/archive/newId；sanitizeId 挡路径穿越（非 `[A-Za-z0-9._-]` 全替换 `_`）；read 活跃优先、缺失回退归档。**SESSIONS_DIR 跟随库路径**（`join(dirname(DB_PATH),'sessions')`，步3 遗留 TODO 收口——隔离库时历史也隔离）。
- **server.ts** 多对话状态（currentConvId + activatedConvs 决定 seedTurns 时机）+ 4 端点（`/api/reset`、`/api/sessions`、`/api/session/{open,archive}`）+ 改造 `/api/chat` 用当前会话、`/api/chat-history` 读当前会话。续聊靠 seedTurns（从历史读最近 `config.workingMemory.maxTurns` 轮转 Turn[]，本进程首次 chat 该会话时传 `core.handleConversationTurn` 重建窗口）。
- **web/index.html** 左侧会话列表侧栏（新建/切换/归档/当前高亮），向导/记忆管理态藏侧栏。
- **tests/chatHistory.test.ts** 11 例（9 基础 + 2 审查修复护栏）。

**决策**：多会话是 Host 自实现（不从 Core 掏会话册，蓝图 §3.3）；归档=软移除可恢复；首启续上最近对话。

**对抗审查（5 维×证伪）修的（1 must-fix + 4 minor，同一归档不变量根因，两招根治）**：
- must-fix：重复归档 `renameSync` 静默覆盖旧 `.archived`、旧历史永久丢失，违背"归档可恢复"。
- minor 群：open 不校验归档态 → open 已归档再聊会分叉/遮蔽历史；currentConvId 未 sanitize → 与 list 口径不一致；sanitizeId 多对一碰撞。
- **根治①** `chatHistory.append` 加不变量：活跃缺失 + 归档存在 → 先恢复（取消归档）再续写，不新建空文件遮蔽、不覆盖旧归档。**根治②** `/api/session/open` 白名单：只接受 list（含归档）里存在的 id，挡碰撞/非规范 id、保证 currentConvId 与 list 口径一致。

**验证**：Core 151 不回归 ✅ ｜ Host typecheck ✅ ｜ Host test 16 过（confBand 5 + chatHistory 11）✅ ｜ build ✅。冒烟（真 .env，llmReady=True）：多会话建/列/切 ✅、**续聊种子重建端到端命中**（切 B 再切回 A，续问"记得我叫什么、爱好吗"→回复"记得小明、喜欢爬山"= A 上下文被 seedTurns 重建）✅、open 不存在 id → 404 ✅、重复归档不丢历史（单测）✅。

**下一步**：步5 备份恢复 + 恢复出厂（导出/导入记忆包从开发者抽屉拆到用户可达；恢复出厂沿用 S0 软入口，走 `core.memory.resetSubject`）。

---

## 2026-07-02 · 架构归位批次5 步3：Host 记忆管理页

**起因**：步2 能配置了，步3 让用户能看/管自己的记忆——列认知/证据、标失效、改授权、删除，全走受控 API。

**做了什么（全在 apps/memoweft-host/）**
- **server.ts** 加 6 端点，全走 `core.memory.*`（零 store 直穿）：GET `/api/cognition`（listCognitions + confBand）、GET `/api/evidence`（listEvidence）、POST `/api/cognition/{invalidate,delete}`、POST `/api/evidence/{authorization,delete}`。都带 reason 留审计（management_log）。证据删除【默认探路】→ 有引用回 `blockers` → 前端提示影响面 → 确认带 force=true 断链删。**不做内容编辑**（用户拍板：留 testbench）。
- **web/index.html** 叠加 mode-memory 页：两 tab「对你的理解」/「你说过的」+ 标失效/改授权(开关)/删除(二次确认+证据删除两步)。把握度用用户词档、不露 0-1000 分数。所有用户内容 textContent 免 XSS。步1/2 聊天/向导原样。
- **confBand.ts**（新）抽把握度档纯函数 + **tests/confBand.test.ts**（Host 首个业务逻辑测试，4 例）。

**对抗审查（5 维×证伪，零 blocker）修的 2 条**：
- **must-fix**：把握度档原来用静态 credStatus，没用后端算好的 effectiveConfidence（读时衰减值）→ 衰减型认知（goal 14 天/trait 60 天半衰期）长期显示偏高的"比较确定"绿档，与「记≠信/会变淡」核心矛盾（审查实测：760 分认知 28 天后有效值衰减到 190，页面仍显"比较确定·活跃"）。改为按 effectiveConfidence 定档（confBand，阈值取自 `config.consolidation.credThresholds`、冲突态优先），抽纯函数 + 单测覆盖该衰减场景。
- **minor**：前端"活跃/失效"漏看 archivedAt（归档认知误标"活跃"）→ dead 判据补 archivedAt、归档标"已收起"、过滤同款口径（当前 Host 无归档端点、属防御性前向兼容）。

**验证**：Core 151 不回归 ✅ ｜ Host typecheck ✅ ｜ Host test 5 过（1 smoke + 4 confBand）✅ ｜ build ✅。端到端聊天/列取/标失效/授权/删除两步：步3 施工冒烟走通（发 5 句 → 后台整理 11 认知 → 证据删除返回 5 blockers → force 断链）；本轮 confBand 修复用单测验证（端到端 updateProfile 本机 write 模型偶发慢、未跑完，不影响逻辑正确性）。

**已知待办**：`SESSIONS_DIR` 硬编码、不跟随 `MEMOWEFT_HOST_DB`（隔离库时聊天历史仍落默认目录）——步4 多会话时一并让 sessions 目录跟随库路径。

**下一步**：步4 多会话（/api/reset、/api/sessions、session/open 续聊、session/archive）。

---

## 2026-07-02 · 架构归位批次5 步2：Host 配置向导（gen-env + mode-wizard）

**起因**：步1 能聊了，但首启没配模型时用户无从下手。步2 搬配置向导——填模型配置 → 生成 .env 文本给用户复制。

**做了什么（全在 apps/memoweft-host/，未碰 Core src/ 与 testbench）**
- **server.ts** 加 POST `/api/gen-env`：`buildEnvResponse` 纯函数收 9 个模型字段（对话必填/写路径可选缺省复用/嵌入可选）+ withExperienceUI 布尔 → 拼 `MEMOWEFT_LLM_*`/`WRITE_LLM_*`/`EMBED_*` 的 .env 文本返回 `{env}`。**隐私铁律**：apiKey 只在纯函数栈内流过，全程无 writeFile、无 console.log(body)、不写任何模块级变量（Grep 核实 writeFile 只在注释）。缺必填 → 400。
- **MEMOWEFT_EXPERIENCE_UI=off 纯库开关**：文件顶部（建任何库之前）判断，off 则打印提示 + 提前退出，不建 host.db/data。
- **web/index.html** 叠加 mode-wizard：首启 health.llmReady=false → 进向导；三组表单（apiKey 输入 password）→ gen-env → 只读框展示 .env（textarea.value，免 XSS）+ 复制按钮 + 保存引导。步1 聊天原样保留。

**对抗审查（5 维×证伪，零 must-fix）修的 3 条 minor（互相关联，一并解决）**：
- .env 值裸拼 `KEY=VALUE`，apiKey/base_url 含 `#` 会被 `loadEnvFile` 当行内注释截断（Node 24 实测复现）→ 加 `q()` 转义：含 `#`/空格/引号的值加双引号并转义内部引号。
- off "先建后收"留空库 + `loadEnvFile` 在 core 构造之后（`.env` 的 `MEMOWEFT_HOST_DB` 读不到、db 落默认路径）→ 把 `loadEnvFile` 上移到文件顶部、off 检查提到建 core 之前，两条一并根治。

**验证**：Core 151 不回归、Host typecheck、build；冒烟：gen-env 含 `#` 的 key 正确加引号（`..._API_KEY="sk-abc#def"`）、off 模式不建 data 目录（实测 False）、正常聊天没坏、缺必填/空脏消息 400、apiKey 不落盘（根 .env 时间戳未变）。**已知现象**：off 退出在 stdout 被管道捕获时（自动化冒烟）Windows 偶报无害 libuv 退出竞态 assertion，真实终端不触发、不影响功能（已在 server.ts 注释说明）。

**下一步**：步3 记忆管理页（core.memory.list* + 标失效/授权/删除，走受控 API；内容编辑不搬）。

---

## 2026-07-02 · 架构归位批次5 步1：Host 最小聊天（单会话）

**起因**：步0 地基（workspaces + Core 缺口）经作者验收通过；步1 搬第一个用户功能——聊天。

**做了什么（全在 apps/memoweft-host/，未碰 Core src/ 与 testbench）**
- **server.ts** 在步0 骨架上加：POST `/api/chat`（`core.handleConversationTurn`，单会话 conversationId 'default'，注入 MemoWeft-aware `REPLY_PERSONA` 人设避免大模型说"不保留记忆"）、GET `/api/chat-history`、GET `/api/bg-status`、GET `/`（serve 前端）。
- **scheduler.ts**（Host 自建·蓝图 §3.3）：后台画像更新调度器，攒够 `config.profileUpdate.batchSize` 或空闲 `idleMinutes` 触发 `core.updateProfile`，单飞锁防并发、fire-and-forget 不挡回话、try/catch 兜底不崩。
- **chatHistory.ts**（Host 自建）：一会话一 `.jsonl`，UTF-8 读写、损坏行容错。
- **web/index.html**：从零写的干净聊天前端，零外部依赖，消息全走 `textContent`（免 XSS），文案遵 naming.md（用户词、不露工程词、不说"真正理解你"、不用"她"），思考动效 + 后台状态轮询 + 未配模型黄条。
- **端口默认 7890→7788**：7890 撞 Clash/FlClash 代理端口（冒烟时实测被占）。

**对抗审查（5 维 × 每条证伪）修掉的 4 条**：① must-fix `outcome.error` 被无视——回话失败时 Core 吞成兜底串，原代码当正常回复落库+回前端；改为检查 outcome.error，失败不落 assistant 历史、回可识别失败信号。② must-fix 前端 fetch 失败文案"你的话已经存下了"是假承诺（请求没到服务端、没入库）；改为"这句话还没发出去，稍后再重发"。③ minor 服务端没校验 message（空串/`[object Object]`/超长都入库耗 LLM）；加服务端兜底：仅收 string、trim 非空、≤20000 字，否则 400 不落库。④ minor 进程退出截断在途 updateProfile（可恢复）→ 记入 host-migration.md §6.5 收尾 TODO。

**验证**：Core typecheck/test 151 不回归 ✅ ｜ Host typecheck ✅ ｜ 端到端冒烟：GET / 返回前端、/api/health、POST /api/chat 真调云端模型拿到中文回复、证据落 host.db、chat-history 读回、bg-status 攒批防抖正确、空/脏消息 400 ✅ ｜ 冒烟数据清空、无残留 ✅。

**下一步**：步2 配置向导（gen-env，Host 自拼 .env 文本不落盘 apiKey + mode-wizard）。

---

## 2026-07-02 · 架构归位批次5 步0：Core 三缺口 + workspaces 骨架 + CI

**起因**：批次4 蓝图定了迁移路线，步0 是地基——补齐 Host 要用但 Core 没暴露的能力，并把仓库改成 npm workspaces monorepo，让 Host 能经 `import 'memoweft'` 调 Core。

**做了什么**
- **补 Core 三缺口**（`src/memory/managementApi.ts` + `src/core/createCore.ts`）：`core.memory.listEvidence/listCognitions/listEvents`（只读列取，配 sourcesOf/effectiveConfidence/evidenceOf）、`core.health()`（llmReady/embedReady，基于 core 实际持有的 pool/retriever 判断）、`core.memory.resetSubject()`（恢复出厂收口：库内三层+审计包事务、向量索引 fire-and-forget 清）。`createMemoryManagementAPI` 加可选第三参 `deps.retriever`（不破坏既有两参调用）。index.ts 纯增量导出。
- **起 workspaces 骨架**：根 `package.json` 加 `"workspaces": [".", "apps/*"]`（**必须含根包**，否则子包的 `"memoweft":"*"` 会去 registry 报 E404——蓝图 §1.2 写的 `["apps/*"]` 是机制误判，施工隔离实验后修正）+ 显式 `exports`（单轨引 dist）+ `dev:core` watch。新建 `apps/memoweft-host/`：`package.json`（`@memoweft/host`，private，deps 只 `memoweft`）+ tsconfig（extends 根 base、重写 include）+ `src/server.ts`（node:http 骨架，经 `import 'memoweft'` 建 Core，只挂 `/api/health`，独立库 `data/host.db`，只绑 127.0.0.1，端口 7890）+ smoke.test。`.gitignore` 加 `apps/*/data/`。
- **CI**：调成 Core typecheck → test → build（前置）→ Host typecheck → test（`-w @memoweft/host`），Node 仍锁 24。

**定下的决策（用户拍板）**：Host 包名 `@memoweft/host`；Host 与 testbench 各独立库；不给用户编辑记忆文案（本步不补 editXxx）。**PM 拍板**：exports 单轨引 dist、Host 不产物化、CI 加 Core build 前置。

**对抗审查（5 维 × 每条证伪，零 must-fix/blocker）**：3 条 minor 全是前瞻性、v1 单 subject 无影响——① resetSubject 的 `indexAll([])` 清整表向量（多 subject 化才需 subject 粒度，已加注释 + 记入 host-migration.md §6.5）；② health 用 instanceof 判定（注入非 OpenAICompatClient 的自定义真 client 会误报，主路径正确，记为未来向）；③ health 测试缺 llmReady=true 分支——**已补用例**（注入真 OpenAICompatClient，构造不触网、零成本）。

**验证**：typecheck ✅ ｜ Core test 151 过（144→151：+6 缺口 +1 审查补的 health true）✅ ｜ build ✅ ｜ Host typecheck ✅（`import 'memoweft'` 经软链+exports 解析到 dist 类型）｜ Host test 1 过 ✅ ｜ Host 骨架冒烟 `/api/health` 200 `{llmReady,embedReady}` ✅ ｜ 零依赖红线：lockfile 仅 3 个 registry 包全 `dev:true`、workspace-link 无 registry runtime ✅。

**下一步**：步0 是地基，等作者验收 workspaces 跑通后，按 host-migration.md 步1-6 渐进搬（步1 聊天+health / 步2 配置向导 / 步3 记忆管理 / 步4 多会话 / 步5 备份出厂 / 步6 S0-S1 正门）。

---

## 2026-07-02 · 架构归位批次4：apps/memoweft-host 迁移蓝图（设计文档）

**起因**：批次1-3 已把 Core/Host 边界、统一入口、受控管理、归档雪藏、testbench 切受控 API 做完；下一步要把 testbench 的用户功能搬进独立 Host 壳。搬之前先出施工图，免得批次5 撞墙（尤其"Host 要用但 Core 没暴露的能力"没查全会中途卡壳）。

**做了什么（纯设计文档，不落代码）**
- 新建 `docs/host-migration.md`（约 2 万字施工蓝图）：最终仓库结构（workspaces + package.json/tsconfig/CI 具体改动）、testbench→Host 逐端点迁移映射表（进 Host 19 个 / 留 testbench 13 个）、Core 公开面缺口与补法、批次5 拆解（步0 补缺口+起骨架 → 步1-6 渐进搬）、testbench 与 Host 最终分工、风险与待拍板项。
- 产出方式：三块并行调研（workspaces 形态 / 迁移映射 / Core 缺口）→ 综合 → 对抗审查（带修放行，4 findings 全修进文档）。

**定下的决策（用户拍板的两条方向）**：① Host 形态=新建干净骨架、渐进迁移（testbench 保留作调试）；② 仓库结构=npm workspaces monorepo，Host 经公开入口 `import 'memoweft'` 调 Core、不相对 import `../../src`。据此设计：Core 引 dist（开发路径=发布路径）、Host `private` 挡误发、CI 加 Core build 前置。

**Core 公开面缺口（批次5 步0 必补，已在文档给建议签名）**：`core.memory.listEvidence/listCognitions/listEvents`（只读列取）、`core.health()`（llmReady/embedReady）、`core.memory.resetSubject()`（恢复出厂收口，含审计清除）。判定属 Host 自实现的：gen-env 配置向导、多会话编排、后台画像更新调度。

**审查修进文档的 4 条**：分歧点2 失实（恢复出厂已有 S0 用户软入口，只导出/导入才需从开发者抽屉拆出）、CSS 类名 mode-developer→mode-dev、resetSubject "原子"措辞降级（evidence 无 removeBySubject）、本地首次必须先 build 的内循环硬前置。

**验证**：纯文档，不触发代码；三绿维持批次3 的 144 过。批次5 施工前另需作者拍板若干项（缺口B 内容编辑、独立库、分歧点1 等，见文档 §6.3）。

---

## 2026-07-02 · 架构归位批次3：收瘦主入口 + 归档全面雪藏 + testbench 切受控 API

**起因**：路线 §5.2（主入口仍导出 8 个真实采集符号）+ 批次2 对抗审查留下的三项待拍板（归档在写路径的待遇、出厂清不清审计、删除审计存不存原文）+ boundaries.md §4.3 登记的 testbench 直调 Store 六处。

**做了什么**
- **收瘦主入口**：`src/index.ts` 删 8 个真实采集导出（createActiveWindowCollector 等 3 函数 + 5 类型）；`activeWindowCollector.ts` / `win32Foreground.ts` / `testbench/run-collector.mjs` 文件头标 experimental（未来迁 plugins/collector-active-window/）。摄入口 ingestObservations / activeWindowToObservation 保留。run-collector 与采集器测试本就直连模块路径，未动 import。
- **归档全面雪藏**：`SqliteCognitionStore.active()` 语义升级为「未失效 且 未归档」（SQL 加 archived_at IS NULL）；consolidate/attribute/proposeAsk/revisitConflicts/expire 全走 active()，随之自动雪藏（各处加注释引拍板）；expire 因此不会给归档临时类标失效（保住可恢复）。新增 `tests/archiveShielding.test.ts` 5 例护栏。
- **审计口径**：removeCognitionSafely 的审计 detail 改存元数据 {contentType, formedBy, credStatus, linkCount}、不存内容原文；`SqliteManagementLog` 新增 `clear()`（仅恢复出厂用）。
- **testbench 切受控 API**（§4.3 六处）：顶部 `createMemoryManagementAPI(stores)`；授权变更→updateEvidenceAuthorization、删证据→removeEvidenceSafely({force:true}，UI 已二次确认)、标失效→invalidateCognition（请求只带非 null invalidAt 时）、删认知→removeCognitionSafely、恢复出厂→保留整库擦除直调 + 新增清 management_log。响应形状不变，index.html 零改动。reason 统一 'testbench:用户…' 缺省。
- **文档**：architecture.md §9 补 8-A 临时入库说明；boundaries.md §4.1/§4.3 打 ✅ 并登记直调例外（内容编辑=调试、恢复出厂=整库擦除）；STATE.md / 项目地图 cell 8+13 同步。

**定下的决策**：① 归档认知【全面雪藏】——画像更新不当现有认知、不被主动问起、定期清理不碰（用户拍板）；② 删除认知的审计 detail 不存内容原文、恢复出厂连 management_log 一起清（用户拍板）；③ trends 聚合保持 all() 现状——趋势是历史口径（看"曾反复出现"，本就含已失效），归档同理计入历史（PM 拍板）。

**验证**：typecheck ✅ ｜ test 144 过（138→144：+5 archiveShielding、+1 managementLog.clear，零回归）✅ ｜ build ✅ ｜ testbench 冒烟（起服 → /api/health `{llmReady,embedReady}` → 停进程；无残留 logs/db 写动）✅。

**对抗审查**（6 维度并行 × 每条发现独立证伪，结论：放行）：归档雪藏五处写路径、审计口径、收瘦入口、文档、零回归——全部零发现；唯一 1 条 minor 已当场修：`/api/evidence/update` 内容+授权一次同发时响应 `updated` 只反映后一次快照（两次写库均已生效、无数据丢失，现有前端永不同发不触发）——改为写库后统一 `store.get` 取最新全量，消除未来调用方（apps/memoweft-host）的隐患。

---

## 2026-07-02 · 架构归位批次2：createMemoWeftCore 统一入口 + 受控记忆管理 API

**起因**：路线 §5.1/§5.3——Host 到处散装拼 Sqlite*Store，记忆管理没有受控入口（删除直删、失效无原因、无审计）。归位第二步：给 Core 一扇正门。

**做了什么**
- 新增 `src/core/`：`createMemoWeftCore({dbPath,llm?,embedder?,retriever?,config?,vectorDbPath?})` 工厂 + `MemoWeftCore` facade（ingestUserMessage / ingestObservation / recall / handleConversationTurn / updateProfile / memory / portable / graph / close）。无 .env 不崩（LLM 缺=真调用才报、嵌入缺=NullRetriever 降级）。
- 新增 `src/memory/`：受控管理 7 操作 + `management_log` 审计表（挂共享连接，改数据+落审计同事务）。审计口径：只给真实发生的变更落行，被拒绝的操作不落。
- 认知表幂等加列 `archived_at`（照 asked_at 先例）；归档=invalid 同款待遇：召回跳过、图谱默认不出（新增 includeArchived 选项）。
- 召回段从 conversation.ts 抽为共享函数 `src/retrieval/recall.ts`，Conversation 与 core.recall 共用，门槛顺序一字不改；既有召回测试零改动保绿。
- index.ts 纯增量导出；新增 tests/core.test.ts + tests/memoryApi.test.ts 共 19 例。

**定下的决策**：路线 §5.3 的 9 项管理能力全做（7 个挂 core.memory + 导入导出挂 core.portable）+ 独立审计表（用户拍板）；merge 仅同 subject、链搬家去重、target 置信度按合并后链重算（假设仍按 hypothesisCap 封顶）、source 标失效不硬删；removeSafely 默认有引用即拒绝并返回影响面、force 才删；checkIntegrity v1 只报告不修（以上 PM 拍板）。mergeCognition/archiveCognition 自此从 boundaries.md §4.4 的"没有"转"已有"。

**对抗审查后补修**（审查结论：放行）：① updateEvidenceAuthorization 零变更不再落审计、reason 改必填（对齐"只记真实变更、操作带 reason"口径）；② merge 拒绝已失效/已归档的 target（活链搬进死目标会从召回静默消失）。审查另留 3 项批次3 前拍板：归档认知在写路径（consolidate/proposeAsk/expire）的待遇、恢复出厂清不清审计表、删除审计存不存内容原文。

**验证**：typecheck ✅ ｜ test 138 过（117→138，零回归）✅ ｜ build ✅。

---

## 2026-07-02 · 架构归位批次1：三层边界文档

**起因**：架构归位路线定稿（2026-07 用户拍板，`docs/架构归位路线.md` 入库）——能力做了不少，但 Core / Host / Plugin 职责混在一起；归位第一步是把边界写清。

**做了什么（纯文档，一行代码不动）**
- 新建 `docs/boundaries.md`：一句话定稿 + 三层"负责/不负责"清单 + 标准交互流（Plugin→Host→Core；感知采集、记忆管理两个示例）+ 插件三类与"插件只能请求，Host 审核，Core 执行"原则 + **当前归位现状表**（主入口实测导出 139 个符号（含 DLA_VERSION 兼容别名）、其中 8 个真实采集相关导出待剥离；testbench 功能分家清单；server.mjs 直调 Store 的行号；受控记忆管理 API 已有/部分/没有清单）+ 归位路线（拆边界→瘦 Core→建 Host→迁旧功能→做插件→清仓库→发 npm）。
- `docs/architecture.md` §1 下加 §1.1 三层边界小节，指向 boundaries.md 与归位路线。
- `docs/项目地图.md` cell 9 登记决策：三层归位定稿；星瑶定位 Experience Plugin（另需 experience-plain 证明通用性）。
- `STATE.md` 改写：阶段行加归位批次1 ✅ + 一行指向 boundaries.md（全文 36 行，红线 ≤40 内）。

**定下的决策（用户拍板）**：三层归位——Core 管记忆怎么正确存在，Host 管用户怎么使用和管理，Plugin 管扩展能力；后续开发不堆功能，围绕归位展开。

**验证**：纯文档改动，不触发代码路径；三绿由 PM 统一跑。

---

## 2026-07-02 · 公开仓加固批次（Wave 1 · T1–T5 + 债登记）

**起因**：公开仓门面与内里对不上（README 写死 87 passing 已过期）+ 多 subject 召回越界隐患 + 嵌入器无超时会无限挂 + 向量索引全量重嵌浪费 + 写路径膨胀没有观测数据。一批并行任务卡收口。

**做了什么（基线 main @ dec1c70，108 tests）**
- **T1 README 同步**：中英 README 的静态测试徽章（写死 87 passing）换成 CI workflow badge（根治手工数字过期）；Done/Not yet 清单对齐 STATE.md；`reference/README.md` 改写为"只读基线快照"说明。
- **T2 召回 subject 硬过滤**：`src/pipeline/conversation.ts` 召回循环加一行 subjectId 兜底过滤（`if (c.subjectId !== stored.subjectId) continue;`）；`tests/recallSubjectGuard.test.ts` 红→绿验证。
- **T3 嵌入器超时**：`src/retrieval/embedder.ts` fetch 加 `AbortSignal.timeout`（默认 60s，`MEMOWEFT_EMBED_TIMEOUT_MS` 可配、兼容 `DLA_EMBED_TIMEOUT_MS`），超时抛中文错误、走既有降级链，不再无限挂起。
- **T4 向量索引增量化**：vectors 表加 hash 列（sha256 内容指纹），`indexAll` 内部改增量 diff——新增/变更才 embed（一次批量）、删除集 DELETE、`indexAll([])` 仍清空全表，**对外替换式语义不变**；旧 schema 缺 hash 列 → DROP 重建（索引是可重建派生物）。`tests/retrieval.test.ts` +5 计数用例。
- **T5 写路径 metrics 落盘**：`ConsolidateResult` 增必有字段 `profileSize`（本轮注入 prompt 的 active 认知条数）/`promptChars`（buildMessages 产物字符总和，无新事件早退时两值为 0）；`updateProfile` 返回 `metrics{profileSize,promptChars}` 透传；runLog `ProfileUpdateRecord.summary` 增两个可选字段；testbench `runProfileUpdate` 已接线落盘。`tests/writePathMetrics.test.ts` +3。
- **债登记（地图）**：① **召回边界 V1 契约** → cell 7（一 subject 一 Retriever 实例 + Conversation 注入点硬过滤兜底；非死规则，"单进程多 subject 宿主"出现时升级 vectors 表加 subject 列 + 接口带 subjectId 的 B 方案）；② **11-A 债 · 写路径膨胀** → cell 10（修复 = 相关性限定注入且防重网先行；触发 = dogfood 看 T5 落盘的 profileSize/promptChars 曲线到疼点由人拍板）。docs-sync 检查单补"README 与 STATE 一致、不含手工测试数字"一条。
- **验证**：全仓三绿实测 typecheck ✅ / test **117 过**（108 基线零回归 +9 新增）/ build ✅。分支 `chore/hardening-batch-202607`。

---

## 2026-07-02 · 体验层 V3（方案A）：测试台改成"以人为正门"的应用壳

**起因**：把测试台从"顶部四 tab 调试台"改成"聊天为中心的应用"。四视角(开发者/用户/软件/简单网页)论证后收敛为方案A——用户房间当正门、渐进展开、开发者另成一室。设计口径见记忆 [[memoweft-experience-layer]]。

**做了什么（分片，每片三绿）**
- **S4a** `Conversation.seedTurns`（续聊地基）+ `systemPrompt` 宿主可注入。修 dogfood 坑：素提示下大模型说"我不保留记忆/聊完就忘"、否定 MemoWeft 价值——按 cell 9(语气/角色归宿主)把系统提示做成宿主可注入，库默认不变、测试台注入 MemoWeft-aware 人设。
- **S0** 首屏归位：聊天成正门，「它对我的了解」收进右上「它记住我 N 件事」抽屉；思考中三点动效；友好版改删就地软编辑（弃浏览器弹框）。
- **S1** 记忆气泡：后台消化出新理解 → 聊天流里就地「记住了：X · 还没确认 · 改/删」（管理即对话）；server `bgLast` 带 `newCognitions`。
- **S3** 首启门：`/api/health` 判模型配没配，没配→向导（带"先逛逛"出口 + 黄条）。
- **S4b** 多会话：`sessions` Map，`+新会话`不销毁旧的；`/api/sessions`(列表)、`/api/session/open`(seedTurns 续聊)、`/api/session/archive`(软移除·日志加 .archived·数据不删可恢复)；`RunLogger` 重开会话轮号续写（防撞号漏显）。
- **前端应用壳**：拆四 tab → 左侧固定 `#rail`（新会话/会话列表/设置组[配置·记忆管理]+调试）；开发者会话单开、不与用户混。
- **改名+洗黑话**：开发者→调试、透视区→调试区；清掉调试面板里 event/profile/cognition/attribution/asking/attribute/observed/distill/updateProfile/evidence 英文括注 + 阶段/M编号路线图残留。

**把关**：两轮对抗式审查。审前端重构一轮，挖出并修掉 **dev 会话隔离"回程失效"严重 bug**（`enterDevSession` 用已被 applyMode 改成 developer 的 `window._mode` 做守卫 → 记账恒丢 → 回用户永远切不回用户会话；修=去坏守卫 + `leaveDevSession` 切会话用 `toUser:false` 不夺模式）。零功能丢失/无 XSS/导航完整。**HTTP 全链路冒烟因本机 PowerShell 起 node 服务易挂未跑通**，逻辑靠 108 单测 + 审查读码兜底，真机交互经作者 dogfood。只碰 testbench + Conversation/RunLogger 加性接口，认知核心未动。

**验证**：typecheck ✅ / test **108 过**（+4：seedTurns×2 / systemPrompt / runLogResume）/ build ✅。分支 `feat/experience-shell`（12 提交，b777946…）。

---

## 2026-07-02 · Phase 6-A 记忆管理页 V1（+证据授权位可编辑）

- 测试台新增「记忆管理」tab：左筛选（contentType/credStatus/formedBy/状态/搜索）/ 中列表（认知⇄证据切换）/ 右详情（全字段 + 溯源链 + 反查）。操作：改内容、**标失效（invalidAt，非删除；假设的「否定」走同条路）**、删除（二次确认+引用计数提示）、allowCloudRead/allowInference 开关即时保存。
- 为此 `EvidenceStore.update` patch 扩为 `{rawContent?,summary?,allowCloudRead?,allowInference?}`（不改表结构）；`/api/evidence/update` 布尔护栏透传、`/api/cognition/update` 透传 invalidAt（不带=不动、null=恢复有效）。
- **「确认假设」按钮明确没做**：用户确认怎么影响置信度属核心机制，待作者拍板（cell 8 把握度自算红线）。
- 对抗式审查（独立 Agent，DOM 桩 25 断言 + 注入实测）：零高中危；3 处低危已修——删除接口 `removed:false` 不再假报成功、prompt 清空拒绝落库防空白卡片、落库前 trim。
- 验证：typecheck ✅ / test 88 过 / build ✅ / inline script `node --check` ✅。分支 `feat/memory-manager`（后台 Agent 实现 + 主控人工审数据层/路由 + 对抗式审查前端）。

---

## 2026-07-02 · Phase 8-A 真·活动窗口采集器 V1（档2）

- `src/perception/collectors/win32Foreground.ts`：spawn PowerShell + P/Invoke 取前台窗口（仅 Windows；失败一律 null 不崩）。**编码双保险**：脚本走 `-EncodedCommand`（UTF-16LE base64）、结果 UTF-8→base64 回传——中文窗口标题全程不碰系统代码页（吸取 GBK 乱码入库事故）。
- `activeWindowCollector.ts`：采集循环（连续相同 app+title 合并、停留 ≥`minDurationSec` 才产出、锁屏/采不到保守截断、start/pause/resume/stop、sampler/时钟/定时器全可注入）。产出不带显式授权位 → 下游 observed 保守默认（**不上云红线，测试有断言**）。
- `testbench/run-collector.mjs`（`npm run collector`）：独立进程投喂现有 `/api/observe-window`，不碰 server.mjs。`config.activeWindowCollector`：5s 采样 / 30s 阈值（dogfood 后调）。
- 已知限制：每次采样 spawn 一个 PowerShell（冷编译 ~0.5–2s，V2 可换长驻）；单窗口超长停留只在切换/stop/pause 时产出。
- 验证：typecheck ✅ / test 94 过（+7 离线假采样测试）/ build ✅ / 真机采样+中文往返+全链路回显冒烟 ✅ 零残留。分支 `feat/active-window-collector`。

---

## 2026-07-02 · Phase 7-A Cloud Guard 验收：补漏 trends / ask 路径

- 核心三步（distill/consolidate/attribute）原本就过 `filterCloudReadable`；本轮查出并补上**三处漏网**的云端写路径：`aggregateTrends`、`proposeAsk`、`revisitConflicts`——此前 allowCloudRead=false 的证据（含 observed 默认不上云的）会经这三条路进云端 prompt。
- `tests/privacy.test.ts` 补端到端断言：local-only 原话不出现在喂给（云端）LLM 的 prompt 里。
- 验证：typecheck ✅ / test 90 过（+3）/ build ✅。分支 `feat/cloud-guard-acceptance`（并行会话完成，rebase 零冲突并入）。
- 合流备注：G1/7-A/8-A/6-A 四分支同日合入 main（互不碰文件，零冲突），合流后全量 **104 过**；docs-sync（本三条 LOG + STATE + config-meta 采集器参数 + index.ts collector 导出）由主控合流时统一补记。

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
