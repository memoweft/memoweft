# CURRENT — 当前状态(Integrator 每个工作段落结束更新)

## 0.5.1 发布收口（2026-07-15）

- 发布候选严格基于已提交的 `4e4a96a`，只叠加 Changelog、发布流程、lint 门禁修复和当前状态文档；原 `main` 工作区里的 D-0033 未提交改动不进入 0.5.1。
- `package.json`、`package-lock.json` 与 `src/version.ts` 已统一为 `0.5.1`；npm 尚无该版本，仓库也尚无 `v0.5.1` tag。
- 标签发布收口为仅发布根包 `memoweft`，并等待全部 CI 门禁通过；公开 adapter / MCP workspace 不随根包 tag 重复发布。
- 下一步：完整本地门禁、pack 内容核对、独立安装验证；确认 GitHub `NPM_TOKEN` 后再推 `v0.5.1`，发布后回到 WeftMate 精确固定公开版本。

更新于:2026-07-16 | **本窗:v0.6 交互语义模型升级 Phase 0(影响面报告·人类批)+ Phase 1(Context 基础设施)+ Phase 2(Semantic Resolution·提示词 v3→v5·§15.3 全量前后对拍:结构 90.5%→97.1%、short-reply 36/51→51/51、旧 6 盘零退化)——均已落并推·D-0034**。**头号发现(亲验)**:真实产品 weftmate 全程走裸 `ingestUserMessage`、从不经 Conversation(`memoweft.ts` 8 符号无 handleConversationTurn·HEAD+工作树 grep 皆空)→ D-0033 的 preceding/confirmed 在真实产品**从未生效**(DECISIONS.md:487 旧断言有误,已在 D-0034 订正)。**四决策(人类拍板)**:①可改 core ②formedBy 全面接管(Phase 3) ③resolver 顺手做 ④v0.6 吸收 D-0033 Phase 2、event 型另排;次要⑤两表进便携包 ⑥Episode 宿主可选传+idle 兜底 ⑦分期 Phase 0–5。**Phase 1 已落**:两张新表(`interaction_context`[+规范没列的 `subject_id`] / `semantic_resolution`·照 management_log 模板·不进 formal migrations·`LATEST_SCHEMA_VERSION` 仍 v1·收敛测试兜住)+ 两 store;**core 承担上下文管理**修头号问题——`InteractionSession`(working memory + episode idle 切分,缺省 30 分)+ `ingestUserMessage` 加 `conversationId?/episodeId?`(带 convId 时抓上一轮 AI 填 `preceding_ai_context`【复用 D-0033 结构墙列】→ distill/consolidate 注入一字不改即对裸 ingest 路生效·**零改 Cognition 逻辑**)+ 落 interaction_context;新门面 `recordAssistantReply`(AI 回复只进窗口、永不落证据·3a);便携包 v2(`BUNDLE_SCHEMA_VERSION` 1→2·两 data 字段可选·向后兼容 v1)。**测试**:`interaction.test` 8 + `interactionCapture.test` 7(端到端证头号问题修复 + 结构墙无泄漏 + episode 切分 + 便携包往返)+ portable/migrations 收敛保绿。gate:**npm test 340/340**(api:update 后 341 全绿)· typecheck 干净 · api:check 一致 · 契约 en/zh(方法 25→26)+ CHANGELOG + D-0034。**范围管理(诚实)**:Phase 1 聚焦 ingest 路;**conversation 路(handleConversationTurn)的 interaction_context 落库延 Phase 2**(memoweft-host 的 D-0033 preceding 已工作·不影响现有·零回归)·`ConversationInput.episodeId` 字段已加。**注**:api 快照 `ChatMessage` role 联合顺序因新增 `VisibleTurn` 被 TS typeToString 重排(成员集不变·语义等价·非破坏)。**git 卫生(已了结)**:上窗记的「D-0033 Phase 1b 从未 commit、与 v0.6 交织在同一批文件」已解决——两批一并入库于 `ccde451`,工作树自此干净。

**Phase 2 已落(本窗·D-0034·分支 `v0.6-phase1-interaction`,6 commit 全推)**:**① resolver produce+store**(`a6cb2bc`):consolidate 对每条【用户真说的】证据落一份 `semantic_resolution`;**3a/3d 由构造保证**(复用既有 `validEvidence` 白名单 → 伪造 / AI-上文 id 的解析结构性丢弃;resolved_content 是解释、不铸 evidence id、永不进 support)+ 新增 **`spokenEvidence` 来源收窄**(observed/tool 不落解析——提示词也教了,但**结构保证优先于提示词自觉**)+ 幂等 + 枚举收敛 null;**只 produce+store、不碰 formedBy**(Phase 3)。**② 短回答语料家族**(`7e1ee8c`):short-reply 盘 7 条(CC-043~049·4zh+3en·语料 42→49),schema 加 `precedingAiContext?` / `expect.newCognitions.formedBy?` / `expect.resolutions.responseAct?` + CORP-19/20/21;**顺带修既有 bug**——corpus.test 的 `FORMED_BY` 镜像缺 'confirmed'(D-0033 Phase 1a 漏更新)。**双向设防**(CC-043/044/047/048 要 confirmed、CC-046/049 要 stated)→ 两种退化模型各红一半、无单一策略通吃。**③ 提示词 v3→v5**(`ca0171e`+`bb50241`):教读懂 ⟨AI 前一句⟩ / 附和→confirmed(含**否认→stated**、**含糊→优先不产**、**select→confirmed**[新拍板]、**与闲聊守卫的 carve-out**)/ 窄范围 / 产 resolutions;**四视角对抗审查 4 个 major 全修**(否认失语 / 守卫与附和在「嗯」上冲突 / 含糊无路 / resolutions 零 eval 覆盖——前两条两视角独立收敛);**铁律 3 已验**(唯三 `-` 行 = version bump + 两行 JSON 闭合符,纪律措辞逐字未动)。**④ §15.3 全量前后对拍**(`3216c8d`·只动提示词一个自变量·before = v3 + **新语料+新口径**,刻意不拿旧 42 条基线比):**结构 90.5%→97.1%(248→266/274)· 全绿 31→41 · overInfer 全程 0.00 · short-reply 36/51→51/51(+29.4pp)· 旧 6 盘零退化**(**chitchat-negative 保持 35/35** = 审查点名的最大回归风险被证伪:carve-out 以「带后缀」为钥、旧 42 条无一条带 precedingAiContext → 结构性够不到)。**基线更新为 v5+49 语料+新口径**,旧 42 条 v3 基线作废。**诚实**:旧盘 +3 条结构可能只是模型抖动(n=7/盘 单跑);gistRecall 涨幅是软判、仅供趋势(D-0009);no-over-inference 28/34 缺口仍是 D-0019 的定义灰区。**头号发现**:v3 在 short-reply 的 gistRecall 已达 **0.90** → mimo 本来就读得懂「是啊」,Phase 2 补的是**溯源纪律与结构化产出、不是理解力**。**新拍板(人类)**:select→confirmed——冲烟暴露已批派生表(impact-report:88)**只议定过 affirm/negate、select 是灰区**;判据=「这条信息的载体是谁的话」而非「AI 有没有预设答案」。gate:**npm test 354/354** · typecheck 干净 · api:check 一致 · corpus 21/21 · eval selftest 全绿。

