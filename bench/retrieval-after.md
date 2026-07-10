# 检索三臂消融报告 — Phase 1 §14.6

> vector-only / keyword-only / hybrid 三臂在黄金集上的对比，量 **hybrid 相对 vector-only
> 基线的增益**——判断是否值得把 hybrid 接进公共 API（§14.4b）的依据。所有确定性臂数字可由
> 生成命令逐位复现（HashEmbedder/BM25/RRF 均确定，无网络、无随机、无系统时钟）。

## 生成环境

| 项 | 值 |
| --- | --- |
| 生成命令 | `node bench/eval-retrieval.mjs --ablation` |
| commit | `6221364` |
| Node | 24.15.0 |
| 平台 | win32/x64 |
| 生成时间 | 2026-07-10T03:48:43.601Z |
| topK | 10 |
| 黄金集 | tests/retrieval/golden.json（36 条 cognition，65 条 case） |
| 确定性自检 | vector-only ✓ / keyword-only ✓ / hybrid ✓（三臂各两遍逐位相等） |
| 真实臂 | opt-in 请求但调用失败（fetch failed）— pending，待联网 nightly |

三臂定义：

- **vector-only**：VectorRetriever（余弦）+ HashEmbedder（dim=256，确定性词袋哈希 + char-bigram）
- **keyword-only**：KeywordRetriever（FTS5 trigram 分词 + BM25 排序，纯词面/子串信号，无嵌入）
- **hybrid**：HybridRetriever（RRF 融合 vector+keyword，kCandidate=50，rrfK=60）

## 一、三臂对比总表（overall）

| 臂 | n | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | --- | --- |
| vector-only | 65 | 0.7154 | 0.7692 | 0.6608 |
| keyword-only | 65 | 0.0462 | 0.0462 | 0.0462 |
| hybrid | 65 | 0.7154 | 0.7692 | 0.6608 |
| **Δ hybrid−vector** | — | +0.0000 | +0.0000 | +0.0000 |

- hybrid overall Recall@5 = **0.7154**，vector-only = **0.7154**，Δ = **+0.0000**。
- keyword-only 仅在 **3/65** 条 case 上有候选（G-006, G-007, G-012）——FTS5 trigram phrase-match 需整条 query 连续命中某 doc，自然语言/2 字中文 query 多数空召回。
- hybrid 与 vector-only 的 top5 在 **65/65** 条 case 上逐 case 相同（**全同** → RRF 在本离线配置下是 no-op，详见「六、诚实结论」）。

## 二、按 kind 分组三臂对比（重点）

### kind = direct

| 臂 | n | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | --- | --- |
| vector-only | 26 | 0.9615 | 0.9615 | 0.9269 |
| keyword-only | 26 | 0.1154 | 0.1154 | 0.1154 |
| hybrid | 26 | 0.9615 | 0.9615 | 0.9269 |
| **Δ hybrid−vector** | — | +0.0000 | +0.0000 | +0.0000 |

### kind = paraphrase

| 臂 | n | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | --- | --- |
| vector-only | 24 | 0.5000 | 0.5000 | 0.3444 |
| keyword-only | 24 | 0.0000 | 0.0000 | 0.0000 |
| hybrid | 24 | 0.5000 | 0.5000 | 0.3444 |
| **Δ hybrid−vector** | — | +0.0000 | +0.0000 | +0.0000 |

### kind = multihop

| 臂 | n | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | --- | --- |
| vector-only | 15 | 0.6333 | 0.8667 | 0.7056 |
| keyword-only | 15 | 0.0000 | 0.0000 | 0.0000 |
| hybrid | 15 | 0.6333 | 0.8667 | 0.7056 |
| **Δ hybrid−vector** | — | +0.0000 | +0.0000 | +0.0000 |

**Δ(hybrid − vector) 按 kind 汇总**（看 hybrid 在各 kind 抬了多少）：

| kind | ΔRecall@5 | ΔHit@5 | ΔMRR@10 |
| --- | --- | --- | --- |
| direct | +0.0000 | +0.0000 | +0.0000 |
| paraphrase | +0.0000 | +0.0000 | +0.0000 |
| multihop | +0.0000 | +0.0000 | +0.0000 |

## 三、按语言分组三臂对比（query 含 CJK=zh，否则 en）

### lang = zh

| 臂 | n | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | --- | --- |
| vector-only | 57 | 0.8099 | 0.8596 | 0.7491 |
| keyword-only | 57 | 0.0526 | 0.0526 | 0.0526 |
| hybrid | 57 | 0.8099 | 0.8596 | 0.7491 |
| **Δ hybrid−vector** | — | +0.0000 | +0.0000 | +0.0000 |

### lang = en

| 臂 | n | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | --- | --- |
| vector-only | 8 | 0.0417 | 0.1250 | 0.0313 |
| keyword-only | 8 | 0.0000 | 0.0000 | 0.0000 |
| hybrid | 8 | 0.0417 | 0.1250 | 0.0313 |
| **Δ hybrid−vector** | — | +0.0000 | +0.0000 | +0.0000 |

## 四、9 条纯 2 字中文子集三臂表现

子集：G-004/G-008/G-009/G-010/G-013/G-015/G-016/G-018/G-019（纯 2 字 direct）。
预期：vector 靠 char-bigram 兜住（≈1.0）；keyword 因 FTS5 trigram 需 ≥3 字符、够不着 2 字词（≈0）；
hybrid 看是否被 keyword 的空召回拖累、还是仍由 vector 兜住。

