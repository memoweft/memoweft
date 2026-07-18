# DECISIONS — ADR-lite

每个有争议的取舍一条。必须记的时机:偏离 PROJECT_PLAN 设计;两个合理方案取舍;修改默认参数;任何 API/schema/权限模型变更(附影响面说明)。

## D-0001 FTS5 tokenizer 选择(trigram)

日期:2026-07-10 / 状态:已采纳(索引体积待 Phase 1 实测补记)
背景:Phase 1 关键词通道用 `node:sqlite` FTS5。
选项:A `trigram`(CJK/拼写变体稳,索引大,查询词需 ≥3 字符才匹配) B `unicode61`(小,不分中文)。
决定:默认 A `trigram`;纯英文场景可配 `unicode61`。
依据/实测(Phase 0.1,node v24):FTS5+trigram 可用;英文 `peanut` MATCH ✓;**中文 2 字词 MATCH 返回 0,3 字才命中**(`饮食`→0、`饮食限`→1)。→ hybrid 里短中文词须靠向量通道兜底,黄金集中文组按此设计。降级链(better-sqlite3 → 纯 TS BM25)本轮未触发。

## D-0002 协作模式 = 务实混合

日期:2026-07-10 / 状态:已采纳(人类拍板)
背景:PROJECT_PLAN 的执行模型是「Integrator 主会话 + 6 个角色锁定子代理(`.claude/agents`)+ hooks 机器强制,重启会话生效」;本会话还能用 Agent/Workflow 即时派子代理并行。
决定:务实混合——Integrator 用 Agent/Workflow 即时委派并行推进;`.claude/` 落地供以后会话用;本会话 hooks 机器强制暂不生效,靠职责分离 + Integrator 守门 + 每次全量测试兜底。

## D-0003 Phase 4 demo = 改造现有 testbench

日期:2026-07-10 / 状态:已采纳(人类拍板)
背景:仓库无 `npm run demo`,有浏览器版 `testbench`/`experience` 与参考宿主 `apps/memoweft-host`。
决定:Phase 4 在现有 testbench/experience 基础上改出「终端四幕、纯文本、无 key、确定性(HashEmbedder + 录制夹具)」demo,而非从零新建。

## D-0004 hook 落地适配(对附录 I.2 的偏离)

日期:2026-07-10 / 状态:已采纳
背景:附录 I.2 的 `protect.py` 逐字落地后 stdin 实测(16 场景)发现三处问题。
决定/偏离:
- ① hook 命令用 `python`(非 `python3`)——避 Windows `WindowsApps` 的 Store 别名 shim。
- ② force-push 正则加固:原 `git\s+push\s+\S*\s*(-f|--force)` 漏拦 `git push origin main --force`(force 标志被参数隔开);改为"命令含 `git push` 且含 force 标志(`--force`/`--force-with-lease`/`-f`)即拦",3 变体已验。
- ③ stderr 强制 UTF-8:否则 Windows GBK 控制台下拦截理由乱码,回传给 Claude/人类不可读。
补充:本 harness 下 hooks **不热加载**(探针实证),需重启会话才激活;角色级写入限制靠 stdin `agent_type` 字段(claude-code-guide 核实当前官方文档确有此字段)。

## D-0005 检索现状修正 + mimo 模型特性(校准结论)

日期:2026-07-10 / 状态:记录(影响 Phase 1/2 设计)
背景:Phase 0.2 校准发现 PROJECT_PLAN §14 对检索现状的描述与代码不符。详见 `docs/internal/phase0-calibration.md`。
修正:
- ① 索引"全量重建"不准确——`VectorRetriever.indexAll` 嵌入侧**已是 sha256 增量 diff**(O(Δ) 嵌入调用);真瓶颈在**读侧**:每次查询 O(N) 读全表 `vectors` + `JSON.parse` + 手写 JS 余弦。→ Phase 1 优化重心在检索侧(FTS/BM25 关键词通道 + RRF + 向量侧 ANN/sqlite-vec),而非重建侧。
- ② 向量以 **JSON 文本**存于独立 `vectors(id,hash,vec)` 表、走独立第二连接、**不纳入 `runMigrations` 版本化**(自带 DROP-重建)——Phase 1 改向量 schema 需单独处理其迁移路径。
- ③ 写路径**非单一事务**:只有 `consolidate`(认知写 + `event.markConsolidated`)那段走共享事务;`evidence.put`、`distill` 的 `event.put`、`attribute` 均无事务。
mimo:`mimo-v2.5-pro` 是**推理模型**(回 `reasoning_tokens`);`client.ts` 不发 `max_tokens` 且自动剥 `<think>…</think>`,天然适配。Phase 2 固化注意给足输出预算。

## D-0006 KeywordRetriever 策略(§14.3)

日期:2026-07-10 / 状态:已采纳
- **tokenizer**:默认 `trigram`(CJK 稳,2 字中文 query 无输出、靠向量兜底,见 D-0001);`unicode61` 可配(纯英文场景)。建表 tokenizer 走白名单校验,绝不让任意串进 DDL;query 走绑定参数(§22 参数化)。
- **失效/过期过滤**:KeywordRetriever 只索引 `indexAll` 交给它的条目(active 认知),**不看认知状态**;`invalidAt`/`archivedAt` 的门控由下游 `recallCognitions` 负责——与 VectorRetriever 完全一致,融合层不重复过滤。
- **增量**:沿用 VectorRetriever 的 sha256 影子表 diff(`kw_meta(id,hash)`),只对新增/变更重建 FTS 行、删除消失条目(满足 §14.5 增量精神;FTS 无嵌入成本但仍走 diff 以最小化写放大)。
- **score 口径**:`score = -bm25()`,正向、越大越相关,与向量余弦口径一致,供 §14.4 RRF 直接融合。

## D-0007 纯 TS BM25 降级暂缓(对 §14.3 降级链的偏离)

日期:2026-07-10 / 状态:已采纳(铁律 4 不过度工程)
背景:§14.3 降级链 `node:sqlite 无 FTS5 → better-sqlite3 → 纯 TS BM25(±200 行)`。
决定:**纯 TS BM25 降级暂缓**,进 ROADMAP Next。依据:FTS5 在本项目**所有支持环境**都可用(Node 24 内置 `node:sqlite` 编译带 FTS5;Node 20/22 的可选 `better-sqlite3` 亦捆绑 FTS5)——纯 TS 降级是当前**不会触发的防御代码**,写 200 行死代码违背铁律 4。
补偿:`KeywordRetriever` 构造函数已留具名探测点 `FtsUnavailableError`(FTS5 建虚表失败即抛),将来真遇到无 FTS5 的环境,工厂 catch 它降级即可,无需返工。

## D-0008 hybrid 不接入公共 API(§14.4b 决策,数据驱动)

日期:2026-07-10 / 状态:已采纳(人类拍板)
背景:§14 假设 BM25+RRF hybrid 带来 Recall@5 +10%。§14.6 三臂消融(`tests/retrieval/golden.json` 36 认知/65 用例)实测:
- **确定性臂**:hybrid ≡ vector-only(Δ 处处 0,top5 65/65 逐条相同);keyword 仅 3/65 有候选,且命中的 doc 恰是 vector 已排 #1 → RRF 是 no-op。根因:FTS5 trigram/BM25 与 HashEmbedder char-bigram **本质同源**(都靠字面/子串),keyword 能命中处 vector 必也命中。
- **真实臂**(bge-m3,本地 Ollama @ 11435):real-vector overall Recall@5=**0.9667**,real-hybrid=**0.9667**(Δ=0)。
结论:hybrid 在**两条臂上都零增益**;真正的召回提升来自**真实语义 embedder 本身**(0.9667,比确定性基线 0.7154 **+35%**,远超 +10% 目标),而系统本就支持注入真实 embedder(`Embedder` 扩展点)。
决定:**不把 hybrid / `mode` 开关接进公共 API**——无数据支持,避免无用复杂度 + 无谓的 API 变更(铁律 2/4)。`KeywordRetriever` / `HybridRetriever` 作为已测好的 building blocks 留仓、**不导出 `index.ts`**。
caveat:本黄金集偏小、偏语义,**低估** keyword 在大语料 / 稀有精确词 / 错拼 / OOV / 代码标识符场景的价值 → 进 ROADMAP Next,那类 workload 出现时以 keyword 有利的黄金集重评估是否接入。

## D-0009 固化提示词 v2:治闲聊过度记忆 + 软判指标可靠性(§15.3)

日期:2026-07-10 / 状态:已采纳
背景:Phase 2.2 基线发现 `chitchat-negative` 结构仅 21/35——mimo 把纯闲聊(问候/天气/附和)也记成认知。
改动:`src/consolidation/consolidate.ts` 的 `SYSTEM` 提示词加 **v2 守卫**「新材料若只是无实质信息的寒暄 → 四类全空 []」,并**明确保留情绪状态/事实/偏好/目标**(不削弱记≠信、冲突、纠正等其它认知纪律)。
实测(全量 42 场景,真实 mimo,前后对比):总体结构断言 **88.8%→94.2%**(198→210/223);全绿场景 25→30;`chitchat` **21/35→33/35**;`correct` gistRecall 0.43→0.71;overInferRate 0.01→0.00。**无真实回退**。
方法学结论(重要):**gistRecall(LLM-judge 软判)单跑高方差**——`emotion-cap` 曾单跑掉到 0.14,同一 v2 提示词复跑回 **0.57**(>v1 0.43),而结构硬指标全程 32–34/35(≈v1 33/35)稳定。→ **提示词回归以结构硬指标为准**;软判仅供趋势,须多跑取势,不据单跑软分下"回退"结论。

## D-0010 不建 `fixtures:refresh`(对 §15.4 的偏离)

日期:2026-07-10 / 状态:已采纳(人类拍板)
背景:§15.4 要求 `npm run fixtures:refresh` 由 live 运行一键再生成**全部录制夹具**,且再生成后确定性套件必须仍绿。落到本仓库,这条是照着一个**本仓库并不存在的架构**写的。

实测(Phase 2 收尾侦察):
- `tests/fixtures/` 下只有 `memoweft-0.1.0.db`,其 README 头一句即「**不要重新生成**」——它的全部价值是永久冻结,供 `migrations.test.ts` 验「0.1.0 老库经 openStores 无损升级」。重新生成它 = 摧毁它要防的东西。
- 仓库**没有任何 LLM 录制夹具**。确定性来自 19 个测试文件里 **48 处内联手写 fake**(`chat: async …`)。这些不是"某次真实模型的录音",而是**为逼出特定代码路径而编的剧本**——如 `tests/jsonRepair.test.ts` 的 `stubLLM(['{半截', '{"ok":1}'])`,先喂坏 JSON 再喂好的,专测「重试一次能修好」。
- 测试从磁盘读的文件只有四类:`tests/api/api-surface.snapshot`、`tests/consolidation-corpus/corpus.json`、runLog 落盘文件、`tests/retrieval/golden.json`。**无一是 LLM 回放数据。**

决定:**不建 `fixtures:refresh`**。依据(与 D-0007 同一逻辑——不写不会触发的防御代码):
1. **无物可刷**。
2. **真去录制是降级而非升级**:真实模型不会在你需要时乖乖回一段坏 JSON 供你测修复逻辑。用真实录音替换故意编造的边界台词,测试反而覆盖不到边界。
3. **代价撞铁律**:改成回放要动 19 个测试文件,其中 `tests/eval/` 属「只增不改」禁区(铁律 1)。

§15.4 想防的「漂移」是真问题,不因本决策而无人接管——它被拆给三道**已经存在**的闸门:

| 会漂的东西 | 谁接 | 机制 |
| --- | --- | --- |
| **模型行为**漂移 | Phase 2 固化评测 | 42 场景 × 真实 mimo × 结构断言;nightly 全量跑(§15.4)。比回放强:回放只能告诉你「跟去年一样」,评测告诉你「跟期望一样」。 |
| **提示词**漂移 | 哈希闸门 | `tests/prompts/prompt-hashes.snapshot`:改内容而不 bump version → `npm test` 立刻红。 |
| **schema**漂移 | 冻结 .db 夹具 | `tests/fixtures/memoweft-0.1.0.db` + `migrations.test.ts`。 |

Phase 2 验收调整:§15.4 的「`fixtures:refresh` 可用,且夹具再生成后确定性套件绿」一项**作废**,改为指向本决策;`test:live` 与 nightly live job 照做。§15.4 原文「CI 主干 = 录制夹具 = 确定性」一句中的"录制夹具"应读作"内联 fake + 确定性 HashEmbedder"。

caveat:若将来出现**真实录制回放**的需求(例如要冻结某个模型版本的行为、做跨模型对拖回归),重开此议题——届时应新建独立的录制层,而非改写现有 48 处意图清晰的内联 fake。进 ROADMAP Next。

## D-0011 `SKIP_LIVE_LLM` 是死变量,从 CI 删除

日期:2026-07-10 / 状态:已采纳(人类拍板)
背景:`ci.yml` 顶层设 `env: SKIP_LIVE_LLM: '1'`,注释称"标记跳过需真实 key 的 live 用例"。
实测:**全仓(src / tests / bench / scripts)没有任何一行代码读取它**——它只出现在 `ci.yml` 与 `PROJECT_PLAN.md §20`。真正让 live 用例跳过的是 e2e 文件里的 `HAS_LLM = Boolean(MEMOWEFT_LLM_BASE_URL || DLA_LLM_BASE_URL)`;而且 `npm test` 的 glob 是 `tests/**/*.test.ts`,**根本不匹配 `.e2e.ts`**,`test:e2e` 也从未被 ci.yml 调用过。
决定:从 `ci.yml` 删除该 env,注释改写为事实(CI 不注入 LLM secrets → `HAS_LLM` 为假 → live e2e 自动跳过;真实覆盖由 nightly 的 `test:live` 承担)。
理由:配置项撒谎比没有配置项更危险——它让人以为有一道并不存在的闸门。`PROJECT_PLAN.md §20` 的环境变量表已陈旧(列的是 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `EMBEDDER` 等旧抽象),订正进 ROADMAP Next。

## D-0012 recall/ingest 降级语义写入契约(§16.2)

日期:2026-07-11 / 状态:已采纳(人类拍板措辞与 200ms 默认)
背景(动机):Phase 3 AD-6 / §16.2「记忆层故障→降级不中断」。校准发现两适配器的记忆层故障处理不一致且有硬伤:
- **A · adapter-ai-sdk**:recall 读路径 catch 已在但**静默、无超时、无 logger**(`recallMiddleware.ts` 旧 :120 catch→原样返回);写路径 `persistOnEnd` 有 onError 但不重试。
- **B · mcp-server**:tool handler **无 try/catch、无降级、无 logger**——记忆层一抛错即以协议错误上浮 / 进程崩。
契约文(`docs/memory-surface-contract.md` 及 zh-CN)此前**未定义降级语义**——本条补这块空白。

决定(人类已批准的措辞,原样写进契约 §16.2 / 契约文新增第七章):
- **recall 超时**:默认 **200ms,可配**(适配器工厂选项 `recallTimeoutMs`);超时即视为失败。
- **重试**:**读路径(recall)不重试**,直接降级;**写路径(ingest)失败重试一次**再放弃。
- **降级行为**:失败/超时 → **注入空上下文(无记忆),对话不中断**;经**注入的 logger** 记一条(默认无 logger = 静默)。
- **实现边界**:超时用适配器内 `Promise.race` 包 `core.recall`;logger 是工厂可选参。
- **logger 只记结构化降级事件**(形状 `{ event:'memory_degraded', op:'recall'|'ingest', reason:'timeout'|'error' }`,MCP 另带可选 `tool`;两适配器事件形状对齐),绝不记用户内容/原话/密钥(认知纪律 + 隐私)。
- **降级 vs 真错**:只有 `core.recall`/`core.ingestUserMessage` 记忆层内部故障/超时才降级;参数非法、协议层错误这类"调用方的错"仍以错误上浮,不被吞。

破坏性:**无——纯加固**。A 保持"抛错即降级不注入"的既有语义,只是补了超时窗与结构化日志;B 从"崩/协议错误上浮"变为"降级不中断"(对调用方更稳,不是变差)。两适配器工厂新增的 `recallTimeoutMs` / `logger` 均为**可选参**,旧调用方不传照跑(非破坏)。

实现边界(铁律 2):**不碰 Core api-freeze**——超时/重试/logger 全在两个适配器包内(`packages/*/src/degrade.ts` + 各自 `recallMiddleware.ts`/`persistOnEnd.ts`/`tools.ts`),不改 core `src/index.ts` 导出面、不动 `tests/api/api-surface.snapshot`。`npm run api:check` 仍"API 快照一致"。api-freeze 快照本就只枚举 core `src/index.ts`,不含适配器包符号,故适配器工厂加可选参不触快照。

调用方 / 两适配器迁移路径:
- **既有调用方**:无需改动(新参可选,缺省 = 旧行为 + 现在自带 200ms 超时窗与静默降级)。
- **A · adapter-ai-sdk**:`createMemoWeftMiddleware(core, { recallTimeoutMs?, logger? })`;`createPersistOnEnd(core, { …, logger? })`(写路径现重试一次)。宿主要观测降级即注入 `logger`(类型 `MemoWeftLogger`,已从包导出)。
- **B · mcp-server**:`createMcpServer(core, { recallTimeoutMs?, logger? })`(透传给 `registerTools`)。宿主注入 `logger`(类型 `McpServerLogger`,已导出)即可收结构化降级事件。
- **kit**:AD-6 从 N/A 翻 applicable,throw + timeout 两模式真跑,断言 degraded===true 且 logged===true。

## D-0013 AD-3:SourceKind 加 'tool' + core.ingestToolResult(工具结果摄入)

日期:2026-07-11 / 状态:已采纳(人类预批 AD-3 方案,见 CURRENT.md「已批的 AD-3 实现清单」6 步)
背景(动机):Phase 3 §16.1 AD-3「工具结果 → evidence 标 source=tool」。两适配器要能把工具执行的【返回结果】(外部客观数据=合法证据)沉淀成记忆,但 `SourceKind` 无 `'tool'` 值、Core 无语义干净的工具结果摄入 API。scout 结论:加 `tool` 枚举本身免迁移(source_kind 是自由 TEXT 列、无 CHECK 约束),真正的工作量与风险在两个隐形雷(见下)。

决定(人类已批的 6 步):
1. **`SourceKind` 加 `'tool'`**(第 4 个来源种类;`src/evidence/model.ts:11`)——唯一类型改动源。
2. **新增 `config.toolDefaults = { allowLocalRead:true, allowCloudRead:false, allowInference:true }`**,并让 `evidence/store.ts` 的 `put()` 把 `tool` 纳入保守默认分支(把原 `isObserved` 判断扩成 observed||tool 的 `conservative` 分流)。**拆隐私雷**:工具返回值常含敏感外部数据(网页/文件/API 响应),不能掉进通用 else 分支默认上云。
3. **新增 `core.ingestToolResult(ToolResultInput): Promise<Evidence>`**——语义干净的摄入门面(`perceive`+`put`,sourceKind 钉死 `'tool'`;带 originId 幂等)。**触 api-freeze**(本条即影响面说明,人类已批;`npm run api:update` 刷快照)。
4. **图谱视图**:`MemoryGraphStats` 加 `toolEvidenceCount`;evidence 节点 `colorKey` 对 `tool` 单独着色(`buildMemoryGraph.ts`)。
5. **两适配器摄入面**:A(adapter-ai-sdk)新增 `persistToolResults(core, { messages, originIdPrefix? })`——从 `role:'tool'` 消息的 `tool-result` part 提 result payload;B(mcp-server)新增 MCP tool `memoweft_ingest_tool_result`(加进 `WRITE_TOOL_NAMES`,白名单 6→7)。
6. **kit**:AD-3 从 N/A 翻 applicable,两适配器 driver 真跑「工具结果 → +1 tool 证据 + 调用意图不落库」。

**铁律 3a 边界(关键)**:AD-3 只摄入工具的【返回结果】(外部客观数据,合法证据),**不摄入** LLM 的工具调用意图/入参(那是助手输出,禁摄入)。机器化落地:A 的 `extractToolResults` 只读 `role:'tool'` 消息,assistant 消息(含 tool-call part)一概不读;B 的 MCP 注册面无任何摄入 assistant/tool-call 的 tool。两适配器契约测试各断言「落库证据里无一条含调用意图标识串」。

破坏性:**无 —— 纯 additive**。
- `SourceKind` 加值:契约 §5.3「枚举增值规则」允许(消费方应容忍未知来源种类)。
- `config.toolDefaults` / `MemoWeftCore.ingestToolResult` / `MemoryGraphStats.toolEvidenceCount` / `ToolResultInput`:均为新增字段/方法/类型,旧调用方零改动。

影响面(API 快照逐条变更):
- `config` / `MemoWeftConfig` / `DlaConfig`:新增 `toolDefaults: { allowLocalRead; allowCloudRead; allowInference; }`。
- `MemoWeftCore`:新增 `ingestToolResult: (input: ToolResultInput) => Promise<Evidence>`。
- `MemoryGraphStats`:新增 `toolEvidenceCount: number`。
- 新增 `interface ToolResultInput { content: string; hostId?; occurredAt?; originId?; subjectId?; }`。
- `SourceKind` 加 `'tool'`(快照按类型别名名渲染,不展开联合成员,故其行文本不变;快照文本变化来自上述四项)。

已知留待(**出 AD-3 范围 → 进 ROADMAP**):distill/consolidate 喂 LLM 时丢 sourceKind(`distill.ts:56` / `consolidate.ts:146`),工具结果理论上可能被误固化为"用户亲口"。**这是既有特性**(observed 证据也这样),要治得动纪律敏感写路径,不在 AD-3 范围内。

调用方 / 两适配器迁移路径:
- **既有调用方**:无需改动(纯新增)。老库照读(`tool` 是自由 TEXT,无 CHECK/迁移)。
- **Host**:要摄入工具结果调 `core.ingestToolResult({ content, originId? })`(默认不上云);要给某条 tool 证据开上云走 `memory.updateEvidenceAuthorization`(带审计),不在摄入口开口子。
- **A · adapter-ai-sdk**:`persistToolResults(core, { messages, originIdPrefix?, logger? })`——传 `generateText` 结果的 `response.messages`;只 `role:'tool'` 的 `tool-result` 落库,写路径失败重试一次再降级(复用 §16.2 语义)。
- **B · mcp-server**:新增只读性质的轻写 tool `memoweft_ingest_tool_result`(`content`, `originId?`);只存一条 tool 证据,不改画像/不消化/不改授权。

## D-0014 §16.3 适配器 SDK 版本矩阵——矩阵化 dependency(不改 peer)+ 声明范围两端口径

日期:2026-07-11 / 状态:已采纳(人类拍板「矩阵化 dependency,不改依赖类型」)
背景(动机):Phase 3 §16.3 要对两适配器的第三方 SDK 各取「声明的最低支持版」与「当前最新版」组矩阵 job,证明适配器在声明范围内真能跑。分岔:mcp-server 的 `@modelcontextprotocol/sdk` 是 **dependency `^1.29.0`**(非 peer),而 adapter-ai-sdk 的 `ai` 是 **peerDependency `^7`**——两者矩阵化手法是否要统一(把 SDK 也改 peer)。

决定:
1. **不改依赖类型**(人类拍板):mcp-server 的 SDK 保持 dependency。理由:mcp-server 是**自带 SDK 的可执行服务器**(bin `memoweft-mcp-server`,`npx` 开箱即用),SDK 是它的**实现依赖**;改 peer 会破坏开箱即用(要求安装者自己装 `@modelcontextprotocol/sdk`)且语义不对(宿主没道理提供 MCP 协议库)。ai-sdk 的 `ai` 保持 peer(宿主本就用 ai SDK,适配器是中间件)。
2. **统一探针机制**:矩阵 job 用 `npm install <dep>@<版本> -w <pkg> --no-save`(**不写回 lockfile / package.json**)覆盖装指定版本,跑该包 typecheck+test。两包同一套探针,只是版本来源不同(dep range vs peer range)。
3. **与 guardrails 隔离**:`--no-save` 使探针不碰 lockfile(与主 job `npm ci` 的锁定版本物理隔离);缓存用**每组合独立 key** 的 `actions/cache`(`npm-sdkmatrix-<dep>-<version>-<lockfile-hash>`),不交叉污染 guardrails 的 npm cache。既有 lockfile registry guard(`grep npmmirror`)只挡镜像源 URL、不挡版本变更,故探针天然不撞它。
4. **版本口径**:测**声明范围两端** = 最低支持版(peer/dep range 下界)+ **范围内最新**(`ai@7` / `@modelcontextprotocol/sdk@1`,由 npm 在声明大版本内解析)。**不追绝对 latest**:上游发布超出声明范围的**大版本**时,那是「是否扩大支持范围」的主动决策(届时改 package.json + 记 DECISIONS),不该让本矩阵无意义地红。
5. **矩阵红语义**(§16.3):声明范围内出现兼容性破裂(minor/patch 破坏)→ job 红挡 PR → 处理结论(升门槛或适配)记 DECISIONS。

