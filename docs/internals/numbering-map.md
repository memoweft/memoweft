# 编号对照表(numbering map)

> 本表是**导航索引**,给第一次接手 MemoWeft 的人把仓库里散落的 `Phase` / `D-xxxx` / `AD-x` / `Step(S)` 编号一次讲清。**权威定义以 [`DECISIONS.md`](../../DECISIONS.md) 与 [`PROJECT_PLAN.md`](../../PROJECT_PLAN.md) 为准**;此处只做一句话索引,拿不准的以源文件为准。
>
> (本页面向新人、用简体中文,参照 [`boundaries.md`](./boundaries.md) 的中文先例,见 [`DECISIONS.md`](../../DECISIONS.md) 的 D-0016。)

---

## 一、Phase(升级阶段 ↔ PROJECT_PLAN 章节)

七个 Phase 是本轮升级的主线,对应 `PROJECT_PLAN.md` 第三部分「执行计划」的各章。速查与依赖关系见 §11。

| Phase | PROJECT_PLAN § | 一句话主旨 |
|---|---|---|
| **0** | §12(机制在 §13) | 奠基:基线可复现、公共 API 快照机器防线、多智能体配置包、治理文件落地 |
| **1** | §14 | **召回更准**:BM25+向量混合召回、RRF 融合、增量索引、双臂(确定性/真实)黄金集评测 |
| **2** | §15 | **固化更可信**:用真实模型度量最脆弱的写路径(场景语料库、两级固化评测、提示词回归、nightly live) |
| **3** | §16 | **适配器更稳**:契约测试套件化(adapter-kit)、故障注入与降级语义、peer 版本矩阵 CI、注入格式锁定 |
| **4** | §17 | **demo 更锋利**:四幕叙事、一条命令、无 key、确定性复现 |
| **5** | §18 | **文档更不绕**:README 60 秒电梯稿、每页一个任务、片段可执行验证、死链清零 |
| **6** | §19 | **公开基准(常态化)**:LongMemEval + LoCoMo、三臂×双 embedder 矩阵、runs 可复现、BENCHMARKS.md |

---

## 二、D-xxxx(决策 · ADR-lite)

每个有争议的取舍一条,全文见 [`DECISIONS.md`](../../DECISIONS.md)(编号即锚点)。下表每条一行,只给主题。

| 编号 | 一句话主题 |
|---|---|
| **D-0001** | FTS5 tokenizer 选择:默认 `trigram`(CJK 稳、索引大,中文需 3 字才命中),纯英文可配 `unicode61` |
| **D-0002** | 协作模式 = 务实混合:Integrator 用 Agent/Workflow 即时委派并行,`.claude/` 落地供以后会话 |
| **D-0003** | Phase 4 demo = 改造现有 testbench,而非从零新建(终端四幕、纯文本、无 key、确定性) |
| **D-0004** | hook 落地适配(偏离附录 I.2):`protect.py` 三处修正(用 `python`、force-push 正则加固、stderr 强制 UTF-8) |
| **D-0005** | 检索现状修正 + mimo 特性:瓶颈在读侧 O(N) 全扫、向量存 JSON 表不版本化、写路径非单事务;mimo 是推理模型 |
| **D-0006** | KeywordRetriever 策略(§14.3):tokenizer 白名单校验、只索引 active、sha256 影子表增量、`score = -bm25()` |
| **D-0007** | 纯 TS BM25 降级暂缓(偏离 §14.3 降级链):FTS5 全支持环境可用,不写不触发的死代码,留 `FtsUnavailableError` 探测点 |
| **D-0008** | hybrid 不接入公共 API(§14.4b):三臂消融显示 hybrid≡vector 零增益,召回提升全来自真实 embedder |
| **D-0009** | 固化提示词 v2:治闲聊过度记忆 + 软判指标可靠性;方法学结论——gistRecall 软判高方差,回归以结构硬指标为准 |
| **D-0010** | 不建 `fixtures:refresh`(偏离 §15.4):本仓无 LLM 录制夹具,防漂移由三道已有闸门接管(评测/哈希/冻结库) |
| **D-0011** | `SKIP_LIVE_LLM` 是死变量,从 CI 删除:全仓无人读它,真正让 live 跳过的是 `HAS_LLM` |
| **D-0012** | recall/ingest 降级语义写入契约(§16.2):recall 超时默认 200ms、读不重试写一重试、降级注空上下文 + logger |
| **D-0013** | AD-3:`SourceKind` 加 `'tool'` + `core.ingestToolResult`(工具结果摄入),保守授权默认 + 语义干净摄入门面 |
| **D-0014** | §16.3 适配器 SDK 版本矩阵:矩阵化 dependency(不改 peer),`--no-save` 探针测声明范围两端 |
| **D-0015** | 可注入时钟 `Clock`(Phase 4 时间注入):方案 C 全局可注入,分 S1a/S1b/S2/S3/S4 落地,确定性 + 时间旅行 |
| **D-0016** | internals 迁移:`boundaries.md` 保留中文(D-a 英文单源的显式例外——纯中文无英文版,翻译损耗>收益) |
| **D-0017** | 无-embedder 召回兜底:`NullRetriever` → `KeywordRetriever`(§14.4b 大语料重评估);FTS5 不可用再降 NullRetriever |
| **D-0018** | 来源感知固化:distill/consolidate 的 utterance 带来源标注,observed/tool 不再被误固化成「用户亲口」 |
| **D-0019** | no-over-inference 的 fact-vs-state 缺口 = `ContentType` 缺「事件」型的已知定义局限(记录·不改源码/语料/提示词) |
| **D-0020** | 补全 D-0015 时钟不变式:`asking` 的 askedAt / `runLog` 的 ts 也可注入 clock——至此全仓时间源皆可注入 |
| **D-0021** | 召回解释:`core.recall({ explain })` 让召回认知带支撑/反证证据链(`provenance` / `RecalledEvidence`),正中「可追溯」卖点 |
| **D-0022** | 召回按 contentType 过滤:`core.recall({ contentTypes })` 按类型允许名单筛 + 结果暴露 `contentType` |
| **D-0023** | 召回负反馈 = Mute:加 `mutedAt` 状态位,召回跳过但认知仍 active、仍参与画像演化(mute⊂archive⊂invalidate) |
| **D-0024** | 召回 v2 端到端收口:透传 explain/contentTypes/provenance/contentType 到两适配器 + MCP 新增 mute tool + provenance 按 tier 预筛 |
| **D-0025** | §16.5 新增 Claude Agent SDK 适配器(`@memoweft/adapter-claude-agent-sdk`,hooks 型进程内)+ MCP 挂载备选文档 |
| **D-0026** | Reranker NO-GO:真实检索序近最优、fusion 净负,不实装(同 D-0008 手法·数据驱动证伪;bench 入仓作背书) |
| **D-0027** | 新增 OpenAI Agents SDK 适配器(`@memoweft/adapter-openai-agents`,run-wrapper 型)—— 更多适配器批次 ① |

