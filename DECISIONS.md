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
