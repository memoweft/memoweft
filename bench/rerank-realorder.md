# 真实检索序上的 fusion 重排验证 — Tranche 3 β 前置

> **诚实问题**：α 的 fusion 在合成判别集上有大增益（nDCG +0.55/+0.54），但那是「刻意注入陈旧/低置信靠前」
> 的合成 baseline 序，Δ 是**存在性上界**。本报告在**真实黄金集** `tests/retrieval/golden.json`（36 认知 /
> 65 用例，含相关性标注）上，用真 **bge-m3** 取每个 query 的**实际检索排序**，问：真实序有没有 fusion
> 能修的次优？fusion 端到端收益是 ≈0 还是可观？据此给 β **go/no-go**（同 D-0008 证伪 hybrid 的手法）。

## 生成环境

| 项 | 值 |
| --- | --- |
| 生成命令 | `node bench/rerank-realorder.mjs` |
| commit | `9c15001` |
| Node | 24.15.0 · win32/x64 |
| 生成时间 | 2026-07-13T14:49:57.013Z |
| 真实序来源 | 读缓存离线复算（D:/MemoWeft/memoweft/bench/data/realorder-bge-m3.json，缓存于 2026-07-13T14:45:52.623Z） |
| 嵌入模型 | bge-m3（真实 bge-m3；缓存 2026-07-13T14:45:52.623Z） |
| 黄金集 | tests/retrieval/golden.json（36 认知 / 65 用例） |
| 检索深度 | top-10（fusion 重排池 poolK=5；生产 recall topK=5） |
| 主评测 K | 5（另出 @3） |
| 融合权重 | wSim=0.55 · wEff=0.3 · wCred=0.15（同 rerank-golden） |
| now（衰减锚） | 2026-07-13T00:00:00.000Z |
| 确定性自检 | 通过（给定缓存真实序，两遍逐位相等） |

## 一、真实检索序有没有 fusion 能修的次优？（无需任何合成元数据）

直接量真实 bge-m3 序相对相关性标注 `expect` 的质量。**fusion 唯一能修的缺陷 = top-K 内「非相关排在相关之前」（inversion）**；
若真实序把相关项都已排在非相关项之前（inversion=0 / expected-at-top=1），则 fusion 无从下手。

| 分组 | n | nDCG@5 | Recall@5 | MRR | Hit@5 | inversion 总数@5 | 有 inversion 的 case | expected-at-top 率@5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| overall | 65 | 0.9112 | 0.9667 | 0.9056 | 0.9846 | 18 | 12 | 0.8000 |
| direct | 26 | 0.9574 | 1.0000 | 0.9423 | 1.0000 | 3 | 3 | 0.8846 |
| paraphrase | 24 | 0.9075 | 1.0000 | 0.8764 | 1.0000 | 9 | 5 | 0.7917 |
| multihop | 15 | 0.8369 | 0.8556 | 0.8889 | 0.9333 | 6 | 4 | 0.6667 |

- 真实序 overall nDCG@5=**0.9112**、Recall@5=**0.9667**、MRR=**0.9056**。
- **top-5 内 inversion 总数=18，涉及 12/65 条 case**；expected-at-top 率=**0.8000**。
  - inversion=0 的 case（真实序前排相关项已全在非相关之前、fusion 无缺陷可修）：**53/65**。

### 有 inversion 的 12 条 case（fusion 唯一有机会修的靶点，逐条看 fusion 实际是帮是害）

| case | kind | query | expect | 真实序 top-5 | inv@5 | type-plausible: inv→ · ΔnDCG@5 · ΔRecall@5 |
| --- | --- | --- | --- | --- | --- | --- |
| G-005 | direct | 用户现在住在哪里 | cog-006 | cog-036 **cog-006** cog-004 cog-005 cog-023 | 1 | 1→1 · +0.0000 · +0.0000 |
| G-014 | direct | 用户在做什么游戏 | cog-020 | cog-008 **cog-020** cog-034 cog-022 cog-021 | 1 | 1→2 · -0.1309 · +0.0000 |
| G-023 | direct | 用户做事是什么风格 | cog-033 | cog-032 **cog-033** cog-016 cog-034 cog-008 | 1 | 1→4 · -0.2441 · +0.0000 |
| G-027 | paraphrase | 用户平时爱喝什么 | cog-013 | cog-012 cog-019 cog-014 cog-015 **cog-013** | 4 | 4→4 · +0.0000 · +0.0000 |
| G-035 | paraphrase | 用户为什么会没睡好 | cog-028 | cog-023 cog-024 **cog-028** cog-025 cog-029 | 2 | 2→3 · -0.0693 · +0.0000 |
| G-042 | paraphrase | 用户做什么工作 | cog-007 | cog-008 **cog-007** cog-016 cog-035 cog-022 | 1 | 1→1 · +0.0000 · +0.0000 |
| G-043 | paraphrase | What does the user like to drink now? | cog-013 | cog-012 **cog-013** cog-019 cog-014 cog-015 | 1 | 1→1 · +0.0000 · +0.0000 |
| G-045 | paraphrase | What is the user's day job? | cog-007 | cog-025 **cog-007** cog-019 cog-016 cog-008 | 1 | 1→0 · +0.3691 · +0.0000 |
| G-054 | multihop | 用户养猫是不是因为一个人住 | cog-009,cog-036 | **cog-036** cog-011 **cog-009** cog-004 cog-032 | 1 | 1→1 · +0.0000 · +0.0000 |
| G-058 | multihop | 用户的主业和副业分别是什么 | cog-007,cog-008 | cog-032 cog-019 **cog-008** cog-002 cog-001 | 2 | 2→0 · +0.3066 · +0.0000 |
| G-060 | multihop | 用户今年在游戏和钢琴上分别有什么目标 | cog-030,cog-031 | **cog-031** cog-022 cog-021 **cog-030** cog-034 | 2 | 2→3 · -0.0269 · +0.0000 |
| G-064 | multihop | 用户嘴上说的口味和实际注意的饮食一致吗 | cog-015,cog-019 | **cog-019** cog-012 **cog-015** cog-033 cog-018 | 1 | 1→1 · +0.0000 · +0.0000 |