破坏性:**无**。纯 CI 加一个 job,不改任何 package.json 依赖声明、不触 api-freeze、不改 lockfile。

本地实测背书(写 CI 前先跑一遍矩阵):4 组合 `ai@7.0.0` / `ai@7`(=7.0.22) · `@modelcontextprotocol/sdk@1.29.0` / `@1`(=1.29.0)各 `--no-save` 覆盖装 + 该包 typecheck + test **全绿**(ai-sdk 30 · mcp 13);`npm ci` 复原本地 node_modules 成功、lockfile 未被污染。
当前边界事实:`ai` peer `^7`(下界 7.0.0 / 范围内最新 7.0.22);`@modelcontextprotocol/sdk` dep `^1.29.0`(下界=最新=1.29.0,上游暂无新版,两档暂装同版,随上游发布自动分开)。

## D-0015 可注入时钟 Clock(Phase 4 时间注入 · demo 确定性 + 时间旅行)

日期:2026-07-11 / 状态:已采纳(人类拍板「全局可注入时钟依赖」)
背景(动机):Phase 4 §17 demo 要**确定性**(同环境连跑两次输出 diff 为空)+ **--fast-forward 快进时钟**(上周坏心情衰减消失、花生过敏与咖啡偏好留存),且 §17.4 硬约束「demo 只经公共 API 调用核心」。但时间源散落 `new Date()`(证据 recordedAt、认知 created/updated、事件 created、consolidate/attribute/管理审计/runLog),无公共入口注入固定/前进的"现在"。人类在 A(仅门面加 now 参)/B(demo 用 internal 散装件)/C(全局可注入时钟)三方案中选 **C**——最根治:连落库时间也能固定,消除散落的 new Date()。

决定(方案 C,分步落地):
1. 新增 `Clock = () => Date` + `systemClock = () => new Date()`(`src/clock.ts`)。
2. **S1a**(internal):三个 store(evidence/event/cognition)构造加可选 `clock`(缺省 systemClock),落库/更新时间(recordedAt / created_at / updated_at)走 `clock()`。store 构造签名是 internal、不进 api 快照。
3. **S1b**(触 api-freeze):`CreateCoreOptions` 加可选 `clock?`、`openStores` 加可选 `clock?` 参数、工厂透传;导出 `type Clock` + `systemClock`。
4. **留待 S2–S4**(同一决策的后续实现步,不再单开 D):写算子里直接 `new Date()` 的(`consolidate.ts:173`、`attribute.ts:118`、`managementApi.ts` 多处、`obs/runLog.ts`)与读路径 now(`core.recall` / `handleConversationTurn` 内部传给 `recallCognitions`/`decay`/`expire`)统一改走注入的 clock。**在此之前 clock 注入只固定 store 落库时间,尚未做到完整确定性与 fast-forward。**
5. fast-forward 机制:demo 持一个可变闭包 clock(`let t=base; ()=>t`),快进时前移 t,后续读路径(S4 后)用新 now 触发衰减。base 固定 → 两次跑一致。

铁律 3b 守护:Clock 只产**时间戳**,绝不进置信度自算——衰减仍是读时基于 updatedAt 与 clock() 之差,置信度底分由 FormedBy 规则算、不吃时间。S1a 测试已验:注入 clock 不改 confidence。

破坏性:**无——纯 additive**。`CreateCoreOptions`/`openStores` 加可选 `clock`(缺省 systemClock=系统时间,旧调用方零改动、行为不变);新增 `Clock`/`systemClock` 导出。
影响面(api 快照):【S1b】`CreateCoreOptions` 加 `clock?: Clock`;`openStores` 加 `clock?: Clock` 第三参;新增 `type Clock` + `const systemClock`。三个 store 构造签名是 internal 不进快照。【S2】写路径 internal Deps(`ConsolidateDeps`/`AttributeDeps`/`UpdateProfileDeps`)各加 `clock?: Clock`(additive,internal 面宽松规则 §5.5;这三个是导出符号故进快照,走 api:update)。【S3】读路径 `ConversationDeps` 加 `clock?: Clock`(additive,internal 面导出符号,同走 api:update);`core.recall`/`handleConversationTurn` 内部用 `clock()` 作 recall 衰减门控的 now,前进 clock → 淡了的 state 衰减出局、fact 留存(测试实证)。【方案C补·门面路径全覆盖】`MemoryManagementDeps` 加 `clock?`(additive,导出符号,走 api:update);`managementApi`(invalidate/archive 的 invalidAt/archivedAt、checkIntegrity 的 checkedAt、listCognitions 读时衰减的 now)、`managementLog`(审计 createdAt)、`core.graph`/`core.portable`(generatedAt/exportedAt,经已有 opts.now,工厂透传 clock)全走注入 clock。**至此经 `createMemoWeftCore` 门面的所有时间源均可注入**(updateProfile 的 `Date.now()` 是耗时计时、非时间戳,保留)。剩 `asking`(proposeAsk/revisitConflicts 的 askedAt)、`obs/runLog`(ts)—— 非门面路径(散装 dev 算子 / 可选诊断,不被工厂调用),进 ROADMAP。
迁移:既有调用方无需改动(不传 = 系统时间);要确定性/时间旅行的宿主/测试/demo 传 clock(如 `createMemoWeftCore({ dbPath, clock: () => fixedDate })`)。

## D-0016 internals 迁移:boundaries.md 保留中文(D-a 英文单源的例外)

日期:2026-07-11 / 状态:已采纳(人类拍板)
背景:Phase 5 §18.2 把 architecture/boundaries/perf 迁入 `docs/internals/`;D-a 定 internals=英文单源、删 zh 镜像。但 `boundaries.md` 是**纯中文、无英文版**(不像 architecture 有 en+zh、perf 只有 en)——D-a「删 zh 镜像留英文」对它不适用,直接套用会要求凭空翻译一份现成的中文边界文档。
决定:`boundaries.md` 迁 `docs/internals/boundaries.md` **保留中文**,作为 D-a 英文单源政策的显式例外。理由:它是内部维护者向的边界文档、维护者是中文团队,翻译损耗风险 > 语言统一收益;D-a 的「降维护」意图对已存在的中文文档不成立。internals/README 用英文,条目描述用英文、指向的 boundaries.md 正文保留中文。
影响面:无 API / schema 变化(纯文档)。若将来 internals 要对外统一英文,再单独起翻译任务。
既存缺陷(记 ROADMAP,本批不处理):`boundaries.md` 无编号章节,但源码/测试台约 14 处注释引用它的 `§3 / §4.1 / §4.3` 锚点——是既存陈旧锚点(与本次 move 无关),后续给 boundaries 补编号锚点或改注释统一。

## D-0017 无-embedder 召回兜底:NullRetriever → KeywordRetriever（§14.4b 重评估,大语料数据驱动）

日期:2026-07-12 / 状态:已采纳(人类拍板)
背景:D-0008(小黄金集)定 hybrid/keyword 不进公共 API——hybrid≡vector、召回提升全来自真实 embedder;但留 caveat:小集低估 keyword 在大语料的价值,待大语料 workload 出现重评估。Phase 6 §19.2 LoCoMo 矩阵(1536 QA)正是该 workload:
- **真实 embedder 下 hybrid-bge 77.7% ≈ vector-bge 78.6%(微降)** → D-0008 核心结论在大语料上成立:真向量下 hybrid 零增益,`hybrid`/`mode` **仍不进 API**(无数据支持,避免无用 API 面·铁律 2/4)。
- **无/弱 embedder 下 keyword-only 55.3% > 确定性 vector 31.3% / hybrid-hash 50.6%** → caveat 被证实:无真实 embedder 时 keyword 是强基线。而当前 `createCore` 无 embedder → `NullRetriever` → **召回恒空(0%)**。这是真正的落差。
决定:`createCore` 的无-embedder 兜底从 `NullRetriever` 改为 **`KeywordRetriever`**(FTS5 关键词,零嵌入成本);FTS5 不可用(`FtsUnavailableError`,D-0007 探测点)再降 `NullRetriever`(召回空、不崩)。有 embedder / 注入 retriever 照旧(优先级不变)。
影响面(api 快照):**无 API 签名变化、api-surface 快照不变**(不新增导出符号;`KeywordRetriever`/`FtsUnavailableError` 仅 core 内部 import)。行为变:无 embedder 时 recall 不再恒空 → 返回 keyword 命中(**行为增强,非破坏**)。`health().embedReady` 仍 = `instanceof VectorRetriever`(专表"语义/向量召回",无 embedder 时 false)——语义正确,但注释加"embedReady=false ≠ 召回恒空"。
调用方 + 两适配器(mcp-server / adapter-ai-sdk):**无需改代码**,透明拿到更多召回(之前是空);recall 契约"返回相关认知"照旧。
测试/文档同步:新增 `core.test.ts` 的 D-0017 用例(无 embedder → keyword 召回非空 + embedReady=false;隔离测试机 .env 的 EMBED 配置);calibration「缺嵌入→NullRetriever」订正。
迁移:既有调用方零改动;想保持"无 embedder→空召回"旧行为者,注入 `retriever: NullRetriever` 即可(注入优先级最高)。

## D-0018 来源感知固化:distill/consolidate 的 utterance 带来源标注,让固化正确定 formedBy

日期:2026-07-13 / 状态:已采纳(人类拍板)
背景:distill(`distill.ts:57` 硬编码"用户依次说了"、只喂 rawContent)与 consolidate(`consolidate.ts:149` utterances 只给 rawContent)把 **observed(行为观察)/ tool(工具返回)证据也框成用户亲口**;consolidate 的 LLM 自定 `formed_by`(认不出默认 inferred),看不到来源 → observed/tool 派生认知可能被标 `stated`。而 formedBy 定置信底分(stated 600 ≫ observed 350)→ "AI 观察到的 / 工具返回的"被当"用户亲口",伤 fact/guess + 来源强度这一立身之本(铁律 3 精神)。
决定:新增内部 `src/evidence/sourceLabel.ts`(sourceKind→`[用户说]/[行为观察]/[工具返回]/[AI 推测]` 前缀);distill、consolidate 的 utterance 视图每行带来源前缀;distill 提示词(v1→v2)从"只总结用户表达"泛化为"保留来源区分",consolidate 提示词(v2→v3)formed_by 规则据来源定([行为观察]=observed、[工具返回] 绝不 stated、[用户说] 明确=stated、自己推断=inferred)。**加固**认知纪律(3b 底分更准),3a(助手输出不成证据)与「只标冲突,不替换」「support_evidence_ids」等纪律措辞**一字不改**。
影响面:**无 API / schema 变化**(`sourceLabel` 不导出 index.ts,api-surface 快照不变;formedBy 是既有字段,只更准)。改两条提示词 → prompt-hashes bump(distill v2 / consolidate v3,走 `npm run prompts:update`)+ 按 §15.3 重跑固化评测。**不改任何 eval 断言值**(语料 `expect` 不 assert 新认知 formedBy;eval 测试用 stubLLM)。
回归(§15.3 前后对比,全量 42 场景 · mimo):结构断言 95.1%→94.2%(212→210/223)、全绿 32→29;overInferRate 全程 0.00。**判定为单跑方差、非回退**:下降全在 D-0009 记录的高方差盘(emotion-cap 34/35→31/35)与已知 fact-vs-state 口径噪声(no-over-inference 29/34→28/34),而 **D-0018 真正作用的 conflict 盘(observed 证据)纹丝不动(40/42→40/42)**;94.2% 恰是 D-0009 v2 中枢值,before 的 95.1% 是偏高单跑;chitchat-negative 33/35→35/35(↑)。
迁移:既有调用方零改动(内部写路径改动);tool 标注 by-construction 覆盖(语料按写路径不含 'tool',AD-3 外,不 eval-测)。

## D-0019 no-over-inference 的 fact-vs-state 缺口 = ContentType 缺「事件」型的已知定义局限(记录·不改)

日期:2026-07-13 / 状态:已采纳(人类拍板 C·记档不改)
背景:固化评测度量清理②(ROADMAP Next,接度量清理① D-... 之后)。no-over-inference 纪律 7 场景在 baseline(commit `744cd7e`)结构分 28/34,丢的 5 分**全部**是 `created类型⊆{types}` 一项不符;CURRENT 曾概括为「模型标 fact、语料期望 state」。**实跑数据(`bench/consolidation-baseline.json`)校准后发现分歧非单一,实为三类,且全部发生在 `overInferRate=0.00` 之上**(真正的过度推断靶心——不升级成 trait/诊断——7 场景全过):
- **4× 一次性完成事件 fact-vs-state**:CC-031 周六加班 / CC-033 今天没吃早饭 / CC-034 listened to sad songs / CC-035 删了聊天记录 —— 语料期望 `state`,模型标 `fact`。
- **1× 从单次行为推 goal**:CC-029 搜「怎么找女朋友」—— 语料期望 `fact`,模型标 `goal`(轻度过度)。
- **1× 从单次行为推 preference**:CC-032 买斯多葛的书 —— 语料期望 `fact`,模型标 `preference`。

根因(scout 核实,带文件:行号):`ContentType`(`model.ts:15-23`,8 值)**没有「事件」型**。而 `fact` = 确定 + 永久不衰减 + 不封顶(`config.ts:135` 不列入 halfLifeDays);`state` = 临时 + 半衰期 1.5 天 / 7 天过期 + **置信封顶 300 + credStatus 只能 {candidate,low}**(`confidence.ts:20-22,45-47`;`config.ts:113-115`)。一次性已完成事件是「**确定发生 + 无需长留**」:标 `fact` 让鸡毛蒜皮永久污染画像,标 `state` 给确定事件上"低置信"(state 的封顶是为**情绪**的记≠信设计,非为确定事件)。两个格子各对一半,模型选 fact、语料选 state 都站得住——**是类型系统缺格子,不是谁错**。consolidate 提示词(v3)对「一次性事件标哪个」**零指引**(`prompts.ts:40` 只列枚举),纯交 LLM 自判。

决定:**记录为已知定义局限,不改任何源码 / 语料 / 提示词 / schema**。依据(与 D-0009 同一逻辑——软/灰指标不据以下结论,硬指标为准):
1. **纪律的真靶心达标**:`overInferRate=0.00`,模型未把任一次观察升级成 trait/诊断/人格标签。`created类型⊆{types}` 咬的是 fact/state/goal 的**定义灰区**,不是过度推断——评测器在用"过度推断"的尺子量"类型定义分歧"。
2. **三个改法都不划算**:①改提示词逼 event→state 要 bump + 重跑 §15.2(77min)+ 高方差盘回归风险,且没解决"确定事件被上低置信封顶"的语义瑕、只把分歧藏进提示词;②改语料期望(铁律 1)放宽 types 会顺带盖章 CC-029 的 goal 轻度过度;③加 `event` 型是**永久扩公开类型面**(api-freeze 铁律 2 + 语料铁律 1 + config 语义 + 全套文档 + 两适配器)——为一个 `overInfer=0` 的**非缺陷**化妆品问题扩类型系统 = 铁律 4 过度工程,且 LLM 分类模糊、可能只把分歧挪位。
3. 这 5 分标为**定义噪声**,非模型缺陷、非回退依据(同 D-0009 对 gistRecall 单跑方差的处置)。

**将来的正解(伏笔,现不做)**:真需要区分"确定但短暂的事件"时,加一个 `event` ContentType(不封顶置信、给中等衰减/过期窗、credStatus 走正常档),让"确定发生 + 会淡出"两个诉求都满足。届时应**由真实产品需求驱动**(如宿主要事件时间线)单独立项,走 D-xxxx + 影响面 + `api:update`,77min 重跑由该功能买单。进 ROADMAP Later。
影响面:**无**(纯文档记录,不改 API / schema / 语料 / 提示词 / eval 断言)。

## D-0020 补全 D-0015 时钟不变式:asking 的 askedAt / runLog 的 ts 也可注入 clock

日期:2026-07-13 / 状态:已采纳(人类拍板批准 —— 明知无当前消费者仍取"补全不变式")
背景(动机):D-0015 方案 C 让"经 `createMemoWeftCore` 门面的所有时间源可注入 clock",但明确遗留两处**非门面路径**的 `new Date()`:①`asking`(`proposeAsk.ts:152` / `revisitConflicts.ts:126` 的 `askedAt`)②`obs/runLog`(`runLog.ts:139`/`161` 的 `ts`)。scout 核实:这两条路径**不经 `createMemoWeftCore` 工厂装配**,唯一调用方是 dev 调试台 `testbench/server.mjs`,且当前无注入需求。本条补全,使"全仓时间源皆可注入"这一 D-0015 立的不变式**真正完整**——避免将来若把 asking 接进工厂、或要 testbench 确定性时踩"askedAt/ts 竟不可注入"的坑。

取舍(记录):这撞铁律 4(没消费者不扩 API 面,同 D-0007/D-0010/D-0008)。Integrator 建议 B(记档为刻意例外、不做);**人类选 A**(补全)——理由:D-0015 已把 clock 注入做成宽面能力(`Clock`/`systemClock` 已导出、多处 Deps 带 `clock?`),两处漏网是**latent 不一致**,补它便宜(纯 additive、照 `ConsolidateDeps` 现成范式)且消除未来 footgun。

决定(照 D-0015 的 S2 范式):
- `ProposeAskDeps` / `RevisitDeps` / `RunLoggerOptions` 各加**可选** `clock?: Clock`;对应 `askedAt`/`ts` 从 `new Date()` 改 `(deps.clock ?? systemClock)().toISOString()`(runLog 用 `this.opts.clock`)。
- 铁律 3b 守护:clock **只产时间戳、绝不进置信度自算**——测试实证注入 clock 不改 confidence(`clockInjection.test.ts` D-0020 四例:proposeAsk/revisitConflicts/runLog 注入 → 时间戳=注入值 + confidence 不变;+ 回归缺省=系统时间)。

破坏性:**无 —— 纯 additive 可选字段**。旧调用方(含 testbench)零改动,缺省 = systemClock = 系统时间、行为逐字不变。
影响面(api 快照逐条,走 `api:update`):`interface ProposeAskDeps` / `RevisitDeps` / `RunLoggerOptions` 各加 `clock?: Clock`。`Clock` 类型已导出,**无新增导出符号**。这三者属 internal 档(契约 §II:proposeAsk/revisitConflicts internal、RunLogger 是 dev logger),不经 `CreateCoreOptions.clock`。
迁移:既有调用方零改动;要确定性 askedAt/ts 的(testbench 确定性 / 测试)在对应 Deps/Options 传 clock 即可。契约文 en/zh 的 §15(clock)订正 D-0015 遗留的"staged as follow-up"陈旧句(该 follow-up 已由 D-0015 后续 + 本条补完);CHANGELOG 同步。**至此全仓时间源皆可注入,无散落 `new Date()` 时间戳**(`updateProfile` 的 `Date.now()` 是耗时计时、非时间戳,保留)。

## D-0021 召回解释:core.recall({ explain }) 让召回认知带支撑证据链(可追溯)

日期:2026-07-13 / 状态:已采纳(人类批准)
背景(动机):召回质量 v2 的「召回解释」子特性。此前 `core.recall` 只回 `{content, confidence, credStatus, score}`——一个光秃秃的相似度分,看不到"这条记忆**为什么/凭什么**冒出来"。而"记忆建立在哪些证据上"正是 MemoWeft**"可追溯记忆"卖点的正中靶心**。scout 核实:证据链**认知里本就存着**(`cognitionStore.sourcesOf(id)` → `EvidenceLink[]`),召回时只是没去取;且 `core.recall`(`createCore.ts`)手里**已有 evidenceStore + cognitionStore**,富化可在门面做、不必动底层 `recallCognitions`/`RecallDeps`。

范围切分(诚实):「召回解释」有两半——**证据链**(便宜、库里现成、最贴卖点)与**命中词**("匹配了哪些 query 词",信息在 `RetrievalHit={id,score}` 检索层就丢了,要贯穿检索器)。**本条只做证据链**;命中词留后按需再做(不塞进这版)。

决定:
- `RecallInput` 加 **`explain?: boolean`**(缺省 false/不传 = 零额外查询、行为逐字不变)。
- `RecalledCognition` 加 **`provenance?: RecalledEvidence[]`**(带 explain 时才有);新增导出类型 **`RecalledEvidence = { evidenceId; relation: EvidenceRelation; summary; sourceKind; allowCloudRead; allowInference }`**(支撑/反证证据简报,summary 用现成惯例 `e.summary || e.rawContent`)。
- `core.recall` 实现:拿到召回项后,若 explain → 逐条 `sourcesOf(id)` + `evidenceStore.get(evId)` 组 provenance;**`recallCognitions` / `RecallDeps` 不动**(门面富化)。证据已不在(悬挂链)则 flatMap 跳过、不凭空造字段。

认知纪律 / 隐私(**经对抗审查加固**):①`provenance` 面向宿主(进程内返回,库**不自动喂云**);但 `summary` 是证据**原文**(可能比派生认知更敏感、含云受限的 observed/tool,默认 `allowCloudRead=false`)→ `RecalledEvidence` **随附 `allowCloudRead`/`allowInference` 授权位**(对齐姊妹 API `buildMemoryGraph` L158-159),让宿主转发云模型前能按 tier 自筛;write-path 的 `filterReadableByTier` 不受影响。②`explain` opt-in、默认关;③纯读、不碰置信度自算。**(初版 `RecalledEvidence` 只 4 字段、未带授权位——对抗审查判为真实隐私元数据缺口:宿主拿到原文却无从判断哪条不可上云、违反库自设的 buildMemoryGraph 惯例;据此补授权位。3 个 nit(悬挂链静默跳过 / contradict 措辞 / 无去重)判非阻断:悬挂链保留"不凭空造字段"、措辞已统一为"支撑/反证"、去重由 consolidate 加链时 dedup 保证。)**

破坏性:**无 —— 纯 additive**。`explain?` 不传 = 旧行为;`provenance?` 可选。
影响面(api 快照逐条,走 `api:update`):`RecallInput` 加 `explain?: boolean`;`RecalledCognition` 加 `provenance?: Array<RecalledEvidence>`;新增 `interface RecalledEvidence { evidenceId; relation; summary; sourceKind; allowCloudRead; allowInference }`(引用既有导出的 `EvidenceRelation`/`SourceKind`)。`RecalledCognitionItem`(底层)不变。契约文 en/zh §18/§23 订正;CHANGELOG 同步。
迁移:既有调用方零改动;想要召回解释的宿主传 `recall({ query, explain: true })`,读每条的 `provenance`。测试:`recallExplain.test.ts` 两例(explain 带出正确证据链含 summary/relation/sourceKind;不传 explain → 无 provenance)。

## D-0022 召回按 contentType 过滤 + 结果暴露类型

日期:2026-07-13 / 状态:已采纳(人类批准)
背景(动机):召回质量 v2 的「content 过滤」子特性。此前 `core.recall` 无法按类型筛——宿主想"只召回 fact/preference,别把临时 state 塞进来"(如生成用户档案摘要要稳定事实)做不到;且召回结果不带 `contentType`,宿主看不到每条是什么类型。scout 核实:认知本就有 `contentType`(8 型)这个稳定维度,recall 里 `c` 已在手,只是没暴露也没筛。("purpose" 认知层不存在,只有无关的 LLMPurpose——本条只做真实存在的 contentType,purpose 留后。)

决定(门面过滤,同 D-0021 手法):
- `RecallInput` 加 **`contentTypes?: ContentType[]`**(允许名单;不传/空 = 全类型、行为不变)。
- `RecalledCognitionItem` 加 **`contentType: ContentType`**(recallCognitions 从 `c.contentType` 填,顺带暴露类型);`RecalledCognition` 加 **`contentType?: ContentType`**。
- `core.recall`:若传 contentTypes → 门面 `items.filter(it => allow.has(it.contentType))`。**recallCognitions 只加填字段、不加过滤参**;过滤在门面(同 explain,是门面特性)。

已知取舍(诚实,文档标注):这是**控制**(给宿主按类型筛)、非"变聪明"。**后过滤会欠填**:过滤在 retriever 取完 top-K **之后**(同 similarity/衰减门控层)——若 top-K 命中里无匹配类型,返回 <topK 甚至空(测试有此极端例)。这是后过滤固有取舍,与既有门控一致。

