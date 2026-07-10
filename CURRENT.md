# CURRENT — 当前状态(Integrator 每个工作段落结束更新)

更新于:2026-07-11 | 所在 Phase:**2 固化更可信(A 路线收尾已完成,待打 tag)**(前置 tag `phase-1-done`)

> 总纲 `PROJECT_PLAN.md`;决策 `DECISIONS.md`;固化质量报告 `bench/consolidation-baseline.md`;回归流程 `docs/internal/prompt-regression-runbook.md`。

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

## 下一步(待人类定向)

- **收尾 Phase 2**:人类加 nightly secrets → 首晚绿 → 打 tag `phase-2-done`。(唯一卡在人类侧的动作)
- **B. 继续用质量线修**(现在修一个问题的成本已被 A 打下来了):
  - `conflict` gistRecall=0.00(不落行为认知)—— 全量基线复现,是最明确的靶子
  - `no-over-inference` 结构 29/34(6 纪律里最低)、偶发过度推断
  - 改提示词的完整流程见 `docs/internal/prompt-regression-runbook.md`,改一条 → bump version → prompts:update → 跑全量 → --compare。
- **C. 转 Phase 3**(适配器更稳)或其它。

## 环境 / 阻塞

- 无阻塞。本地 Ollama(bge-m3 @ 11435)本会话**未起**(检索真实臂要用才起:`ollama serve`)。固化走 mimo 云端 API,不依赖 ollama。
- 固化评测慢:实测 82–141s/场景,全量 42 场景约 77 分钟(CURRENT 旧记的 30s/场景是错的,已在评测器注释订正)。
- `.env`(gitignored,DLA_/MEMOWEFT_ 双前缀,mimo + bge-m3)本会话在。

## 本轮范围冻结(铁律 4)

host、采集插件、perception、asking、attribution、background、graph、portable、memory 管理 API —— 只在某 Phase 明确需要时才碰,否则进 ROADMAP Later。
