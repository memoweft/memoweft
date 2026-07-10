# 运行记录 — 检索三臂消融（Phase 1 §14.6）

可复现记录（评测执行员职责）。本次运行**纯离线、确定性**，无 LLM 调用、无 judge、无网络、无 token 消耗。

## 命令

```
node bench/eval-retrieval.mjs --ablation
```

回归自检（默认路径数字须与 committed retrieval-baseline.md 逐位一致）：

```
node bench/eval-retrieval.mjs   # 仅 commit/时间/latency 变，全部指标不变
```

## 环境

| 项 | 值 |
| --- | --- |
| commit | `6221364` |
| Node | v24.15.0 |
| 平台 | win32/x64 |
| 生成时间 | 2026-07-10T03:45:45Z |
| topK | 10 |
| 黄金集 | tests/retrieval/golden.json（36 cognition / 65 case） |
| 模型/用量 | 无（三臂均确定性离线：HashEmbedder / FTS5 BM25 / RRF；0 token、0 网络） |
| 真实臂 | opt-in（EVAL_REAL_ARM=1 + .env MEMOWEFT_EMBED_* + 联网），默认离线 — pending，待联网 nightly |

## 确定性自检

三臂各跑两遍、指标逐位相等：vector-only ✓ / keyword-only ✓ / hybrid ✓。

## 成绩（overall Recall@5 / Hit@5 / MRR@10）

| 臂 | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | --- |
| vector-only | 0.7154 | 0.7692 | 0.6608 |
| keyword-only | 0.0462 | 0.0462 | 0.0462 |
| hybrid | 0.7154 | 0.7692 | 0.6608 |
| Δ hybrid−vector | +0.0000 | +0.0000 | +0.0000 |

按 kind（Recall@5，vec / kw / hyb / Δ）：direct 0.9615 / 0.1154 / 0.9615 / +0.0000 · paraphrase 0.5000 / 0.0000 / 0.5000 / +0.0000 · multihop 0.6333 / 0.0000 / 0.6333 / +0.0000。

9 条 2 字中文子集（Recall@5，vec / kw / hyb）：1.0000 / 0.0000 / 1.0000。

## 关键事实（诊断）

- keyword-only 仅在 3/65 条 case 上有候选（G-006 后端工程师 / G-007 独立游戏开发 / G-012 远程办公，均为整条 query 恰是某 doc 子串的关键词式 direct）。
- hybrid 与 vector-only 的 top5 在 65/65 条 case 上逐 case 相同 → RRF 在本离线配置下是 no-op。

## +10% 判定

基线 overall Recall@5 = 0.7154 → 目标 ≥0.7869（+10%）；确定性 hybrid = 0.7154 → **未达标 ✗**（缺口 0.0715）。
确定性 hybrid 天花板 = vector-only 基线；+10% 与 paraphrase/en 语义/跨语言缺口的达成主要落在真实嵌入臂，待联网 nightly 补测。

## 产物

- 报告：`bench/retrieval-after.md`
- 脚本：`bench/eval-retrieval.mjs`（新增 `--ablation` 分支；默认 vector-only 基线行为不变）