破坏性:**无 —— 纯 additive**。`contentTypes` 不传 = 全类型;新字段 additive(`RecalledCognitionItem.contentType` 必填但 recallCognitions 恒填,不破坏消费方;`RecalledCognition.contentType?` 可选)。
影响面(api 快照逐条,走 `api:update`):`RecallInput` 加 `contentTypes?: Array<ContentType>`;`RecalledCognitionItem` 加 `contentType: ContentType`;`RecalledCognition` 加 `contentType?: ContentType`。`ContentType` 既有导出。契约文 en/zh §18/§23 订正;CHANGELOG 同步。
迁移:既有调用方零改动;想按类型筛的宿主传 `recall({ query, contentTypes: ['fact','preference'] })`。测试:`recallExplain.test.ts` D-0022 例(两类认知 → 按类型筛只留指定类型 + 结果带 contentType + 无匹配类型返空)。经对抗审查(3 视角×怀疑者)。

## D-0023 召回负反馈 = Mute(仅从召回静音,认知仍 active、仍参与画像演化)

日期:2026-07-13 / 状态:已采纳(人类拍板 Mute 语义 + 批准多步实现)
背景(动机):召回质量 v2 的「负反馈」子特性——用户标"这条召回没用"→ 系统别再召回它。scout 核实全库**无任何现成的"用户导向召回抑制"概念**,是全新信号,要动 schema。语义有岔路(mute/downweight/复用 archive/只记录),人类选 **Mute**。

**语义决策(人类拍板 Mute)**:新增 `mutedAt` 状态位。**召回跳过它,但它仍 active、仍参与 consolidation/画像演化**。填补一个真实空白——此前只能 invalidate(不再为真)或 archive(全面雪藏),没有"这条挺好、但别老推给我"的中间档。阶梯:**mute(仅召回)⊂ archive(召回+画像演化雪藏)⊂ invalidate(不再为真)**。为何不选另三个:downweight 太模糊(碰召回打分、难调、近"学习")、复用 archive 太重(连画像演化都停,超出"这条召回没用"本意)、只记录太没劲(库不做事)。

决定:
- schema:cognition 加 `muted_at TEXT`(SCHEMA 常量 + store 五处映射:CognitionRow/fromRow/put/insert/update + CognitionPatch)。
- **迁移走 `store.migrate()` 缺列补(非 migrations.ts formal v2)——对提案的偏离,实现中定**:提案原写"首个 formal v2",但实现暴露真摩擦:①formal v2 只在 openStores/runMigrations 路径生效,**直接构造老库**(不经 openStores)拿不到列(migrations.test 的直接构造老库用例即证 `no column named muted_at`);②它与 3 个用 version 2 的"假 v2"测试撞号。故改走 store.migrate 缺列补(与 archived_at/asked_at 同族的既有范式):对**任何构造路径**都稳、零测试破坏。formal migrations.ts 路径留给将来真需版本化/备份/数据变换的迁移(nullable 状态位加列不需要;同铁律 4 精神,不为足够的场景引更重机制)。这正是提案里标的"最大风险点" + 给人类的"老土办法"选项,实现验证后取之。
- 召回门控:`recall.ts` archived 门控后加一行 `if (c.mutedAt) continue;`。**不碰 `active()`**——这是 mute≠archive 的定义性区别(muted 仍在 active 集,仍被索引、仍参与 consolidation)。
- 管理 API:`muteCognition({ cognitionId, muted, reason }): Cognition | null`(muted:true→mutedAt=now / false→null,审计 op 'mute'/'unmute';不存在返 null)+ 新 `MuteCognitionInput`。
- 便携包:自动继承(整对象序列化 `data.cognitions: Cognition[]`,新列随包走;import 侧 `?? null` 兼容旧包);**不 bump `BUNDLE_SCHEMA_VERSION`**(纯加列向后兼容)。

认知纪律:**铁律 3b**——`mutedAt` 与 confidence **正交**,`muteCognition` 只动 mutedAt、不碰 confidence/credStatus(测试实证静音前后 confidence 不变);不进 `ConfidenceInputs`。3a/3c/3d 不涉及。

实现细节(**对抗审查加固**):初版只在 recall.ts 加门控,审查发现真质量回归——muted 认知仍 active 故被 updateProfile 索引进检索器,门控后跳过又不补足 top-K,静音几条主导某话题的认知会**永久占满 top-K 槽、饿死同话题其它召回**(且不像 archive/invalid 会随 active 变化自愈)。**修:muted 从召回索引排除**——updateProfile 重建索引时 `active().filter(!mutedAt)` 后再 indexAll,故 muted 不占检索槽;`active()` 本身不动 → consolidation/attribute 仍见 muted、仍演化(mute≠archive 不变)。recall.ts 门控留作【刚静音、索引尚未重建】那段窗口的守门(双保险)。**残留(可接受)**:mute 后到下次 updateProfile 之间 muted 仍在旧索引占槽,靠门控保证绝不召回、重建即消。

破坏性:**无 —— 纯 additive**。新列(nullable)、新可选管理方法、Cognition 新可选字段;旧库经 store.migrate 补列、旧调用方零改动、旧包 import `?? null`。
影响面(api 快照逐条,走 `api:update`):`Cognition`/`CognitionPatch`/`CognitionWithMeta`/`CognitionWithSources` 各加 `mutedAt?: string | null`;`MemoryManagementAPI` 加 `muteCognition`;新增 `interface MuteCognitionInput { cognitionId; muted; reason }`(导出)。契约文 en/zh 订正;CHANGELOG 同步。
迁移:既有调用方零改动;宿主收到"这条召回没用"→ 调 `core.memory.muteCognition({ cognitionId, muted: true, reason })`;恢复传 `muted: false`。测试:`recallExplain.test.ts` D-0023 例(mute→不召回但仍在 listCognitions/confidence 不变、unmute→恢复召回、不存在→null)+ 既有 migration 测试(0.1.0 冻结库经 store.migrate 得 muted_at、fresh vs 迁移库 schema 收敛)。经对抗审查(3 视角×怀疑者)。

## D-0024 召回 v2 端到端收口:透传到两适配器 + MCP 新增 mute tool + provenance 按 tier 预筛

日期:2026-07-13 / 状态:已采纳(人类批准三岔口:MCP mute tool=**加**、provenance tier 预筛=**②**、Reranker 走 **α**另立不在本条)
背景(动机):D-0021/22/23 让 `core.recall` 门面支持 explain→provenance、contentTypes 过滤 + 结果带 contentType、muteCognition 负反馈,但两适配器(adapter-ai-sdk / mcp-server)仍停在 v1 召回形状、新面未透传(`ROADMAP.md:15` / CURRENT deferred)。本条把召回质量 v2 四件**端到端**落到宿主可用。

决定:
- **A · adapter-ai-sdk(纯 additive)**:`MemoWeftMiddlewareOptions` 加 `contentTypes?` / `explain?` 透传进 `core.recall`;`onRecall` 回调类型从被窄化的 `RecalledLike` 扩为带 `id?/contentType?/provenance?/score?`(从 `memoweft` import `ContentType`/`RecalledEvidence`,松耦合紧一档,知情取舍)。**隐私硬约束**:provenance **绝不进注入 prompt 的 `buildKnowledgeBlock`**(证据原文含云受限项,会绕过 tier),只经 `onRecall` 交宿主自筛。mute:读中间件不含(A 的 AD-9 声明 N/A)。
- **B · mcp-server**:①`memoweft_recall` input 加 `contentTypes?`/`explain?`(zod optional,ContentType enum 手动复刻并与 `src/cognition/model.ts` 8 值对齐),输出加 `contentType`、explain 时加 `provenance`;②**provenance 按 tier 预筛(岔口②)**:只回 `allowCloudRead=true` 的 summary,受限项只回 `evidenceId+relation`、隐去 summary(与 mcp-server "tool 证据默认 local-only" 姿态一致——库自持隐私,不把决定权全推协议对端);③**新增 `memoweft_mute_cognition` 写 tool(岔口①,契约变更非纯 additive)**:转发 `core.memory.muteCognition`,进 `WRITE_TOOL_NAMES`(2→3)/`ALL_TOOL_NAMES`(7→8)、更新 `ToolName` 与不注册黑名单注释、同步 `server.test` 集合断言。突破 `tools.ts` 自述"写工具只存原料证据"边界,故走本 D-xxxx + 人类批准。
- **adapter-kit**:`RecallFixtureItem` 加 `contentType`/`provenance`(含混合 `allowCloudRead` 以测 tier 预筛);新增 **AD-7**(contentTypes 过滤生效)、**AD-8**(explain 带出 provenance 含授权位)、**AD-9**(mute 闭环:mute→召回消失 + confidence 不变,仅 B applicable,A 声明 N/A);golden 重刷(A 文本块 provenance 不进块故大概率不变;B `ad4-mcp.json` 加 contentType 会变)。

认知纪律 / 隐私:**铁律 3b**——mute 与 confidence 正交(AD-9 断言 mute 前后 confidence 不变);provenance 授权位(D-0021 加固的隐私元数据)透传时 A 由宿主自筛、B 由 tool 层按 tier 预筛,均不把证据原文无差别喂云。3a(助手输出不成证据)在两适配器摄入面**不涉及本条**(只动召回读面 + mute 写面)。

破坏性:A 全 additive;B 的 recall 增强 additive;B 的 mute tool 是白名单契约扩张(additive 地加一个可逆、不改上云授权、与 confidence 正交的轻写 tool,老客户端不调不受影响)。

影响面(api 快照):**不触 core api-freeze**(快照只枚举 core `src/index.ts`,适配器符号不在内——`scripts/api-snapshot.mjs:21` / D-0012);改动全在两适配器包 + `tests/adapter-kit`;各适配器 golden(AD-4/7/8)按正常流程刷新。契约文 `docs/reference/memory-surface-contract` en/zh 补两适配器 v2 透传面 + MCP mute tool + provenance tier 预筛策略;CHANGELOG 同步。

迁移:既有调用方零改动(A 新选项可选、B 新参可选、mute tool 新增)。想用 v2 面的宿主:A 传 `createMemoWeftMiddleware(core, { contentTypes, explain })` 读 `onRecall` 回调的 provenance/contentType;B 调 `memoweft_recall` 带 `contentTypes`/`explain`、调 `memoweft_mute_cognition` 做负反馈闭环。

## D-0025 §16.5 新增 Claude Agent SDK 适配器(hooks 型进程内)+ MCP 挂载备选文档

日期:2026-07-13 / 状态:已采纳(人类批 hybrid 形态)
背景(动机):§16.5「至多新增一个适配器作 adapter-kit 试金石」的 stretch。adapter-kit 已被 Phase 3 证明(AD-1…AD-6 全绿 + D-0024 加 AD-7/8/9)。候选 = **Claude Agent SDK(TS)**,与本会话运行环境契合最高。SDK 事实(claude-code-guide 查官方文档核实,带 URL):`@anthropic-ai/claude-agent-sdk` 是**进程内库**、原生消费 stdio MCP server(`mcpServers` 选项);hooks 齐全——`UserPromptSubmit`(每轮前可注入)/`PreToolUse`(调用意图)/`PostToolUse`(工具结果)/`Stop`·`PostToolBatch`(轮结束),带 `session_id`/`tool_use_id` 作幂等键。

决定(人类批 hybrid):
- **新包 `packages/adapter-claude-agent-sdk`**(`@memoweft/adapter-claude-agent-sdk`),peer-dep `@anthropic-ai/claude-agent-sdk` + `memoweft`(照 D-0014:宿主自带 SDK 的中间件型 → peer,同 ai-sdk)。
- **召回注入**:`UserPromptSubmit` hook(或 systemPrompt append)注入 recalled memory(照 A 的 `buildKnowledgeBlock` 中性措辞、逐字对齐 Core action.ts)。
- **用户原话摄入**:`UserPromptSubmit` 闭包捕获原文 → `ingestUserMessage`(spoken)。
- **工具结果摄入**:`PostToolUse` hook → `ingestToolResult`;**只注册 `PostToolUse` → LLM 工具调用意图/入参 by-construction 排除**(铁律 3a,比 A/B 更机器化)。
- **降级**:复用 §16.2 语义(200ms recall 超时 / 读不重试 / 写重试一次 / 注入 logger),`degrade.ts` 可整段照搬 adapter-ai-sdk。
- **幂等**:`session_id`/`tool_use_id` 作 originId。
- **接 adapter-kit**:AD-1…AD-6 全绿 + 继承 D-0024 的 AD-7/8/9。
- **备选路径(hybrid 的"a"半)**:README/recipe 文档化「Agent SDK 用 `mcpServers` 直接挂 `@memoweft/mcp-server`」的近零代码路径。
- **交付(§16.5 口径)**:kit 全绿 + 可运行示例 + README(en+zh)+ 本 D-xxxx。

破坏性:**无 —— 纯新增包**。不触 core api-freeze(只消费既有 core 门面 recall/ingestUserMessage/ingestToolResult,不加 core 符号;`scripts/api-snapshot.mjs:21` 只枚举 core `src/index.ts` / D-0012)。
影响面:`packages/*` 放包即自动纳入 workspace(根 package.json workspaces 无需改);CI guardrails +3 步(该包 typecheck/test/build,排 Core Build 后);§16.3 SDK 版本矩阵**可选** +1 行(声明范围两端);apps/plugins 之外的新包默认会被 `npm publish --workspaces` 发布(设 publishConfig.access=public、非 private)。
实现风险(记录,落地时验):hook 修改 prompt 注入的确切返回 API、以及能否干净拿到**未注入的用户原话**(guide 对此仅 60% 把握)须对**安装的 SDK 实测核对**,不臆测;若 `UserPromptSubmit` 拿不到纯原话,fallback = streaming input 捕获或如实声明该点限制(AD-2 相应调整)。SDK 自带 native 二进制,CI 安装偏重(装包步骤时限放宽)。
迁移:宿主新装该适配器,把 hooks 传进 Agent SDK options;或走备选:`mcpServers` 挂 `@memoweft/mcp-server`。

## D-0026 Reranker NO-GO:真实检索序近最优、fusion 净负,不实装(记录·同 D-0008 手法)

日期:2026-07-13 / 状态:已采纳(人类拍板"接受 NO-GO·记档 + 提交 bench")
背景(动机):ROADMAP Next「Reranker 实装」。Phase 1 时间紧整体下放,源码**无** Reranker 接口/no-op(绿地)。本轮做了 α→β 两步数据驱动调查(纯 bench,不碰 src/api):
- **α(合成判别集 · `bench/rerank-golden.json` 11 用例,信号隔离)**:证明重排"能显差异"的**上界**——MMR 修冗余(αnDCG@3 +0.324,真 bge-m3 交叉验证 =1.000)、fusion 修 recency/confidence(nDCG +0.55/+0.54);且 fusion 是纯内部 sort、**不触 api-freeze**,MMR 需候选向量 seam、**触 api-freeze**。
- **β = 先验真实序(人类批)· `bench/rerank-realorder.mjs`**:用真 bge-m3 在真实黄金集 `tests/retrieval/golden.json`(65 用例)上量。**真实检索序近最优**(nDCG@5=**0.9112** · Recall@5=**0.9667**,与 `bench/retrieval-after.md` 的 real-vector 逐位吻合;**53/65 零缺陷**、top-5 inversion 仅 12 例);**fusion 在真实序上端到端净负**(ΔnDCG@5=**−0.0425**,帮 2 害 12;放大重排池更差 −0.13;权重扫描全负,唯一非负是"fusion 什么都不做")。

机制(可泛化到本集之外):effConf/credStatus 是**逐认知、与 query 无关**的先验;相关性是**逐 query** 的。检索器已排好逐 query 语义序,再叠一个 query 无关的固定逐认知先验只能**稀释**——数学上任何固定逐认知元数据都无法跨异质 query 与相关性正相关。

决定:**Reranker 判定"现有数据不支持",不实装(NO-GO)**。同 D-0008 证伪 hybrid、"B 靶子=度量假象"的手法(调查清楚、软/上界指标不据以下结论,真实硬指标为准):真实序无缺陷可修 + fusion 净负 → **不为真实系统不出现的问题加装置(铁律 4)**。α+β 的 bench(判别集 + 三臂评测 + 真实序验证 + 报告)入仓作背书。

诚实边界(伏笔,不做):`golden.json` 的 `expect` 是**纯语义**相关性标注(不含 recency/confidence 意图),且认知**无真实 confidence/credStatus/时间戳**(β 的元数据是按 contentType 机械合成的、非按 query 调)。故本结论严格是"fusion 会破坏一个已近最优的语义序";**不能**排除"带真实元数据的语料(dogfood cognition 层 / LoCoMo)+ query 有隐含时效/可信度意图"下 fusion 有益。**将来若 dogfood 暴露真实序确有"陈旧/低置信/冗余靠前"的次优(本黄金集未见),以带真实元数据的新 tranche 重启评估**(进 ROADMAP;fusion 纯内部 sort、不触 api-freeze 的判断仍成立,届时直接可用)。LoCoMo 本轮跳过(`LOCOMO_PATH` 未设 + 需全 pipeline 产带元数据认知,更大工程)。
影响面:**无**(纯 bench + 记档,不改 src / api / schema / eval 断言 / 提示词)。

## D-0027 新增 OpenAI Agents SDK 适配器(run-wrapper 型进程内)—— 更多适配器批次 ①

日期:2026-07-14 / 状态:已采纳(人类「按顺序来」推进 ROADMAP Next「更多适配器」;形态由 SDK 缝决定,adapter-kit 已被 3 适配器证明)
背景(动机):ROADMAP Next「更多适配器(OpenAI Agents / LangChain / LlamaIndex),待 adapter-kit 被证明后批量做」。人类「按顺序来」→ 按序批量。三家并行侦察(查官方源码/文档)结论:均 feasible;**OpenAI Agents 裁决 clean**(三缝各有一等官方 API、3a 比 claude-agent-sdk 更纯)。本条 = 批次第一个。

决定:
- **新包 `packages/adapter-openai-agents`**(`@memoweft/adapter-openai-agents`),peer-dep `@openai/agents`(^0.13,0.x pre-1.0 → 保守窄范围)+ `memoweft`(^0.5);devDep 镜像二者供离线契约测试(照 D-0014:宿主自带 SDK 的中间件型 = peer)。
- **召回注入**:`RunConfig.callModelInputFilter`(模型调用前编辑 instructions/input);**guard 只在用户回合注一次**(该 filter 每次模型调用含工具回合都触发);若宿主已传自己的 filter → **chain**(先跑宿主再追加召回块,不覆盖)。
- **用户原话摄入**:run-wrapper 闭包捕获 `run()` 的 input 实参(未注入的原文)→ `ingestUserMessage`(spoken)。
- **工具结果摄入**:run 结束后扫 `RunResult.newItems` 筛 `tool_call_output_item.output` → `ingestToolResult`;`tool_call_item`(调用意图/入参)是**独立 item 类型**、只筛 output 型 → 调用意图永不在作用域(**铁律 3a by-construction,比 Claude PostToolUse 更纯**)。
- **工厂**:`createMemoWeftRunner(core: Pick<MemoWeftCore,'recall'|'ingestUserMessage'|'ingestToolResult'>, opts?) → { run; callModelInputFilter; persistToolOutputs }`(opts 镜像 A:subjectId/lang/contentTypes/explain/onRecall/callModelInputFilter/recallTimeoutMs/ingestTimeoutMs/logger)。**实现订正**:侦察草案曾写第三件为 `hooks`,但实测 `run()` 无 hooks 选项(RunHooks 是 Runner 实例的 event-emitter、塞不进 run()),故第三件改为可测的 `persistToolOutputs(newItems)`(③ 的单一代码路,自驱动 run 的宿主拿 result.newItems 后手动调)。
- **隐私(同 A/claude-agent-sdk)**:provenance/contentType/score 绝不进注入块(callModelInputFilter 的注入文本走 `buildKnowledgeBlock`,只用 content/confidence/credStatus),只经 onRecall 交宿主自筛。
- **降级 §16.2**:callModelInputFilter 内 `withTimeout(200ms)` 包 recall、超时/抛错 → 返回原 modelData 不注入 + logger(filter 返回 ModelInputData 即天然「降级为不注入」);ingest 走 `runIngestWithRetry`(超时不重试防重复、真错重试一次);绝不向 SDK 抛。
- 接 adapter-kit **AD-1…AD-9**(ad5/ad9 声明 N/A,同 A/claude-agent-sdk);示例 + 双语 README;`degrade.ts`/`knowledgeBlock`/kit 驱动范式复用。

破坏性:**无 —— 纯新增包**,不触 core api-freeze(只消费 memoweft 门面三方法,不改 core 导出面)。
影响面:`packages/*` 自动纳入 workspace;ci.yml guardrails +3 步(typecheck/test/build,排 Core Build 后)+ 发布注释同步;§16.3 版本矩阵可选(0.x 保守,或暂缓同 D-0025);publishConfig.access public、非 private。
实现风险(落地对安装的 SDK 实测,不臆测):`@openai/agents` 0.x churn(peer 保守窄范围);`callModelInputFilter` 在声明下界版本是否已存在须实测;`tool_call_output_item` 结果项的 originId 字段名(callId vs id)须对 .d.ts 核对;`callModelInputFilter`/`hooks` 是**单值** RunConfig 选项 → 与宿主自带的需 chain/merge(wrapper 设计决策)。
迁移:宿主用 `createMemoWeftRunner(core).run` 替代直接 `run()`,或取 `callModelInputFilter`/`persistToolOutputs` 自组进自己的 `run`/`Runner`。

## D-0028 新增 LangChain 适配器(retriever + callback 型)—— 更多适配器批次 ②

日期:2026-07-14 / 状态:已采纳(人类「按顺序来」推进更多适配器;形态由 SDK 缝决定)
背景(动机):更多适配器批次 ②。侦察裁决 **workable-with-caveats**——**LangChain callbacks 是观察-only**(实测 @langchain/core@1.2.2:CallbackManager `await handler.handleChatModelStart?.(...)` 丢弃返回值),故召回注入**不能走 callback、必须走 Runnable/retriever**。这与另 3 个适配器「hook/filter 返回值注入」是结构性不同。

决定:
- **新包 `packages/adapter-langchain`**(`@memoweft/adapter-langchain`),peer-dep **`@langchain/core` ^1**(**只锁 @langchain/core**、不依赖上层 `langchain` 编排层 → peer 面最小;BaseRetriever/BaseCallbackHandler/Document 都在此包)+ `memoweft` ^0.5;devDep 镜像(照 D-0014:宿主自带 SDK 的中间件型 = peer)。
- **读(召回注入)= `MemoWeftRetriever extends BaseRetriever`**:`_getRelevantDocuments(query)` → `core.recall({ query, contentTypes, explain })` → `Document[]`(`pageContent`=content、`metadata`=confidence/credStatus/id/contentType)。另给薄函数 `formatMemoWeftDocs`(=`buildKnowledgeBlock` 中性措辞)供宿主拼进 prompt。宿主用标准 RAG 组合(`RunnablePassthrough.assign` / prompt `{memory}` 变量 / `MessagesPlaceholder`)。**隐私(D-0024)**:provenance 绝不进 `pageContent`(会被注入 prompt);provenance/授权位只经 `onRecall`(或 `Document.metadata` 明确标注 host-facing、不注入)。
- **写(工具结果)= `MemoWeftWriteCallback extends BaseCallbackHandler`**:**只实现 `handleToolEnd`**(工具返回结果 → `ingestToolResult`),**不声明 `handleToolStart`**(`handleToolStart` 给的是调用意图/入参 string)—— CallbackManager `if (handler.handleToolStart)` 便不投递 → **调用意图串永不到达本 handler**(铁律 3a by-construction,「少声明一个方法」即物理隔离,比逐个运行时判别更彻底)。经 `config.callbacks` 挂。
- **写(用户原话)= 宿主闭包 `persistUserTurn(core, { text, originId })`**(同 ai-sdk:调用点持有原话,不从注入后 prompt 回捞)。
- **明确不实现 `BaseChatMessageHistory`/`BaseMemory`**:那是短期对话历史,与 MemoWeft 画像级语义记忆正交;实现它会语义错位(把长期记忆当逐轮历史)。
- 工厂:`createMemoWeftLangChain(core, opts) → { retriever, writeCallback, formatKnowledge, persistUserTurn }`(或分件导出)。
- **降级 §16.2**:`_getRelevantDocuments` 内 `withTimeout` 包 recall、超时/抛错返回 `[]` + logger;writeCallback/persistUserTurn 走 `runIngestWithRetry`(超时不重试、真错重试一次);绝不向链抛。
- 接 adapter-kit **AD-1…9**:AD-4 `recallSurface` 用 `formatKnowledge` 文本块(text-block,en/zh golden,与另 3 适配器一致 + 隐私安全);ad5/ad9 N/A。示例 + 双语 README。`degrade.ts`/`knowledgeBlock`/kit 驱动复用。