**待续**:Phase 3(deriveFormedBy 代码接管 + 删 consolidate `formed_by` 指令 + 全量 eval 重跑贴前后分)→ Phase 4 dogfood → Phase 5 发布。**Phase 3 输入已就位**:semantic_resolution 表现在有真实模型产出的数据(49 场景全绿),且 eval 已能判解析质量(覆盖 + responseAct 允许集),不再是盲区。 | 更新于:2026-07-14 | **本窗末:dogfood 深挖 → 「附和/AI 上下文」机制立项(D-0033·分期实现·Phase 1a 已落)+ batchSize 调参(D-0032)**。**dogfood 对话链**(拼豆 → 香菜 → 早饭 → 爬山)逼出一个真产品缺口:weftmate 是爱主动问的伴侣,"AI:你喜欢爬山吧? 用户:是的" 这类**孤儿回应的信息只在 AI 那句里**、用户自己的话啥也没带 → 当前 distill 只看用户话 → **存不了**;而这又是 3a 要防的自我印证最危险面(AI 编 X、一句"是的"洗成事实)。**议定机制(D-0033)**:AI 那句只当**只读上下文**注入 distill/consolidate(捕获自 handleConversationTurn 的 working memory·先存后答时上一轮 AI 话还在)、存为**不可溯源的 `preceding_ai_context` 列**、**永不给证据 id**(3d 白名单结构性挡死→3a 守住);用户附和产 **`confirmed` 来源**(底分 280、自然封顶 480<limited·纯附和顶天低置信、只有主动说才升级破顶)、**不进 trends 聚合**(防诱导灌成 ruled)、窄范围(短原子命题才产、长文档+含糊"好"不产)。三决定人类批:导出剥离 AI 文本 + listEvidence 不当证据显示 / 结构墙为真保障(对抗测试证提示词判漏也拦得住)/ stated→confirmed 升级。**可行性经 scout 对抗验证 CONFIRMED**(先存后答捕获时机对、够着 weftmate 真聊天路 server.ts:366)。**Phase 1a 已落并推(`47bbc96`)**:FormedBy 加 confirmed + config baseByFormedBy confirmed:280(api:update·baseByFormedBy 内联类型 diff、FormedBy 联合不透明未 diff)+ VALID_FORMED + trends 排除 + 封顶回归测试;结构层 only·inert(产出路径待接)。gate:**npm test 310/310** · typecheck 干净 · api:check 一致 · 契约 en/zh + CHANGELOG。**待续(下窗)**:**Phase 1b**(证据列 `preceding_ai_context` + v2 迁移 + perceive/conversation 捕获 plumbing + reinforce 的 stated→confirmed 升级逻辑 + exportBundle 剥离 + listEvidence 处理 + **诱导风暴对抗测试**——语义敏感、**派重对抗审查工作流**)→ **Phase 2**(distill+consolidate 提示词 bump 教认附和/窄范围/拆分/只溯源用户 + **§15.3 全量 eval 重跑贴前后分**)。scout 已备逐条 file:line 改动清单(见 wf 输出)。**另:D-0032 batchSize 5→12**(`4a575d5`·dogfood 调参·整理次数↓省 token + distill 前后文更足;记忆气泡"扎堆+迟到"UX = weftmate 侧待办·缓议·不进 memoweft)。 | **本窗后半:dogfood 复现 + weftmate 换内核 + 下一轮两阶段规划(D-0031,人类拍板)**。**① dogfood 拼豆样本**:人类拿最初版 memoweft 产的一条认知(`特点/trait·还没确认`「用户注重基于观察的准确评价…」·据"你都没看到你就说看起来挺精致的")问准不准 → Integrator 判半准(行为层准、拔成 stated 价值观是过度概括、类型偏重);人类给全对话(那句是用户抬杠助手"没看到就夸精致"的一次 meta 反应)→ 改判过度推断。**只读复现当前 HEAD**(`repro-pintou.ts`·喂 3 条用户原话→updateProfile→列认知·独立 db·跑完删):**当前版大幅缓解**——干净偏好"不喜欢拼豆"稳定被抓(3/3·preference/stated/600);meta 抬杠 en 不生成、zh 1/2 生成但已降级为 `trait/inferred/candidate/conf=200`(D-0018 来源感知固化在起作用),残留是**缺事件型(D-0019)的低危活体样本**。诚实:n=3、非"已修"。**② weftmate 换内核**:weftmate(`../weftmate`·独立 git 仓·电子桌面伴侣产品)的 memoweft 依赖从 npm `0.5.0` 换成**本地在进行中的仓**(`file:../memoweft` 软链→node_modules/memoweft→本地 0.5.1·先 `npm run build` 重建 dist)。守 weftmate 红线(不碰库源码·门面层 `src/memoweft.ts` 8 符号全兼容);验:typecheck 0 · 运行期 import 得 0.5.1 · 全测 46/46。**未提交**(weftmate 树有其阶段 2 进行中改动、非本次)。**③ 下一轮两阶段规划(D-0031)**:原 Later 7–11 提上日程,但 9/10(REST/多租户/后端·SaaS/Web 管理/多模态/同步)与"库不是应用"宪章冲突 → 人类拍板**守库身份、9/10 归 weftmate、memoweft 只留接缝**(官网试用 demo=到时候项·真体验需迷你后端归 weftmate/演示版纯前端 memoweft 可承)。ROADMAP 加「下一轮」节:**阶段1 建设**(1.1 事件类型打头[dogfood 驱动 D-0019] / 1.2 适配器规模化 / 1.3 Python 移植 / 为 9/10 留接口的接缝审计)→ **阶段2 打磨**(人类真用 weftmate 一段 → dogfood 驱动优化 memoweft·是所有 deferred 项的触发收割场·铁律4 成片才修)。CLAUDE/AGENTS/Non-goals 不改。**④ 顺带仓库清理**(为下一阶段干净起步)。 | **本窗推进 ROADMAP Next ③「召回质量 v2 尾项」两半 → 均 DEFER(D-0030,人类拍板)**:派只读 scout 三维度(purpose 影响面 / 命中词影响面 / 适配器-先例)+ 对每维承重判断派怀疑者对抗验证(**21 agent、0 error、~1.17M token**;发现几处 overstatement 已订正、不改结论),三 scout 独立结论一致 → 两半现无真实消费者/触发,按铁律 4 均 defer(同 D-0026 Reranker NO-GO / 相似度阈值收口 / D-0019 手法)。**半 A·purpose 过滤**:认知层无 purpose 字段(`LLMPurpose='chat'|'write'` 无关);`scope` 是 src/ 内**死字段**(consolidate/trends/attribute 三写入方全不填 → 恒 null、无 populator、唯 testbench 调试端点能手写);参考宿主只取 `{content,score}`、连已上线 `contentTypes` 都没调过;**contentType 已覆盖 D-0022 举的用例**("只召回 fact/preference 做档案摘要")→ purpose 若≠contentType(已能筛)也≠scope(死),便是**宿主任务意图=query 侧属性、不属认知层**;四选项 A 新立维度(造无值分类法+populator+6 适配器,REJECT)/B 映射 scope(需先做实 scope)/C 已被 contentType 覆盖/D 不做 → **采纳 D**。**半 B·命中词**:信息在 `RetrievalHit={id,score}` 检索层丢、须**贯穿导出的 Retriever 契约**(无 D-0021 那种门面捷径——provenance 便宜因门面已有 sourcesOf);**VectorRetriever 无离散 term**(整 query 嵌一个稠密向量算余弦)→ **默认生产 bge-m3 向量路径恒空**,只在无-embedder KeywordRetriever 兜底才亮;不能干净 explain-gate(terms 源自门面之下);宿主 recall 是「future 记忆气泡(步6)」占位、无消费者;D-0021/ROADMAP 均标「留后按需」→ **采纳 D**;唯一干净形态已侦定备用:`RetrievalHit.terms?: string[]`(可选、additive、触 api-snapshot 一行、对注入/Null/Vector retriever 结构安全)→ 贯穿 recall.ts→门面→`RecalledCognition.matchedTerms?`,KeywordRetriever 走 FTS5 highlight 填、两适配器经 onRecall/mcp 透传,重启=宿主 explain UI 或命中词 eval 出现。**影响面:无**(纯只读侦察 + 记档,不改 src/api/schema/eval/提示词);顺手校正 ROADMAP 陈旧行(contentType 透传"现仅门面"→ 实已 D-0024/25/27/28/29 端到端做完 6 适配器)。gate:纯文档改,不触代码。 | **本窗「按顺序来」推进 ROADMAP Next 队列**(①更多适配器 → ③purpose/命中词半;②录制回放 & ④Reranker-dogfood 触发门控·无触发不做):**① 批次第一个 = OpenAI Agents 适配器已做待推(D-0027)**——新包 `@memoweft/adapter-openai-agents`(run-wrapper 型:`callModelInputFilter` 注入 instructions + 扫 `RunResult.newItems` 摄 `tool_call_output_item`;3a 比 claude 更纯——tool_call_output_item 是独立 item 类型)接 adapter-kit AD-1…9(11/11)+ 示例 + 双语 README(含 MCP 挂载备选)+ ci.yml 3 步。守门:typecheck/build/api:check 全绿 · docs 绿 · **对抗审查 4 维度×怀疑者、9 确认(全 minor/nit·0 blocker)全修**(part-array 空白 trim、persistToolOutputs 返回值 JSDoc 订正、混用接线重复注入防呆标记、D-0027 记档 hooks→persistToolOutputs、**补 D-0024 隐私铁律「provenance 不进 instructions」的契约断言**[真缺口]、AD-1 注释精度、README drop-in 措辞;#5 AD-4 结构断言判 systemic——全 3 文本块适配器同款·不动冻结 kit)。三家 SDK 侦察均 feasible(LangChain=retriever+callback、LlamaIndex=memory-block+stream-tap)。**② LangChain 适配器已做待推(D-0028)**:新包 `@memoweft/adapter-langchain`(retriever+callback 型——召回走 `MemoWeftRetriever`[callbacks 观察-only 不能注入]、写走 `MemoWeftWriteCallback` 只 handleToolEnd[**不声明 handleToolStart=3a 物理隔离**]、用户原话走 persistUserTurn 宿主闭包;**provenance 不进 Document[pageContent/metadata]、只经 onRecall**)接 adapter-kit 11/11 + 示例 + 双语 README + ci.yml 3 步。**(构建首撞会话用量上限,过 04:20 上海重置后续跑成功。)** 对抗审查 8 确认去重 3 真项全修(toolOutputText 改真 BaseMessage[`_getType`]判据防直调工具静默丢数据、retriever onRecall 独立兜底不毁已成功召回、AD-1 3a 守卫补 4 个 1.x 承载助手输出的 hook[含 handleChatModelStreamEvent])。守门:typecheck/build/api:check 全绿 · docs 绿。**③(末)LlamaIndex 适配器已做待推(D-0029)**:新包 `@memoweft/adapter-llamaindex`(memory-block + stream-tap 型——召回走 `MemoWeftMemoryBlock`[extends BaseMemoryBlock,`get()` 注入 role:'memory' 消息、`put()` 空实现防把助手回话/已注入上下文当证据存脏]、写走 `persistFromAgentStream` 透传 async generator[只摄 `agentToolCallResultEvent`、调用意图物理不入=3a、re-yield 不受摄入影响];provenance 不进 block 输出、只经 onRecall)接 adapter-kit 11/11 + 8 项 smoke + 示例 + 双语 README。**上游弃维取舍(人类拍板「发布·记弃维」)**:`@llamaindex/core`/`@llamaindex/workflow` 最新版被 npm 标弃维,**重定向到维护中的伞包 `llamaindex@^0.12`**——直接依赖弃维 `@llamaindex/core` 已去掉(伞包 re-export),残留 `@llamaindex/workflow`(现代事件驱动 agent API 唯一出处、伞包自身也依赖),记档 D-0029 + README/CHANGELOG 说明。守门:**Integrator 亲读 memoryBlock/streamTap 两新文件(干净)替代重审查——省 ~1M token**(4 个近亲适配器,前 3 个已全 4 维度审透,末个新面自读守门)。**「更多适配器」批次 ① 收工**:OpenAI Agents(cbd97ed)+ LangChain(40904de)+ LlamaIndex 三个新适配器全落地。接下来按序进队列 **③ 召回质量 v2 尾项(purpose 过滤/命中词半)**。 | **本窗「都做」四 tranche 并行推进**(人类选四方向全推;Integrator 拆解 + 守门 + 逐 tranche 收口):**Tranche 1(Phase 5 文档降级·全部)已做并推**——`README.zh-CN` + `reference-host.zh-CN` 中文版已建 + internals `numbering-map.md` 编号对照表(Phase/D/AD/S 四表零编造)+ contract en/zh 可点击 TOC;**守门抓出并撤销 `demo-script.zh-CN`**(源本就整页中文、中译中冗余,工作流+复审均漏、Integrator 逐页核出);docs:links deadPath=0 · docs:snippets 28/28 绿。**收尾(人类批「全做」)**:concepts 六页重排(read-write→sourcing→no-self-evidence→confidence→correct-conflict→decay,前向 Next 链闭合;实测本无链、ROADMAP 高估 churn)+ perf recall 真实基准(bge-m3 P50 36.8/59.8ms,拆解 query 嵌入 ~33ms + O(N) 扫描;订正误导 0ms 行 + 新增真实召回节 + `bench/perf-recall.mjs`)+ internal↔internals 走**轻触消歧横幅**(不改目录,两 README 各加一句·Integrator 推荐轻触换高 churn 改名)。**Tranche 2(召回 v2 适配器透传)已做并推(`9c15001`)**:两适配器透传 explain/contentTypes/provenance/contentType + MCP 新增 mute tool(WRITE 2→3)+ provenance 按 tier 预筛(岔口②);adapter-kit 加 AD-7/8/9;记 **D-0024**。守门:adapter-ai-sdk 33/33 · mcp-server 17/17 · api:check 一致 · **对抗审查 4 维度×怀疑者 0 代码 bug**(唯一确认项=CHANGELOG/契约/recipes 收口,含 recipes 7→8,已随提交补齐)。**Tranche 3 Reranker 收口 = NO-GO(D-0026,人类拍板)**:α 合成判别集证"能显差异"上界(fusion +0.55、不触 api-freeze),但 β 真实序验证(真 bge-m3 × golden.json 65 用例)显**真实序近最优**(nDCG@5=0.9112、53/65 零缺陷)、**fusion 端到端净负**(−0.043,帮 2 害 12)——固定逐认知先验只稀释逐 query 语义序。同 D-0008 证伪 hybrid,铁律 4 不实装;bench(`bench/rerank-*`)入仓作背书;重启条件=dogfood 带真实元数据语料暴露真实缺陷。**Tranche 4 Claude Agent SDK 适配器**:人类批 **hybrid**(hooks 适配器新包 + MCP 挂载文档),记 **D-0025**;SDK 真实 hook API **实测确认**(`@anthropic-ai/claude-agent-sdk@0.3.207`;`UserPromptSubmit` 返回 `additionalContext` 注入召回 + 读 `input.prompt` 拿纯净原话[注入不碰 prompt·by-construction 干净·**方案①成立推翻 60%**]、`PostToolUse` 只读 `tool_response` 不碰 `tool_input`[铁律 3a 代码级])。**已实现待推**:新包 `@memoweft/adapter-claude-agent-sdk`(`createMemoWeftAgentHooks` → UserPromptSubmit 注入召回+存原话、PostToolUse 存 tool_response、复用 §16.2 降级)接 adapter-kit **AD-1…9(11/11)**+ 示例 + 双语 README(含 MCP 挂载备选)+ ci.yml guardrails 3 步 + CHANGELOG + numbering-map。守门:typecheck/build 干净 · api:check 一致 · docs 绿 · **对抗审查 4 维度×怀疑者、6 确认(全 minor/nit·0 blocker)全修**(onRecall/拼块纳入降级 guard 防观测回调 reject hook、超时不重试防 null-originId 重复落库、AD-1 空断言→守 hook 结构不变量、AD-6 隔离 recall 降级、README 工具数 1→3)。§16.3 版本矩阵按铁律 4 暂缓(SDK 0.x 窄 `^0.3` 范围 + 自带 native 二进制装包重,待 SDK 稳)。**遗留(另修)**:对抗审查发现 adapter-ai-sdk `recallMiddleware.ts` 有同款 onRecall 逃逸 latent bug(A 既有,非本轮引入),另开小修。 | **D-0023 召回负反馈=Mute 已做并推 —— 召回质量 v2 四件全落**(本会话·人类拍板 Mute 语义 + 批准·动 schema + api 完整流程 + 4 视角对抗审查):`core.memory.muteCognition({cognitionId, muted, reason})` → 认知加 `mutedAt` 状态位,召回跳过它**但仍 active、仍参与 consolidation/画像演化**(阶梯 mute⊂archive⊂invalidate;铁律 3b:与 confidence 正交,muteCognition 不碰置信度,测试实证)。schema 加 `muted_at` 列 + store 五处映射;**迁移改走 store.migrate 缺列补(非提案的 formal v2)**——实现暴露 formal v2 漏直接构造老库 + 与假-v2 测试撞号,store.migrate 对四条构造路都稳、零破坏(理由记 D-0023,正是提案标的最大风险点 + 给的"老土办法"选项)。管理 API `muteCognition` + `MuteCognitionInput`;便携包自动继承。**4 视角对抗审查(17 agent)确认修 2 条**:①[minor]muted 曾永久占 top-K 检索槽→饿死同话题召回(不像 archive 自愈)→ **加固:从召回索引排除**(updateProfile `active().filter(!mutedAt)` 后 indexAll,active 不动、consolidation 仍见)+ recall 门控留作重建前窗口守门 + 索引排除测试;②[nit]op 注释补 mute/unmute。api:update(4×Cognition+mutedAt、muteCognition、MuteCognitionInput)+ 契约 en/zh + CHANGELOG。gate:**npm test 309/309** · typecheck 干净 · api:check 一致 · docs:links 0 · 迁移测试(0.1.0 冻结库 + schema 收敛)全绿。 | **D-0022 召回按 contentType 过滤已做并推**(本会话·人类批准·铁律 2 完整流程 + 对抗审查):`core.recall({ contentTypes:['fact','preference'] })` 按类型允许名单筛(不传=全类型、行为不变);每条召回结果暴露 `contentType`(宿主能看类型)。门面过滤(`items.filter(it=>allow.has(it.contentType))`),**recallCognitions 只加填字段、不加过滤参**;后过滤(top-K 之后)可能欠填(文档标注,同 similarity/衰减门控层)。api 三处 additive(RecallInput+contentTypes?、RecalledCognitionItem+contentType 必填、RecalledCognition+contentType?)+ 契约 en/zh §18/§23 + CHANGELOG + recallExplain.test D-0022 例。**对抗审查(3 视角×怀疑者)0 confirmed**(contentType 填值安全、过滤/顺序正确、必填字段下游全读不构造、conversation 路径不受影响、隐私/3b clear)。诚实取舍:这是"控制"非"变聪明";"purpose"认知层无此字段留后;MCP/ai-sdk 适配器暂不透传 contentType(仅门面,进 Later)。gate:**npm test 307/307** · typecheck 干净 · api:check 一致 · docs:links 0。 | **D-0021 召回解释(证据链)已做并推**(本会话·人类批准·铁律 2 完整流程 + 对抗审查):`core.recall({ explain:true })` → 每条召回认知带 `provenance?: RecalledEvidence[]`(支撑/反证证据链,`{evidenceId, relation, summary, sourceKind, allowCloudRead, allowInference}`),正中"可追溯记忆"卖点。门面富化(`core.recall` 已有两 store),**底层 `recallCognitions`/`RecallDeps` 不动**;`explain` 缺省关 = 零额外查询、行为不变。**对抗审查(3 视角×怀疑者)确认并修 1 条真隐私缺口**:初版 `RecalledEvidence` 未带授权位 → 宿主拿证据原文却无从判断哪条不可上云、违反库自设的 buildMemoryGraph 惯例 → 补 `allowCloudRead/allowInference`,宿主转发云模型前可自筛(收窄了我"不弱化云读隐私"的过满断言)。范围只做证据链,"命中词"半留后。api:update(RecallInput+explain?、RecalledCognition+provenance?、新 RecalledEvidence)+ 契约 en/zh §18/§23 + CHANGELOG;recallExplain.test 2 例(含授权位)。全量 gate:**npm test 306/306** · typecheck 干净 · api:check 一致 · docs:links 0。 | **召回质量 v2·相似度阈值 已收口**(本会话·测+记文档,不扩 API):用户选做 ①,先拿真实 bge-m3 黄金集(65 用例·top5)测阈值——gold 命中中位 0.771/最低 0.559,非-gold 中位 0.641,**两分布重叠、阈值是"修边"**;权衡出 **0.55 甜点**(零召回损失砍 ~9% 噪声)、≥0.6 误杀 gold。**诚实结论:收益温和 + 值 embedder-specific + 旋钮(`config.retrieval.minSimilarity`)本就存在(默认 0=关)** → 按铁律 4 不为 9% 扩公开 API,把数据背书写进 config 注释 + calibration(旋钮从"没人知设多少"变"拿来就能用")。ROADMAP「相似度阈值」据此收口。**下个召回质量真特性候选 = ③ 召回解释**(契合"可追溯"卖点)。 | **D-0020 补全可注入时钟不变式已做并推**(本会话·人类批准·铁律 2 走完整流程):D-0015 遗留两处非门面 `new Date()`(asking 的 askedAt·`proposeAsk.ts:152`/`revisitConflicts.ts:126`;runLog 的 ts·`runLog.ts:139`/`161`)接上可注入 clock——`ProposeAskDeps`/`RevisitDeps`/`RunLoggerOptions` 各加可选 `clock?: Clock`,照 S2 范式 `(deps.clock ?? systemClock)().toISOString()`。**至此全仓时间源皆可注入、无散落 `new Date()` 时间戳**。纯 additive(旧调用方零改动、缺省系统时间);api 快照三处 additive 字段(逐条核对无意外)走 `api:update`;契约 en/zh 订正 D-0015 遗留的「staged as follow-up」陈旧句;CHANGELOG 同步。clockInjection.test 加 4 例(proposeAsk/revisitConflicts/runLog 注入→时间戳=注入值·含**铁律 3b:clock 不改 confidence**·回归缺省=系统时间)。全量 gate:**npm test 304/304** · typecheck 干净 · api:check「一致」· docs:links deadPath=0。取舍记录:Integrator 建议 B(铁律 4·无消费者不扩 API),人类选 A(补全不变式·消除未来 footgun)。 | **§15.5 多模型分差矩阵已做并推**(本会话):固化评测器加 `--subject-env` 被测模型注入(judge 固定 mimo 温度 0,只动被测模型一个自变量 → 结构硬指标跨臂可比;非默认被测写 runs/、不碰基线;selftest 第 7 节 + 缺 creds 干净 BLOCKED)。跑 gpt-4o 臂全 42 场景(用户自配 `MEMOWEFT_GPT4O_*`,我不碰 key)。**结论:指标跨模型稳健**——mimo 94.2% vs gpt-4o 96.0%(+1.8pp),**3/6 盘逐检查完全相同**(chitchat/fact-vs-belief/no-over-inference)、overInfer 两模型全 0;评测器量的是认知纪律本身、非 mimo 怪癖。**no-over-inference 28/34 两模型一模一样 → 跨模型印证 D-0019**(fact-vs-state 是 ContentType 缺「事件」型的定义局限、非 mimo 缺陷)。有分差处 gpt-4o 略干净(emotion-cap +3/conflict +2)。`--compare` 时"被测模型变了"+"gist 口径变了"两告警都正确触发(①与本 plumbing 端到端验证)。落 BENCHMARKS §5 + plumbing commit `45452f8`。gpt-4o 臂产物在 bench/runs(gitignore)、只发聚合分。 | **固化评测度量清理①已落地**(本会话·纯 bench·`bench/eval-consolidation.mjs` 单文件,本地):conflict 的 gistRecall 恒 0 是**度量盲区**——judge 只看 active 认知文本,看不见「打 conflicted 标」这条「只暴露不裁决」路径(consolidate 挂 contradict 证据 + credStatus='conflicted',不落断言矛盾的新认知)。→ 对 discipline==='conflict' 场景把 shouldForm 改**确定性硬判**:命中 = `run.active.some(c=>c.credStatus==='conflicted')`(冲突已暴露 + 旧认知仍留档;比 conflicted 计数更 faithful——模型误删旧认知则正确判 miss)。shouldNot(overInferRate)仍 LLM 软判不变。**对抗审查(3 视角 reviewer × 每条发现怀疑者验证)确认并修 2 处工具自洽缺陷**:①新增 `GIST_SCORING_VERSION='v2'`(判分口径版本 → diffRuns 跨版本高声告警,防旧基线 `--compare` 把「软判恒0→硬判1」的方法学跳变无告警地误读成质量提升,已端到端复现验证)②printDiff 的 conflict 行标「[确定性]」、不再误盖「软判高方差」注。selftest 加 2b(命中)/2c(非永真)/6f(口径告警),`--selftest` 全绿 · `npm test` **300/300** · api 快照一致 · typecheck 干净 · 认知纪律四点未触(反而更 faithful 守住「暴露不裁决」)。**度量清理②已收口(D-0019·记档不改·人类拍板 C)**:实跑校准发现 no-over-inference 的 5 分缺口实为三类(4× 一次性事件 fact-vs-state + CC-029→goal + CC-032→preference),且全在 `overInferRate=0.00` 之上——真过度推断纪律达标,`created类型⊆types` 咬的是 **ContentType 缺「事件」型**的定义灰区(fact=确定+永久、state=临时+封顶,一次性事件两个格子各对一半)。判为定义噪声(同 D-0009 手法),零改源码/语料/提示词;正解「加 `event` 型」进 ROADMAP Later(伏笔,待真实产品需求驱动)。落 DECISIONS D-0019 + calibration 注 + 评测器指向注(纯文档/注释)。**①②两项度量清理均已清。** | **Phase 6 已完成并推送**(10 commit + `phase-6-done` tag 已上公开 main);现进 **ROADMAP Next**(已连做并推:D-0017 无-embedder keyword 兜底 · §20 环境变量表 · .gitattributes eol · eval-retrieval 命令行 · **D-0018 来源感知固化**)。**D-0018**:distill/consolidate 带来源标注,observed/tool 不再被误固化成 stated(加固来源强度纪律·人类批准·无 API/schema/eval 断言变化·§15.3 前后 95.1%→94.2% 判为单跑方差)。历史 Phase 状态见下。 | 所在 Phase:**5 文档更不绕(§18·第一批英文页已上线 main + 第二~七批(中文版 / internals / README / glossary+naming / 文档 CI / 新人巡检处理)已落地本地;§18 实质全完成,只差打 tag phase-5-done(人类);Phase 6 验收项全绿(两套基准各 ≥1 次完整跑:LoCoMo §19.2 矩阵 + LongMemEval_S 500题标准分 51.3%;§19.3 敏感性 + BENCHMARKS.md 就位);仅剩 phase-6-done tag(人类)。本会话·纯 bench,本地)**(Phase 3/4 全绿,已推 main,待打 `phase-3-done`/`phase-4-done` tag)

## Phase 6 起头(进行中·§19 公开基准):LoCoMo 冒烟链路通(本地)

**用户拍板开工 LoCoMo-10 冒烟(§19.1 第一步)**。侦察(2-agent:bench 骨架复用度 + LongMemEval/LoCoMo web 研究)结论:现有 eval 骨架(runner/judge/记录器/三臂工厂/config 注入 seam)大多可复用;先接 LoCoMo(小、自带、F1 免 judge、Mem0 标准基线,**CC BY-NC 只发聚合分**),LongMemEval(MIT 可商用背书,需 GPT-4o judge)后续。
- **`bench/locomo-eval.mjs`**(新):loader(LoCoMo 会话→ingest 序列,排除 category5 adversarial)+ evidence 层关键词 top-k 检索 + 外挂答案 LLM(mimo)+ partial-match F1 分桶 + token 记录 + runs 报告。CLI `--dry`(无 key 验管线)/`--limit`/`--qa`。
- **链路全通**(--limit 1 --qa 5 接 mimo):loader→ingest 419 轮→检索→mimo 答题→F1→token(4311)→runs 报告。
- **冒烟暴露的真实取舍**(第一步的价值):evidence 层关键词检索 gold-evidence 命中率参差(single-hop 48% / multi-hop 32% / temporal 75%),F1 低——量化了「MemoWeft 画像级 recall vs 基准 episodic 事实召回」的粒度落差(scout 预判)。改进方向第一条已验证(见下)。
- **已加 embedder 语义检索臂(bge-m3,`--retriever semantic`)**:bench 层逐条 embed + 手算 cosine(绕开本地 ollama 的 batch 坑)。前 40 轮 8 QA 同 evidence 集对比:**multi-hop 命中率 keyword 0% → bge-m3 语义 100%**(语义完胜关键词死角);temporal keyword 100% > 语义 75%(时序题含明确词、关键词占优);open-domain 都 100%。**量化了「换真 embedder 后 multi-hop 召回质变」**——呼应 D-0008 自建集 +35%,这里公开 LoCoMo 上 multi-hop 0→100%。
- **本地 ollama 坑(记录)**:bge-m3 的 **batch embed 退化**(5 条就 >90s、单条仅 ~1s)且慢 batch **卡死串行服务**(需重启清队列)。脚本已改逐条 embed 绕过;但本地 CPU ~1s/条,完整跑(10 sample × 数百 evidence)需 **GPU 或稳定云 embedder**。(→ 已迁 3090,embed 改由 llama.cpp bge-m3 GPU 供,见「环境」段。)
- **已加 cognition 层召回臂 + 会话日期注入(§19 剩余项①②,本会话·纯 bench,范围锁 `bench/locomo-eval.mjs`,src/tests 零改)**:
  - `--layer cognition`:updateProfile 消化证据→`core.recall`(真实系统召回路径,env bge-m3);命中率走**溯源链**(recall 的认知→其 sources 的 evidenceId→该证据 originId=dia_id→对 gold)。
  - **会话日期注入** evidence content(`[8 May, 2023] …`)+ occurredAt;`--no-dates` 可关做 A/B。修 §19.1 标注的 temporal「已知偏差」。
  - 补 **core 侧 token 记账**(`core.usage()`):cognition 臂大头在 updateProfile(走 core 自带池),旧代码只记外挂答题 LLM 会严重漏算——§19.0 要求记实际 token。
  - 认知纪律四点**只读不改**(全程经 updateProfile/recall/listCognitions/usage 公开门面);无 src/schema/API/快照改动。
- **四臂对比·首批真实数(conv-26·全 419 轮·前 30 题;mimo-v2.5-pro + bge-m3@3090;方向性,非最终基准)**:
  - **日期注入对 temporal 决定性**:keyword 臂 temporal 平均 F1 **0.131→0.613(×4.7)**(命中率两边同 88%,差在模型读到日期才答得出)——旧「已知偏差」实修。
  - **evidence 层语义完胜关键词**:multi-hop F1 0.133→**0.407**(命中 20%→60%)、temporal 0.613→0.658、open-domain 命中 50%→100%(呼应+扩展 D-0008:真实 embedder 是召回提升来源,LoCoMo 大语料上 multi-hop 尤显)。
  - **cognition 层在逐句题上更差**:temporal F1 **0.025**(命中 31%)、multi-hop 0.343、成本 **87k token(≈2×,其 76k 在消化)**——消化丢 episodic 细节,是「画像级 recall vs 逐句事实召回粒度落差」的硬量化(非缺陷,定位使然)。
  - **结论**:LoCoMo 逐句 episodic 题 **evidence 层 + 语义 bge-m3 是赢家**(F1 最高、更省);cognition 层不适合此类题。4 份 runs 入 `bench/runs/`(gitignore)。
- **§19.2 三臂×双 embedder 完整矩阵已跑(全 10 sample·1536 有-gold 题·Recall@15·dry;本会话·纯 bench,范围锁 `bench/locomo-eval.mjs`)**:生产级 retrievers(Vector/Keyword/HybridRetriever)× HashEmbedder(确定性)/bge-m3(真实),evidence 层。overall:**vector-bge 78.6% / hybrid-bge 77.7% / keyword 55.3% / hybrid-hash 50.6% / vector-hash 31.3%**。
  - **真实 bge-m3 压倒性**:+47pt vs 确定性向量(>D-0008 自建集 +35%)——最大杠杆,每类别都碾压。
  - **强 embedder 上 hybrid≡vector**(77.7≈78.6,微降):在 LoCoMo 大语料再次坐实 **D-0008「hybrid 不进公共 API」**;生产 vector-only 正确。
  - **弱 embedder 上 hybrid≫vector**(50.6 vs 31.3,+19pt):keyword 补真信号——**refine D-0008 caveat**(hybrid 价值取决 embedder 强弱)。
  - **keyword-only 55.3% > 确定性向量/hybrid**:验证 D-0008「keyword 大语料被低估」。
  - **踩坑+鲁棒化**:全量单进程第 9 sample `node:sqlite` 累积 `:memory:` 连接 **native 崩**(exit 127·无 JS 错)→ 改**每 sample 独立进程**(`--offset`/`--limit`)+ JSON 分片 + **`--merge-matrix`** 合并,全 10 绿、可复现。分片+合并 runs 入 `bench/runs/`(gitignore)。
- **§19.3 + BENCHMARKS + LongMemEval scaffold 一并落地(本会话·纯 bench+根 doc)**:
  - **§19.3 参数敏感性网格**(`bench/sensitivity-confidence.mjs` + `.md`,**纯确定性零 LLM**:底分/半衰期是规则算的、与 LLM 无关 → 离线重算,不必 9× 重跑固化):底分 ±20% credStatus 翻转率 28.1% 但**野翻转(跳档)=0**(全相邻档、集中在 stated 底分 600 处 limited/stable 中点);半衰期→保留窗口线性。**未发现更优默认、不触发改默认/断言流程**。
  - **BENCHMARKS.md**(§19.4,根目录):汇总 §19.2 矩阵 + §2 端到端 F1 + §19.3 敏感性 + LongMemEval 状态 + 复现命令 + 条件差异纪律(不做不对等比较)。
  - **LongMemEval_S scaffold**(`bench/longmemeval-eval.mjs`):loader(web 核实 schema)/检索/答题/LLM-judge/弃权,`--selftest` 离线全绿(证 loader+只摄入user+judge)。**真实跑受阻**:①本机无数据集(需 LONGMEMEVAL_PATH)②无标准 gpt-4o judge(mimo 非标准)③铁律 3a 只摄入 user 回合 → single-session-assistant 类按设计答不出(如实报告)。
- 数据 NC 许可:`bench/data/` + `bench/runs/*-locomo-*` 已 gitignore,数据只在本地(LOCOMO_PATH),绝不入库。
**Phase 6 验收(§19)**:
- [x] LoCoMo 接入(§19.1)+ **§19.2 完整矩阵**(全 10 sample·1536 题·三臂×双 embedder)
- [x] runs 可复现(per-sample 分片 + `--merge-matrix`);token/费用记录(答题 + core 侧 + embed 分桶)
- [x] **§19.3 敏感性报告入库**(`bench/sensitivity-confidence.md`)
- [x] **BENCHMARKS.md 就位**(根目录·§19.4)
- [x] **LongMemEval_S 全 500 题标准跑**(judge=gpt-4o-2024-11-20):**overall 51.3%**;强 single-session-user 71.4%/knowledge-update 69.3%,低 single-session-preference 10%(应走 cognition 层)/single-session-assistant 19.6%(铁律 3a 不摄入助手输出)。per-batch 进程隔离(2 批崩已 limit-5 补齐 500)+ --merge。judge 成本 <$1,答题 mimo ~1.70M token
- [ ] 打 tag `phase-6-done`(发布动作,待人类;且待 LongMemEval 跑完)

## Phase 5:文档更不绕(§18)—— 第一批用户文档已上线(已推 origin main)

**已落地**(`384d6d9`,已推):
- **`docs/getting-started.md`**:5 分钟装→存一条证据→读回(第一段无 key 立即可跑;第二段配模型解锁固化/召回)。
- **`docs/concepts/` 六页**:认知纪律各一屏(sourcing / confidence / correct-conflict / decay / no-self-evidence / read-write),每页配【已实跑通】的无 key 片段。
- **`docs/recipes/` 两页**:Vercel AI SDK / MCP server 五分钟接入。
- **`docs/reference/`**:memory-surface-contract 归位(双语)+ 旧位跳转桩;`docs/README.md` 改成三入口索引。
- 质量:11 页 69 内链 **0 死链**;所有无 key 片段实跑输出与注释逐字一致(核实 computeConfidence/effectiveConfidence/persistUserTurn 等真实签名,doc-writer 未编造)。起草走 8-agent doc-writer 工作流 + Integrator 逐页审+验+落盘。

**第二批:用户文档中文版 zh-CN 已落地(本地未推)**:
- **10 个 zh-CN 页**:getting-started + concepts6(含 README)+ recipes2,镜像已上线的英文页(分层双语 D-a·用户页双语)。
- **代码围栏逐字节保留**(`compare-fences.mjs` 机械验全 10 页 VERBATIM):中文页只译围栏外散文,代码块与英文源逐字一致 → 英文页已实跑过的片段中文页照样能跑,无需重跑。
- 术语提炼自现有中文页保持一致(证据/事件/认知/置信度/亲口/观察/推测/固化/召回/冲突/纠正/衰减);内链重指向中文版(目录自指 `[Concepts](./)`→README.zh-CN、嵌链接括注全处理对);标点全角化对齐全仓惯例(`check-punct.mjs` 验 0 残留半角)。
- **10 个英文页顶部加 `English | 简体中文` 互链行** + docs/README「Start here」三条加中文链接。
- 验证:死链 0(`check-links.mjs`:48 文件 269 内链)、typecheck/api:check「一致」(纯文档零碰代码/快照)。
- 做法:doc-writer 并行起草 + reviewer 逐页对抗审(代码围栏逐字为硬指标,全 pass)+ Integrator 三脚本机械终验 + 两轮标点收口(第一轮宪法漏定括号规则、误判 sourcing 为全角页 → v2 强化括号规则全 10 页补齐;教训:翻译镜像的风格规范应在首版宪法定清)。

**第三批:internals 迁移已落地(本地未推)**:
- **三文件 git mv 到 `docs/internals/`**(保留历史):architecture(瘦身+修陈旧)、boundaries、perf;旧位留 301 桩(architecture / architecture.zh-CN / internal-boundaries / perf 四桩)。
- **architecture 瘦身**(§18.0 删透,人类拍板):删 Mem0「未来将」/ route-seam 路线图腔 /「✅已落地 tier2·step6」changelog 腔 / differentiator 营销腔 / 经纬比喻附录;§4.5 重述并入 §4.1(标题改成 Attribution)。
- **修 3 陈旧点**(D-c,用 scout 核实的源码真实值·0 编造):sourceKind 3→4 补 `tool`(model.ts:12);§8 补 `toolDefaults={local:true,cloud:false,inference:true}`(config.ts:104)+ 写模型 tier;§2.3 cognition 补 `archivedAt`(model.ts:57)。
- **改活入链**(architecture 5 + 中文 4 改指英文单源 + boundaries 5 + perf 1,含新中文页 concepts 2 处)+ 建 `internals/README`(D-b 分区索引)+ 清 `internal/README` 的 boundaries 条目。
- **boundaries 保留中文**(D-0016:D-a 英文单源的例外——纯中文无英文版,翻译损耗>收益);naming 拆分移到第 4 项(和 glossary 一起,避免词表悬空)。
- 验证:死链 0(`check-links.mjs`:52 文件 271 链接)、typecheck/api:check「一致」;既存缺陷(boundaries 无编号锚点 vs 源码 §引用)记 ROADMAP。
- **独立 reviewer 复查抓到并修正 3 处**(`fe938e3`):§5 删 Mem0 bullet 后「Three implementations」悬空计数→「Two」、§4「Five/Six」口径去数字、en correct-conflict 漏改的 architecture 入链→指 internals 真身。三处陈旧点值经 reviewer 逐一对源码确认一致。

**第四批:README 60 秒电梯稿已落地(本地未推)**:
- 根 `README.md` + `README.zh-CN.md` 双语重写:184 行 → ~70 行,照 §18.1 结构(一句定位 → demo GIF → 「为什么不是又一个记忆库」3 句 → 60 秒安装+首次调用 → 三入口)。
- 4 条重复定位句(英文版里还混着 2 句中文)收敛成 1;Why different 7 条 → 3 句核心;reference host 保留精简一键启动 + GIF、4 张截图移 `docs/reference-host`(人类拍板:留精简启动、删三层表格)。
- 三入口更新为 getting-started/concepts/recipes;memory-surface-contract 链接改指 `reference/` 正本。
- 验证:死链 0(`check-links.mjs`:53 文件 282 链接,含 README 双语)、中文锚点无残留 warn。

**第五批:glossary + naming 拆分已落地(本地未推)**:
- 建 `docs/glossary.md` + `glossary.zh-CN.md`(双语术语表):18 核心术语三列(code ↔ 一句话定义 ↔ 用户词)+ 把握度定性档(credStatus 5 档)。reviewer 抓到 `cognition` 的 ContentType 漏 `project`(7→8 值)已修、credStatus 用户词逗号统一。
- `naming.md` 拆分:§3 词表 / §5 定性档 → glossary;引言 / §1 / §2 / §4 / §5 UI 表述纪律 / §6 / §7 → `docs/internal/naming-positioning.md`(§7 进度腔清除、章节重编号);`naming.md` → 301 桩。
- **11 处 host 注释入链分流**(用户授权碰 host):§3/§5 词表类(index.html 4 处)→ glossary,§2/§6 定位纪律类(experiences.test / server / xingyao / plain 共 7 处)→ internal/naming-positioning,§号按拆分更新(§6→§5)。**只改注释、不碰逻辑**。
- docs/README Reference 节加 glossary 入口。
- 验证:死链 0(56 文件 299 链接)、typecheck/api:check「一致」。

**第六批:文档 CI 自动化已落地(本地未推)**:
- `scripts/doc-links.mjs`(§18.4 死链):扫 docs + README 相对内链,硬查路径存在(死链 → exit 1)、锚点软警告(中文 slug 跨渲染器不稳只提示)。
- `scripts/doc-snippets.mjs`(§18.3):抽 docs + README 里【未标 `snippet:skip`】的 ts 围栏 → 写临时文件用 Node 冒烟跑(无 key、内存库、靠 build 出的 dist 做包 self-reference)。**25 个可运行片段全绿**(含全部中文版);需模型/长驻/非自包含的片段标 skip。
- 顺手补 6 处 `snippet:skip`:README 电梯稿 ts(en+zh,需 model)、integration.md/zh 两片段(需 model / 接上文非自包含)。
- `ci.yml` guardrails 加两 step:Docs dead-links(Lint 后、不需 build)+ Docs runnable-snippets(Core Build 后、靠 dist);`package.json` 加 `docs:links` / `docs:snippets`;`.gitignore` 加临时目录。
- 本地全绿(docs:links deadPath 0 · docs:snippets 25 passed)、YAML 校验通过;**CI 真跑待下次 push**。

**第七批:新人视角巡检 + 处理已落地(本地未推)**:
- 5 路 scout 以新人视角并行走不同路径(首屏 / 概念 / 接入+术语 / internals / 纯中文),产 ~54 条「仍然绕」。
- **本批修 5 个 HIGH + quick-win**(人类拍板力度 🔴+🟡):① README「60 秒首次调用」改真·无 key(修第四批标 snippet:skip 引入的矛盾——doc-snippets 现真跑 README en+zh 片段,27 全绿)② glossary 补 `subject/subjectId` + `formedBy` 词条 ③ sourcing 补 inferred 来源(消 no-self-evidence 的「inferred 从哪来」矛盾)④ vercel/mcp recipe 补 updateProfile/embedder 前提 ⑤ concepts/README + README 加 glossary 链接、Recipes 入口统一指 docs/recipes ⑥ 中文快修(README.zh recipes/glossary/contributing 指中文、getting-started.zh 中文标签)⑦ 措辞(卖点 3+3=6、confidence high→limited/Scene→Act)。全 en+zh 双改。
- **驳回 1**:README.zh 免责声明(README 是平等双语门面、非英文镜像,加「英文为准」会矮化中文门面)。
- **降级 ROADMAP**(Next 段):三个新中文版(demo-script.zh / docs-README.zh / reference-host.zh)、concepts 六页重排、internals 编号对照表 + internal↔internals 目录改名 + contract TOC、perf recall 基准、若干 low 措辞。
- 验证:doc-snippets 27 片段全绿(README en+zh 从 skip 转真跑)、死链 0(56 文件 307 链接)。

**Phase 5 验收(§18)**:
- [x] README 电梯稿 ≤ 一屏半;迁移映射表执行完毕(architecture/boundaries/perf → internals,naming 拆 glossary)
- [x] snippets 验证进 CI(27 片段)、死链 0、glossary 定稿双语
- [x] 「仍然绕」清单全部关闭或降级 ROADMAP
- [ ] **打 tag `phase-5-done`**(发布动作,待人类)

**三决策(见工件 `docs/internal/phase5-migration-map.md`,含全表映射)**:
- **D-a 分层双语**:用户页(README/getting-started/concepts/recipes/glossary/契约)双语;internals(architecture/boundaries/perf)英文单源。
- **D-b 目录分工**:`docs/internals/`(新·"怎么建的":architecture/boundaries/perf)vs `docs/internal/`(旧·维护者账本:halumem/calibration/runbook/publishing)。
- **D-c**:architecture 迁移时修 `sourceKind` 3→4 值(加 tool)+ 补 toolDefaults/archivedAt。

**剩余(后续批,迁移映射表是蓝图)**:
1. ~~concepts/recipes/getting-started 的**中文版** zh-CN(分层双语)~~ ✅ **已落地**(本地未推,见上「第二批」)。
2. ~~**architecture/boundaries/perf → `docs/internals/`**(迁移 + 修 sourceKind 陈旧 + 改入链 + 旧位留桩)~~ ✅ **已落地**(本地未推,见上「第三批」);naming 拆分移到第 4 项(和 glossary 一起做)。
3. ~~根 **README 收敛成 60 秒电梯稿**(§18.1;现有 README 4 条重复定位句收敛成 1)~~ ✅ **已落地**(本地未推,见上「第四批」)。
4. ~~**glossary.md**(naming §3 词表提炼)~~ ✅ **已落地**(本地未推,见上「第五批」;含 naming 拆分 + 11 处 host 注释入链分流)。
5. ~~**§18.3 snippets 进 CI** + **§18.4 死链检查进 CI**~~ ✅ **已落地**(本地未推,见上「第六批」;25 片段全绿、死链 0,CI 真跑待 push)。
6. ~~**§18.5 新人视角巡检**("仍然绕"清单 → 逐条处理)~~ ✅ **已落地**(本地未推,见上「第七批」;5 HIGH + quick-win 已修、结构性降级 ROADMAP)。
7. 收尾:打 tag `phase-5-done`(人类)。

## Phase 4:demo 更锋利(§17)—— 时间注入 S1-S3 + 四幕 demo S4 全落地(已推 main)

**方案 C(人类拍板·可注入时钟,D-0015)**:demo 要确定性(两次跑 diff 空)+ `--fast-forward`(情绪衰减、事实留存),且 §17.4「只经公共 API」。散落的 `new Date()` 无法注入 → 加可注入 `Clock`。
- **S1a**(`86905a9` refactor):三个 store 时间源参数化(构造加可选 `clock`,缺省 `systemClock`),落库/更新时间走 `clock()`。internal,api-freeze 不动(store 构造签名不进快照,已验)。
- **S1b**(feat):`CreateCoreOptions.clock` + `openStores` 加 `clock` 参 + 导出 `type Clock`/`systemClock`。触 api-freeze 走全流程(D-0015 + api:update + 契约 en/zh + CHANGELOG),纯 additive(缺省系统时间、旧调用方零改动)。
- **铁律 3b**:clock 只产时间戳、绝不进置信度自算(测试已验注入 clock 不改 confidence)。
- **S2**(`fb0ec69` feat):写路径算子(consolidate correct/conflict 显式时间、attribute 归因窗口上界)+ updateProfile 透传走 clock。
- **S3**(`6ebc091` feat):读路径 now(`core.recall`/`handleConversationTurn` → `recallCognitions` 衰减门控)走 clock —— 前进 clock → 情绪 `state` 有效置信衰减出局、`fact` 留存(测试实证前进 11 天)。
- **S4**(`a941429` feat):四幕 demo `examples/demo.ts`(记住/纠正/矛盾/时间)+ `npm run demo` + 三段式纯文本 CLI(`!! CONFLICT` 标记)+ `--fast-forward`/`--act` + 录屏脚本 `docs/demo-script.md`。**确定性验收:两次运行 diff 逐字为空(已验)**;只经公共 API(`import 'memoweft'`)。
- 验证:core 298 · typecheck/api:check「一致」/build 全绿。**六提交本地未推**(S1a/S1b/S2/S3/S4 + 更早的 AD-3/§16.3)。
- **方案 C 门面路径全覆盖**(后续提交):`managementApi`/`managementLog`/`core.graph`/`core.portable` 也接了注入 clock —— **至此经 `createMemoWeftCore` 门面的所有时间源均可注入**。剩 `asking`(proposeAsk/revisitConflicts 的 askedAt)、`obs/runLog`(ts)两处【非门面路径】未接(散装 dev 算子 / 可选诊断,不被工厂调用)→ ROADMAP。

## Phase 4 验收(§17 · 只差打 tag)
- [x] clone 一条命令 `npm run demo` 走完四幕;**两次运行 diff 为空**(确定性)
- [x] 四幕点题输出 + 认知状态表可读;conflict 幕 `!! CONFLICT` 呈现
- [x] demo 仅用公共 API(`import 'memoweft'`);录屏脚本 `docs/demo-script.md` 入库
- [ ] 打 tag `phase-4-done`(发布动作,待人类)

> 总纲 `PROJECT_PLAN.md`;决策 `DECISIONS.md`;固化质量报告 `bench/consolidation-baseline.md`;回归流程 `docs/internal/prompt-regression-runbook.md`。

## Phase 3 进行中(适配器更稳,§16)—— 本地三提交,未推

**已落地**(commit `2d087c1` §16.1 起步 · `596d8f3` 进度 · `156065d` AD-6):
- **§16.1 adapter-kit**:`tests/adapter-kit/` 参数化契约套件(一份喂两个适配器,每适配器约 50 行薄驱动)。
- **AD-1 绿**(助手→evidence 零新增)、**AD-2 绿**(用户→恰好一条 spoken;A 幂等、B 前后计数)。
- **AD-4 已定**(credStatus='conflicted' 即算冲突提示,人类拍板)= 纯 golden 快照(A 文本块 en/zh、B structuredContent,含一条 conflicted),无契约变更。
- **AD-6 绿**(`156065d`,§16.2 契约 D-0012):recall 超时 200ms 可配、读不重试写一重试、降级注入空+logger。**修掉 MCP "记忆层抛错即崩" 硬伤**(实证抛错下 recall 返 [] 不崩)。两适配器降级事件形状统一 `{event:'memory_degraded',op,reason}`(MCP 另带可选 tool)。
- **AD-5**:N/A(两适配器无 LLM→evidenceId 回捞面,by-construction)。
- 顺手修:非法 credStatus bug(`corroborated`/`single-source`→真实枚举,conflicted 路径首测)、注释与实现不符。
- 验证:core 284 · adapter-ai-sdk 23 · mcp-server 12 · typecheck/build/api:check「一致」/lint 全绿。**契约红线(Core src/枚举/api-snapshot)一个没碰**,Integrator 独立复核过。

## Phase 3 剩余:只剩 §16.3 版本矩阵 CI(AD-3 已落地)

### AD-3 加 `SourceKind 'tool'`——已落地(D-0013,本会话)
按原「已批实现清单」6 步全部落地;api-freeze 走完整流程(**D-0013** + `npm run api:update` + 契约 en/zh + CHANGELOG 同一逻辑变更内):
- **Core**:`SourceKind` 加 `'tool'`(`model.ts`,唯一类型改动源);`config.toolDefaults`(local✓/cloud✗/infer✓)+ `store.put` 把 observed/tool 收进同一 `conservative` 保守分支(**拆「工具返回值默认上云」隐私雷**);新增门面 `core.ingestToolResult(ToolResultInput)`(perceive+put,sourceKind 钉死 tool、带 originId 幂等)。
- **两适配器摄入面**:A `persistToolResults(core,{messages,originIdPrefix?})`(只读 `role:'tool'` 消息的 tool-result;assistant 的 tool-call 意图**一概不读**——铁律 3a 机器化);B MCP tool `memoweft_ingest_tool_result`(白名单 6→7,server.test.ts 集合断言同步)。
- **kit**:AD-3 从 N/A 翻 applicable,两 driver 真跑「工具结果→+1 tool + 调用意图不落库」。
- **图谱**(非阻塞顺手):`MemoryGraphStats.toolEvidenceCount` + tool 节点独立着色。
- **铁律 3a 边界**:只摄入工具返回结果(外部客观数据=合法证据),不摄入 LLM 调用意图/入参(助手输出,禁摄入)。两适配器测试各断言「落库无一条含调用意图标识串」。
- **已知留待(进 ROADMAP,出 AD-3 范围)**:distill/consolidate 喂 LLM 丢 sourceKind(`distill.ts:56`/`consolidate.ts:146`),工具结果理论上可能被误固化为"用户亲口"——**既有特性**(observed 也这样),要治得动纪律敏感写路径。

**Ultracode 对抗审查(22 agent · 7 维度 → 每 finding 3 视角对抗验证)修掉 1 个真 bug + 1 处文档陈旧**:
- **真 bug(已修 + 回归护栏,先红后绿)**:`persistToolResults` 对畸形 json 工具结果(`{type:'json',value:undefined}`,真实可达:自定义 `tool.toModelOutput` 返回缺失字段)会 `JSON.stringify(undefined)===undefined` → 下游 `text.trim()` 抛 TypeError,且在 per-item try/catch **之外** → 逃逸崩宿主 turn,违反「绝不向外抛」契约。修:`toolOutputText` 严守 `string|null`(非串收 null)+ `extractToolResults` guard 双保险。
- **文档陈旧(已修)**:契约文档 en/zh 章节标题「24 host-facing methods」漏改 → 25(计数行已对,只标题陈旧)。
- **对抗验证驳回 2 条**(经复核认同非真缺陷):config 缺 `toolDefaults` 的 fail-open(`toolDefaults` 是**必填**字段,只有故意传畸形 config 才触发);MCP `callIntentExcluded` 冗余(by-construction 已有注释解释)。
- 验证:core 291 · adapter-ai-sdk 30 · mcp-server 13 · typecheck/build/api:check「一致」/lint 0-error 全绿。**契约红线走了完整流程**(D-0013 + 影响面说明 + api:update + 文档),非手改快照。

### §16.3 版本矩阵 CI——已落地(D-0014,本会话)
`ci.yml` 新增 `sdk-version-matrix` job(4 组合,fail-fast:false):adapter-ai-sdk `ai@7.0.0`/`ai@7`、mcp-server `sdk@1.29.0`/`sdk@1`。**人类拍板「矩阵化 dependency,不改 peer」**(mcp-server 是自带 SDK 的可执行服务器 bin,SDK 属实现依赖;ai-sdk 的 ai 本就是 peer)。
- **探针机制**:`npm install <dep>@<版本> -w <pkg> --no-save`(不写 lockfile)覆盖装 → 该包 typecheck+test。**与 guardrails 隔离**:--no-save 不碰 lockfile、缓存用每组合独立 key 的 actions/cache。**核实**:既有 lockfile guard 只 `grep npmmirror`(挡镜像源、不挡版本),探针天然不撞它——CURRENT 原「撞 lockfile guard」的担心用 --no-save 化解。
- **版本口径**:测声明范围两端(下界 + 范围内最新),**不追绝对 latest**(超范围大版本是「是否扩大支持」的主动决策,不该让矩阵无意义地红)。矩阵红 = 声明范围内兼容性破裂 → 记 DECISIONS。
- **本地实测背书**:写 CI 前先本地跑 4 组合 --no-save 覆盖装 + typecheck+test **全绿**(ai-sdk 30 · mcp 13);npm ci 复原、lockfile 未污染。YAML 经 `yaml.safe_load` 校验。GitHub Actions 真跑需 push(人类的事)。

### §16.5 新适配器 / §16.4 快照-beyond-baseline:进 ROADMAP(§16.4 的注入格式 golden 其实已由 AD-4 recallSurface golden 覆盖;§16.5 新适配器 calibration 建议 adapter-kit 稳后再做)

## Phase 3 验收(§16 · 只差打 tag)
- [x] adapter-kit 就位;两适配器 **AD-1…AD-6 全绿**(AD-3 本会话翻 applicable)
- [x] 故障注入(AD-6 throw/timeout)测试绿;降级语义进 contract(D-0012)
- [x] **版本矩阵 CI 落地**(§16.3 · D-0014);注入格式快照(AD-4 golden 已锁)
- [ ] **打 tag `phase-3-done`**(发布动作,待人类点头);§16.5 新适配器进 ROADMAP

## 刚完成:A 路线(Phase 2 收尾管道)三段全部落地 + 全量基线入库

本轮从 CURRENT 的 A/B/C 里走了 **A(收尾管道)**,四个提交成链(`c74b05c..431f028`):

- **A1 提示词版本化 + 哈希闸门**(`bc618f3`):8 条散落的提示词收敛到各模块 `prompts.ts`,`{id,version,text}`,由 `src/prompts/registry.ts` 聚合(**不经 index.ts 导出,公开 API 面零变化**)。新增 `tests/prompts/registry.test.ts` + `prompt-hashes.snapshot` 哈希闸门:改内容不 bump version → `npm test` 立刻红。**搬家逐字节无损**(搬家前后 16 个 sha256 全中,Integrator 独立复算)。字段 `system`→`text`(jsonRepairNudge 实际以 role:'user' 注入)。新增 `.gitattributes` 钉死两个机读快照为 LF —— 修了一个既有潜在 bug:`core.autocrlf=true` 且无 `.gitattributes` 时 fresh clone 把 `api-surface.snapshot` smudge 成 CRLF,api-freeze 在 Windows 新克隆假红(CI 跑 Linux 故没暴露)。
- **A2 评测器可归因**(`83eeec4`):meta 记 `promptVersions`;全量跑写 `baseline.{md,json}`,**部分跑(--limit/--discipline)只写 `bench/runs/`、绝不碰基线**;新增纯离线 `--compare <a.json> <b.json>`(0.167s,把 `consolidate: v1→v2` 与分数变化并排摆出,底部吐可粘贴的 commit 摘要)。**修了一个会毁基线的脚枪**:`--limit 0`/`abc` 曾落成假值→跑满 42 场景并覆盖基线,现在退化输入一律 exit 1。
- **A3 test:live + nightly**(`bf8242e`):`scripts/test-live.mjs` 三腿编排(live e2e + 固化全量42 + 检索真实臂),**缺 LLM key 直接 exit 1 不静默跳过**,腿2 只设崩溃门(errored>0)不设质量阈,embed 未配→腿3 大声跳过。nightly 去掉 `--if-present`、timeout 150min、传 artifact。**从 ci.yml 删掉死变量 SKIP_LIVE_LLM**(全仓无人读它)。记 **D-0010**(不建 fixtures:refresh)、**D-0011**(删 SKIP_LIVE_LLM)。runbook 入库。
- **全量基线入库**(`431f028`):在 bf8242e 上跑一次全量真实 mimo,**首次把 `.json` 与 `.md` 一起入库**(补齐前后对比链条起点 —— 此前只提交 .md,`git show HEAD:...json` 取不到)。结果 **95.1%(212/223),全绿 32/42,errored 0**,较 D-0009 记录的 v2 基线(94.2%/30)+2/+2、无回退 —— 在**全部 6 类纪律**上坐实提示词搬家真实模型侧无损。`--compare` self-compare 实跑 Δ=0、exit 0,工具链闭合。

## 已上线:整段历史推公开 main + nightly 首晚绿(2026-07-10)

- **推送**:此前 Phase 0-2 纯本地开发,远端停在 v0.5.1。本轮经人类逐条授权,把 25 提交(`e7d5ec4..0ec3006`)+ 后续 e2e 修复(`800adde`)快进推上**公开** `memoweft/memoweft` 的 main。`.env` 未跟踪、未泄露。详见记忆 `memoweft-repo-publish-state.md`。
- **secrets**:GitHub Actions 加了 3 个必需 LLM secret(`MEMOWEFT_LLM_BASE_URL/_API_KEY/_MODEL`,值来自本地 `.env` 的 `DLA_LLM_*`)。**故意不设 EMBED**(端点是 localhost、CI 不可达,设了反让腿3 变红)。key 经 stdin 喂 `gh secret set`,未落终端/日志。
- **nightly 首跑红→修→第二跑绿**:首跑暴露一个**既有 e2e bug**(`conflict.e2e.ts` 的 observed 证据没显式 `allowCloudRead:true`→云 tier 下被 distill 滤掉→6.9ms 空转失败;`800adde` 修)。**这个测试直到 nightly 接真实模型才第一次真跑**(本地 e2e 不加载 .env 全 skip、旧 nightly 空转)。第二跑(run 29116360383)**全绿 1h20m**:腿1 e2e 通过、腿2 固化 errored=0 结构 210/223 全绿 30/42(提示词版本 consolidate@v2 记入)、腿3 大声跳过。

## Phase 2 验收清单(§15 · 只差打 tag)

- [x] 语料库 42 场景、6 纪律各 7、覆盖矩阵达标
- [x] 评测器两级比对可跑,judge 3 次多数;基线报告入库(`bench/consolidation-baseline.{md,json}`)
- [x] 提示词版本化(A1)+ `test:live` 可用(A3);~~fixtures:refresh~~ → 作废见 D-0010
- [x] **nightly live job 首晚绿**(run 29116360383,test:live 通过)
- [ ] **打 tag `phase-2-done`**(发布动作,待人类点头 —— nightly 已绿,就差这一下)
- 强化项:§15.5 多模型分差矩阵未做 → 进 ROADMAP

## B 靶子经诊断为【度量假象】—— 已用质量线证伪(2026-07-10 只读 scout)

动手改提示词前先诊断了 CURRENT 原列的两个"B 靶子",结论:**两个都不是真缺陷,是度量退化**(依据见 baseline.json 逐场景明细):

- **conflict gistRecall=0.00 = 度量盲区,非质量缺陷**。gist 判官只看【落库认知的文本】(`eval-consolidation.mjs:129-133`),而 conflict 是靠"给旧认知打 conflicted 标 + 不造新认知"处理的(证据在计数器里、不在文本里)→ 这条路径**天生产不出可被 gist 匹配的文本**,gistRecall 恒为 0,与模型好坏无关。真实处理质量由**结构 40/42** 背书(且语料 `newCognitions.min=0` 明说可不落新认知)。叠加 D-0009 单跑噪声(CC-003 created=0 却抖出一票 YES)。**改提示词动不了这个数字。**
- **no-over-inference 29/34 = fact/state 类型口径分歧,非过度推断**。`overInferRate=0.00`、所有 `shouldNotFormGists` 陷阱 100% 躲过——真正的过度推断靶心全达标。挂掉的 5 分全是同一类 `created类型⊆{types}`:模型把"一次性完成事件/情绪残留"标 `fact`,语料期望 `state`,**两边都站得住,甚至可能是语料期望该松绑**。

→ **B 手里没有一个经得起结构硬指标检验的真靶子。** 若真要动,最划算的是一次**廉价的语料/度量清理**(见 ROADMAP Next:定 fact-vs-state 规范类型 / 让 conflict 的 gist 度量改看 conflicted 标而非落认知),而不是"改提示词→bump→跑全量→compare"的 77 分钟迭代。

## 下一步(待人类定向)

- **C. 转 Phase 3(适配器更稳,PROJECT_PLAN §16)** —— 证据推荐方向。开工照惯例:先只读 calibration scout 侦察 `packages/mcp-server` 与 `packages/adapter-ai-sdk`(AD-1…AD-6 契约、peer 版本矩阵、故障注入降级)。
- **B'(可选·低优先)**:上面那次廉价语料/度量清理(不是提示词战役);想让这两个数字"好看"时再做,已进 ROADMAP Next。
- 改提示词的完整流程(将来真需要时)见 `docs/internal/prompt-regression-runbook.md`。

## 环境 / 阻塞

- 无阻塞。**已迁至 3090 机器**(RTX 3090 24G);嵌入端点(bge-m3 @ 127.0.0.1:11435)本会话**改由 llama.cpp GPU 供**(非 Ollama:`llama-server --embedding --pooling cls -ngl 99`,启动脚本 `Desktop/Working/start-llama-bge-m3-embed.cmd`;坑:须清空全局 `LLAMA_API_KEY` 否则 401)。旧机 CPU ollama 的 batch embed 退化在 GPU 上不再是瓶颈。固化/答题走 mimo 云端 API。
- 固化评测慢:实测 82–141s/场景,全量 42 场景约 77 分钟(CURRENT 旧记的 30s/场景是错的,已在评测器注释订正)。
- `.env`(gitignored,DLA_/MEMOWEFT_ 双前缀,mimo + bge-m3)本会话在。

## 本轮范围冻结(铁律 4)

host、采集插件、perception、asking、attribution、background、graph、portable、memory 管理 API —— 只在某 Phase 明确需要时才碰,否则进 ROADMAP Later。
