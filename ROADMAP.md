# ROADMAP

MemoWeft 是 **library-first** 的可移植 AI 长期记忆库。公共 API 稳定分层与破坏性变更策略见 [`docs/memory-surface-contract.md`](./docs/memory-surface-contract.md)。具体推进见 `PROJECT_PLAN.md` 与 `CURRENT.md`。

## Now(本轮升级,对应 PROJECT_PLAN.md Phase 1–6)

- 召回更准(Phase 1)· 固化更可信(Phase 2,真实模型质量线)· 适配器更稳(Phase 3)· demo 更锋利(Phase 4)· 文档更不绕(Phase 5)· 公开基准(Phase 6,常态化)

## Next(本轮之后优先考虑)

- 更多适配器(OpenAI Agents / LangChain / LlamaIndex),待 adapter-kit 被证明后批量做
- Reranker 实装(Phase 1 若时间紧,`Reranker` 接口先留 no-op,实装下放此处)
- ~~**keyword / hybrid 召回重评估**(见 D-0008)~~ → **已重评估(D-0017)**:Phase 6 §19.2 LoCoMo 大语料矩阵证实——真实 embedder 下 hybrid 仍零增益(`hybrid`/`mode` **仍不接 API**),但无 embedder 时 keyword 55.3% ≫ 空,故**无-embedder 兜底 NullRetriever→KeywordRetriever**。剩:keyword 有利的**稀有精确词/错拼/OOV/代码标识符** workload 仍可专门黄金集再评 hybrid 是否值得接入(暂无此需求)
- 纯 TS BM25 降级(D-0007,FtsUnavailableError 探测点已留,待无 FTS5 环境出现)
- 召回质量 v2:相似度阈值、purpose/content 过滤、召回解释、负反馈
- 保持 Core / Host / Plugin 权限边界的新插件
- **真实 LLM 录制回放层**(D-0010 的 caveat):当出现"冻结某模型版本的行为做跨模型对拖"的需求时新建**独立**录制层,不要改写现有 48 处意图清晰的内联 fake
- ~~**`PROJECT_PLAN.md §20` 环境变量表订正**~~ ✅ 已订正(2026-07-12):换成实际 `MEMOWEFT_*`(+ `DLA_*` 回退)全表,含 WRITE_LLM/EMBED/EXPERIENCE_UI/LANG/JUDGE/基准 PATH;删旧抽象与 `SKIP_LIVE_LLM`
- ~~**`.gitattributes` 全仓 eol 归一**~~ ✅ 已归一(2026-07-12):加 `* text=auto eol=lf` + 常见二进制 `-text`。**实测索引本已全 LF**(`git ls-files --eol` 全 i/lf),故 `git add --renormalize .` 零内容 diff——旧「200+ 无关 diff」的担心不成立,只改了 `.gitattributes` 一个文件。工作树已检出的 CRLF 就地归一需 `git add --renormalize . && git checkout .`(可选)
- **§15.5 多模型分差矩阵**(Phase 2 强化项,未做):固化在 2–3 个模型上各跑 §15.2,度量对 mimo 的依赖度
- ~~`bench/eval-retrieval.mjs` 报告「生成命令」行硬编码~~ ✅ 已修(2026-07-12):`INVOKED_CMD` 反映实际调用(`EVAL_REAL_ARM` 前缀 + `--ablation`/`--out`/`--real` 等 flag)
- **可注入时钟方案 C 尾巴**(2026-07-11 Phase 4,D-0015):**经 `createMemoWeftCore` 门面的所有时间源已全部可注入**(store 落库 + consolidate/attribute + recall 衰减 + managementApi/managementLog + graph/portable)。**只剩两处【非门面路径】的 `new Date()` 未接 clock**:`asking`(proposeAsk/revisitConflicts 的 askedAt)、`obs/runLog`(ts)—— 它们是散装 dev 算子 / 可选诊断,不被工厂调用,直接调用方目前也没注入需求。要彻底"全仓零散落 new Date()"再补:各自 deps/options 加 clock、调用方透传即可(同 S2 手法)。
- ~~**来源感知固化**~~ ✅ 已修(2026-07-13 · **D-0018**):distill/consolidate 的 utterance 视图带来源标注(`src/evidence/sourceLabel.ts`),两提示词(distill v2 / consolidate v3)据来源定 formedBy——observed/tool 不再被误固化成 `stated`。加固来源强度纪律,不改 API/schema/eval 断言。§15.3 前后对比:结构 95.1%→94.2%(判为单跑方差,D-0018 目标区 conflict 盘纹丝不动、overInfer 全 0)。
- **固化评测的两处度量退化清理**(2026-07-10 scout 诊断,均非模型质量问题):① conflict 的 gistRecall 恒为 0——判官只看落库认知文本,看不见"打 conflicted 标"这条处理路径;可让 conflict 类场景的 gist 度量改看 `conflicted` 计数/状态,而非要求落新认知。② no-over-inference 的 5 分缺口是 fact-vs-state 类型口径分歧(模型标 fact、语料期望 state,两边都成立);需先定"一次性完成事件/情绪残留"的规范类型,再决定改语料期望还是加一条提示词规则。两项都廉价、都属"让数字好看"而非"修真缺陷",非紧急。
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
- IDEA-xxx <一句话>(来源 Phase/日期)

## Non-goals(纪律)

不把参考宿主变成产品;不把本仓库扩成桌面产品路线图;不为便利削弱认知纪律;不拆分开闭源分层。
