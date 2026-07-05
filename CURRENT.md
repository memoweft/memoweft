# CURRENT.md · 当前任务白板

> 唯一的"现在该做什么"看板。只写**当前主线 + 允许做 + 不做 + 验收**。历史不写这儿——看 git 提交与 `CHANGELOG.md`。

## 当前状态：总纲第 1–6 步已完成（第 6 步施工完在分支、待作者合 main），下一 = 第 7 步

- **第 1 步 · 0.3.0 补漏加固** ✅ 已发布 `memoweft@0.3.0`（npm latest）。
- **第 2 步 · 接口契约 Memory Surface Contract v1** ✅（合 `74b58c3`）。
- **第 3 步 · 质量证据** ✅（合 `1d09c55`）：eval 25 用例 + lint 关卡 + 覆盖率 97.42% + SECURITY/模板/维护声明 + perf 实测 + CI provenance。
- **第 4 步 · 英文化与模型兼容（0.4.0）** ✅（合 `feb713c`·分支 `step4/i18n-model-compat` T1–T6）**+ 已发 `memoweft@0.4.0`（npm latest·2026-07-05）**：双语层（`config.language` 缺省 en + `resolveLang`）+ 8 处提示词双语化、宿主/用户文案双语化、`temperature` 可配（`LLMConfig`+env 按 prefix 分 chat/write）、reasoning 剥 `<think>`+`extractJsonObject` 括号配平、`hostId` 默认改 `local`、examples 扩到 3（以包名入口）、INSTALL/integration 英文化（`.zh-CN` + 互链）+ 明文落盘声明。三绿 202/202 + lint 0 + 零依赖。
- **第 5 步 · 图谱前端 G2** ✅（合 `9496a3d`·分支 `step5/graph-frontend-g2`）：Host 加只读 `GET /api/memory-graph`（走 `core.graph` 门面）；记忆管理页加「记忆图谱」tab——**手搓 canvas 力导向图（零依赖）** + 丰富交互（拖/缩放/过滤/边类型图例/搜索/详情/重置）。preview 真起 Host 只读渲染真数据验过（12 节点/14 边、力模拟冷却、全交互无报错）。Core 202/202 + Host 27/27 + lint 0。

- **第 6 步 · 本地模型档 2（cloud/local tier 路由）** ✅ 施工完成（分支 `step6/local-model-tier`·T1–T6·**待作者亲核合 main**）：写路径隐私关 `filterCloudReadable`→`filterReadableByTier(items, tier)`（cloud 筛 `allowCloudRead` / local 筛 `allowLocalRead`）；`MEMOWEFT_WRITE_LLM_TIER=local` 让本地写模型消化 observed（默认不上云）成画像——采集线真闭环。含覆盖修复（distill 只覆盖真消化的、被挡留 pending 可再扫）+ `allowInference` 门三处一致 + 挂账信号 `tierBlockedCount` + 向导 tier 字段/风险提醒。**不动认知判定算法（confidence.ts/cognition 零改）**、零依赖。根 209/209 + Host 32/32 + lint 0。任务书 / 拍板 D1–D8 / 对抗校对纪要见 `docs/internal/tasks/step6-local-model-tier/`。

**下一主线 = 总纲第 7 步：插件契约 v2（10-A）+ 采集器跨平台**——hooks / PluginContext 从"预留"转正式（踩第 2 步契约）；采集器现只 Windows，补 macOS/Linux 或明说不做。**待作者拍板开工后细化成施工任务书。**

## 待作者手动（发布 / 平台侧尾巴，AI 做不了）

- **合并第 6 步 + 推 origin**：`step6/local-model-tier`（领先 main 7 提交：任务书 + T1–T6）施工完三绿，**待作者亲核 diff → 合 main**（红线：推 main 前 PM 亲核）。合完连同之前 main 领先 origin 的 4 提交一起 `git push origin main`（本机无 gh，作者手动）。
- **第 4 步真模型 e2e 英文验**（0.4.0 唯一未闭验收）：配好模型的机器上，真 LLM 跑 `tests/eval/cognition-discipline.eval.e2e.ts` 换**英文对话输入**，验三纪律（冲突暴露 / 情绪封顶 / 记≠信）在英文侧真生效——离线 eval 只断结构、证不了这个。
- **`v0.4.0` tag + GitHub Release**（若未打）：`git tag v0.4.0 && git push origin v0.4.0`。
- **Q5 provenance 发布**：往 GitHub secrets 放 `NPM_TOKEN` → 打 `v*` tag 触发 publish job。
- **GitHub 仓库设置**：开启 "Private vulnerability reporting"。
- **覆盖率徽章**：CI（ubuntu Node24）跑出后按其 "all files" line% 再校。

## 发现待办（不阻塞，回头清）

- lint 6 个警告（存量）：4 个未用变量 + `tests/store.test.ts` 两处 `@ts-expect-error` 缺说明。松档已降 warn、不阻断；清理时给未用变量加 `_` 前缀 / 给 ts-comment 补一句说明即可。
- README `Node ≥24` 徽章与 `engines>=20` 口径可再对齐（≥24 是零依赖路径，20/22 走可选 `better-sqlite3`）。

## 后期前端打磨待办（功能都通了再统一打磨 · 作者拍板 2026-07-05）

> 前端显示/反馈的打磨排到最后，现在不过度雕；桌面端后续要做、先留改造空间（web 前端别过度定制）。
- **S0「它记住我 N 件事」记忆胶囊 = demo 脚手架** → 后期改成朴素的「记忆管理」入口（demo 是为证明"它真记住了"，稳定后不用一直留这拟人化提示）。
- **「你说过的（记忆线索）」tab 口径**：evidence ≈ 原话/对话记录 → 后期或重构为「对话记录」这类更直白的说法。
- **记忆图谱（G2）力参数 / 视觉**：斥力/弹簧/聚拢是手调经验值，节点多时可能挤/散；随前端打磨一起在真库上调（毛线球收敛度）。
- **桌面端优化**：后续做桌面客户端时前端要重整，现在 web 端保持克制、留改造口子。

## 后续总排序

第 6 步 → … → 第 10 步收口 1.0，商用线 + 功能线合排共 11 步，见 [`docs/internal/tasks/后续批次总纲.md`](./docs/internal/tasks/后续批次总纲.md)——每步开工前才细化成施工任务书。