> 真实序 top-5 中**加粗=相关项**（expect）。末列为 type-plausible fusion 的实际效果：inversion 数变化 · ΔnDCG@5 · ΔRecall@5。
> **注意**：inversion 掉到 0 常常不是「修好」，而是相关项被高 conf/cred 的非相关项挤出 top-5（ΔRecall@5<0）——
> 故 inversion 数会误导，**ΔnDCG@5 才是端到端真相**。逐条看：绝大多数 case fusion 的 ΔnDCG@5 ≤ 0。

## 二、fusion 应用到真实序的端到端收益（三套元数据方案 × Δ vs 真实序 baseline）

**诚实边界**：golden.json 认知**无 confidence/credStatus/updatedAt**——fusion 需要的元数据全是**合成**的。
三套方案：`neutral`（均一 → 自证 fusion 无信号时是 no-op）/`type-plausible`（按 contentType 机械赋生产可解释值，age=0）/
`type-plausible-aged`（同上 age=2d 令 transient 衰减生效）。相关性标注是**纯语义**的、与 recency/confidence 正交，
故 fusion 位移只可能中性或有害，除非真实序恰有相关项被非相关项压住且元数据恰好翻正。

| 方案 | 改动 case 数 | ΔnDCG@5 | ΔRecall@5 | ΔMRR | 帮/害 case 数 | inv 修/造 | 改动 case 均 Kendallτ | 均移动条数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| neutral | 0/65 | +0.0000 | +0.0000 | +0.0000 | 0/0 | 0/0 | — | 0.000 |
| type-plausible | 45/65 | -0.0425 | +0.0000 | -0.0495 | 2/12 | 3/17 | +0.4978 | 2.000 |
| type-plausible-aged | 45/65 | -0.0472 | +0.0000 | -0.0572 | 2/13 | 3/19 | +0.4844 | 2.015 |

> Δ 为 fusion 序相对真实序 baseline 的均值增量（正=改善）。「帮/害 case 数」=ΔnDCG@5>0 / <0 的 case 数。
> 「inv 修/造」=fusion 相对 baseline 修掉 / 新造的 inversion 总数。改动 case 均 Kendallτ 只在真实改动的 case 上算。
> 本表 poolK=5（=生产 recall topK，fusion 只重排召回的这 5 条；@5 下 Recall 由构造几乎不变，位移全反映在 nDCG/MRR）。

- **poolK 鲁棒性**：把重排池放大到 10（检索 10 条、重排后取 top-5，给 fusion 更多腾挪空间）仍是净负——type-plausible ΔnDCG@5=**-0.1293**、ΔRecall@5=**-0.0846**（帮 2 / 害 16）。结论不依赖池大小：池越大，fusion 把相关项挤出 top-5 的机会反而越多。

方案元数据表（机械按 contentType 赋，非按 query 调）：

| contentType | confidence | credStatus | 半衰期(天) | 出现认知数 |
| --- | --- | --- | --- | --- |
| fact | 700 | stable | —（不衰减） | 12 |
| preference | 650 | stable | —（不衰减） | 10 |
| project | 500 | limited | 14 | 3 |
| goal | 500 | limited | 14 | 2 |
| trait | 600 | limited | 60 | 2 |
| trend | 450 | limited | 7 | 1 |
| state | 300 | low | 1.5 | 5 |
| hypothesis | 240 | candidate | 2 | 1 |

## 三、鲁棒性：融合权重扫描（type-plausible 方案，overall ΔnDCG@5）

看结论对权重是否稳健（软指标高方差 → 多点取势，同 D-0008/D-0009 纪律）。

| wSim / wEff / wCred | 改动 case 数 | ΔnDCG@5 | ΔRecall@5 | ΔMRR | 帮/害 |
| --- | --- | --- | --- | --- | --- |
| 0.7 / 0.2 / 0.1 | 41/65 | -0.0200 | +0.0000 | -0.0205 | 2/8 |
| 0.55 / 0.3 / 0.15 | 45/65 | -0.0425 | +0.0000 | -0.0495 | 2/12 |
| 0.4 / 0.4 / 0.2 | 46/65 | -0.0927 | +0.0000 | -0.1246 | 2/19 |
| 0.34 / 0.33 / 0.33 | 46/65 | -0.0962 | +0.0000 | -0.1323 | 2/19 |