破坏性:**无 —— 纯新增包**,不触 core api-freeze(只消费门面三方法)。
影响面:workspace 自动纳入;ci.yml guardrails +3 步 + 发布注释;publishConfig.access public。
实现风险(对安装的 .d.ts 实测):`BaseRetriever`/`_getRelevantDocuments` 与 `BaseCallbackHandler.handleToolEnd`(input=意图 string / output=结果)确切签名;`handleToolEnd` 的 `output` 形状(ToolMessage/string/结构化)照 `toolOutputText` 规整;originId 用 `handleToolEnd` 的 `runId` 或事件里的 `tool_call_id`(实测承载字段);峰值:确认 `@langchain/core` 0.3→1.x 上述签名稳定(range 若要兼容 0.3 用 `>=0.3 <2`)。

## D-0029 新增 LlamaIndex 适配器(memory-block + stream-tap 型)—— 更多适配器批次 ③(末)

日期:2026-07-14 / 状态:已采纳(人类「按顺序来」推进更多适配器;形态由 SDK 缝决定)
背景(动机):更多适配器批次 ③(末)。侦察裁决 **workable-with-caveats**——LlamaIndex 有原生 memory 抽象(`BaseMemoryBlock`)可由 MemoWeft 实现召回注入;写走 agent stream 的透传式 tap。精确对齐 adapter-ai-sdk 的「读=中间件 / 写=onEnd」拆分。

决定:
- **新包 `packages/adapter-llamaindex`**(`@memoweft/adapter-llamaindex`),peer-dep **`llamaindex` ^0.12 + `@llamaindex/workflow` ^1.1.24 + `memoweft` ^0.5**;devDep 镜像(照 D-0014:宿主自带 SDK 的中间件型 = peer)。
- **上游弃维取舍(人类拍板·记档)**:落地时发现 `@llamaindex/core` / `@llamaindex/workflow` 最新版被 npm 标 "deprecated and no longer maintained",而维护中的伞包 `llamaindex@0.12.1` 未标弃维、却仍依赖它们(整个 granular 层弃维、LlamaIndex.TS 尚在重构中)。人类选**重定向到伞包**:①**直接依赖弃维的 `@llamaindex/core` 已彻底去掉**——BaseMemoryBlock/MemoryMessage/MessageType 改从伞包 `llamaindex` import(伞包 `export *` re-export 了 `@llamaindex/core/*`),`@llamaindex/core` 降为伞包的传递依赖;②`@llamaindex/workflow`(事件驱动 agent API 唯一出处、伞包**不** re-export)去不掉,仍是直接 peer——但它正是维护中的伞包 `llamaindex@0.12.1` 自身的常规依赖,**与伞包同舟**。诚实结论:重定向甩掉了最糟的直接弃维依赖,残留的 `@llamaindex/workflow` 是 LlamaIndex.TS 现代 agent API 的必经之路;README/CHANGELOG 明记此取舍,待上游包结构稳定再收敛。
- **读(召回注入)= `createMemoWeftMemoryBlock(core, opts)` 返回一个 `BaseMemoryBlock`**:塞进 `createMemory({ memoryBlocks:[block] })`,再 `agent({ llm, tools, memory })`——block 在每轮把召回的记忆作为 memory context 供模型。**隐私(D-0024)**:provenance 绝不进 block 输出文本(照 `buildKnowledgeBlock` 只用 content/confidence/credStatus),只经 onRecall。
- **写(用户原话 + 工具结果)= `persistFromAgentStream(core, stream, { userMessage, originId })` 透传式 stream-tap**:包住 `agent.runStream(userMsg)`,**原样 re-yield 全部事件**、顺路摄入——用户原话(`userMessage` 由宿主显式持有传入)→ `ingestUserMessage`(spoken);工具结果(`agentToolCallResultEvent` 的 result)→ `ingestToolResult`;**调用意图(`agentToolCallEvent`)不摄**(铁律 3a——只认结果事件类型)。
- 降级 §16.2 复用(memory block 内 recall withTimeout 降级为不注入 + logger;写走 runIngestWithRetry:超时不重试、真错重试一次;绝不向 stream 抛/中断)。
- 接 adapter-kit **AD-1…9**(AD-4 recallSurface 用 block 的召回文本 text-block golden;ad5/ad9 N/A);示例 + 双语 README;`degrade.ts`/`knowledgeBlock`/kit 驱动复用。

破坏性:**无 —— 纯新增包**,不触 core api-freeze(只消费门面三方法)。
影响面:workspace 自动纳入;ci.yml guardrails +3 步 + 发布注释;publishConfig.access public。
实现风险(对安装的 .d.ts 实测,不臆测):`llamaindex`/`@llamaindex/*` 确切包名与版本;`BaseMemoryBlock` 抽象接口(实现召回注入要 override 哪个方法、返回什么形状——ChatMessage[] / 文本?);agent stream 事件类型(`agentToolCallResultEvent` vs `agentToolCallEvent` 的字段、result/toolCall id 承载);memory block 注入是每轮动态还是静态。

## D-0030 召回质量 v2 尾项两半均 DEFER(purpose 过滤 / 命中词半)——只读侦察 + 对抗验证,不实装(记录·同 D-0026 手法)

日期:2026-07-14 / 状态:已采纳(人类拍板两半均"记 D-xxxx defer")
背景(动机):ROADMAP Next 队列 ③「召回质量 v2 尾项」的最后两半——**purpose 过滤** 与 **召回解释的「命中词」半**(D-0021 只做了证据链半)。本窗派只读 scout 三维度(purpose 影响面 / 命中词影响面 / 适配器-先例)+ 对每维承重判断派怀疑者对抗验证(21 agent、0 error;发现的几处 overstatement 已订正、不改结论)。三 scout 独立结论一致:两半**现无真实消费者/触发,按铁律 4 均 defer**——与 Reranker D-0026 NO-GO、相似度阈值收口、D-0019 event 型「记录不做」同纪律。**影响面:无**(纯只读侦察 + 记档,不改 src / api / schema / eval 断言 / 提示词)。

### 半 A · purpose 过滤 —— 无连贯定义(不同于 contentType+scope)、无 backing data、无消费者
- **无 purpose 字段(代码层)**:认知层只有 `contentType`(8 型)+ `scope`(自由文本「适用场景」);`LLMPurpose='chat'|'write'` 是选 LLM 客户端、无关(`src/cognition/model.ts:15-23,49-50`;`src/llm/pool.ts:18`)。("purpose" 作为**待办概念**在 `ROADMAP.md:15` / D-0022 背景 / `CURRENT.md` 有记——本条即其收口;代码里无字段。)
- **scope 是死字段(src/ 内 write-dormant)**:三个认知写入方(consolidate/trends/attribute)**全不填 scope** → 恒 null;src/ 内无任何 populator,唯一能写非 null 的是 testbench 调试端点(非管线)(`src/consolidation/consolidate.ts:89`、`src/background/trends.ts:123`、`src/attribution/attribute.ts:181`;`src/cognition/store.ts:169`)。→ 按 scope 过滤在有 populator 前**恒返空**。
- **无消费者**:参考宿主只从 recall 取 `{content, score}`,连已上线的 `contentTypes` 过滤都从没调过(`apps/memoweft-host/src/server.ts:269`)。
- **contentType 已覆盖已知用例**:D-0022 立此特性时举的动机(「只召回 fact/preference 做档案摘要、别塞临时 state」)**就是 purpose 式过滤**,已端到端过 6 适配器(`DECISIONS.md:291`;`src/core/createCore.ts:363-366`)。
- 机制:若 purpose≠contentType(已能筛)、也≠scope(死字段),它便是**宿主任务意图**(摘要/人设/安全)——那是 query 侧属性、不属认知层。**四选项 A 新立维度 / B 映射 scope / C 映射 contentType / D 不做** 中,A 需造无值分类法 + prompt/eval populator + 贯穿 6 适配器(REJECT);B 需先做 scope populator 才有值、且自由文本过滤脆(reject-unless-scope-made-real);C 即"已被 contentType 覆盖";**D = 采纳**。
- **重启条件**:出现真实宿主/dogfood 召回需求,是 contentType(判断类型)+ 一个**做实的 scope**(适用场景)都无法表达的——届时先定分类法 + populator(prompt + §15.x eval),**再**加过滤参,不先扩冻结面。

### 半 B · 命中词(matched terms)—— 生产 VectorRetriever 路径恒空、只在无-embedder keyword 兜底才亮、无消费者
- **信息确在检索层丢**:`RetrievalHit={id,score}`,命中词必须**贯穿导出的 Retriever 契约**才能带出——**无 D-0021 那种门面捷径**(provenance 便宜是因 `cognitionStore.sourcesOf()` 门面已有;命中词只活在 `retriever.search()` 内)(`src/retrieval/retriever.ts:9-13`;`DECISIONS.md:275`)。
- **VectorRetriever 无离散 term**:整 query 嵌成一个稠密向量算余弦——命中词语义上不存在,诚实答案=空(`src/retrieval/vectorRetriever.ts:106-116`)。
- **生产路径恰是 vector**:有 embedder(本机 bge-m3)→ VectorRetriever;KeywordRetriever 只是**无-embedder 兜底**(D-0017);HybridRetriever 根本没接线、未导出(`src/core/createCore.ts:241-243`;`src/retrieval/hybridRetriever.ts:15`)。→ 做出来只在没人跑的兜底臂才有内容。
- **keyword 侧可取但有损**:需给 FTS5 SELECT 加 `highlight()`/`snippet()` + JS 后处理;默认 trigram 分词 → 取回的是**文档 3-gram 片段**(非干净 query 词)、<3 字 query 词永远漏(`src/retrieval/keywordRetriever.ts:24,70-75,167-173`)。(注:此为"若按 FTS5 highlight 实装"的前瞻 caveat——当前代码**尚无** highlight/snippet,对抗验证据此订正过 scout 一处把它写成现有行为的 overstatement。)
- **不能干净 explain-gate**:terms 源自门面**之下**,会随非-explain 召回一起流出(除非门面回头剥掉,别扭)——与 explain 门控的 provenance 结构不同(`src/core/createCore.ts:367`)。
- **无消费者**:宿主 recall 是「future 记忆气泡(步6)」占位、前端未必显示;D-0021 与 ROADMAP 均标「命中词留后按需」(`apps/memoweft-host/src/server.ts:268-270`;`DECISIONS.md:275`;`ROADMAP.md:15`)。
- 若将来做,**唯一干净形态**已侦定:`RetrievalHit.terms?: string[]`(可选、additive、只触 api-snapshot 一行 `tests/api/api-surface.snapshot:153`、对注入的自定义/Null/Vector retriever 结构安全——它们留 undefined 即合契约)→ 贯穿 `recall.ts` 的 `RecalledCognitionItem` → 门面 → `RecalledCognition.matchedTerms?`(`Retriever.search` 签名不变、返回类型只加可选字段);KeywordRetriever 走 FTS5 highlight 填值,Vector/Null 留空;两适配器只经 onRecall / mcp 输出透传(命中词=调用方原话、低敏)。
- **重启条件**:出现真实宿主 explain/「记忆气泡」UI,或一个给命中词召回打分的 eval,构成 D-0021/ROADMAP 等的那个「按需」触发;届时 `RetrievalHit.terms?` 形态直接可用(触 api-freeze → 走铁律 2 完整流程 + 本条已备的两适配器迁移路径)。

## D-0031 下一轮两阶段规划 + memoweft/weftmate 产品边界(9/10 归 weftmate,memoweft 只留接口)

日期:2026-07-14 / 状态:已采纳(人类拍板)
背景(动机):Phase 1–6 收尾,规划下一轮。原 ROADMAP Later 的 7–11(7 事件类型 / 8 Python 移植 / 9 REST·多租户·Postgres 后端 / 10 SaaS·Web 管理台·多模态·CRDT 同步 / 11 大规模适配器)提上日程。但 **9/10 与 memoweft「是库、不是应用」的宪章(CLAUDE.md/AGENTS.md)+ Non-goals(「不把仓库扩成产品/桌面路线」)直接冲突**——而产品那摊现在正是 weftmate。人类拍板:**守库身份**,9/10 归 weftmate(产品/服务层),memoweft 只留接缝。

决定:
- **产品边界**:REST server · 多租户 · Postgres/pgvector 后端 · SaaS · Web 管理台 · 多设备同步(原 Later 9/10)= **weftmate 的活**;memoweft **不做服务本体**,只保证有接缝可接(存储后端可换[现钉 node:sqlite/better-sqlite3] / 多模态证据[现仅 text rawContent] / 同步[便携包 exportBundle/importBundle 已有])。**CLAUDE/AGENTS/Non-goals 不改**(库身份、「不把参考宿主变产品/不扩桌面路线」仍成立)。
- **官网试用 demo**(简单 WebUI 供用户体验)= 到时候项:搭在上述接缝上。**「真体验」**(用户真输入、真看到记忆固化)浏览器跑不了内核(要 Node+sqlite+模型端点+key)→ 需迷你后端 → 沾服务化、归 weftmate;**「演示版」**(预置/录好交互、不接真内核)纯前端、memoweft demo/`site/` 可承。归属到时候按形态再划,不预先定死。
- **下一轮两阶段**(详见 ROADMAP「下一轮」节):
  - **阶段 1(建设)**:1.1 事件类型(打头,dogfood 驱动,见 D-0019)/ 1.2 适配器规模化 / 1.3 Python 移植 / 为 9/10 留接口的**接缝审计**(先只读侦察缺口)。
  - **阶段 2(打磨·dogfood 驱动)**:人类在 weftmate 实际用一段 → 真实使用暴露的问题回头优化 memoweft;是所有「等触发才做」deferred 项(事件型 / Reranker D-0026 / 命中词·purpose D-0030 / 阈值 / 手感 / 性能)的**触发收割场**。铁律 4:成片才修(D-xxxx)、一次性记档。预演模板 = 2026-07-14 拼豆 dogfood 只读复现。

破坏性:**无 —— 纯规划记档 + ROADMAP 更新**(7/8/11 从 Later 提到「下一轮」、9/10 标归 weftmate),不改 src/api/schema/eval/提示词/宪章。落地各条(尤其 1.1 事件类型触 api-freeze)时各自走铁律 2 完整流程 + D-xxxx。
影响面:ROADMAP.md 新增「下一轮」节 + Later 标注;本条。CLAUDE.md/AGENTS.md/PROJECT_PLAN.md 宪章**不动**(边界是"9/10 出 memoweft 范围"、非"改库身份")。

## D-0032 画像整理攒批阈值 `profileUpdate.batchSize` 5 → 12(dogfood 调参)

日期:2026-07-14 / 状态:已采纳(人类授权定值,dogfood 调参)
背景(动机):`config.profileUpdate.batchSize` 控制"攒够几条新对话才触发一次 `updateProfile`(整理画像)"。原值 5(注释即标"dogfood 后调");dogfood 讨论中判 5 太勤。宿主(weftmate + 参考宿主)的 `scheduler.ts` 动态读此值,不硬编码。
决定:默认值 **5 → 12**。理由:①**更省 token**——每次整理最贵的是 consolidate 把「整个现有画像」重发给模型比对;整理次数 ∝ 1/batchSize,batch↑ → 整理次数↓ → 画像重发次数↓ → 总 token↓(注释原文"太勤又费"即此);②**上下文更足**——攒更多轮再整理,distill 手里用户自己的前后文更多、引用消解更准(如"不喜欢[拼豆]"靠前文补全);③代价是单批事件摘要略粗 + "记忆气泡"更迟冒,但 `idleMinutes:30` 空闲兜底不变(静默期照常整理,现实中多数批本就不满 12)。
破坏性:**无**——纯默认值调整,不改配置**类型**结构(`profileUpdate:{batchSize:number;idleMinutes:number}` 不变)→ **不触 api-freeze**(快照记类型非值,`api:check` 不变);无测试硬钉 batchSize;宿主动态读、自动跟随。
影响面:`src/config.ts:132` 一行值 + 注释;`tests/api/api-surface.snapshot` 不变;CHANGELOG。**记忆气泡的"扎堆+迟到"UX 后果 = weftmate 侧待办**(memoweft 只递 newCognitions 列表、不管显示;缓议)。

## D-0033 新增 `confirmed`(附和)来源强度 + AI 上下文注入 distill/consolidate(分期实现)

日期:2026-07-14 / 状态:已采纳(人类批准·分期实现·三决定按 Integrator 推荐)
背景(动机·产品驱动非投机):weftmate 是**爱主动问**的伴侣("你喜欢爬山吗?"→"嗯")。这类**孤儿回应**的信息**只藏在 AI 那句里**、用户自己的话啥也没带 → 当前 distill 只看用户话 → **存不了**(丢失真实自我披露)。且这是 3a 要防的自我印证最危险的一面(AI 编 X、用户一句"是的"就洗成事实)。经只读侦察 + 对抗验证(scout 1 可行性 CONFIRMED)。

决定(机制):
- **AI 上下文注入(只读、不成来源)**:`handleConversationTurn` 是**先存后答**——存用户"是的"那刻,working memory 里**还留着上一轮 AI 那句**(`conversation.ts:119-120` 尚未 push 当前轮)。在 `conversation.ts:90` 存证据前抓下它,作为**可空、不可溯源的上下文列 `preceding_ai_context`** 挂在用户证据行。distill/consolidate **本就逐行读证据**、顺路读到,穿过 12 轮攒批延迟仍在。**3a/3d 结构性守住**:该列**无自己的证据 id** → 溯源白名单(`consolidate.ts:136,157` 只认真证据 id)永收不进它。触及 weftmate 真实聊天路(`server.ts:366` handleConversationTurn ✓;agent 干活路走裸 ingestUserMessage、不产画像、不涉及)。**裸 ingestUserMessage 路捕不到**(限于走 Conversation 的路)——可接受(孤儿确认发生在聊天、非 agent 干活)。
- **`confirmed` 来源强度**:`FormedBy` 加第 5 值。底分 **280**(夹 inferred 200 与 observed 350);**自然封顶** 280+支持满 200 = **480 < limited 500** → 纯附和顶天"低置信"、单次"候选";`deriveCredStatus` 零改(不看 formedBy)。**在 consolidate 里赋**(formedBy+溯源在那定),非 distill。
- **只有主动说才升级**(新逻辑):现 `consolidate.reinforce`(`:216`)从不改 formedBy;新增:stated 强化 confirmed 认知时 confirmed→stated,破 480 天花板。**AI 带你确认的永远低档,你自己捅出来的才够"比较确定"。**
- **不进任何规则聚合**:`trends.ts:78` 排除 `formedBy==='confirmed'`(防诱导灌成 ruled);attribution 等其它聚合面同审。
- **窄范围(提示词软判)**:只对**短/具体/原子**的 AI 命题、用户直接点头才产 confirmed;长文档/一大段 + 含糊"好"→**不产出**;复合("喜欢爬山、不喜欢游泳")拆多条。**诚实**:窄范围是提示词软判、非结构保证——**真保证是结构墙**(280/480 封顶 + trend 排除 + id 白名单),对抗测试必须证:**哪怕提示词判漏、结构墙也拦得住**。

三决定(人类批):①**AI 文本外泄口**:导出**剥离** `preceding_ai_context`(`exportBundle.ts`)+ `listEvidence` 不当证据显示——防 AI 话溜出机器/被当证据。②**结构墙为真保障**(见上)。③**采纳 stated→confirmed 升级**。

**分期(人类批·推荐)**:
- **Phase 1(结构层)**:①1a `FormedBy`+config `confirmed:280`+`VALID_FORMED`+trend 排除 + **封顶回归测试**(机械·自读守门);②1b 证据列 `preceding_ai_context` + v2 迁移 + perceive/conversation 捕获 plumbing + reinforce 升级逻辑 + 导出剥离 + listEvidence 处理 + **对抗测试**(诱导风暴)——**语义敏感、派重对抗审查**。
- **Phase 2**:distill+consolidate 提示词 bump(教认附和 + 窄范围 + 拆分 + 只溯源用户)+ **全量 eval 重跑贴前后分**(§15.3/D-0009)。

破坏性/影响面(铁律 2 完整流程):`FormedBy` 联合加值在快照**不透明**(不 diff)、但 `baseByFormedBy` 内联类型 + `Evidence` 接口会 diff → **api:check 变红 → api:update**;证据表 v2 迁移(nullable 列,旧库/裸 ingest 无碍);`BUNDLE_SCHEMA_VERSION` 视导出剥离决定(剥离则不 bump);两提示词 bump + eval;契约 en/zh + CHANGELOG。认知纪律四点测试覆盖(尤其 3a:AI 上下文永不成来源;3b:confirmed 底分由规则算)。

## D-0033 实现进度

- **Phase 1a 已落**(本会话):`FormedBy` 加 `confirmed`;`baseByFormedBy` 类型+值加 `confirmed:280`;`VALID_FORMED` 加 `confirmed`;`trends.ts` 排除 confirmed;回归测试证"纯 confirmed 封顶 480<limited、永不达 limited/stable"。inert(暂无产出路径,待 1b/2 接通)。api:update(baseByFormedBy 类型 diff)+ typecheck + 新测试绿。
- **Phase 1b 已落**(2026-07-15·下窗·**Plan B** 实现,见上「实现设计」):证据 `preceding_ai_context` 列(SCHEMA + `migrate()` 缺列补)+ `EvidenceInput.precedingAiContext`(只写)+ `EvidenceStore.precedingAiContextOf`(唯一读取,供注入)+ conversation 捕获(先存后答抓上一轮 AI)+ distill/consolidate 经隐私门注入(`aiContextSuffix`,只作原话后缀、不铸带 id 条目)+ reinforce `confirmed→stated` 升级(LLM 判 stated ∧ 有 spoken 支撑,经 `CognitionPatch.formedBy`)。**结构无泄漏**:字段不进 Evidence 读结构/`fromRow` → exportBundle/listEvidence/MCP/TurnOutcome 物理拿不到(**零剥离代码**)。**对抗测试 16 例**(`precedingAiContext.test.ts` 结构+捕获+注入+无泄漏+缺列补迁移;`confirmedLaundering.test.ts` 3a 白名单/封顶/trends 排除/升级门+护栏)。gate:**npm test 326/326** · typecheck 干净 · **api:check 一致**(api:update:EvidenceInput+EvidenceStore+SqliteEvidenceStore+CognitionPatch 四处 additive、Evidence 读结构不 diff)· 契约 en/zh + CHANGELOG。**落库前派重对抗审查工作流**(诱导灌爆 laundering 核心靶)。
- **待续**:Phase 2(distill+consolidate 提示词 bump 教认附和/窄范围/拆分/只溯源用户 + §15.3 全量 eval 重跑贴前后分)。

### Phase 1b 实现设计(2026-07-15·下窗·**人类拍板 Plan B**·经 4-scout 侦察交叉核实)

侦察(api-surface / migration-schema / leak-surface 三 scout·CONFIRMED)在动手前揪出**两处对原计划的结构化改进**,均**朝认知纪律更强的方向**,人类拍板采纳:

**偏移① 字段"只写不读"、不进 Evidence 读结构(结构无泄漏 > 剥离)**。原计划"字段挂 `Evidence` 接口 + 导出/listEvidence 剥离"要在 3 处记得剥离、且 listEvidence 返回类型要么 `Omit<>` 要么容忍类型层可见。**Plan B**:`precedingAiContext` 只加到 **`EvidenceInput`**(写入用)+ 新增专用只读方法 **`EvidenceStore.precedingAiContextOf(id): string|null`**(给 distill/consolidate 注入用);**不进 `Evidence` 接口、不进 `fromRow`**。因 `fromRow` 是逐字段手写映射,列虽在 SQLite 里、但读结构永不带它 → **exportBundle / listEvidence / MCP `list_evidence` / host `/evidences` / `TurnOutcome.storedEvidence` 全部物理上拿不到该字段,零剥离代码,AI 话不外泄=结构保证**(不靠"记得剥离")。契合 D-0033「真保证是结构墙」哲学。
  - **api-freeze diff(全 additive,3 行)**:`EvidenceInput`(+`precedingAiContext?`)、`EvidenceStore`(+`precedingAiContextOf`)、`SqliteEvidenceStore`(+`precedingAiContextOf`)。`Evidence` 接口**不 diff**(比原计划更小)。`BUNDLE_SCHEMA_VERSION` **不 bump**(字段结构上进不了 bundle)。
  - **reinforce 的 confirmed→stated 升级**:触发判据 = LLM 在 reinforce 上标 `formed_by:'stated'`(与 `new` 路同款语义分类,3b:分数仍规则算)**且**该次强化引用的支撑原话里有 `spoken` 来源(结构护栏:observed/tool/inferred 永不能升 stated)。纯附和(LLM 标 confirmed)永不触发。需给 `CognitionPatch` 加 `formedBy?`(+ cognition store `update` 的 SET)→ 该处 api 快照 additive diff。Phase 1b 落**机器 + 确定性对抗测试**(脚本 LLM 驱动);production 触发要 Phase 2 提示词教会 reinforce 标 formed_by。

