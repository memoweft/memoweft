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
