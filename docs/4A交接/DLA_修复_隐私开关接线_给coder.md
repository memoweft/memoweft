# MemoWeft 修复 · 隐私开关接线（让 allowCloudRead 真生效）· 给 Coder

> **来源**：产品经理审查（2026-07-01）发现，经所有者确认列入"现在要改"。
> **纪律**：给方向 + 影响面，不写死代码；碰核心处标注；带前提的判断标明前提（沿用 `AGENTS.md`）。

---

## 1. 问题（为什么改）

证据上有"准不准上云"的授权位 `allowCloudRead`，但**把证据喂给云端模型的三个写路径都不看它**——开关形同虚设。
现在没出事（目前证据全是对话 `spoken`、默认 `allowCloudRead=true`），但 **4-A 一引入"默认不上云"的 `observed` 行为证据，数据会照样被送上云**。这是"隐私本地优先"的根基，**4-A 前必修**。

## 2. 现状（基于代码，行号供定位）

- 字段在：`evidence/model.ts:35` `allowCloudRead`；默认跟随 `privacyMode`（`config.ts:108`、`store.ts:130`）。
- 三处"取证据 → 喂云端 LLM"之间**无过滤**：
  - `distillation/distill.ts`：`evidenceStore.all()`(:33) → 喂 LLM(:48)
  - `consolidation/consolidate.ts`：`evidenceOf`/`get`(:142-147) → 喂 LLM(:153)
  - `attribution/attribute.ts`：只按 `allowInference` 过滤(:141)、**未**按 `allowCloudRead` → 喂 LLM(:155)
- 全项目搜 `allowCloudRead` 仅命中证据层，无任何下游使用。

## 3. 方案（推荐：最小版）

**最小版**：在上述三处"取证据 → 喂 LLM"之间，加一道过滤，只保留 `allowCloudRead=true` 的证据再喂。
- **现在的效果**：所有证据 `cloud=true`，筛子筛不掉任何东西，**行为完全不变（零风险）**。
- **4-A 后的效果**：`observed`（默认 `cloud=false`）被自动挡在云端写路径外——正是隐私要的。
- **做成一个共用小函数**（如 `filterCloudReadable(evidences)`，放 `evidence/` 或公共 util），三处共用，别各写一遍——好维护、对实现也省事。

**必须带的前提注释**（重要，符合"别把临时判断写成永久死规则"）：
> 该过滤**假设 `deps.llm` 是云端模型**。当写路径接入本地模型（3090）时，本地模型可读 `cloud=false` 的证据，需把过滤改成"按当前模型是云端/本地决定是否筛"。在过滤处注释写明这一点。

**完整版（不在本次，留给"上本地模型"任务）**：给 `LLMClient` 加 `'cloud' | 'local'` 标识，过滤按 tier 决定（云端筛、本地放行）。本次**不做**——避免现在就多碰 `LLMClient` 这个核心。

## 4. 影响面清单

| 动作 | 文件 | 备注 |
|---|---|---|
| ✏️ 改 | `distillation/distill.ts`、`consolidation/consolidate.ts`、`attribution/attribute.ts` | 各加一道 `allowCloudRead` 过滤 |
| 🆕 新增 | 一个共用过滤小函数 | 三处共用 |
| 🚫 不碰 | `LLMClient` 抽象、证据存储层 | 最小版不动它们 |
| ✅ 测试 | 新增用例 | 见验收 |

## 5. 一个所有者要知道的后果

接线之后，`observed` 证据（4-A 默认 `cloud=false`）用**云端** consolidate/attribute 时会被挡住 → 它**变不成画像**，除非走本地模型。
所以这一步修完，**4-A 交接文档 §6 的"折中 A（只验到归因）还是折中 B（临时借云端、代码标临时）"那个决定就该正式摆上台面**——但那是 4-A 的事，等所有者说继续 4-A 时再定。

## 6. 验收标准

- [ ] 三处喂云端前按 `allowCloudRead` 过滤，且共用同一个函数。
- [ ] 自动化测试：`cloud=false` 的证据不会进喂给 LLM 的 prompt；`cloud=true` 的照常进。
- [ ] 现有 42 测试 + `typecheck` + `build` 全绿（对现有 `spoken` 证据行为不变）。
- [ ] 过滤处带"假设云端、上本地模型时需改"的前提注释。

## 7. 纪律提醒

- 最小版只加过滤、不动 `LLMClient`，碰核心很轻。
- 注释标明前提，别让"假设云端"变成永久死规则。
- 这是 4-A 的前置必修；改完再继续 4-A 档 1。