**偏移② 迁移走 store 本地 `migrate()` 缺列补,非 formal migrations.ts v2**(同 D-0023 muted_at 先例·人类批)。证据表**当前无 `migrate()`**(证据自 0.1.0 未加过列)→ 须**新增整个 `migrate()` 方法** + 构造末调用,镜像 `cognition/store.ts:144-157`(`pragma_table_info` 判 + `ALTER TABLE evidence ADD COLUMN preceding_ai_context TEXT`)。理由同 D-0023:nullable 状态列、缺列补对**任何构造路径**(含直接构造老库、不经 openStores/runMigrations)都稳;formal v2 留给需版本化/备份/数据变换的迁移。`LATEST_SCHEMA_VERSION` 仍 v1(asked_at/archived_at/muted_at 已立此先例,无新前向兼容风险)。`migrations.test.ts` 的 **schema 签名收敛测试**(fresh vs 迁移自 0.1.0 fixture)自动兜住"忘了补列"、frozen `memoweft-0.1.0.db` fixture 证旧库无损打开。

**注入 plumbing(Phase 1b 落线,对 eval 语料为 no-op)**:distill:59 / consolidate:152 的 `.map` 在 `filterReadableByTier + allowInference` **之后**建 LLM 行 → 注入即被隐私门守住;把 `precedingAiContextOf(e.id)` 的文本**追加进该原话的 text**(带清晰 AI-上文标签)、**绝不铸成带 id 的 `{id,text}` 条目**(否则进 `validEvidence` 白名单)→ AI 上文无 id、`pickSupport`(consolidate:157)结构性引不到 = **3a/3d 守死**。既有 eval 语料无 precedingAiContext(null)→ 注入是 no-op → Phase 1b 不改 eval 分;Phase 2 提示词教会用它 + 重跑 eval。

## D-0034 v0.6 交互语义模型升级 · Phase 1(Context 基础设施)

日期:2026-07-16 / 状态:已采纳(人类批准影响面报告 `docs/internal/v0.6-impact-report.md`·四决策拍板)
背景(动机·产品驱动):当前链路 Evidence→Distill→Event→Cognition 听不懂依赖上下文的短回答("是"/"后者"/"可能吧"),而 D-0033 的附和捕获只建在 Conversation 路上——侦察发现真实产品 **weftmate 全程走裸 `ingestUserMessage`、从不经 Conversation**(`server.ts` 无 `handleConversationTurn`·HEAD+工作树 grep 皆空;**DECISIONS.md:487 的旧断言「weftmate 走 handleConversationTurn(server.ts:366)」有误**),故 D-0033 的 preceding/confirmed 在真实产品**从未生效**。v0.6 把「理解人机对话」升级成结构化交互语义模型,并让它在裸 ingest 路真正生效。

四决策(人类 2026-07-16 拍板):① **可改 core**——让裸 ingest 路也能带上下文,不死守 Conversation-only;② **formedBy 全面接管**——所有来源标签由代码从语义解析算(Phase 3);③ **resolver 顺手做**——融进 distill/consolidate,不新建独立 step;④ **v0.6 吸收 D-0033 Phase 2**,event 型(阶段 1.1)另排。次要:⑤ 两张新表**进便携包**;⑥ Episode 边界**宿主可选传 + 库内 idle 兜底**;⑦ 分期照报告 Phase 0–5。

**Phase 1 已落(本会话·Context 基础设施)**:
- **两张新表**(照 `management_log` 模板:挂共享连接、构造函数 `CREATE TABLE IF NOT EXISTS`、**不进 formal migrations**、`LATEST_SCHEMA_VERSION` 仍 v1、schema 收敛测试自动兜住):`interaction_context`(id/subject_id/conversation_id/episode_id/context_json/context_hash/created_at——**加了规范草案没列的 `subject_id`** 用于多 subject 隔离 + 按 subject 导出)+ `semantic_resolution`(id/evidence_id/resolved_content/response_act/prompt_act/proposition_origin/assertion_strength/required_context/resolver_version/created_at)。两个 store。**semantic_resolution 表 Phase 1 只建结构、不产数据**(Phase 2 resolver 填);一次建好两张表 = schema/bundle 一次稳定,减少 api-freeze 反复。
- **core 承担上下文管理**(修头号问题):新 `InteractionSession`(per-conversation working memory + episode idle 切分,缺省 30 分钟)。`ingestUserMessage` 加 `conversationId?`/`episodeId?`——带 conversationId 时用 session 抓上一轮 AI 填进 **`EvidenceInput.precedingAiContext`(复用 D-0033 结构墙列)** → 下游 distill/consolidate 注入逻辑一字不改即对**裸 ingest 路**生效(头号问题修复,**零改 Cognition 逻辑**);同时落一条 interaction_context。新门面方法 `recordAssistantReply({conversationId,content})`:宿主把自建 agent 循环生成的 AI 回复报告给 core、push 进 session(**只作后续上下文、永不落证据**,3a)。weftmate 侧接线量 = 两处调接口(ingest 带 convId + AI 回复后调 recordAssistantReply),AI 文本全在 core 内部流转。
- **便携包 v2**:`BUNDLE_SCHEMA_VERSION` 1→2,`data` 加 `interactionContexts?`/`semanticResolutions?`(可选,向后兼容 v1 包 `?? []`)+ `ImportPlan.counts` 加两计数;export/import/validate 接入。**结构墙不受影响**:interaction_context 含 AI 文本但仍是独立表、永不进 consolidate 白名单(3a/3d 与是否进包正交)。
- **测试**:`interaction.test`(store CRUD/幂等/隔离/clock,8 例)+ `interactionCapture.test`(InteractionSession 单元 + 端到端捕获证头号问题修复 + 结构墙无泄漏 + episode 切分 + 便携包往返,7 例);portable/migrations 收敛保绿。gate:**npm test 340/340**(仅 api-freeze 待 api:update)· typecheck 干净。

**Phase 1 范围管理(诚实)**:聚焦 **ingest 路**(头号问题所在)。**conversation 路(`handleConversationTurn`)的 interaction_context 落库延到 Phase 2 统一**——memoweft-host 的 D-0033 preceding 已工作,不落 interaction_context 不影响现有功能,且保 conversation 路零回归。`ConversationInput.episodeId?` 字段已加(additive,Phase 2 消费)。

破坏性/影响面(铁律 2·人类已批影响面报告):**全 additive**——新导出类型(`InteractionContext`/`SemanticResolution`/`VisibleTurn`/`ResponseAct`/`PromptAct`/`PropositionOrigin`/`AssertionStrength`/两个 store 接口/`RecordAssistantReplyInput`)、加可选字段(`UserMessageInput`/`ConversationInput`/`MemoryBundle.data`/`ImportPlan.counts`/`ExportDeps`/`ImportDeps`/`StoreBundle`)、新方法(`recordAssistantReply`)、`BUNDLE_SCHEMA_VERSION` 1→2。无删字段/改必填/改类型。api:update 刷新快照。**注**:快照里 `ChatMessage` 的 role 联合顺序(`'system'|'user'|'assistant'` → `'user'|'assistant'|'system'`)因新增 `VisibleTurn` 联合被 TS `typeToString` 重排——**成员集不变、语义等价、非破坏**(结构类型顺序对宿主无影响)。

**Phase 2 已落(本会话·Semantic Resolution + 模型侧)**:
- **resolver produce+store**:consolidate 对每条【用户真说的】证据落一份 `semantic_resolution`(resolvedContent + response_act / prompt_act / proposition_origin / assertion_strength / required_context + resolverVersion 绑 prompt 版本可追溯)。**3a/3d 由构造保证**:复用 consolidate 既有的 `validEvidence` 白名单 → 引伪造 / AI-上文 id 的解析结构性丢弃;`resolved_content` 是解释、不铸 evidence id、永不进 support 白名单。幂等(同证据不重落)+ 非法枚举收敛 null。**来源收窄**:新增 `spokenEvidence` 白名单(⊆ validEvidence,故 3d 一并守住)——[行为观察]/[工具返回] 不落解析(「这句在回应谁提出的什么」对一条行为观察本就无意义,而 Phase 3 的 deriveFormedBy 要读这张表:垃圾进→垃圾出);提示词也教了同样的收窄,但**结构保证优先于提示词自觉**。**Phase 2 只 produce+store、不碰 formedBy**(那是 Phase 3)。
- **短回答语料家族**(short-reply 盘 7 条·CC-043~049·4 zh + 3 en;语料库 42→49):测「信息只在 AI 那句里的短回答」。corpus schema 加 `messages[].precedingAiContext?` + `expect.newCognitions.formedBy?` + `expect.resolutions.responseAct?`;新增 CORP-19/20/21。**顺带修既有 bug**:corpus.test.ts 的 `FORMED_BY` 镜像缺 'confirmed'(D-0033 Phase 1a 加枚举时漏更新)→ 此前任何用 confirmed 的 seed 都会被误判非法。
- **双向设防(刻意)**:CC-043/044/047/048 要 confirmed、CC-046/049 要 stated → 「全标 confirmed」与「全标 stated」两种退化模型各红一半,无单一退化策略通吃。**真实数据验证**:v3 本质就是「把一切标 stated」的退化模型,它恰好蒙对 CC-046/049、红在其余四条——正因语料双向才看得出它会什么、不会什么。
- **提示词 v3→v5**:v4 教「读懂 ⟨AI 前一句⟩ / 附和→confirmed / 窄范围 / 产 resolutions」,v5 补 select 分支(见下)。**四视角对抗审查**抓到 4 个 major 并全修(否认失语 / 闲聊守卫与【附和】在「嗯」字面上冲突 / 含糊点头无路可走 / resolutions 零 eval 覆盖);其中前两条由两个独立视角收敛。**铁律 3 已验**:diff 里唯三的 `-` 行 = version bump + zh/en 各一行 JSON 示例的闭合符,纪律措辞逐字未动、新内容全部追加。
- **§15.3 全量前后对拍**(只动提示词一个自变量;before = v3 + **新语料 + 新口径**,刻意不拿旧 42 条基线比——语料集与口径皆已变、本就不可比):**结构 90.5%→97.1%(248→266/274)· 全绿 31→41 · overInfer 全程 0.00 · short-reply 36/51→51/51(+29.4pp)· 旧 6 盘零退化**。尤其 **chitchat-negative 保持 35/35**——审查点名的最大回归风险被证伪:v5 的 carve-out 以「带 ⟨AI 前一句⟩ 后缀」为钥,而旧 42 条无一条带 precedingAiContext → 结构性够不到。**诚实**:旧盘那 +3 条结构(conflict/emotion-cap/fact-vs-belief 各 +1)可能只是模型抖动(n=7/盘 单跑);gistRecall 涨幅是软判、仅供趋势(D-0009);no-over-inference 28/34 的缺口仍是 D-0019 的 ContentType 缺「事件」型定义灰区。**基线更新为 v5 + 49 语料 + 新口径**,旧 42 条 v3 基线作废。
- **发现·补的是纪律不是理解力**:v3 在 short-reply 的 gistRecall 已达 **0.90**——mimo 本来就读得懂「是啊」在确认什么、认知内容也写得对;它红的 15 条全在结构(7/7 **一条 resolution 都不产** + 4 条把附和标成 stated + 4 条连带的 responseAct「无解析可判」)。Phase 2 补的是**结构化产出与来源纪律**,别把功劳记成「模型变聪明了」。

**Phase 2 新拍板(人类·2026-07-16)· select → confirmed**:冲烟(short-reply 单盘、15 分钟)暴露 mimo 把「window or aisle?」+「The former.」标成 stated/600/limited,而**已批的派生表(`docs/internal/v0.6-impact-report.md:88`)只议定了 affirm 与 negate、select 从来是灰区**。人类拍板 **select → confirmed**,判据是「这条信息的载体是谁的话」而非「AI 有没有预设答案」:「前者」两个字不承载任何内容、解析全靠 AI 那句(若上文被 240 字截断、或选项顺序记反,解出来就是反的),这种「理解依赖上下文」的不确定性正是 confirmed 低置信(280、封顶 480)的用途;凭两个字给 600/limited 偏高。**反方也成立并记录**:AI 问的是开放选择、没预设答案,不存在「AI 编 X + 用户随口是」的诱导风险,用户是主动选择——mimo 自己就这么判的。**派生表据此补一行**:`spoken ∧ assistant_proposed ∧ response_act=select` → `confirmed`(与 affirm 同档)。

**Phase 2 度量教训(记档·防重犯)**:① 起草时给 short-reply 盘加过一条「confirmed 封顶 ≤480」的 eval 断言,被对抗审查指出**恒 pass**——confirmed 底分 280 + 支持满 200 = 480,由 `confidence.ts:30` 的算式结构保证(即本报告 :95 的安全不变式),数学上不可证伪 → 加了就是给每场景 +1 条恒绿 check、把 structRate 抬成假提升,正是 §15.2 口径纪律禁止的事,已删(config 被改高底分的回归由单测 confirmedLaundering.test 守,eval 不是回归测试)。② **元数据靶心不能写进 gist**——judge 只拿到 active 认知的 content 串(scoreGists),看不到 formedBy/confidence/credStatus;写进去要么恒判 NO、白占 overInferRate 分母把靶心稀释,要么抓住正确的语义主干对**正确**模型判假阳。元数据一律交机判(expect.newCognitions.formedBy / expect.resolutions.responseAct)。③ **提示词的 few-shot 例子必须逐个对语料查重**:v4 起草时「爬山」撞 CC-043/046、拟用的「素食者」撞 CC-049(vegetarian 在语料命中 8 次)、「内向/安静/夜猫」皆撞 → 撞题 = 模型照抄提示词就能得分,评测分数即失真(既有的「咖啡」是 v3 就有的话题,已含在 v3 基线里,故不影响 v3→v5 的前后**对比**——区分「有没有泄漏」与「相对前一版**新增**了泄漏吗」很关键)。

**待续**:Phase 3(deriveFormedBy 代码接管 + 删 consolidate `formed_by` 指令 + 全量 eval 重跑贴前后分)→ Phase 4 dogfood → Phase 5 发布。**Phase 3 的输入已就位**:semantic_resolution 表现在有真实模型产出的数据(49 场景全绿),且 eval 已能判解析质量(覆盖 + responseAct 允许集),不再是「垃圾进→垃圾出」的盲区。

## D-0035 v0.6 Phase 3 · formedBy 代码接管的三个设计拍板

日期:2026-07-16 / 状态:已采纳(人类逐条拍板)
背景:D-0034 四决策之②「formedBy 全面接管——所有来源标签由代码从语义解析算」进入 Phase 3 实现。动手前派 4 维只读侦察(multi-evidence / blast-radius 完成;current-flow / table-gap 因 StructuredOutput 重试超限失败,其内容部分被前两维覆盖),挖出**两个阻塞级问题 + 一个安全硬结**——均非实现细节,而是**规范真空** → 逐条由人类拍板。

**拍板① · inferred 冲突 → B:窄化决策②,代码只接管「载体维」**
- **问题**:派生表(`docs/internal/v0.6-impact-report.md:84-96`)按「这句话是谁说的、怎么说的」派生 formedBy,而现行 formedBy 编码的是**另一个维度**——「这条认知**离原话有多远**」。v5 提示词自己举的例子:「怎么找女朋友」(spoken ∧ user_stated ∧ explicit) 推出「用户单身」,**按派生表得 stated/600、按 v5 教的该是 inferred/200**。差 3 倍,且**所有推断型认知都中招 → `inferred` 这一档被实质消灭**。
- **拍板**:代码只算 **stated / confirmed / observed**(载体维 = 这条信息是谁的话);**`inferred` 仍由模型报**(推断距离 = 这条认知离原话多远)。
- **理由(风险不对称)**:真正的洗白风险只在载体维——模型把 `confirmed`(280) 说成 `stated`(600) 是**往高了骗**(3a 要防的);模型说「这是我推断的」(`inferred`/200) 是**往低了报**、无骗人动机。且推断距离本来就只有模型知道(它才知道这条认知是不是从原话推出来的)。
- **代价(诚实记录)**:决策② 由「**全面**接管」收缩为「接管**载体维**」。这是真的范围收缩,人类已知悉并批准。
- **弃案**:**A**(给 resolution 加 `inference_distance` 维)——与 B **实质等价**(该字段仍是模型报的,「等于把自报换个马甲」),但代价大得多(schema + store + api + 提示词 + 多一轮全量 eval);**C**(接受 inferred 消失)——直接推翻「区分事实与猜测」的核心卖点。另侦察过第三条路(用 resolution 的 `resolved_content` 与认知 content 比、超出即算推断):「怎么算超出」只能靠 LLM 判或 embedder,而 consolidate 里两者皆无 → 不干净,放弃。

**拍板② · 多证据聚合 → 取最弱(min)**
- **问题**:派生表逐条证据给规则,但 `support_evidence_ids` 是**数组**、签名是 `deriveFormedBy(supportEvidences)` 复数 → 一条认知引多条证据时算哪个,**规范一字未提**。
- **拍板**:**取最弱**——支持集里最低的那个赢。
- **理由**:「支持集里有一条是附和 ⇒ 这条认知至少有一环是附和」是**蕴含**;取最强隐含的反向命题**不是蕴含**。取最弱是唯一由**结构**守住 confirmed 安全不变式的选项。
- **取最强被否的实证(侦察坐实,非推测)**:`pickSupport`(`consolidate.ts:197`)**只查 id 白名单、不查相关性**;`validEvidence` 覆盖**整批**(生产 `batchSize=12` 轮对话);「只引真正相关的」今天**只由提示词软判**(`prompts.ts:54`)。→ 取最强时,「AI 诱导 + 用户附和」的认知**只要顺带引一条同批的无关主动陈述**就得 stated:600+40 = **640 ≥ limited(500)**;引 5 条 → 600+200 = **800 ≥ stable(750)**。**不需要恶意模型**,一个「过度引用」的模型就够。
- **代价**:「用户主动说 + 顺带一条行为观察」被压成 observed(低估)。侦察查证此代价在产品里罕见(`createCore.ts:74-75` `ingestUserMessage` 的 sourceKind 缺省 'spoken';observed 只在宿主显式传时出现)。
- **⚠ 已知盲区(必须知悉)**:当前 49 场景**异质支持集 0/49**(47 条只有 1 条 message → 一条认知最多引 1 条证据是物理上限)→ 四种聚合策略在 §15.3 全量 eval 上产出**完全相同**、前后分对本决策**零鉴别力**;真实频率也**测不出来**(baseline 的 summary 不落 support ids)。**本决策是在没有频率数据的情况下做的**——靠单测钉住,混合支持集语料家族另排。

**拍板③ · reinforce 升级路 → ④ 取消升级,改为并存新认知**
- **问题**:confirmed→stated 的升级路(`consolidate.ts:262-269`)是破 480 封顶的**唯一钥匙**,其 gate① 靠 **LLM 自报 `formed_by:'stated'`** 触发。Phase 3 删掉该指令后 gate① 输入源消失 → 升级路**默认失效** → D-0033 决定③「只有主动说才升级」**名存实亡**(悄悄推翻,是最不诚实的选项)。
- **拍板**:**取消升级路**。用户后来主动说 → 形成一条**并存的新 stated 认知**,旧的 confirmed **留档**。
- **理由**:彻底消灭洗白洞;且最贴合宪章「**冲突只暴露不裁决**」——两条认知并存、各自溯源清楚,而不是把一条认知的来源标签**就地改写**。
- **代价(诚实记录)**:**推翻 D-0033 决定③**(人类当时拍的 confirmed→stated 升级路)。人类已知悉并批准。另:同一命题会并存两条认知(旧 confirmed + 新 stated)——去重/合并另议。
- **弃案**:**①不动**(= 悄悄推翻 D-0033,不诚实);**②** gate① 换成「新引证据里*有* user_stated∧explicit」(= 取最强的有界版本,洗白洞随之回来);**③** gate① 换成「新引证据集*全体*派生成 stated」+ 保留 spoken 护栏(残留一个 D-0033 今天已有的洞)。

**侦察发现 · Phase 3 的前置项与风险(均带 file:line)**
- **【阻塞·已解除】旧 42 盘的解析覆盖率从未被测**:eval 只对 short-reply 盘查解析覆盖(`eval-consolidation.mjs:355-367`)。派生表 :93 规定「无法解析 → `inferred`(200)」→ 若模型只对短回答产解析、不对普通陈述产,旧盘本该 stated(600) 的认知会被兜底打成 inferred(200) → **全盘回归**,且**「取最弱」会放大它**。v5 教的是「给每条 [用户说] 原话出解析」,但**教了不等于做了**。→ 给 `buildSummary` 加 `resolutionProbe`(**纯观测、不加 check、不动 structTotal** → 不破 §15.2 与 v5 基线的可比性)后跑全量探针。

  **探针实测(2026-07-16·全量 49 场景·mimo + v5)**:spoken 证据解析覆盖率 **82.5%**(40 条里 33 条有解析)。逐盘:correct 7/7、fact-vs-belief 7/7、short-reply 10/10 均 **100%**;chitchat-negative 5/7 = 71%;**emotion-cap 仅 4/8 = 50%**;no-over-inference 0/1;conflict 无 spoken 证据(全 observed,符合语料)。健全性 `nonSpokenWithResolution = 0` ✓(spokenEvidence 收窄守住)。**结论:兜底真的会被用到(17.5%),侦察的预警不是杞人忧天。**
  **propositionOrigin 分布(定兜底的关键证据)**:旧盘(correct/emotion-cap/fact-vs-belief/chitchat)**全部 user_stated、`assistant_proposed` 计数为 0**;只有 short-reply 盘有 assistant_proposed(9)+user_stated(1)。原因是结构性的:旧 42 条**无一条带 `precedingAiContext`**(CORP-20 强制,只有 CC-043~049 带)。

  **兜底拍板(人类批·2026-07-16)**:**不照派生表 :93 的字面**,改用**结构事实**——**没有 AI 上一句,就不存在可附和的命题** ⇒ `proposition_origin` 结构上只可能是 `user_stated` ⇒ 正是派生表**第 3 行** → **`stated`**;**有** AI 上文但没解析 → 可能是附和 → 保守取 **`confirmed`**(与拍板② 的取最弱同一品味)。**这不是新规范,是「派生表 + 结构事实」的推论**:表 :93 的「其它 / 无法解析 → inferred(200)」写的是「**解析失败**」的情形,照字面套到「spoken 但模型没产解析」会把旧盘本该 stated(600) 的认知打成 200 → 全盘塌。**探针实证零回归**:旧 42 盘 assistant_proposed = 0 → 兜底对它们恒取 `stated` = 与现行行为一致。实现见 `src/consolidation/deriveFormedBy.ts`,单测见 `tests/deriveFormedBy.test.ts`(16 例,含反洗白 3 例——兜底路径也必须扛住同款攻击,否则「模型漏产解析」就成了绕过防线的后门)。

  **v6 线索(记档·不混进兜底)**:emotion-cap 覆盖率仅 50% —— 「今天好累」这类**陈述句不是「回应」**,模型大概觉得 `response_act`(「这句在回应什么」)无从谈起,就整条不产解析了。v6 可教「不是回应也要给,`response_act=none`」以提高覆盖率;但那会改变覆盖率、须进 §15.3 前后对拍,属 v6 的改动,**不与兜底混谈**。
