# CURRENT.md · 当前任务白板

> 唯一的"现在该做什么"看板。只写**当前主线 + 允许做 + 不做 + 验收**。历史不写这儿——看 git 提交与 `CHANGELOG.md`。

## 当前主线：0.3.0 — 补漏加固批次（先行）

2026-07-04 全库审计抓出的红灯修复：隐私红线 B 下沉 core、发布保险丝、JSON 解析统一、busy_timeout、扫尾、SQLite 驱动抽缝（Node 20/22 触达）、全开源承诺。**任务书七份，在 [`docs/internal/tasks/0.3.0/`](./docs/internal/tasks/0.3.0/README.md)，按总览顺序领任务，一次领一份。**

排序依据（作者 2026-07-04 拍板）：补漏是小刀先行；原 0.3.0 主线 **Memory Surface Contract v1** 顺延为下一主线（见文末），本批不做。

## 允许做

- 七份任务书（T1–T7）写明的事，按任务书的"改哪里 / 不许动 / 验收"执行。
- 任务书标注"随车快赢"的小项。

## 不做（本批明确不碰）

- ❌ Memory Surface Contract v1 的接口重设计（顺延下一主线；T1/T4 涉及的行为变化以任务书为限）。
- ❌ 完整插件平台 / weftmate / daemon / schema 重构 / tool 权限模型。
- ❌ WAL、加密落盘、投毒防护、成本观测、i18n、MCP、eval——属后续批次。
- ❌ 任务书之外动 `src/` 核心逻辑或认知纪律。

## 验收

- 每任务对应任务书的验收清单全勾 + `npm run typecheck && npm test && npm run build` 三绿。
- 全批合完跑 `docs/internal/tasks/0.3.0/README.md` 的"批次完成验收"。

---

## 下一主线（排队中）：Memory Surface Contract v1

把宿主接触记忆的公共接口（`src/index.ts` 导出、`createMemoWeftCore`、`core.recall / ingest* / memory.*` 的入参与返回形状）定成一份写清楚、带版本的稳定契约——哪些字段算稳定、破坏性变更怎么算。子任务等补漏批次合完由 PM 拍板细化。

再往后的完整步骤（第 2 步接口契约 → … → 第 10 步收口 1.0，商用线+功能线合排共 11 步）见 [`docs/internal/tasks/后续批次总纲.md`](./docs/internal/tasks/后续批次总纲.md)——每步开工前才细化成施工任务书。
