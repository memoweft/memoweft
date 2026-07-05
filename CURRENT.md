# CURRENT.md · 当前任务白板

> 唯一的"现在该做什么"看板。只写**当前主线 + 允许做 + 不做 + 验收**。历史不写这儿——看 git 提交与 `CHANGELOG.md`。

## 当前状态：总纲第 1–4 步已完成，等开工第 5 步

- **第 1 步 · 0.3.0 补漏加固** ✅ 已发布 `memoweft@0.3.0`（npm latest）。
- **第 2 步 · 接口契约 Memory Surface Contract v1** ✅（合 `74b58c3`）。
- **第 3 步 · 质量证据** ✅（合 `1d09c55`）：eval 25 用例 + lint 关卡 + 覆盖率 97.42% + SECURITY/模板/维护声明 + perf 实测 + CI provenance。
- **第 4 步 · 英文化与模型兼容（0.4.0）** ✅（合 `feb713c`·分支 `step4/i18n-model-compat` T1–T6）**+ 已发 `memoweft@0.4.0`（npm latest·2026-07-05）**：双语层（`config.language` 缺省 en + `resolveLang`）+ 8 处提示词双语化、宿主/用户文案双语化、`temperature` 可配（`LLMConfig`+env 按 prefix 分 chat/write）、reasoning 剥 `<think>`+`extractJsonObject` 括号配平、`hostId` 默认改 `local`、examples 扩到 3（以包名入口）、INSTALL/integration 英文化（`.zh-CN` + 互链）+ 明文落盘声明。三绿 202/202 + lint 0 + 零依赖。

**下一主线 = 总纲第 5 步：图谱前端 G2**——后端 payload 已有（G1 ✅），接 `/api/memory-graph` + 力导向图，记忆管理可视化收尾。工作量小、演示价值大。**待作者拍板开工后细化成施工任务书**（`0.4.0/` 那套即样板）。（若要调整顺序 / 先插商用线，作者定。）

## 待作者手动（发布 / 平台侧尾巴，AI 做不了）

- **推 origin**：有未推提交时 `git push origin main` 由作者手动（本机无 gh）。
- **第 4 步真模型 e2e 英文验**（0.4.0 唯一未闭验收）：配好模型的机器上，真 LLM 跑 `tests/eval/cognition-discipline.eval.e2e.ts` 换**英文对话输入**，验三纪律（冲突暴露 / 情绪封顶 / 记≠信）在英文侧真生效——离线 eval 只断结构、证不了这个。
- **`v0.4.0` tag + GitHub Release**（若未打）：`git tag v0.4.0 && git push origin v0.4.0`。
- **Q5 provenance 发布**：往 GitHub secrets 放 `NPM_TOKEN` → 打 `v*` tag 触发 publish job。
- **GitHub 仓库设置**：开启 "Private vulnerability reporting"。
- **覆盖率徽章**：CI（ubuntu Node24）跑出后按其 "all files" line% 再校。

## 发现待办（不阻塞，回头清）

- lint 6 个警告（存量）：4 个未用变量 + `tests/store.test.ts` 两处 `@ts-expect-error` 缺说明。松档已降 warn、不阻断；清理时给未用变量加 `_` 前缀 / 给 ts-comment 补一句说明即可。
- README `Node ≥24` 徽章与 `engines>=20` 口径可再对齐（≥24 是零依赖路径，20/22 走可选 `better-sqlite3`）。

## 后续总排序

第 5 步 → … → 第 10 步收口 1.0，商用线 + 功能线合排共 11 步，见 [`docs/internal/tasks/后续批次总纲.md`](./docs/internal/tasks/后续批次总纲.md)——每步开工前才细化成施工任务书。