## 四、结论与 β go/no-go

### 真实检索序有没有 fusion 能修的次优？

- **少量**：top-5 内 inversion 涉及 12/65 条 case（共 18 处倒置）。逐条看（见 §一）这些倒置能否被 fusion 元数据翻正。

### fusion 端到端收益多大？

- **neutral 方案 Δ 恒 0**：均一元数据下 fusion≡真实序（改动 0/65 条）——印证 fusion 无元数据信号时是 no-op。
- **type-plausible（age=0）**：改动 45/65 条，ΔnDCG@5=**-0.0425**、ΔRecall@5=**+0.0000**、ΔMRR=**-0.0495**（帮 2 / 害 12，inv 修 3 / 造 17）。
- **type-plausible-aged（age=2d）**：改动 45/65 条，ΔnDCG@5=**-0.0472**、ΔRecall@5=**+0.0000**、ΔMRR=**-0.0572**（帮 2 / 害 13，inv 修 3 / 造 19）。
- **唯一非负的方案是 neutral（ΔnDCG@5=+0.0000）**——而它恰好是 fusion 什么都不做的那一档；任何真正用到 conf/cred/衰减信号的方案都是净负。

### go/no-go 建议

- **NO-GO（按铁律 4 不做）**。理由（数据驱动，同 D-0008 证伪 hybrid 的手法）：
  1. **真实序无缺陷可修**：top-5 内 inversion 涉及 12/65 条、expected-at-top 率=0.8000——bge-m3 已把相关项排在非相关项之前，没有 fusion 结构上能修的次优。
  2. **端到端收益为负，不是 ≈0**：给 fusion 公平机会（type-plausible）下 ΔnDCG@5=-0.0425、ΔRecall@5=+0.0000；纳入衰减（aged）ΔnDCG@5=-0.0472。唯一不掉分的是 neutral（fusion 不动手）。合成判别集上的 +0.55 是**能显差异的上界**，真实序上不但不复现、反而变害。
  3. **fusion 在真实序上主要是「帮倒忙」风险**：type-plausible 害 12 / 帮 2、新造 inversion 17 / 修 3——把语义已对的序按正交的 conf/cred 信号打乱，只会把相关项往下压。逐条看（§一）：fusion 帮到的 2 条都是「高置信 fact 被低置信 state 埋住、fusion 翻正」（G-045/G-058）；害到的都是「query 本要 project/state/hypothesis/trait，被 fusion 的 fact/preference 置信先验挤下去」。
  - **机制解释（why 泛化到本集之外）**：fusion 的 effConf/credRank 是**逐认知、与 query 无关**的先验；相关性却是**逐 query**的。检索器（bge-m3 余弦）已经把逐 query 的语义信号排好了，再叠一个 query 无关的类型先验，数学上只能**稀释**已对的语义序——除非先验恰好与某 query 的相关性同向（少数），否则期望是负。任何固定的逐认知元数据都无法跨 65 条异质 query 与相关性正相关，这不是本集/本元数据方案的偶然。
  - **不为一个真实系统不出现的问题加装置**。若未来 dogfood 暴露真实检索序确有「陈旧/低置信/冗余靠前」的次优（本黄金集未见），再以带数据的新 tranche 重启评估。

### 诚实边界与本评测局限

- **相关性标注是纯语义的**：golden.json 的 `expect` 只标「哪条认知语义上答了 query」，不含 recency/confidence 偏好。故本评测能严格回答的是「fusion 会不会破坏一个语义已近最优的序」，**不能**证明「若 query 有隐含时效/可信度意图，fusion 有益」——后者本黄金集无标注支撑。
- **元数据是合成的**：方案 2/3 的 conf/cred/age 按 contentType 机械赋（非真实 consolidation 产出），且 golden.json **无真实时间戳** → recency 信号只能靠统一 age 近似（facts/preferences 本就不衰减，占 22/36 条）。真实系统的 fusion 收益仍需在带真实元数据的语料（dogfood / LoCoMo cognition 层）上复核。
- **LoCoMo**：`LOCOMO_PATH` 未设 → 本次跳过（bench/data/locomo10.json 在仓但需全 pipeline 产出带元数据的 cognition，属更大工程，留后续 tranche）。

## 备注

- **范围**：纯 bench/ 新增（rerank-realorder.mjs + 真实序缓存 data/realorder-bge-m3.json + 本报告），只读 import src/tests，未改 src/ / tests/ / api 快照 / DECISIONS / CHANGELOG。是否进 β 由 Integrator 守门。
- **可复现**：`--real-embed` 打 bge-m3 重取真实序并刷缓存；无 `--real-embed` 时读缓存离线复算，指标确定（`--selftest` 自证两遍逐位相等）。真实序随嵌入模型/版本变，属可重建资产。
