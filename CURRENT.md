# CURRENT.md · 当前任务白板

> 唯一的"现在该做什么"看板。只写**当前主线 + 允许做 + 不做 + 验收**。历史不写这儿——看 git 提交与 `CHANGELOG.md`。

## 当前主线：Memory Surface Contract v1（记忆面契约 v1）· 总纲第 2 步

把宿主接触记忆的公共接口（`src/index.ts` 171 个导出、`createMemoWeftCore` 门面、`core.*` 24 个宿主接触方法的入参与返回形状）定成一份带稳定性标签的契约——哪些 stable、哪些 experimental、破坏性变更怎么算怎么通知。**只加文档 + 类型/导出标注，不动核心运行时逻辑。** 是第 7 步插件契约、第 10 步 1.0 API 收口的地基。

**施工任务书两份，在 [`docs/internal/tasks/S2-surface-contract/`](./docs/internal/tasks/S2-surface-contract/README.md)**（S2-1 定级 + 写契约文档 → S2-2 `index.ts` 分组注释 + 政策成文，串行）。6 项设计选择作者已拍板（见 README）。

## 允许做

- S2-1 / S2-2 两份任务书写明的事，按"改哪里 / 不许动 / 验收"执行。
- 契约文档产出到对外 `docs/memory-surface-contract.md`（作者拍板 ⑤）。

## 不做（本主线明确不碰）

- ❌ 删任何导出、重排导出结构、逐符号铺 `@stable` JSDoc（属第 10 步 1.0 收口）。
- ❌ 改任何入参 / 返回的实际形状、运行时逻辑、认知纪律、隐私三红线。
- ❌ 动 config 单例实现（P2-5 去单例是另一条线；本步只在契约里把它的取用方式标 experimental）。
- ❌ `DLA_*` 回退、`'./dla.db'` 默认路径、两处现有 `@deprecated`——不许动。

## 验收

- 两份任务书的验收清单全勾 + `npm run typecheck && npm test && npm run build` 三绿（本步不改运行时，三绿应无实质变化，跑是为兜"没误碰"）。

---

## 已完成上一主线：0.3.0 补漏加固批次 ✅（2026-07-05 · 已发布 memoweft@0.3.0）

T1–T7（隐私 B 下沉 put 层 / 发布保险丝 / JSON 加固 / busy_timeout / 扫尾 / 驱动抽缝 + better-sqlite3 可选 / 全开源承诺）已合 main（merge `758c129`）+ 发布 npm（`0.3.0` latest）。**发布尾巴（作者手动）**：main 推 origin、打 tag `v0.3.0`、建 GitHub Release；CI 矩阵在真 Node 20/22 上验 better-sqlite3 路径（本机仅 Node 24，那条支持声明靠 CI 兜底）。

## 后续总排序

第 2 步（当前）→ … → 第 10 步收口 1.0，商用线 + 功能线合排共 11 步，见 [`docs/internal/tasks/后续批次总纲.md`](./docs/internal/tasks/后续批次总纲.md)——每步开工前才细化成施工任务书（`S2-surface-contract/` 这套即样板）。
