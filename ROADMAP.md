# ROADMAP

MemoWeft 是 **library-first** 的可移植 AI 长期记忆库。公共 API 稳定分层与破坏性变更策略见 [`docs/memory-surface-contract.md`](./docs/memory-surface-contract.md)。具体推进见 `PROJECT_PLAN.md` 与 `CURRENT.md`。

## Now(本轮升级,对应 PROJECT_PLAN.md Phase 1–6)

- 召回更准(Phase 1)· 固化更可信(Phase 2,真实模型质量线)· 适配器更稳(Phase 3)· demo 更锋利(Phase 4)· 文档更不绕(Phase 5)· 公开基准(Phase 6,常态化)

## Next(本轮之后优先考虑)

- 更多适配器(OpenAI Agents / LangChain / LlamaIndex),待 adapter-kit 被证明后批量做
- Reranker 实装(Phase 1 若时间紧,`Reranker` 接口先留 no-op,实装下放此处)
- **keyword / hybrid 召回重评估**(building blocks 已建:`KeywordRetriever` FTS5/BM25 + `HybridRetriever` RRF,未接 API,见 D-0008):当前黄金集上 hybrid 零增益、召回提升全来自真实 embedder;待**大语料 / 稀有精确词 / 错拼 / OOV / 代码标识符**这类 keyword 有利的 workload 出现时,以对应黄金集重评估是否接入
- 纯 TS BM25 降级(D-0007,FtsUnavailableError 探测点已留,待无 FTS5 环境出现)
- 召回质量 v2:相似度阈值、purpose/content 过滤、召回解释、负反馈
- 保持 Core / Host / Plugin 权限边界的新插件
- **真实 LLM 录制回放层**(D-0010 的 caveat):当出现"冻结某模型版本的行为做跨模型对拖"的需求时新建**独立**录制层,不要改写现有 48 处意图清晰的内联 fake
- **`PROJECT_PLAN.md §20` 环境变量表订正**:现列 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `EMBEDDER` / `SKIP_LIVE_LLM` 等旧抽象,与实际的 OpenAI 兼容单端点 `MEMOWEFT_*`(+ `DLA_*` 回退)不符;`SKIP_LIVE_LLM` 已按 D-0011 删除
- **`.gitattributes` 全仓 eol 归一**:现只钉了两个机读快照为 LF。仓库无全局规则 + 开发机 `core.autocrlf=true` + `.prettierrc` 定 `endOfLine: lf` → `npm run format` 在 Windows 本地报 200+ 文件"style issue"(CI 跑 Linux 故不暴露)。修法是加 `* text=auto eol=lf`,但会产生一次全仓无关 diff,宜单独一个 commit
- **§15.5 多模型分差矩阵**(Phase 2 强化项,未做):固化在 2–3 个模型上各跑 §15.2,度量对 mimo 的依赖度
- `bench/eval-retrieval.mjs` 在 `--out` 模式下,报告里的「生成命令」行仍印硬编码的 `node bench/eval-retrieval.mjs`(小瑕疵,改它要动报告逻辑)
- **可注入时钟方案 C 尾巴**(2026-07-11 Phase 4,D-0015):**经 `createMemoWeftCore` 门面的所有时间源已全部可注入**(store 落库 + consolidate/attribute + recall 衰减 + managementApi/managementLog + graph/portable)。**只剩两处【非门面路径】的 `new Date()` 未接 clock**:`asking`(proposeAsk/revisitConflicts 的 askedAt)、`obs/runLog`(ts)—— 它们是散装 dev 算子 / 可选诊断,不被工厂调用,直接调用方目前也没注入需求。要彻底"全仓零散落 new Date()"再补:各自 deps/options 加 clock、调用方透传即可(同 S2 手法)。
- **来源感知固化**(2026-07-11 AD-3 scout 诊断,出 AD-3 范围):distill(distill.ts:56)与 consolidate(consolidate.ts:146)喂 LLM 时**丢弃 sourceKind**,只给 rawContent。后果:observed / tool 等**非用户亲口**的证据可能被误固化成 `formed_by:'stated'`(当成用户亲口说的)。这是既有特性(observed 现在就这样),AD-3 加 tool 后尤其易踩(工具返回值就是客观事实)。治法:在 distill/consolidate 的 utterance 视图带上来源标注,让固化知道哪些不是用户原话。**碰纪律敏感写路径,改后须重跑固化评测验回归**(§15.3 流程)。
- **固化评测的两处度量退化清理**(2026-07-10 scout 诊断,均非模型质量问题):① conflict 的 gistRecall 恒为 0——判官只看落库认知文本,看不见"打 conflicted 标"这条处理路径;可让 conflict 类场景的 gist 度量改看 `conflicted` 计数/状态,而非要求落新认知。② no-over-inference 的 5 分缺口是 fact-vs-state 类型口径分歧(模型标 fact、语料期望 state,两边都成立);需先定"一次性完成事件/情绪残留"的规范类型,再决定改语料期望还是加一条提示词规则。两项都廉价、都属"让数字好看"而非"修真缺陷",非紧急。

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
