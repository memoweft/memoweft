# CURRENT — 当前状态(Integrator 每个工作段落结束更新)

更新于:2026-07-11 | 所在 Phase:**4 demo 更锋利(时间注入 S1-S3 + 四幕 demo S4 全落地——只差人类打 `phase-4-done`;Phase 3 也待 `phase-3-done`)**(前置 tag `phase-2-done`)

## Phase 4:demo 更锋利(§17)—— 时间注入 S1-S3 + 四幕 demo S4 全落地(本地未推)

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

- 无阻塞。本地 Ollama(bge-m3 @ 11435)本会话**未起**(检索真实臂要用才起:`ollama serve`)。固化走 mimo 云端 API,不依赖 ollama。
- 固化评测慢:实测 82–141s/场景,全量 42 场景约 77 分钟(CURRENT 旧记的 30s/场景是错的,已在评测器注释订正)。
- `.env`(gitignored,DLA_/MEMOWEFT_ 双前缀,mimo + bge-m3)本会话在。

## 本轮范围冻结(铁律 4)

host、采集插件、perception、asking、attribution、background、graph、portable、memory 管理 API —— 只在某 Phase 明确需要时才碰,否则进 ROADMAP Later。
