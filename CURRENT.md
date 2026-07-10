# CURRENT — 当前状态(Integrator 每个工作段落结束更新)

更新于:2026-07-10 | 所在 Phase:0 奠基(tag: `phase-0-start` @ `5a66dcb`)

> 总纲见 `PROJECT_PLAN.md`(Phase 0.5 落地);本文件是 Integrator 的工作状态,每个工作段落结束更新。

## 正在进行

- 即将进入 **0.4 `.claude/` 多智能体配置包**(6 子代理 + hooks/protect.py)。0.1/0.2/0.3 已完成(见下)。

## 刚完成(最近 5 条,附证据)

- **0.3 公共 API 冻结机制**(§13):`scripts/api-snapshot.mjs`(用 TypeScript 编译器 API 枚举 `src/index.ts` 导出面、形状级渲染、按名排序、过滤 private/protected;零新增依赖)+ `tests/api/api-freeze.test.ts` + 首版快照 `tests/api/api-surface.snapshot`(184 行)+ `npm run api:update`/`api:check`。**变更流程演练通过**:加临时导出 → `api:check` exit 1(红)→ 回滚 → exit 0(绿)。全量 **223/223 绿**。
- **0.2 校准侦察**(scout×3,报告 `docs/internal/phase0-calibration.md`)。三条净结论:① 置信度/衰减**精确数值已核实**,铁律 3b(置信度只由规则算)/3d(证据 ID 白名单)在代码中成立;② **检索真瓶颈在读侧**——每次查询 O(N) 读全表 + JSON.parse + JS 余弦,而"增量索引"嵌入侧**已有**(sha256 diff)→ 改写 Phase 1 打法(重心放检索侧);③ 适配器只依赖 6 个门面方法,注入格式须同锁 Core+适配器两处,mcp-server 的 `health()` 注释与实现不符。**5 处文档不符**已列报告(铁律 7)。
- **0.1 基线**:`npm test` 222/222 全绿(node v24.15.0);FTS5+trigram 可用,**中文需 ≥3 字符**(D-0001 待记);mimo 连通验证 OK(key 未持久化)。
- **工作区清理**:`e10dfc1` 版本同步 0.5.1;`5a66dcb` gitignore 杂物。

## 阻塞(等人类或等依赖)

- 无。

## 下一步(按序)

1. **0.4 `.claude/` 多智能体包**(附录 I):6 子代理 + settings.json + hooks/protect.py。**解释器指向 Windows 可用 `python`**(D-0004),protect.py 喂 stdin 跑七场景,两条关键拦截记本文件。
2. **0.5 治理文件**:`PROJECT_PLAN.md` 入仓;AGENTS.md 升级为 Integrator 章程(diff 给人类);新建 CLAUDE.md 入口;ROADMAP.md 重置;新建 DECISIONS.md(D-0001 FTS5 / D-0002 协作模式=务实混合 / D-0003 demo=改造 testbench / D-0004 hook 解释器 / D-0005 mimo 推理模型 + 向量层在迁移体系外)。
3. **0.6 CI 补强**:api-freeze、lint/typecheck、SKIP_LIVE_LLM、真 key 用例统一跳过、nightly 骨架。

## 本轮范围冻结(铁律 4)

host、采集插件、perception、asking、attribution、background、graph、portable、memory 管理 API —— 只在某 Phase 明确需要时才碰,否则进 ROADMAP Later。
