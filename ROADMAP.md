# ROADMAP

MemoWeft 是 **library-first** 的可移植 AI 长期记忆库。公共 API 稳定分层与破坏性变更策略见 [`docs/memory-surface-contract.md`](./docs/memory-surface-contract.md)。具体推进见 `PROJECT_PLAN.md` 与 `CURRENT.md`。

## Now(本轮升级,对应 PROJECT_PLAN.md Phase 1–6)

- 召回更准(Phase 1)· 固化更可信(Phase 2,真实模型质量线)· 适配器更稳(Phase 3)· demo 更锋利(Phase 4)· 文档更不绕(Phase 5)· 公开基准(Phase 6,常态化)

## Next(本轮之后优先考虑)

- 更多适配器(OpenAI Agents / LangChain / LlamaIndex),待 adapter-kit 被证明后批量做
- Reranker 实装(Phase 1 若时间紧,`Reranker` 接口先留 no-op,实装下放此处)
- ~~**keyword / hybrid 召回重评估**(见 D-0008)~~ → **已重评估(D-0017)**:Phase 6 §19.2 LoCoMo 大语料矩阵证实——真实 embedder 下 hybrid 仍零增益(`hybrid`/`mode` **仍不接 API**),但无 embedder 时 keyword 55.3% ≫ 空,故**无-embedder 兜底 NullRetriever→KeywordRetriever**。剩:keyword 有利的**稀有精确词/错拼/OOV/代码标识符** workload 仍可专门黄金集再评 hybrid 是否值得接入(暂无此需求)
- 纯 TS BM25 降级(D-0007,FtsUnavailableError 探测点已留,待无 FTS5 环境出现)
- 召回质量 v2:~~相似度阈值~~ ✅ **收口(2026-07-13·测+记文档,不扩 API)**:机制既存(`config.retrieval.minSimilarity`,默认 0=关,`recall.ts:48` 门控)。真实 bge-m3 黄金集实测:两分布重叠、阈值是"修边"——**0.55 甜点**(零召回损失砍 ~9% 噪声),≥0.6 误杀 gold。收益温和 + 值 embedder-specific + 旋钮已在 → 按铁律 4 不扩 API,只把数据背书写进 config 注释 + calibration。~~**召回解释**~~ ✅ **证据链增量已做(2026-07-13·D-0021·人类批准)**:`recall({ explain:true })` → 每条召回认知带 `provenance`(支撑/反证证据链 + summary + 授权位),门面富化、底层不动、纯 additive。经对抗审查加固:provenance 随附 `allowCloudRead/allowInference`(对齐 buildMemoryGraph),宿主转发云模型前可自筛。剩「命中词」半(要贯穿检索器)留后按需。~~**content 过滤**~~ ✅ **已做(2026-07-13·D-0022·人类批准)**:`recall({ contentTypes:[...] })` 按类型允许名单筛 + 每条召回结果暴露 `contentType`;门面过滤、底层只加填字段、纯 additive。对抗审查 0 confirmed。剩:**purpose 过滤**(认知层无此字段,要么另立要么映射 contentType/scope,留后)、**contentType 透传到 MCP/ai-sdk 适配器**(D-0022 仅 core.recall 门面;适配器现只投影固定字段,想适配器级筛需另接线,进 Later)、**负反馈**(唯一动 schema 的独立项目)
- 保持 Core / Host / Plugin 权限边界的新插件
- **真实 LLM 录制回放层**(D-0010 的 caveat):当出现"冻结某模型版本的行为做跨模型对拖"的需求时新建**独立**录制层,不要改写现有 48 处意图清晰的内联 fake
- ~~**`PROJECT_PLAN.md §20` 环境变量表订正**~~ ✅ 已订正(2026-07-12):换成实际 `MEMOWEFT_*`(+ `DLA_*` 回退)全表,含 WRITE_LLM/EMBED/EXPERIENCE_UI/LANG/JUDGE/基准 PATH;删旧抽象与 `SKIP_LIVE_LLM`
- ~~**`.gitattributes` 全仓 eol 归一**~~ ✅ 已归一(2026-07-12):加 `* text=auto eol=lf` + 常见二进制 `-text`。**实测索引本已全 LF**(`git ls-files --eol` 全 i/lf),故 `git add --renormalize .` 零内容 diff——旧「200+ 无关 diff」的担心不成立,只改了 `.gitattributes` 一个文件。工作树已检出的 CRLF 就地归一需 `git add --renormalize . && git checkout .`(可选)
- ~~**§15.5 多模型分差矩阵**~~ ✅ **已做(2026-07-13)**:固化评测器加 `--subject-env` 被测模型注入(judge 固定 mimo,只动一个自变量),跑 gpt-4o 臂全 42 场景。**结论:指标跨模型稳健**——总体 mimo 94.2% vs gpt-4o 96.0%(+1.8pp),3/6 盘逐检查完全相同、overInfer 两模型全 0;评测器量的是纪律本身、非 mimo 怪癖。**no-over-inference 28/34 两模型一模一样 → 跨模型印证 D-0019**(fact-vs-state 是 ContentType 缺口非 mimo 缺陷)。见 BENCHMARKS §5。(可选后续:配第 3 个模型进一步坐实)
- ~~`bench/eval-retrieval.mjs` 报告「生成命令」行硬编码~~ ✅ 已修(2026-07-12):`INVOKED_CMD` 反映实际调用(`EVAL_REAL_ARM` 前缀 + `--ablation`/`--out`/`--real` 等 flag)
- ~~**可注入时钟方案 C 尾巴**(D-0015 遗留两处非门面 `new Date()`)~~ ✅ **已补完(2026-07-13·D-0020·人类批准)**:`ProposeAskDeps`/`RevisitDeps`(askedAt)、`RunLoggerOptions`(ts)各加可选 `clock?`(照 S2 范式,纯 additive、走 api:update + 契约 en/zh + CHANGELOG)。**至此全仓时间源皆可注入、无散落 `new Date()` 时间戳**(`updateProfile` 的 `Date.now()` 是耗时计时非时间戳,保留)。clockInjection.test 加 4 例(含铁律 3b:注入 clock 不改 confidence)。
- ~~**来源感知固化**~~ ✅ 已修(2026-07-13 · **D-0018**):distill/consolidate 的 utterance 视图带来源标注(`src/evidence/sourceLabel.ts`),两提示词(distill v2 / consolidate v3)据来源定 formedBy——observed/tool 不再被误固化成 `stated`。加固来源强度纪律,不改 API/schema/eval 断言。§15.3 前后对比:结构 95.1%→94.2%(判为单跑方差,D-0018 目标区 conflict 盘纹丝不动、overInfer 全 0)。
- **固化评测的两处度量退化清理**(2026-07-10 scout 诊断,均非模型质量问题):~~① conflict 的 gistRecall 恒为 0——判官只看落库认知文本,看不见"打 conflicted 标"这条处理路径~~ ✅ **已修(2026-07-13·纯 bench)**:conflict 场景的 shouldForm 改确定性硬判(看 `run.active` 是否存在在册 credStatus='conflicted' 认知 = 冲突已暴露且旧认知留档);shouldNot 仍 LLM 软判。附带经对抗审查加 `GIST_SCORING_VERSION='v2'`(diffRuns 跨口径高声告警,防旧基线对比误读)+ printDiff conflict 行标[确定性]。~~② no-over-inference 的 5 分缺口是 fact-vs-state 类型口径分歧~~ ✅ **已收口(2026-07-13·D-0019·记档不改)**:实跑校准发现 5 分实为三类(4× 一次性事件 fact-vs-state + CC-029 推 goal + CC-032 推 preference),且全在 `overInferRate=0.00` 之上——真过度推断纪律达标,type-check 咬的是 ContentType 缺"事件"型的**定义灰区**。判为定义噪声、零改源码/语料/提示词(同 D-0009 手法);真正的正解「加 `event` 型」进 Later(伏笔,待真实产品需求驱动)。①②两项均已清。
- **Phase 5 文档巡检的降级项**(2026-07-11 §18.5 新人视角巡检,5 路 scout 出 ~54 条;本批已修 5 个 HIGH + quick-win,以下结构性/新翻译部分降级):
  - **三个新中文版**:`docs/demo-script.zh-CN.md` / `docs/README.zh-CN.md`(文档总入口)/ `docs/reference-host.zh-CN.md` —— 中文读者从中文页点这些会掉进英文(全路径最高频的中→英断裂)。
  - **concepts 六页重排**:read-write(先存后消化)是前置知识却排第 6、no-self-evidence 与 sourcing 同族却被隔开;重排要同步改一堆交叉链接与 Next。
  - **internals 导航**:给 D-/AD-/Step/Phase 编号一张对照表(新人看不懂);`internal/`(私账)vs `internals/`(公开机制)一字之差易混,评估改名;memory-surface-contract 加可点击 TOC + §II 锚点。
  - **perf recall 基准**:Results 表 recall≈0ms 是 NullRetriever,想评估召回性能的读者拿不到数;补一组配 embedder 的基准或显式标注"仅存储层开销"。
  - 若干 low 措辞:README Why 段补一句 vs vector store 的定位、profile↔cognition 对应、decay 变量名 anchoredAt↔updatedAt、WorkingMemory/Graphiti 脚注、sourcing 无 event 片段、三个示例出口(no-key-demo/minimal/demo)的选择指引。

## Later(明确不在本轮;想法只进不丢)

- Python 移植与跨语言一致性
- REST server、多租户、pgvector / Postgres 后端
- 托管 SaaS、Web 管理界面、多模态证据、CRDT 同步
- 大规模新增适配器(本轮至多新增一个作为契约套件试金石)
- 把参考宿主做成产品 / 扩成桌面产品路线
- 拆分开源 / 闭源功能分层
- **加 `event` ContentType**(第 9 个类型:确定发生但短暂的一次性事件,如"今天没吃早饭/删了记录";不封顶置信、给中等衰减/过期窗、credStatus 走正常档)——填 fact(确定+永久)与 state(临时+封顶)之间的缺口(见 D-0019)。**待真实产品需求驱动**(如宿主要事件时间线),届时走 D-xxxx + api-freeze + 语料 + 提示词 bump + §15.2 重跑;不为 eval 的化妆品缺口提前做(铁律 4)。(来源:2026-07-13 度量清理② D-0019)
- IDEA-xxx <一句话>(来源 Phase/日期)

## Non-goals(纪律)

不把参考宿主变成产品;不把本仓库扩成桌面产品路线图;不为便利削弱认知纪律;不拆分开闭源分层。