- **`confirmedLaundering.test.ts` 必须重写**(不是「跑一跑看绿不绿」):6 个用例经 pickSupport 后**支持集全是单条证据** → 抓不住任何多证据失败模式;且全都不传 `semanticResolutionStore` → Phase 3 后派生无解析可读、按兜底落 inferred → 其 `formedBy==='confirmed'` 断言会**全红**。
- **存量认知的 formedBy 不重算**(Integrator 判,待人类复核):DECISIONS/报告/CHANGELOG 对此**完全空白**(不是没找到,是没议过)。物理上也算不了——存量证据没有 semantic_resolution,回填只能走兜底、把历史认知**全刷成 inferred/200**。**后果知悉**:库里会长期并存两套 formedBy 语义,且没有任何列能区分它们。
- **`deriveFormedBy` 不从 `index.ts` 导出**(铁律 4·无消费者)。先例矛盾已知:`computeConfidence`/`deriveCredStatus` 导出了(`index.ts:90`),而同为 consolidate 内部件的 `sourceLabel` 明确不导出(`sourceLabel.ts:8`)。
- **派生表第 5 行「`confirmed`(弱)或**不形成**」的归属澄清**:**产不产永远是模型的事**,`deriveFormedBy` **只贴标签、不删认知**(否则就是代码在删认知 = 新行为)。CC-048 的 min=0 让两种实现都能过 eval、会掩盖这个歧义 → 单测钉死。
- **文档订正待办**:报告 :229 写「bump v4」而当前已是 v5 → 应为 **v5→v6**;报告 :104 的 seam 行号(199-201 / 255-258)是旧版,实际 new 分支在 `consolidate.ts:225-242`、correct 在 :280-300;**「删 formed_by 指令」应订正为「教学归宿迁移」**——v5 的【附和】五分支 post-Phase-3 正是 proposition_origin / response_act / assertion_strength 的教学,**简单删会把 deriveFormedBy 的输入质量打下去**。
- **eval 口径已决:留(Integrator 判)**。侦察担心 Phase 3 后 `created来源⊆{...}`(`eval-consolidation.mjs:312`)「测的是代码、语料的双向设防会退化成 deriveFormedBy 的单测」——**该担心不成立**:`deriveFormedBy` 确实是确定性的,但**它的输入(resolution 的 proposition_origin / response_act)是模型产的** → 端到端**仍在测模型**,只是靶心从「模型把 formedBy 标得对不对」变成「**模型把这句话解析得对不对**」。这反而**更本质**(测理解力,而不是测贴标签)。CC-046/049 要 stated、CC-043/044/047/048 要 confirmed 的双向设防因此照旧有鉴别力:模型把附和解析成 user_stated 就会红。→ **保留断言**,在贴 §15.3 前后分时讲清这个口径迁移。
- **混合支持集语料:不做——eval 在结构上做不到(Integrator 判,订正侦察的建议)**。侦察建议「新开一盘」测多证据聚合;但语料只能给 `messages`,**引哪几条证据当支撑是模型自己决定的**(`support_evidence_ids` 由 LLM 输出,语料强制不了)。要在真模型 eval 里造出「一条认知引多条**异质**证据」**不可控**。→ 这也解释了「异质支持集 0/49」**不是语料没写好,而是结构使然**;新开一盘也改变不了。该靶心由**单测**承担:`deriveFormedBy.test`(纯函数层,3 例反洗白 + 1 例对照防「取最弱被误实现成无脑降级」)+ `confirmedLaundering.test`(consolidate **端到端**,穿过 `pickSupport` 这个真实攻击面:附和 + 1 条无关主动陈述、+ 5 条、以及全 stated 的对照)。
- **可能撞 DoD 钩子**:报告 :241 的「tests/eval/ 既有断言未改(只增新文件)」——若 `cognition-discipline.eval.test.ts` 因 Phase 3 变红需改,**须人类批**。(`bench/` 不在该目录、改它不触钩子。)

**Phase 3 已落(2026-07-17)**:
- **`deriveFormedBy`**(`src/consolidation/deriveFormedBy.ts`):载体维 + 取最弱 + 结构兜底;16 例单测(含 3 例反洗白 + 1 例对照防「取最弱被误实现成无脑降级」)。不从 index.ts 导出(铁律 4)。
- **consolidate 接线**:new/correct 用它;解析的规整/收窄**前移**到写循环之前(`resolutionOf`,落库与派生共用一份);`semanticResolutionStore` **保持可选**(派生吃内存、不读表——唯一要读历史解析的场景本是升级路,已被拍板③ 取消)。
- **⚠ EVAL-T05 抓到一个真退步并修**:接线初版把「缺 formed_by」当成「模型说这不是推断」→ 走载体维 → 一条 spoken 证据就把它抬到 stated(600),而旧世界是「缺 = 未知 → 保守 inferred(200)」。**拍板① 的论证「模型往低了报、无骗人动机」没考虑【漏报】** —— 模型疏忽漏标时新逻辑更激进。修成「**缺 / 非法 → 保守当推断**」,EVAL-T05 的既有断言语义原样保留 → 顺带避开报告 :241 的 DoD 钩子。
- **reinforce 改并存**(拍板③);`RawRef.formed_by` 删除(无消费者)。**只能代码判**:画像注入给模型的只有 `[id] (contentType) content`、**不含 formedBy**(consolidate.ts:147)→ 模型不知道哪条是附和来的,没法自己决定改报 new;而给它看 formedBy 又与拍板① 自相矛盾。**用 `add`(本次新增证据)而非 `cited` 派生**:cited 可能含旧的附和证据、取最弱会让并存永不触发。
- **提示词 v5→v6**(教学归宿迁移,非简单删):五分支的「②怎么解析 ③产不产」全保留、只摘「①怎么标 formed_by」——② 现在正是 deriveFormedBy 的**输入**,删了就自断输入。**第三次抓到答案泄漏**(初稿用「今天好累」撞 CC-015 逐字)→ 去掉例子;v6 未引入任何新例子。
- **`confirmedLaundering.test`** 补 consolidate 端到端多证据 3 例(穿过 `pickSupport` 这个真实攻击面);文件头 ④「升级门」→「并存」+ 新增 ⑤「取最弱」。

**§15.3 全量验证(commit `dcd2415`·before = v5+Phase2 的旧基线,同语料/口径/模型/judge,直接可比)**:
  结构 **97.1%→96.0%**(266→263/274)· 全绿 41→38 · **overInferRate 全程 0.00** ·
  **short-reply 51/51 保持** · **formedBy / resolution 断言零红** · **解析覆盖率 82.5%→100.0%(40/40)**。
- **拍板① 验证成立**:short-reply 从「模型直接标 formedBy」变成「模型解析 → 代码派生」后**仍 51/51**——
  从**标签题变成理解题**(模型得先把附和解析成 assistant_proposed、把否认解析成 negate)分数没掉 → 载体维交给代码成立。
- **v6 的覆盖率教学生效**:82.5%→100% —— 探针当初测出 emotion-cap 仅 50%(模型对「不是回应」的陈述句整条不产解析),v6 补的「每条 [用户说] 都要给一条」补上了。**结构兜底因此几乎用不到**(但仍是安全网、单测钉着)。
- **−1.1pp 全部是 `created类型⊆{...}`(contentType)的红,没有一条与 formedBy 有关**:CC-001/019/021 越界 fact、CC-020/026 越界 preference,而 **CC-033/034 转绿**。正是 **D-0019 记过的 fact-vs-state 灰区**(CC-019「昨晚没睡好/今天一整天都很困」、CC-021「刚跟同事吵了一架,气死我了」都是**一次性事件**,fact 与 state 各对一半)——ContentType 缺「事件」型的已知局限、非纪律破裂。有红有绿是抖动特征,幅度与 D-0019 当年判为单跑方差的 −0.9pp 同量级。**诚实**:不能排除 v6 提示词变长稍微加剧了 contentType 抖动,n=7/盘 的单跑分不出来,不硬下结论。
- **一个侦察预测被证伪**:侦察断言 `confirmedLaundering.test` 会「Phase 3 后全红、必须重写」——实测全绿,因为兜底不按派生表字面走 inferred、而用结构事实。

**Phase 4 机制验收已过(2026-07-17·真人对话·weftmate)**:接线落在 `weftmate/src/server.ts` 的 `recordChat`(**唯一**该接的点:`record` 是「帮我干活」的任务记录、`:1508` 是认知纠正的管理操作,都不是对话轮)——`ingestUserMessage` 带 `conversationId` + AI 回复后 `recordAssistantReply`。**只能代码判**:画像注入给模型的只有 `[id] (contentType) content`、不含 formedBy,它不知道哪条是附和来的。真人聊 24 轮后的画像:

| formedBy 分布 | `{"stated":3, "confirmed":6, "inferred":1}` |
|---|---|
| `[stated/600/limited]` | 「用户年龄26岁」「用户喜欢的歌手是大宽」「用户不追星」——**用户主动说出内容** |
| `[confirmed/280/candidate]` ×6 | 「用户喜欢爸爸而不是妈妈」(用户只说了**「前者」**)、「用户喜欢草莓而不是苹果」「用户社恐但想亲近人」(只答**「是的/是啊」**)等 |
| `[inferred/200/candidate]` | 「用户是男性」——AI 猜性别、用户答「错了」后**推断**出来的 |