| 臂 | n | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | --- | --- |
| vector-only | 9 | 1.0000 | 1.0000 | 1.0000 |
| keyword-only | 9 | 0.0000 | 0.0000 | 0.0000 |
| hybrid | 9 | 1.0000 | 1.0000 | 1.0000 |
| **Δ hybrid−vector** | — | +0.0000 | +0.0000 | +0.0000 |

| case | query | expect | vec Hit@5 | kw Hit@5 | hyb Hit@5 | hyb firstRank |
| --- | --- | --- | --- | --- | --- | --- |
| G-004 | 搬家 | cog-005 | ✓ | ✗ | ✓ | 1 |
| G-008 | 团子 | cog-009, cog-010 | ✓ | ✗ | ✓ | 1 |
| G-009 | 咖啡 | cog-013, cog-014 | ✓ | ✗ | ✓ | 1 |
| G-010 | 口味 | cog-015 | ✓ | ✗ | ✓ | 1 |
| G-013 | 饮食 | cog-019 | ✓ | ✗ | ✓ | 1 |
| G-015 | 吉他 | cog-021 | ✓ | ✗ | ✓ | 1 |
| G-016 | 睡眠 | cog-024 | ✓ | ✗ | ✓ | 1 |
| G-018 | 疲惫 | cog-025 | ✓ | ✗ | ✓ | 1 |
| G-019 | 烦躁 | cog-026 | ✓ | ✗ | ✓ | 1 |

- vector-only 子集 Recall@5 = **1.0000**，keyword-only = **0.0000**，hybrid = **1.0000**。
- **hybrid 未被 keyword 的空召回拖累**：RRF 融合下 keyword 对 2 字词无候选贡献，vector 的名次照常进入融合，hybrid 仍靠 vector 兜住这组（子集内 keyword-only Recall@5=0.0000，印证 trigram 够不着 2 字词）。

## 五、对 +10% 目标的判定

- 基线 overall Recall@5 = **0.7154**（committed retrieval-baseline.md，vector-only）。
- +10% 目标线 = 0.7154 × 1.10 = 0.78694 → **≥ 0.7869**。
- 本次确定性 hybrid overall Recall@5 = **0.7154**。
- **判定：未达标 ✗** — 确定性 hybrid（0.7154）< 目标（0.7869），缺口 **0.0715**。
- **+10% 的达成主要落在真实嵌入臂**，待联网 nightly 补测（见下「诚实结论」）。不粉饰：确定性两臂都是词面/子串信号，抬不动语义/跨语言缺口。

## 六、诚实结论

- **确定性 hybrid ≡ vector-only 基线（零增益）**：三臂对比中 hybrid 相对 vector 的 Recall@5 Δ 在 overall 及全部 kind/lang 分组上均为 **+0.0000**，且两者 top5 在全部 65 条 case 上逐 case 相同——RRF 融合在本离线配置下是 **no-op**。这是如实测量结果，未凭空造增益、也未掩盖。
- **为什么是 no-op**：keyword-only 仅在 3/65 条 case 上有候选（G-006, G-007, G-012，均为「整条 query 恰是某 doc 子串」的关键词式 direct），且这几条命中的 doc 恰是 vector 已排 #1 的 doc → RRF 只是叠加确认，top5 不变；其余 case keyword 空召回，hybrid 完全退化为 vector。
- **确定性 hybrid 的天花板 = vector-only 基线**：keyword（FTS5 trigram/BM25）与 HashEmbedder（char-bigram）两条离线臂**本质同源**——都靠字面/子串重叠，且 keyword 能命中处 vector 必也命中；RRF 只能重排两臂各自能召回的 doc，无法凭空生出 vector 之外的召回，故在此黄金集上抬不动任何一格。
- **paraphrase 与 en 是语义/跨语言缺口，确定性 hybrid 抬不动**：paraphrase vector 基线仅 **0.5000**（Δhyb=+0.0000）、en vector 基线仅 **0.0417**（Δhyb=+0.0000）——换词/近义/翻译后词面重叠稀薄，keyword 与 HashEmbedder 两条**词面/子串信号**都够不着。
- **语义/跨语言缺口需真实嵌入臂**（pending）：设 `EVAL_REAL_ARM=1`、配 `.env` 的 `MEMOWEFT_EMBED_*` 并联网后，用真实嵌入替换 HashEmbedder 通道（real-vector + real-hybrid），才可能补 paraphrase/en。**待联网 nightly 补测**。
- **+10% 目标落在真实臂**：确定性 hybrid（overall Recall@5=0.7154）**未达** ≥0.7869（缺口 0.0715）；本报告不将其记为已达标，+10% 的达成主要落在真实嵌入臂，待联网 nightly 补测。

## 备注

- **确定性臂**：vector-only（HashEmbedder）/ keyword-only（FTS5 BM25）/ hybrid（RRF），全部离线、无网络、无随机、无系统时钟；每个数字可由生成命令逐位复现。
- **真实臂仍 opt-in**：opt-in 请求但调用失败（fetch failed）— pending，待联网 nightly
- **API 决策交回 Integrator 守门**：本报告只呈现数据（增益 Δ、+10% 判定），是否把 hybrid 接进公共 API（§14.4b）由 Integrator 裁决，评测不代拍板。
- 默认 `node bench/eval-retrieval.mjs` 仍产出 vector-only 基线（retrieval-baseline.md），行为与数字不变。