---

## 三、AD-x(适配器契约 · adapter-kit)

Phase 3 把散落的适配器断言收敛为可复用工具包 `tests/adapter-kit/`,任何适配器接入即得完整套件。编号写进测试名,定义源在 `PROJECT_PLAN.md` §16.1。

| 编号 | 测什么 | 落地语境 |
|---|---|---|
| **AD-1** | 助手消息流经适配器后,**evidence 表零新增**(铁律 3a:助手输出永不成为证据) | 两适配器已绿 |
| **AD-2** | 用户消息 → **恰好一条 evidence**,role=user(用户「亲口说」= 一条 spoken 证据) | 两适配器已绿 |
| **AD-3** | 工具结果 → evidence,**source 标记为 `tool`**(工具返回的客观数据 = 合法证据,不摄入调用意图) | D-0013 从 N/A 翻 applicable |
| **AD-4** | recall 注入内容含**置信度与冲突提示**,格式与 §16.4 golden 快照一致 | 已定:credStatus=`conflicted` 即算冲突提示,纯 golden 快照 |
| **AD-5** | mock LLM 返回**虚构 evidenceId** 时被白名单丢弃(铁律 3d) | 两适配器无 LLM→evidenceId 回捞面,by-construction → 落地为 **N/A** |
| **AD-6** | 记忆层抛错/超时,适配器**降级为「无记忆但对话不中断」**,以注入 logger 记录 | D-0012 从 N/A 翻 applicable(修掉 MCP「抛错即崩」硬伤) |

---

## 四、Step(S)记号

`S1a/S1b/S2/S3/S4` 是 **D-0015 可注入时钟**在 Phase 4 的分步落地记号(同一决策的实现步,不再单开 D)。逐步从「只固定落库时间」推进到「完整确定性 + 时间旅行」。

| 记号 | 一句话 |
|---|---|
| **S1a** | (internal)三个 store(evidence/event/cognition)构造加可选 `clock`,落库/更新时间走 `clock()`;store 构造签名是 internal、不进 api 快照 |
| **S1b** | (触 api-freeze)`CreateCoreOptions` + `openStores` 加可选 `clock` 参、工厂透传;导出 `type Clock` + `systemClock` |
| **S2** | 写路径算子(consolidate / attribute / updateProfile / managementApi / runLog)里直接的 `new Date()` 统一改走注入的 clock |
| **S3** | 读路径的 now(`core.recall` / `handleConversationTurn` → 传给召回衰减·过期门控)改走注入 clock——前进 clock → 情绪 `state` 衰减出局、`fact` 留存 |
| **S4** | 四幕 demo + fast-forward 时间旅行(demo 持可变闭包 clock,快进前移 `t` 触发衰减);确定性验收 = 两次运行 diff 逐字为空 |