- **正路 ✓**:AI 询问 → 用户确认 → `confirmed`。
- **反路 ✓(3a 最危险面)**:AI 连猜六件事 + 用户连答「是啊/前者/都听」→ **6 条 confirmed 全是 280/candidate、一条都没进 limited**。诱导风暴洗不出高置信,**在真实产品里得到验证**(此前只有 eval 语料与单测)。
- **select 拍板(v5)在真人对话生效**:「用户喜欢爸爸而不是妈妈」——用户全程只说了「前者」两个字,系统把指代解对了(爸爸/不是妈妈)、**却没因此当成他亲口说的**。这正是那次拍板的判据「信息的载体是谁的话」。
- **拍板①(inferred 仍由模型报)生效**:「用户是男性」诚实标成 200/candidate 的推断,没冒充事实——而这条恰是最险的(它源自 AI 猜错 + 用户否认)。
- **对照鲜明**:stated → 600/limited;confirmed → 280/**candidate**。载体维的档位差在真人数据上清晰可见。

**Phase 4 待续**:验收要求 **≥100 轮**,当前 24 条证据 —— **机制已验、量还差**,靠人类继续 dogfood 攒。重点观察拍板③ 的并存场景(confirmed 认知被后续的主动陈述另起一条 stated)——尚未在真人数据里出现过。

**Phase 4 的环境坑(记档·防重踩)**:① weftmate 经 `file:../memoweft` 依赖时 npm 建的是 **Junction**(非 symlink,不需管理员)——Electron 跟随正常,**不是**问题源。② **Windows 上文件句柄开着时 mtime 冻结**,杀进程才刷新 → 用 mtime 判断「库有没有被写」会得出完全错误的结论。③ **外部进程只读打开该库恒读到 0 行**(journal_mode=delete、无 -wal、文件确被写)——**原因未明、尚未解决**;绕法是让 core 自己 `listEvidence()/listCognitions()` 打日志。④ `agent.ts` 的 `safeFinish` 里 recordChat 是**空 catch**——入记忆失败会静默吞掉,聊天照常、库里一条不落,排障时先给它加日志。⑤ probe 副本(`weftmate-probe`,改 name 隔离 userData)是有效的 dogfood 场地,与本体互不干扰。

**待续**:Phase 4 攒量 → Phase 5 发布。**Phase 3 遗留(均已记档、不阻塞)**:① 同一命题可能并存两条认知(旧 confirmed + 新 stated),去重/合并另议;② 存量认知的 formedBy **不重算**(见上「侦察发现」段,待人类复核);③ ContentType 缺「事件」型(D-0019)仍是 contentType 灰区的根因,ROADMAP Later 有伏笔。

> **⚠ 本节三处已被 D-0036 订正(2026-07-17)**:① 环境坑③「外部进程读该库恒 0 行·原因未明」**是错的**——真相是 Git Bash(MSYS)文件视图陈旧,与 SQLite 无关;② 「拍板③ 的并存场景尚未在真人数据里出现过」**已不成立**——2026-07-17 首次真实触发(证据链见 D-0036);③ 「当前 24 条证据」是 Phase 4 起始快照,现为 106 条(验收 ≥100 轮**已达标**)。详见 D-0036。

## D-0036 v0.6 Phase 4 · consolidate 整批空转的根因 = 模型截断 evidence id(dogfood 实锤)

日期:2026-07-17 / 状态:已采纳(人类批准 **A + C**;B 治本择期、完整 C 待批)
背景:Phase 4 dogfood 期间真人对话 15 个 event 里 **5 个整批空转**(0 解析**且** 0 认知,完全共变),event 仍被标 `consolidated=1` ⇒ **47 条原话静默永久丢失**。七个内容层假说曾被活库逐一证伪;症状一度归因于 `consolidate.ts:228` 的 `?? {}` 静默降级 + reasoning_content 故障——**两者本次均被证伪**。

**根因(实锤 · 6/6 零反例)**
- **提示词输出示例是 4 字符占位**(`prompts.ts:87` `"support_evidence_ids":["ev-1"]`),而 `buildMessages`(`consolidate.ts:151`)**实际喂的是 36 字符 UUID** → mimo-v2.5-pro **间歇性照示例的形态**把 UUID 截成**前 8 位**回写(`"25601f4c"`),偶尔拼成 `"ev-25601f4c"`(示例的 `ev-` 前缀 + 前 8 位)。**模型在模仿示例的 id 形态,而非照抄输入里的真 id。**
- 两处白名单都做**精确匹配**:`pickSupport`(`:216` `validEvidence.has`)与 resolutions 收窄(`:251` `spokenEvidence.has`)→ 短 id **全部落空** → `:302 support.length===0 → continue` 逐条丢掉认知、resolutions 整批被 `continue` 丢掉 → `:426 markConsolidated` **无条件**标记 ⇒ **模型干了活,产出被静默丢弃、证据永久蒸发**。
- **「0 解析且 0 认知完全共变」的机制**:两个通道**共用同一套 id 白名单**(`spokenEvidence ⊆ validEvidence`)——模型两个通道都写短 id 时,解析与认知**一起**全灭。这正是困扰整个 Phase 4 的那个特征。
- **一个根因、两个现象**:模型若只在 new 通道写短 id、resolutions 写长 id → **解析全出但 0 new**(活库 12:42 批:20 解析 / 0 new;重放实验 #1 精确复刻)。

**为什么零信号(观测盲区,是本次调查最大的障碍)**
JSON **完全合法** → jsonRepair 零告警、`finish_reason=stop`、`llmCalls=1`、`completion_tokens` 5200~8500;只有落库是空的。而 weftmate 侧**从不记 `llmCalls`**、`jsonRepair` 默认 sink **刻意不记原文**(`jsonRepair.ts:67-69`,隐私优先)、`weftmate-console.log` 落盘机制当天才加且未重启 ⇒ **历史那 5 次的真凶靠数据库终态永远分不开**(CLAUDE.local.md「数据库最终状态是有损结果」的实证)。

**被本次证伪的既有记述(三处,均已订正)**
- **`?? {}` 不是嫌疑人**:走到它 ⟹ `jsonRepair.ts:91,101` **必打 2 条告警** + `:97` **必重试**(llmCalls=2)。实测**告警 0 / llmCalls=1** ⇒ **根本没经过 `?? {}`**。此前用来排除它的「jsonRepair 零告警」本身是**假阴性**(Electron 里 console.warn 不落盘就没处看)。
- **reasoning_content 与 consolidate 空转无关**:6/6 次空转的 `reasoning_content` 里**都没有** JSON,答案一直在 content 里。`src/llm/client.ts:82-84` 注释中「→ consolidate 四类全空(`?? {}`)→ 整批 0 解析 0 认知」这条因果链**对 consolidate 不成立**(该修复对**聊天路径**有效、已单独验证)。**该注释待订正**——它属未提交的用户资产,本窗未碰。
- **「压根没调用」已排除**:`:167` 是**唯一**不调 LLM 的出口,且在 `:426 markConsolidated` **之前** ⇒ 没调用就不会标 consolidated,而 5 批全被标了。

**修复(人类批准 A + C)**
- **A · id 归一容错**(`db42925`):新增内部 `resolveEvidenceId`——**精确匹配优先**(模型写对时**行为零变化**),失败才做**唯一前缀**容错(先剥 `ev-`);落库用**解出的真 id**(短 id 进表即脏数据)。**护栏一寸不让(3a/3d)**:只可能解到白名单**内**、且必须**唯一命中**;捏造 id / 歧义前缀 / 过短前缀(<8)一律丢弃。**不碰提示词** ⇒ 不触发 §15.3/D-0009 的 bump version + 全量 eval。
- **C · 覆盖率仪表**(`f51ca10`):模型**产了 N 条解析、却一条都没落地** → `console.warn` 带上它写的前几个 id。**刻意只抓这一形态**:「模型压根没产」不告警(那是模型能力/材料问题,也是既有测试简化 stub 的常见形态);「部分没落地」不告警(3d 收窄 / resolved_content 空 / 先到先得都会合法挡掉一些)。**只观测、不改行为**。
- **测试**:`tests/evidenceIdTruncation.test.ts` 9 例(2 钉「真 id 别被误杀」、4 钉「护栏不许松」、3 钉仪表),与 `confirmedLaundering.test.ts` 的 3a 用例互补。gate:**385/385 绿** · typecheck 干净 · `api:check` 一致(两者均不进公共面) · build。

**端到端验证(真实 LLM,非单测)**
同一输入 × 6 次:**空转 5/6 → 0/6**;新认知 **全 0 → 10,10,11,9,9,8(均 9.5)**;解析表脏数据 **0**。**模型行为没变**——仍在写短 id(合计 短 122 / 长 13)⇒ **修的不是模型,是让代码认得出模型的正确产出**。

**记忆恢复(人类批准·真库已备份 `weftmate-backup-20260717-before-idfix-recovery.db`,MD5 校验)**
5 个空转批逐批翻回 `consolidated=0` 重跑:**认知 17 → 42(+25)、解析 59 → 106(+47)、仍零解析的 event 0**。找回含用户亲口的「我今年26岁有一辆25款的小鹏G6」、日语 N2、东方树叶口味、作息/单休、幻觉纠正「没有亲弟弟」、十问性格 8 条 confirmed。**逐批跑**(非一次喂 47 条)以避开 120s 超时。

**拍板③ 首次在真人数据上真实触发(Phase 4 重点观察项,证据链完整)**
「用户年龄26岁」现有两条**并存**:`confirmed 320`(created 06:10,支撑 2 条:06:06「是的」+ 06:12「我今年26岁…」)与 `stated 600`(created 11:01,只挂 06:12 那条)。四条判据全中:旧 confirmed **被 reinforce 涨分**(280→320,支撑 1→2)、新 stated 的 content 与旧条**逐字相同**(`:356` 复制 `cog.content` 的特征)、新条**只挂新证据**、两者**不同刻创建**(模型 reinforce 的是已存在的 cognition_id)⇒ 走的正是 `:351-366` 拍板③ 路径。语义符合设计:附和得 confirmed/低置信,用户后来**亲口说**则**不就地升级**、另起 stated 并存,旧条留档。
**注**:此前一次「首现」判读**是误判**(那两条 content **不同**、**同刻创建** ⇒ 普通 new 路),本条与之不同、已逐项核验。**讽刺的是它一直触发不了,正因为用户亲口说的那批被本 bug 整批吞了。**

**未做 / 待批**
- **B · 治本(择期)**:`buildMessages` 改发**短序号** `[e1] [e2]`、代码维护 序号↔UUID 映射 ⇒ 模型**结构上不可能写错**,且示例与真实形态一致、根除诱因。代价:改提示词 ⇒ 按 §15.3/D-0009 必须 bump version + 重跑全量 eval + commit 附前后分数。
- **完整 C(待批·涉 schema)**:「有 spoken 证据却零解析落库 → **不标 consolidated**、留待下次」需给 `event` 加重试计数(防死循环)⇒ schema 变更,按 develop-memoweft §1 需人类批准。按实测漏读率,重试 3 次可把丢失率压到 ~5%。
- **新发现 · LLM 超时不可配且会真触发**:`client.ts:174` 默认 **120s**,而 weftmate 用 `config-store.injectEnv()` 只注入 9 个 ENV_KEYS、**`MEMOWEFT_LLM_TIMEOUT_MS` 不在其中** ⇒ **weftmate 运行期超时恒为 120s**。本窗实测撞上过(17 证据 + 17 认知的 prompt,响应 20K 字 / 8233 tok,就在 2 分钟边界)。这是**随规模恶化**的——攒批越多 prompt 越大,与 `batchSize=12`(D-0032)的攒批策略对冲。

**同款陷阱:三条同构路径未修(B 的对抗审查发现·8 agent 零 blocker·已亲自坐实)**
「示例短占位 id + 真实喂 UUID + 精确匹配 + 静默 continue/落空」这套结构,全仓**不止 consolidate 一处**:
- **attribute**(`attribution/prompts.ts:29`zh/:41en 示例 `["ev-1"]` · `attribute.ts:66` 喂 `[${e.id}]` UUID ·
  `:168` `candidateIds.has` 精确匹配 · `:170` `continue` 静默丢)——**无 A 兜底 / 无 B 标号 / 无 C 告警**,比修复前的 consolidate 更脆。
- **trends**(`background/prompts.ts:25`zh/:34en 示例 `["ev-1","ev-2","ev-3"]` · `trends.ts:50` 喂 `[${i.id}]` UUID · 同款精确匹配+静默丢)——同上。
- **cognition_id 通道**(consolidate 自身内):`reinforce/correct/conflict` 走 `cognitionStore.get(cognition_id)`
  精确匹配、**零容错零告警**;示例 `"cog-x"`(`prompts.ts:101/141`)与真实 UUID 形态不一致的**诱因仍在**。
  B **刻意不动它**:实测模型对 cognition_id **从不截断**(dogfood 报文逐字完整、出现频率低)——但这是 **v6 观察、v7 未验**。
- **分级(诚实)**:机制在 consolidate/evidence-id 路**已实测**(6 撞 5);上述三条**是否实际触发未证实**
  (eval 触发不了 = eval 盲区;也未被 dogfood 同强度压过)⇒ **结构同构的潜在同类风险**,非已实测 bug。
- **不阻塞 B**(非 B 引入、v6 及更早就在);**待人类拍板的独立跟进**:attribute+trends 可直接复用 B 的「发标号」手法一次收口;
  cognition_id 至少该补一条「get 落空但模型给了 cognition_id」的告警(同 C 的取向)。**范围纪律:本窗不顺手改。**

**环境坑订正(D-0035 环境坑③ 是错的)**
「外部进程只读打开该库恒读到 0 行·原因未明」**与 SQLite 无关**:真相是 **Git Bash(MSYS)的文件视图陈旧**(连它派生的 powershell.exe 都中招),会拿到数小时前的快照。**正解:读该库一律用 PowerShell 工具直接发起 node。** 本窗全程照此执行,零异常。

**⚠ 已证实的 eval 盲区(比修复本身更值得记)**
**§15.3 全量 eval 结构上抓不到这类 bug。** 实测:在**同一份代码**(含 A/C + client.ts 修复)上重立的 v6 基线 =
**262/274(95.6%)**,与 04fa43c 的旧基线 **263/274(96.0%)** 差 **−0.4pp / 全绿 38→38 持平**;
且逐盘**有红有绿、互相抵消**(chitchat-negative −2 · no-over-inference −2 · conflict +1 · emotion-cap +1 ·
fact-vs-belief +1),是 D-0019/D-0035 判过的**单跑方差特征**。**判据(关键)**:若 A 真在 eval 里生效
(把被 id 截断丢弃的产出救回来),分数应呈**单向提升**——实际不是 ⇒ **id 截断从未污染过 eval 分数**。
原因:eval 每个场景只有 **1~3 条证据**,触发不了模型的缩写倾向;而 dogfood 的 **12~20 条证据批次**必触发。
⇒ 49 个场景、95.6% 通过率,对一个「每次整理有 83% 概率把整批对话静默吞掉」的 P0 **完全视而不见**——
**真人 dogfood 是它唯一的出口**(这正是 Phase 4 存在的意义)。**推论**:eval 语料需要一个「大批次」家族
(≥12 条证据/场景)才能覆盖这类**规模触发型**缺陷;现有 49 条**全部**是小批次。已记 ROADMAP 候选。

**方法论教训(记档·本窗差点第五次犯同一个错)**
- **对非确定性现象,单次重放零证明力**:单次重放 14:39 批「不复现」,差点判成「无从查起」;改用**重复测量**(同输入 8 次)后空转率立刻现形(单 event **37.5%**、三 event **62.5%**),现象从「查不出」变成「可按需复现」,这才够到 CLAUDE.local.md 证据门槛第 5 条,根因随后一击即中。
- **漂亮的完美分离要先查样本量**:历史数据显示「多 event 4/4 空转、单 event 0/4」完全分离(随机概率 1.4%),**差点当成根因**——精确重放当场证伪。那是**统计效应**(每 event 独立漏读概率 p,三 event「至少漏一个」= 1-(1-p)³),「单 event 0/4」纯属样本太小(实测单 event 照样 37.5%)。
- **别拿聚合数字当结论**:一度因「12:42 批认知=2」宣布现象 B 不存在——那 2 条的 `created_at` 是更早批次的老认知被 reinforce,该批 **new 实为 0**。

## D-0037 下一轮排序调整:1.2 打头,1.1 + dogfood + 官网 demo 归打磨阶段(调 D-0031)

日期:2026-07-18 / 状态:已采纳(人类拍板)
背景(动机):D-0031 定的下一轮顺序是「1.1 事件类型打头 → 1.2 适配器规模化 → 1.3 Python 移植」+ 阶段 2(dogfood 打磨)。但 1.1 是 **dogfood 驱动**的特性(D-0019 的正解),阶段 2 整个以「人类在 weftmate 真用一段时间」为燃料——**两者都卡在用户本人的真实使用数据上**,而当前档期用户没空 dogfood(Phase 4 机制验收已过、攒量暂停)。1.2/1.3 恰是全案里**不需要 dogfood 喂料的独立工程线**。先干不卡人的活,是填空、不是插队。

决定(人类 2026-07-18 拍板):
- **1.2 适配器规模化打头**(原 1.1 的位置),1.3 Python 移植随后。
- **1.1 事件类型移入最后的打磨阶段**——它本就 dogfood 驱动,跟着 dogfood 走,归位而非降级;落地流程不变(D-xxxx + api-freeze + 语料 + 提示词 bump + §15.2 重跑)。
- **打磨阶段(原阶段 2)扩充明确**:用户亲自 dogfood 找问题 + 1.1 事件类型 + **官网试用 demo(web 页面)**(形态划分仍按 D-0031:真体验归 weftmate、演示版 memoweft 可承)。
- Phase 4 攒量(验收 ≥100 轮已达标,拍板③ 已首触)与 FTS 补齐(见下)都顺延到用户 dogfood 时自然完成。

破坏性:**无——纯排序调整记档 + ROADMAP 更新**。D-0031 的产品边界(9/10 归 weftmate、memoweft 只留接缝)与各项内容**一字不动**,只动顺序。
影响面:ROADMAP「下一轮」节 + 本条。

**附:FTS 缺口调查结论(2026-07-18·已对抗验证·记档不修)**
live 库 cognition=42 而 cognition_fts=17 的缺口已查清:2026-07-17 记忆恢复脚本在 memoweft/ 目录运行 → `process.loadEnvFile()` 自动装载 `.env` 的 `DLA_EMBED_*` → createCore 选 VectorRetriever(绕开 FTS)→ 嵌入端点未起 → 向量写入失败被吞进 `indexError`、恢复脚本未打印 ⇒ 双重静默,恢复批 25 条未进关键词索引。**会自愈**:下一轮 `updateProfile` 的 `indexAll` 增量 diff 一次性补插(weftmate 点「立即整理」即时触发;纯启动不触发)。**非技术债,不修**。衍生加固候选已记 ROADMAP Next(indexError 可见性 / ENV_KEYS 清 `DLA_*` 旧名 / 离线脚本必须打印 indexError)。

## D-0038 新增适配器 `@memoweft/adapter-mastra`(Mastra Processor 面·1.2 第①个)

日期:2026-07-18 / 状态:已采纳(人类批准形态岔口;实现已落·门禁全绿)
背景(动机):D-0037 下一轮 1.2「适配器规模化」打头。生态侦察(2026-07-18·联网实测)定第一个新目标 = **Mastra**(@mastra/core v1.51、~115 万周下载、TS-first 增长最猛):是唯一「高流量 + TS 同族 + 双向钩子与 memoweft 三面 API 一一对应 + repo 尚无覆盖」的净新增目标。既有 6 适配器覆盖 ai-sdk / mcp / claude-agent-sdk / openai-agents / langchain / llamaindex——ROADMAP Next 旧候选(OpenAI Agents/LangChain/LlamaIndex)已全部落地,故 1.2 需定**新**候选。

**接缝(从 @mastra/core@1.51.0 的 `.d.ts` 实测钉死,非文档臆测)**:
- 接口 `Processor { readonly id; processInput?; processOutputResult? }`(`dist/processors/index.d.ts`),纯接口、无需继承 BaseProcessor。
- **读缝** `processInput({ messages, systemMessages, state })`:可返回 `{ messages, systemMessages }` 改 **system 通道**(`ProcessInputResultWithSystemMessages`)。
- **写缝** `processOutputResult({ result, state })`:`result.text`(AI 回复)、`result.steps[].toolResults[].payload.{ result, toolCallId, args }`。
- `MastraDBMessage { id, role, threadId?, content:{ parts:[{type:'text',text}] } }`——消息自带稳定 `id`(天然 originId)、`threadId`(天然 conversationId)。
- 注册:`new Agent({ inputProcessors:[p], outputProcessors:[p] })`,同一实例进两路。type:module 纯 ESM、peer 仅 zod。

决定(形态):
- **读走 system 通道**(比 ai-sdk 塞 user 消息更贴 knowledgeBlock 原意,且物理不碰 user 消息 → 用户原话零污染)。措辞照搬 Core 中性 knowledgeBlock;隐私硬约束不变(provenance/id/score 只经 onRecall、绝不进注入)。
- **捕获-落库分离(经 processor `state` 跨方法递)**:processInput 捕获【注入前】用户原话 + originId + conversationId 塞进 `state`;processOutputResult 读回落库。**这不只是方便,是 0.6 preceding_ai_context 语义的要求**——须「先有上一轮 AI 在 session 里,再 ingest 本轮 user」,故落库放模型答完后;此刻 session 里正是上一轮 AI 回复,随后 `recordAssistantReply(本轮 AI)` 供下一轮捕获。**state 万一未跨阶段带过来**,processOutputResult 从自己的 messages 兜底取(注入不碰 messages,兜底同样拿到干净原话)——对「state 是否跨阶段共享」这一不确定点的防御。
- 工具结果只取 `payload.result`/`toolCallId`,**绝不取 `payload.args`(调用入参)/`toolName`**、`result.text` 只经 recordAssistantReply 进上下文窗口——铁律 3a by-construction。
- **peer `memoweft ^0.5.0 || ^0.6.0` + 能力探测**(人类拍板):`recordAssistantReply` 是 0.6 面,`typeof core.recordAssistantReply === 'function'` 探测——0.6 宿主启用会话上下文线,0.5 宿主降级为基础摄入+召回,均不报错、不卡发布。**首个跨 0.5/0.6 双 peer 的适配器**(其余 6 个都是 `^0.5.0`)。
- peer `@mastra/core ^1.51.0`(发版快、窄下界,Processor 是公开面相对稳)。

破坏性:**无——纯新增包 `packages/adapter-mastra/`**,只消费 Core 门面(recall/ingestUserMessage/ingestToolResult/recordAssistantReply),**不触 core api-freeze**(`api:check` 一致·已验)。
影响面:新包 11 文件(package/两 tsconfig/src 4[processor+index+degrade+knowledgeBlock,后二逐字对齐 ai-sdk]/契约测试+golden en/zh/README en+zh/examples)。degrade/knowledgeBlock 照搬件复用。ci.yml +3 步 guardrails;CHANGELOG 同步;root package-lock(+@mastra/core devDep)。
gate(2026-07-18 实跑):**adapter typecheck 干净 · 契约测试 11/11 绿(AD-1…9,ad5/ad9 声明 N/A、复跑锁 golden)· build 出 dist · 核心 api:check 一致**。
分级(诚实):`state` 跨 processInput→processOutputResult 是否共享,未启真实 Mastra Agent 端到端验证(离线契约测试驱动 state 手递主路径)——已加 messages 兜底防御;真实端到端待打磨阶段 dogfood 或宿主接入时坐实。

## D-0039 adapter-langchain 追加 v1 Agent Middleware 入口(Processor 面·1.2 第②个)

日期:2026-07-18 / 状态:已采纳(在 D-0037 批准的「1.2 加 middleware 入口」方向内;实现已落·门禁全绿)
背景(动机):D-0028 的 adapter-langchain 是 **v0 时代载体**——retriever(读)+ callback(写工具结果)+ 宿主闭包(写用户原话)。硬事实:LangChain callbacks 观察-only、召回注入只能走 retriever 让宿主自拼进 prompt。但 **LangChain v1(267 万周下载生态最大)主推 `createAgent` + Agent Middleware**(`beforeModel`/`afterModel`/`wrapModelCall`/`wrapToolCall`/`beforeAgent`/`afterAgent`),是新正门;且 `afterAgent` 能接 **0.6 的 recordAssistantReply**(v0 retriever/callback 面做不到)。生态侦察(2026-07-18)推荐:不新建包,在现有适配器**追加 middleware 入口**。

**接缝(从 `langchain@1.5.3` 的 `.d.ts` 实测钉死)**:
- `createMiddleware({ name, beforeAgent?, wrapModelCall?, wrapToolCall?, afterAgent? })`,主入口 `langchain`(`.`)直接 re-export。
- **读缝** `wrapModelCall(request, handler)`:`request.systemMessage: SystemMessage` 有 `.concat()`;官方示例 `handler({ ...request, systemMessage: request.systemMessage.concat(块) })` = **临时扩 system 消息、只对本次模型调用生效、不写进会话 state**(避开逐轮累积旧召回)。handler 返回 AIMessage。
- **写缝** `wrapToolCall(request, handler)`:`await handler(request)` 得 `ToolMessage`(工具真实返回结果·取 content);`request.toolCall`(调用意图/入参)绝不读。
- `beforeAgent`/`afterAgent`:一轮一次;state.messages 是完整对话(用 `getType()==='human'/'ai'` 取)。
- conversationId:`runtime.configurable?.thread_id`(LangGraph 线程)。

决定(形态·**纯追加,v0 retriever/callback 一字不动**):
- 新入口 `createMemoWeftMiddleware(core, opts)` + 可单测的 `buildMemoWeftHooks(core, opts)`(返回 4 个纯函数 hook)。hook 挂法:**读**=wrapModelCall 临时注入 systemMessage;**写①**=beforeAgent 摄用户原话(spoken);**写②**=wrapToolCall 只落工具 result(非 args·铁律 3a);**写③**=afterAgent 接 recordAssistantReply(0.6·只上下文·永不落证据)。0.6 会话语义:beforeAgent 带 conversationId ingest(此刻 session 是上一轮 AI)→ afterAgent record 本轮 AI 供下一轮。
- **`langchain` 伞包设为【可选 peer】**(`peerDependenciesMeta.langchain.optional`):只用 retriever/callback 老路的宿主仍只需 `@langchain/core`,import middleware 入口才需伞包——**保住 D-0028「peer 面最小」性质**。
- memoweft peer `^0.5.0` → **`^0.5.0 || ^0.6.0`**(middleware 的 recordAssistantReply 是 0.6 面,能力探测;老路仍 0.5 兼容)。
- SDK 边界(4 个 hook 的 request/handler/state/runtime)用 `any`+eslint-disable(LangChain v1 middleware hook 重泛型、wrapModelCall 的 handler-of-handler 逆变令结构化类型无法干净对齐)——内部全部委托给结构化 typed 的私有函数,落库/注入逻辑仍类型安全(同本仓适配器 SDK 边界惯例)。

破坏性:**无——纯追加**。新增 `src/middleware.ts` + 导出;`writeCallback.ts` 的 `runIngestWithRetry` 加 `export`(内部复用、index 不再导出);不碰 core、不触 api-freeze(`api:check` 一致·已验)。
影响面:middleware.ts + index 导出 + package.json(可选 peer/dev `langchain` + memoweft peer 扩 0.6)+ 新契约测试 `tests/middleware.test.ts`(name `langchain-mw`)+ golden mw en/zh + README en/zh 加 middleware 章 + ci.yml 注释 + CHANGELOG + root package-lock(+langchain 树)。
gate(2026-07-18 实跑):**adapter typecheck 干净 · lint 0 · 契约 22/22 绿(langchain 11 + langchain-mw 11,AD-1…9)· build 出 dist · 核心 api:check 一致 · 核心 399/399 无回归**。
分级(诚实):离线契约测试直接驱动 `buildMemoWeftHooks` 的纯函数(未启真实 `createAgent`);`createMiddleware` 接线由 typecheck 保证。真实 agent 端到端 + thread_id→conversationId 的实链待打磨阶段 dogfood 或宿主接入坐实。

## D-0040 adapter-openai-agents 薄补 0.6 recordAssistantReply 会话上下文(1.2 第③个·裁剪自「Session 形态」)

日期:2026-07-18 / 状态:已采纳(在 D-0037 批准的 1.2 方向内;实现已落·门禁全绿)
背景(动机):1.2 第③项原描述为「openai-agents 补 Session 形态」。生态侦察(2026-07-18)同时给出**强约束**:OpenAI Agents SDK 仍 0.x、官方已预告「下一代」(harness/sandbox/可配置 memory,TS 版未发)⇒ **Session/记忆面中期可能大改,现在深投 Session 形态有返工风险,建议薄实现+跟踪**。而现有 `createMemoWeftRunner`(D-0027)已覆盖召回①+用户原话②+工具结果③,**唯独缺 0.6 的 recordAssistantReply**(会话上下文面,让下一轮短回答/附和能被理解)——这正是 mastra/langchain 本轮已补的 0.6 面。

**裁剪决定(诚实记录)**:③ 从「造一个 `MemoweftSession`(完整对话历史存储 + 旁路证据流)」**裁剪为「把 0.6 recordAssistantReply 薄补进现有 run() 收尾」**。理由:①现有 runner 已是低摩擦入口,Session 会与它**重复** surface;②Session 语义是 message store、memoweft 不是,需内置 pass-through 历史层,是更大的面;③官方下一代将重塑 Session ⇒ 现在造 Session = 追移动靶、返工风险高。薄补 recordAssistantReply **同样达成 ③ 的核心目标(给 openai-agents 补 0.6 会话上下文面)**,零新依赖、纯追加、返工面最小。**完整 Session 形态 defer**,待 SDK 下一代稳定后按真实需求重评。

决定(形态·纯追加):
- `RunnerCore` += 可选 `recordAssistantReply`(能力探测);`MemoWeftRunExtras` += `conversationId?`(每轮传)。
- run() 收尾:带 `conversationId` 且能力具备时——用户原话 ingest 带上 conversationId(Core 捕获上一轮 AI 进 preceding_ai_context)+ run 结束后把**本轮 AI 最终回复**经 `recordAssistantReply` 报告(供下一轮)。**AI 回复只进上下文窗口、永不落证据**(铁律 3a)。
- 新增导出 `finalAssistantText(result)`(从 RunResult 提最终回复文本:finalOutput string / 倒扫 message_output_item 的 output_text)+ `recordFinalReply(core,result,conversationId)`(自带能力/conversationId/非空/抛错门控,可离线单测)。
- memoweft peer `^0.5.0` → **`^0.5.0 || ^0.6.0`**(recordAssistantReply 是 0.6 面,能力探测;0.5 整条静默跳过)。

破坏性:**无——纯追加**。不带 conversationId / 0.5 Core = 行为完全同旧。不碰 core、不触 api-freeze(`api:check` 一致·已验)。
影响面:runner.ts(+recordFinalReply/finalAssistantText/conversationId 线)+ index 导出 + package.json(peer 扩 0.6)+ 新单测 `tests/recordReply.test.ts`(8 例:finalAssistantText 三形 + recordFinalReply 五门控含抛错静默)+ README en/zh 加 0.6 会话上下文节 + ci.yml 注释 + CHANGELOG。**无新依赖 → 无 root install**。
gate(2026-07-18 实跑):**adapter typecheck 干净 · lint 0 · test 19/19 绿(契约 11 + recordReply 8)· build 出 dist · 核心 api:check 一致**。
分级(诚实):recordFinalReply/finalAssistantText 离线单测充分;真实 `run()` 里的 recordAssistantReply 时机(动态 import 真 SDK)未端到端跑,待打磨阶段 dogfood 或宿主接入坐实。**完整 Session 形态明确 defer**(见上裁剪理由)。

## D-0041 adapter-llamaindex 标 legacy 冻结(上游 LlamaIndex.TS 归档·1.2 第④个)

日期:2026-07-18 / 状态:已采纳(在 D-0037 批准的 1.2 方向内;纯文档 + peer 兼容,无功能改动)
背景(动机):生态侦察(2026-07-18·联网实测)确认 **`run-llama/LlamaIndexTS` 已于 2026-04-30 归档为只读、官方声明 deprecated / no longer maintained**(团队转向 Python 与 LlamaCloud,最后发布 2025-12)。本适配器(D-0029)peer 依赖的 `llamaindex@^0.12` 伞包 + `@llamaindex/workflow` 上游全部冻结。若不标注,会给用户「官方支持活框架」的错误预期,且其依赖面随生态前进逐渐腐化。侦察建议:标 legacy、冻结当前功能面、把额度让给 Mastra/LangChain。**核实纠错**:侦察 eco 笔记称「adapter-llamaindex 已接入 recordAssistantReply」——grep 实测**不成立**(src 无此调用),本适配器从未有 0.6 面。

决定(纯文档 + install 兼容,**零功能改动**):
- README en/zh **顶部加醒目 legacy 冻结横幅**:上游 2026-04-30 归档、本适配器冻结在当前功能面、**不实现 0.6 的 recordAssistantReply**(活跃维护的 mastra/langchain/openai-agents 本轮都补了)、起新项目请选维护中框架。并把 D-0029 的「上游 granular 包 deprecated」旧上游说明**升级**为「整仓归档」。
- package.json:description 前缀标 `[LEGACY — upstream ... archived 2026-04-30, frozen]`;memoweft peer `^0.5.0` → **`^0.5.0 || ^0.6.0`**——**纯 install 兼容**(适配器只用 recall/ingestUserMessage/ingestToolResult,全在 0.6 有,widen 让存量用户在 memoweft 0.6 下不报 peer 警告),**明确不是加 0.6 特性**(recordAssistantReply 仍不实现)。
- ci.yml:guardrail 注释标冻结;**CI 仍跑 typecheck/test/build**——冻结 ≠ 不守回归,存量用户仍受保护。

破坏性:**无——纯文档 + peer widen + description**。src / 功能面一字未动;不碰 core、不触 api-freeze。
影响面:README en/zh + package.json(description + memoweft peer)+ ci.yml 注释 + 本条 + CHANGELOG。
gate(2026-07-18 实跑):typecheck / test / build 仍绿(仅文档/元数据改动,功能未动);核心 api:check 一致。
**发布跟进(记档)**:真正的 npm `deprecate` 标记须在发布时由发布者执行(`npm deprecate @memoweft/adapter-llamaindex@... "..."`),本仓改动不含发布动作(发布是受控门槛)。
**重启条件**:若 LlamaIndex.TS 复活维护 / 出等价活跃继任框架、且有真实用户需求,再评估解冻或迁移。

## D-0042 1.3 Python 移植 · parity 内核优先(分阶段策划)

日期:2026-07-18 / 状态:**范围已采纳(人类拍板「parity 内核优先」);Phase 0 + 1a + 1b + 1c 全落地 ⇒ Phase 1(parity 内核)完成,逐位对拍/互通全绿;Phase 2(LLM 写路径)defer 待批**
背景(动机):D-0037 排序下,1.2 收官后进 **1.3 Python 移植 + 跨语言一致**(ROADMAP:独立语言线,给「可移植」上跨语言背书)。动手前派 4 路只读侦察(模块分层/依赖 · 必守不变量 · parity 策略 · Python 2026 技术栈,均带 file:line / 联网查证),据此定策划。**范围纪律**:REST/多租户/pgvector 归 weftmate(D-0031),不在移植面;memoweft 只移库本体。

**关键洞察(定策划的依据)**:代码天然两分,parity 验证方式不同——
- **纯确定性逻辑(逐位可对拍·移植即验证)**:`computeConfidence`/`deriveCredStatus`/`deriveFormedBy`/`decay`(effectiveConfidence)/`resolveEchoedId`+id 白名单/隐私 tier 筛/schema DDL/召回门控/config 常量/hashEmbedder。**这堆正是「可移植记忆」招牌的命脉**(事实vs猜测、置信度规则算、冲突只暴露)。
- **LLM 耦合(parity 靠 eval 分)**:consolidate/distill/attribute/trends 的模型产出 + 8 条提示词。大头工作量,parity 只能比 §15.3 分布。

**人类拍板范围 = parity 内核优先**:先把纯逻辑不变量核心 + 存储 + 便携包移到位、用逐位对拍验证(最强、最低风险的跨语言背书,基本自验证);LLM 写路径 defer,Phase 1 跑完再决定要不要往下走。

**分阶段计划**:
- **Phase 0(前置·TS 侧【纯追加】·零行为改动)**:把「跨语言必须同源」的资产落成**语言中立共享文件 + 守门测试**,**不重构 TS 运行时逻辑**(降风险):
  - config 数值常量(baseByFormedBy/阈值/半衰期/transientCap 等,config.ts:109-143)→ 生成一份 `shared/config-constants.json` **快照** + 守门测试(断言 JSON == TS 常量,漂移即红);TS 仍是真相源,Python 读 JSON。
  - 8 条受治理提示词(zh/en 正文,registry)→ 生成 `shared/prompts.json` + 守门测试(哈希对齐现有 prompt-hashes.snapshot)。
  - 从 TS 侧生成**纯逻辑 parity 夹具**(输入→期望输出):遍历 formedBy×contentType×support×contradict 全组合喂 computeConfidence/deriveCredStatus、deriveFormedBy 组合、effectiveConfidence 时间点、hashEmbedder 对 golden.json、resolveEchoedId 样本 → `shared/parity/*.json`。
  - 验收:prompt-hashes 快照绿、api:check 一致、399/399 不变、新守门测试绿。**不触公共 API/schema**。
- **Phase 1(parity 内核·新 `py/` 子目录)**:移植上述纯逻辑 + 存储(evidence/event/cognition/interaction 四 store + store 驱动 + 迁移)+ 便携包(export/import/validate)。验证=**逐位对拍**(读 Phase 0 夹具断言同输出)+ **便携包互通**(TS export→Python import 往返)。
  - 拓扑序:config/types/枚举 → sqlite3 驱动+schema(照 SCHEMA 常量建全列、user_version=1)→ 四 store → 纯规则引擎(confidence/credStatus/deriveFormedBy/decay)→ hashEmbedder+召回门控 → 便携包。
- **Phase 2+(defer·Phase 1 后另议)**:LLM 写路径(distill/consolidate/attribute/trends/asking)+ httpx LLM/embed 客户端 + json-repair + echoedId 短标号 buildMessages + eval harness 重建 + §15.3 同语料对分。

**Python 技术栈(联网查证 2026·带理由)**:`uv`+hatchling(monorepo workspace)· 边界 `pydantic v2` + 内部 `dataclasses(slots)` · `pyright strict`(对齐 tsc)+ CI `mypy --strict` 二闸 · **stdlib `sqlite3` 同步**(FTS5 trigram 经查 CPython 默认自带、开箱可用;仍照搬 TS 运行时探测降级 FtsUnavailableError→NullRetriever)· 向量先纯 numpy 余弦(sqlite-vec 仍 alpha 不上)· `httpx.AsyncClient` 直打 OpenAI 兼容端点(不引 SDK,同 TS 精神)· LLM 脏 JSON 用 `json-repair` 库替换手写括号配平、但保留一次重试+隐私日志+reasoning_content 兜底编排 · `pytest`+`syrupy`+语言中立 golden JSON 对拍。async 取舍:异步只落 HTTP,SQLite 同步直调(勿盲上 aiosqlite)。

**三个 parity 杀手(Phase 1 红测必须显式覆盖)**:① `Math.round` 半值向 +∞ ≠ Python `round()` 银行家舍入 → 用 `floor(x+0.5)` 复刻(computeConfidence/effectiveConfidence);② hashEmbedder 的 `Math.imul`(32 位有符号)+`>>>0` → numpy int32 / `&0xFFFFFFFF` 掩码复刻;③ config 常量单一源(Phase 0 共享 JSON),严禁 Python 手抄。另:`[e1]/[e2]` 短标号(D-0036 防截断)、reasoning_content 兜底、usage 缺失静默——Phase 2 移 LLM 路径时逐字保留(dogfood 血泪逻辑)。

**parity 三层策略**:① 纯逻辑逐位对拍(Phase 0 夹具·CI 可跑·最强证据)② LLM 层 §15.3 同语料同模型同 judge 对分(Phase 2·比分布不比单值·先核 promptVersions/judgeVersion/gistScoringVersion 三口径)③ 便携包 TS↔Python 互通往返。

**位置**:memoweft 仓内新建 `py/`(uv workspace);共享资产放语言中立目录(如 `shared/`)供两语言载入。

破坏性:Phase 0 **无**(纯追加共享文件 + 守门测试,不触 API/schema/逻辑);Phase 1 **无**(新 `py/` 树,不碰 TS)。任何触 TS 公开 API/schema 的动作(目前策划里没有)另走铁律 2 影响面报告。
影响面:Phase 0 = `shared/` 新文件 + 生成脚本 + 守门测试 + 本条;Phase 1 = `py/` 新树 + uv 配置 + CI 加 Python job。
诚实分级:纯逻辑层 parity 是硬证据(逐位);LLM 层 parity 模糊(eval 分布,且语料全 1~3 证据小场景抓不到规模型缺陷,记忆 [[eval-blind-to-scale-bugs]])——**Phase 1 交付的是「不变量内核跨语言一致」,不是「整库等价」**,后者要 Phase 2+ 且最终仍须 dogfood。侦察未逐行读 expire/revisitConflicts/proposeAsk/各 prompt 正文/importBundle 悬空引用处理——Phase 1/2 各自动手前另侦察。
待批:~~Phase 0 开工信号~~ → **已批「开工」·Phase 0 已落**(见下);下一步 **Phase 1 开工信号**。

**Phase 0 已落地(2026-07-18·人类批「开工」)**:
- `scripts/gen-shared-assets.mjs`(+`.d.mts` 声明):import 真 TS 函数/常量,生成 `shared/` 下语言中立资产;`--check` 逐字比对(镜像 api:update/prompts:update)。npm 加 `shared:update`/`shared:check`。
- 产物 `shared/`:`config-constants.json`(纯逻辑数值常量+CARRIER_RANK/MIN_ID_PREFIX)、`prompts.json`(8 提示词 zh/en 原文)、`parity/{confidence(1280 例),cred-status,formed-by,decay(decayFactor double + effectiveConfidence 整数),hash-embedder(fnv1a32 uint32/tokenize/embed 向量),echoed-id}.json`——全是 `{input,expected}` 供 Python 逐位对拍。`shared/README.md` 说明。
- 守门 `tests/shared/shared-assets.test.ts`:①committed 与现生成逐字比对(TS 一改不刷新即红)②`prompts.json` 的 zh/en sha256 对齐 `prompt-hashes.snapshot`(证同字节)③三 parity 杀手样例在位。
- 为让生成器精确锚定 Math.imul,给 `tests/retrieval/hashEmbedder.ts` 的 `fnv1a32` 加 `export`(测试夹具、非公共面)。
- **零行为改动**:TS 仍是真相源,shared/ 是生成快照 + 守门(未重构 config.ts/prompts 去读 JSON)。gate:**typecheck 干净 · test 402/402(399+3 守门,无回归)· api:check 一致 · build · prompt-hashes 一致 · lint 0**。**不触公共 API/schema**。

**Phase 1a 已落地(2026-07-18·人类批「继续」·Python 纯逻辑 parity 内核)**:
- 新建 `py/`(自包含 uv 项目,`pyproject.toml` hatchling·requires-python ≥3.11·dep `regex`·dev `pytest`/`mypy`/`types-regex`)。**独立语言线,不碰 TS**(TS 门禁 typecheck/lint 实测不受 py/ 干扰)。
- 移纯逻辑不变量层(逐位对拍 TS 源):`config`(载入 `shared/config-constants.json`,单一源不手抄)、`confidence`(compute_confidence/derive_cred_status/is_transient)、`formed_by`(derive_formed_by 载体维取最弱)、`decay`(decay_factor/half_life_of/effective_confidence)、`echoed_id`(resolve_echoed_id 三级)、`hash_embedder`(fnv1a32/tokenize/HashEmbedder)、`types`(枚举对齐 union)。
- **三个 parity 杀手全复刻并验过**:①`_math.round_half_up = floor(x+0.5)`(≠ Python banker round)②`_math.imul` 32位 + `utf16_code_units`(复刻 charCodeAt 码元,≠ Python 码点迭代)③常量全从 `shared/` 载入。
- 验证=**读 `shared/parity/*.json` 逐位对拍**(`py/tests/`):confidence 1280 例、cred-status、formed-by、decay(decayFactor double + effectiveConfidence 整数)、echoed-id、hash-embedder(fnv1a32 uint32 精确 + tokenize 精确 + embed 向量极紧 tol)。gate:**pytest 9/9 全过 · mypy --strict 0 问题**。
- CI 加 `python-parity` job(uv sync → pytest → mypy --strict;与 TS 护栏独立、不阻塞发布)。`py/.gitignore` 排 .venv/__pycache__;`uv.lock` 入库。
- **移植即验证**:纯逻辑层已证「Python 与 TS 逐位一致」——这是 D-0042「parity 内核优先」最强的低风险跨语言背书。**待续 Phase 1b(四 store + SQLite schema)/1c(便携包 export/import/validate + TS↔Python 往返互通)**。

**Phase 1b 风险 spike:FTS5 trigram parity 实测解除(2026-07-18)**:侦察点名的**全案最高 parity 风险**——「trigram 中文排序在 node:sqlite 与 CPython sqlite3 可能细微不一致」——已用探针实测**证伪**:同 DDL(`fts5(... tokenize='trigram')`)+ 同数据 + 同 `bm25()` 查询,node:sqlite(SQLite 3.51.3)与 CPython 3.11 stdlib sqlite3(SQLite 3.50.4)结果**逐位一致**(含 CJK:'户外运动'→c1:-1.415061、'爬山爬山'→c5:-1.951142;'coffee' 两条全同)。≤2 字 CJK 查询两侧都空 = trigram 通性(需 ≥3 字成 token)、非分叉。⇒ **Phase 1b 存储层的 FTS5 schema + bm25 查询可放心照搬,parity 靠共享 golden 对拍(已证会一致)**;仍移植 FtsUnavailableError 探测降级(非官方 sqlite 构建兜底)。探针脚本在 scratchpad `fts_spike.{mjs,py}`;记忆 [[fts5-trigram-cross-lang-parity]]。

**Phase 1b 已落地(2026-07-18·人类批「继续」·SQLite schema + driver + FTS + parity)**:
- 生成器扩两份权威 golden(从 `openStores(':memory:')` 真建库 dump):`shared/parity/schema.json`(8 表逐列 pragma_table_info + user_version=1,**驱动无关**)、`shared/parity/fts.json`(FTS5 trigram + bm25 **id 排序** golden;只锁排序不锁分数)。**跨驱动验过**:node:sqlite(3.51)与 `MEMOWEFT_TEST_DRIVER=better-sqlite3` 两侧生成的 golden 逐字一致 → CI 矩阵不漂移。
- Python `py/src/memoweft/store/`:`schema.py`(照 TS SCHEMA 常量的 8 表 DDL,fresh 库建全列)、`driver.py`(open_db 单连接 + busy_timeout + user_version + FTS5 探测/FtsUnavailableError)、`keyword.py`(KeywordRetriever:to_match_query 消毒 + trigram FTS + bm25 search)。async 取舍照 D-0042:SQLite 同步直调。
- 验证:`py/tests/test_schema_parity.py`(建库 dump 逐表逐列 == schema.json)+ `test_parity_fts.py`(FTS5 可用 + 同数据同 MATCH 的 id 排序 == fts.json)。**schema 结构与 FTS 排序跨语言逐位一致坐实。**
- gate:**Python pytest 12/12(9 纯逻辑 + 3 存储)· mypy --strict 0**;**TS shared:check 一致(两驱动)· npm test 402/402 · typecheck · api:check 一致 · lint 0**。
- 诚实分级:Phase 1b 交付 **schema/driver/FTS 基座 + parity**;**逐 store 的 CRUD(put/get/list/update)延到 Phase 1c 便携包**(那里真用到 insert/list)。管理表 CRUD / 老库 runMigrations 升级 / 向量表另阶段。

**Phase 1c 已落地(2026-07-18·人类批「继续」·便携包 validate + interop → Phase 1 收尾)**:
- 生成器扩两份夹具:`shared/parity/bundle.json`(手工构造【固定 id/时间戳】的完整包,**用 TS `validateBundle` 断言确为合法包**)、`shared/parity/bundle-validate.json`(16 个好/坏例 + 各自 TS `validateBundle` 现算的 `ValidateResult`,en)。
- Python `py/src/memoweft/portable/`:`validate.py`(逐字对拍 validateBundle.ts,en 消息含 `JSON.stringify` 语义复刻)、`importer.py`(字段映射 camelCase→snake_case + bool→0/1,INSERT OR IGNORE 去重)、`model.py`(常量)。
- 验证:`test_parity_validate.py`(16 例 ValidateResult **逐字**含消息一致)+ `test_bundle_interop.py`(**TS 合法包 → Python 建同构库导入 → id/时间戳/关键字段/溯源链逐条保真 + 幂等**)。**这是 1.3 最强的跨语言证据:TS 产的记忆包 Python 原样读回、数据不丢。**
- gate:**Python pytest 15/15 · mypy --strict 0**;**TS shared:check 一致 · npm test 402/402 · typecheck · api:check 一致 · eslint 0**。
- 诚实分级:interop 用的是**手工构造但经 TS validateBundle 认证合法**的包(store.put 会生随机 UUID、不能作确定性 golden),非「真从填充库 exportBundle」;字段集按 MemoryBundle 接口完整覆盖。完整 export(Python 侧产包)/ dryRun/merge 明细 / 老库迁移 = 后续。**Phase 2(LLM 写路径 distill/consolidate + httpx 客户端 + eval harness + §15.3 对分)整体 defer,待人类拍板是否推进**。

## D-0043 1.3 Phase 2 分阶段策划 · LLM 写路径全量移植(承 D-0042·人类批「全量到 eval 对分」)

日期:2026-07-18 / 状态:**范围已采纳(人类 2026-07-18 拍板「全量到 eval 对分」);7 路只读侦察落地,分阶段计划入库,P2-1 开工**
背景(动机):D-0042 定「Phase 1(parity 内核)跑完再决定是否推进 Phase 2」,现为决策点。动手前派 **7 路并行只读侦察**(llm基建 / distill / consolidate / attribute+trends / asking / prompts+eval / store-crud 缺口,全带 `file:line`,~91 万 token,0 error),据此定分阶段。人类拍板范围 = **全量**(到 §15.3 eval 跨语言对分 + 便携包硬化)。

**侦察关键洞察(定分阶段的依据)**:
- Phase 2 的大头**不是「移 5 个写路径」**,而是 **py 侧尚未移的一整套底座**:5 个 store 的 CRUD(py 现只有建表 DDL + `driver` + FTS,**零 CRUD 类**)、httpx LLM 客户端、JSON 加固、隐私门。写路径本身逻辑不重(判定+落库),且 `computeConfidence`/`deriveFormedBy`/`resolveEchoedId` 三块纯逻辑 Phase 1 已移完并逐位对拍 → **大幅降 consolidate 风险**。
- parity 分层:底座 + expire = **逐位/结构对拍**(硬证据);LLM 写路径 = FakeLLM 定值下 **buildMessages 字节对拍 + 结构判定对拍**(CI 可跑),端到端只能比 §15.3 **分布**(非逐位),**最终验收仍须 dogfood**([[eval-blind-to-scale-bugs]])。

**分阶段计划(约 10 子阶段 + 旁路,任务清单 #2…#13)**:
- **P2-1a** store CRUD 地基(evidence/event/cognition + model + clock 产 JS 兼容 ISO)→ **P2-1b**(semanticResolution/interactionContext + 感知入口 perceive/ingest + privacy + sourceLabel)
- **P2-2** expire(纯规则,`parity/expire.json` 逐位,写路径第一个绿灯)
- **P2-3** httpx 客户端 + jsonRepair + prompts loader + resolveLang(三写路径共享底座)
- **P2-4** distill(最简)→ **P2-5** consolidate 判定(离线红测)→ **P2-6** consolidate 落库+事务 → **P2-7** attribute+trends → **P2-8** asking(先补 maxAsks 生成器)→ **P2-9** updateProfile 编排
- **P2-10** §15.3 Python harness(读同 `corpus.json`/同 judge/同 schema run JSON,复用 TS `--compare` 对分;§15.5 第二被测 gpt-4o 见 [[eval-second-model-gpt4o]])
- **P2-旁** 便携包硬化(importBundle 悬空/去重/校验/dryRun,Phase 1c 遗留,可与 P2-1 并跑)

**Phase 2 新 parity 杀手(侦察揪出,实现逐一钉死)**:① json-repair 库比手写宽松 → 首过修好会吞「必重试」契约、`llmCalls` 分叉;② `json.loads` 默认收 `NaN/Infinity`(JS 拒)+ `bool` 是 `int`(usage 字段);③ consolidate 私有 `resolveEvidenceId` **只剥 `ev-`**(≠共享 `ev-|cog-`),必单独复刻;④ `??` vs `or`(空串不回落)、`[...new Set()]` 保序(用 `dict.fromkeys`)、`toISOString` 毫秒3位+`Z`、UTF-16 slice(`AI_CONTEXT_MAX=240`)、`ORDER BY`/迭代序→标号字节;⑤ JS `trim` ≠ py `strip`(BOM/异体空白)。已知三杀手(round/imul/config)Phase 1 已解。

**偏离 D-0042 一处(记档)**:D-0042 原写「LLM 脏 JSON 用 json-repair 库替换手写括号配平」。侦察证此有 parity 风险(库宽松度 > 手写 → 首过就成功 → 少一次重试、`llmCalls` 分叉)。**改为:逐字复刻手写 `extractJsonObject` + 首过严格(`json.loads` `parse_constant` 抛错、只认对象),json-repair 库仅在明确对齐位置兜底或不用于此路径**。纯 parity 正确性考量;D-0042 的「一次重试 + 隐私日志(只记结构特征不记原文)+ reasoning_content 兜底」编排不变。

**单一源缺口(P2-8 前必修)**:`shared/config-constants.json` 的 `asking` 块缺 `maxAsks`(生成器 `gen-shared-assets.mjs:89` Phase 0 时故意漏,纯逻辑内核用不到)。移 asking 前先扩生成器纳入 + 守门测试,py 从 JSON 读、禁手抄。

**范围边界(明确排除 Phase 2)**:`reply`/`action.ts` + `REPLY_PROMPT`、`Conversation` 回话半边、`recall`/`retriever`、asking 的调度触发、memory 管理 API、graph——属读/表达层或 weftmate 产品侧(D-0031)。REST/多租户/pgvector 仍归 weftmate。

破坏性:**无**——py 新树 + 各写路径 py 移植;唯一触 TS 侧的是 P2-8 扩 shared 生成器纳入 `asking.maxAsks`(**内部跨语言资产,非公共 API/schema**,走 `shared:check` 守门)+ P2-2/P2-8 扩 `shared/parity` 夹具 + P2-10 抽 corpus/judge 进 shared。**不触 core api-freeze**。任何触 TS 公开 API/schema 的动作(目前计划里没有)另走铁律 2 影响面报告。
影响面:`py/` 新增写路径全树 + shared 生成器扩 `asking.maxAsks`/`expire.json`/asking parity + corpus/judge 抽 shared + CI python job 扩测。每子阶段在本条范围内 + gate(pytest + mypy --strict)+ commit。
诚实分级:底座/expire parity 是硬证据(逐位/结构);LLM 写路径 parity = FakeLLM 字节对拍 + 结构判定(CI)+ §15.3 分布(非逐位);**Phase 2 交付「写路径跨语言等价的强证据链」,但「整库真等价」的终审仍在 dogfood**。侦察诚实标注未通读处(`interactionSession`/`workingMemory` 的 precedingAiContext 捕获、各 `model.ts` 字段、`consolidate.ts` 部分尾段)——各子阶段动手前按需补读。
待批:~~Phase 2 是否推进~~ → **已批「全量到 eval 对分」**;分阶段见上,P2-1a 开工。

**P2-1a 已落地(2026-07-18·store CRUD 地基:evidence/event/cognition + model + clock)**:
- 扩生成器 `gen-shared-assets.mjs`:config-constants 纳入【证据授权默认】(`privacyMode`/`evidenceDefaults`/`observedDefaults`/`toolDefaults`,跨语言授权红线,补 Phase 0 缺口);新增两 parity 夹具——`evidence-auth.json`(4 sourceKind × 8 显式 × 2 privacyMode = 88 例,授权分流)、`cognition-order.json`(all/active 的 `ORDER BY confidence DESC, created_at ASC` + active 排除 invalid/archived 的 id 序 golden,补 Phase 1b 未覆盖面),均用【真 TS store】产 golden。
- Python:`clock.py`(Clock + system_clock + `to_iso_z` 复刻 JS `toISOString` 毫秒 3 位 + Z);`types.py` 补 Evidence/EvidenceInput/Event/EventInput/Cognition/CognitionInput/CognitionPatch/EvidenceLink dataclass(+ `_Unset`/`UNSET` 哨兵复刻 patch 三态);`config.py` 补授权默认 runtime 面 + `cloud_read_default`;`store/{evidence,event,cognition}.py` CRUD(`driver` 改 `isolation_level=None` autocommit 对齐 node:sqlite;局部 Row 游标不扰 keyword)。
- 验证:授权分流 88 例逐位对拍 TS;all/active 排序对拍 golden;内部单测(origin_id 幂等 / summary·occurred_at 缺省 / preceding_ai_context 结构墙 / update 三态 UNSET 保留 vs None 复位 / event 覆盖链 / clock 格式)。gate:**py pytest 26/26 · mypy --strict 0**;**TS shared:check 一致 · typecheck · api:check 一致 · npm test 402/402**。
- 诚实分级:store CRUD 结构/授权/排序跨语言坐实;put/insert 用随机 uuid → id 值不逐位对拍(结构对拍)。**待续 P2-1b(semanticResolution/interactionContext + 感知入口 perceive/ingest + privacy + sourceLabel)**。

**P2-1b 已落地(2026-07-18·interaction 两 store + 感知入口 + privacy + sourceLabel)**:
- 扩生成器:config-constants 纳入 `identity`(perceive/ingest 缺省身份);新增 parity 夹具 `source-label.json`(sourceLabel 8 例 + aiContextSuffix 含 BOM/240 截断/emoji)、`context-hash.json`(hashContext 6 例含中文/emoji/JSON 转义)。
- Python:`_jsstr.py`(js_trim 复刻 JS trim 白名单含 U+FEFF、utf16_length/slice 复刻 UTF-16 code unit,**用码点数字构造避免字面空白不可靠**);`privacy.py`(filter_readable_by_tier + 别名,Protocol `@property` 适配 frozen dataclass);`source_label.py`;`store/{interaction_context,semantic_resolution}.py`(`hash_context` 用 `json.dumps(ensure_ascii=False, separators=(",",":"))` + `role,content` 字段序对齐 JS `JSON.stringify`);`perceive.py`/`ingest.py`;config 补 `identity`/`Identity`。
- 验证:sourceLabel/aiContextSuffix 逐位对拍(js-trim/UTF-16 slice/全角括号字节);hashContext sha256 逐位对拍(JSON 字节等价);两 store 内部单测(record context_hash 幂等 + roundtrip / semantic put·of_evidence·for_evidence_ids)+ privacy 筛选顺序保留 + perceive 缺省 identity + ingest observed 幂等/授权。gate:**py pytest 33/33 · mypy --strict 0**;**TS shared:check 一致 · npm test 402/402**。
- 诚实分级:store CRUD 结构 + hash/label 字节跨语言坐实。**Phase 2 底座层(store 全套 + 感知 + 隐私门 + sourceLabel)完成 → 待续 P2-2(expire 纯规则,写路径第一个逐位对拍绿灯)、P2-3(httpx LLM 客户端 + jsonRepair + prompts loader)**。

**P2-2 已落地(2026-07-18·expire 纯规则,写路径第一个逐位对拍绿灯)**:
- 扩生成器:新增 `parity/expire.json`(用真 TS expire + SqliteCognitionStore;固定 now + 9 条认知集 → `{expired, invalidIds}`);config-constants 已含 `background.expireAfterDays`。
- Python:config 补 `expire_after_days`;`clock.py` 补 `epoch_ms`/`parse_iso_ms`(**整数** epoch 毫秒,对齐 JS `Date.getTime()`、避 float 误差);`expire.py`(遍历 active、`ageDays = (now_ms - updated_ms)/DAY_MS` 严格 > 阈值标 invalid、名单外永不过期)。
- 验证:9 条认知集逐位对拍(state7/hypothesis14/trend30 严格 > 边界、fact/preference 不列、归档 active 排除不碰)+ 内部单测(invalid_at = now ISO + 幂等)。gate:**py pytest 35/35 · mypy --strict 0**;**TS shared:check 一致 · npm test 402/402**。
- **待续 P2-3(httpx LLM 客户端 + jsonRepair + prompts loader + resolveLang,首个引入网络 I/O 的子阶段)**。

**P2-3 已落地(2026-07-18·httpx LLM 客户端 + jsonRepair + prompts loader + resolveLang)**:
- 扩生成器:`client.ts` 的 `stripReasoning`/`readReplyText` 加 `export`(测试夹具用途,**不进 index/api 快照**);新增 `parity/llm-text.json`(strip 9 例 + read 8 例)、`parity/json-extract.json`(extract 11 例 + parse 11 例含 NaN/Infinity)。
- Python:pyproject 加 `httpx`;config 补 `resolve_lang`(env `MEMOWEFT_LANG`);`llm/client.py`(ChatMessage/UsageStats/LLMConfig/LLMClient Protocol/OpenAICompatClient + `load_llm_config` + `strip_reasoning`/`read_reply_text` + `_js_number` 复刻 JS Number 语义 + `_int_or_zero` 治 bool-is-int + 双前缀 env is-None 回退);`llm/json_repair.py`(逐字复刻手写 `extract_json_object` 括号配平 + `parse_json_object` 首过严格 `parse_constant` 拒 NaN/Infinity + `parse_json_object_with_repair` 重试);`llm/prompts.py`(从 shared/prompts.json 载 8 条 + get/versions/nudge)。
- **两处偏离 D-0042 记档**:① async → 用【同步 `httpx.Client`】(py 无 JS fetch 的 async 强制,整条写路径同步、与 SQLite 一致、测试无 asyncio;parity 不受影响);② JSON 修复 → 【不引 json-repair 库、逐字复刻手写】(库宽松度 > 手写会吞掉「首坏必重试」契约、`llmCalls` 分叉——有专项测试 `test_nan_forces_retry_library_does_not_rescue` 坐实 NaN 首过失败触发重试)。
- 验证:strip/read/extract/parse 逐位对拍 TS;client chat via httpx MockTransport(请求体/去尾斜杠/Bearer/temperature 缺省 0.3/usage 累加/total 回退/reasoning 兜底 + think 剥离/HTTP 错误);load_llm_config env(temperature 0 合法/非法→None/tier 大小写不敏感/拼错→None/DLA 回退/缺项抛错);json_repair 重试(首过成功不重试/首坏必重试/两坏→None/NaN 触发重试/日志不泄原文);prompts loader(8 条/版本/nudge)。gate:**py pytest 55/55 · mypy --strict 0**;**TS shared:check 一致 · typecheck · api:check 一致 · lint 0 errors · npm test 402/402**。
- **Phase 2 底座 + LLM 层全部就位 → 待续 P2-4(distill:首个把 LLM 底座 + store CRUD + 隐私门 + sourceLabel 串起来的写路径端到端闭环)**。

**P2-4 已落地(2026-07-18·distill 证据→事件,写路径第一个端到端闭环)**:
- 扩生成器:新增 `parity/distill.json`(真 TS distill + stub llm,zh/en 两组;固定证据集经隐私门各分支 → dump messages 逐字节 + event summary/occurredAt + pending/tierBlocked/digestible 计数)。
- Python:`distill.py`(同步;两道早退 + tier+inference 隐私门 + 材料行拼接 sourceLabel/aiContextSuffix/`occurred_at[:16]` + 时间锚 digestible[0] + D8 覆盖修复 + summary js_trim)。**首个把 LLM 底座 + store CRUD + 隐私门 + sourceLabel + prompts loader 串起来的端到端**。
- 验证:zh/en messages 逐字节对拍(system=distill@v2 / user=材料行)+ event summary(trim)/occurredAt(时间锚)/pending4·tierBlocked1·digestible2 计数;隐私门(observed cloud 挡、infer=false 不消化,均不算已覆盖)+ 两道早退(无 pending / 全被挡都不调模型)。gate:**py pytest 57/57 · mypy --strict 0**;**TS shared:check 一致 · npm test 402/402**。
- 诚实分级:messages 字节 + 隐私门 + 计数跨语言坐实;LLM 真实产出(summary 文本)非确定,用 stub 固定验管线。**待续 P2-5(consolidate 判定层,Phase 2 最核心;离线红测,无写)**。
