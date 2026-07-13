# 重排判别评测报告 — Tranche 3 α

> 在**判别性**黄金集 `bench/rerank-golden.json` 上量化两种确定性重排相对「检索器原序（baseline）」
> 的可测收益：**A·MMR 多样性** / **B·score 融合**。现有 `tests/retrieval/golden.json` 上检索原序已近最优
> （hybrid 零增益，见 `bench/retrieval-after.md`），显不出重排差异；本集专补这一判别缺口——刻意构造
> 「检索器原序次优、可被重排修复」的用例。全部离线确定（HashEmbedder + 生产 effectiveConfidence，
> now 由 golden 固定注入），数字可由生成命令逐位复现。

## 生成环境

| 项 | 值 |
| --- | --- |
| 生成命令 | `node bench/rerank-eval.mjs --real-embed` |
| commit | `872c0f3` |
| Node | 24.15.0 · win32/x64 |
| 生成时间 | 2026-07-13T11:00:13.650Z |
| 黄金集 | bench/rerank-golden.json（11 case，redundancy / recency / confidence / control） |
| evalK | 3（另出 @5） |
| MMR λ | 0.7 |
| 融合权重 | wSim=0.55 · wEff=0.3 · wCred=0.15 |
| effConf | 生产 effectiveConfidence（src/background/decay.ts，半衰期口径同 config） |
| 确定性自检 | 通过（两遍逐位相等，默认 HashEmbedder 臂） |
| 真实嵌入交叉验证 | 已跑（model=bge-m3）：redundancy MMR αnDCG@3=1.0000（非确定，网络） |

三臂定义：

- **baseline**：检索器原序（按 score 降序的恒等重排）——对照。
- **A·MMR**：贪心 MMR，相关性=score、冗余度=候选文本 HashEmbedder 两两余弦，λ 权衡（本次 λ=0.7）。
- **B·fusion**：`wSim·score + wEff·(effConf/1000) + wCred·credRank`，只用召回项已有字段（零新数据）。

指标：nDCG@K（效用分档，看高效用条是否早出）· αnDCG@K（多样性感知，α=0.5）· distinct@K（top-K 不同 topic 数）· Kendallτ（相对 idealOrder 秩相关）· firstMaxRank（首条满分 gain 名次，越小越好）。**每类 scenario 主指标**：redundancy→αnDCG、recency/confidence/control→nDCG。

## 一、判别结论（每类 scenario 谁赢、幅度多大）@K=3

| scenario | n | 主指标 | baseline | A·MMR | B·fusion | 赢家 | Δ(赢家−baseline) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| redundancy | 3 | αnDCG@K | 0.6760 | 1.0000 | 0.6760 | **mmr** | +0.3240 |
| recency | 3 | nDCG@K | 0.4491 | 0.4491 | 1.0000 | **fusion** | +0.5509 |
| confidence | 3 | nDCG@K | 0.4627 | 0.4231 | 1.0000 | **fusion** | +0.5373 |
| control | 2 | nDCG@K | 1.0000 | 1.0000 | 0.9734 | **baseline** | +0.0000 |

> 赢家按该 scenario 主指标取最高臂。Δ 为赢家相对 baseline 的主指标增量。

## 二、各 scenario 三臂全指标（@K=3）

### scenario = redundancy（n=3）

| 臂 | nDCG@K | αnDCG@K | distinct@K | Kendallτ | firstMaxRank |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.5732 | 0.6760 | 1.000 | +0.2000 | 1.000 |
| mmr | 0.9865 | 1.0000 | 3.000 | +0.8222 | 1.000 |
| fusion | 0.5237 | 0.6760 | 1.000 | +0.0667 | 1.333 |

### scenario = recency（n=3）

| 臂 | nDCG@K | αnDCG@K | distinct@K | Kendallτ | firstMaxRank |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.4491 | 1.0000 | 1.000 | -1.0000 | 3.667 |
| mmr | 0.4491 | 1.0000 | 1.000 | -1.0000 | 3.667 |
| fusion | 1.0000 | 1.0000 | 1.000 | +1.0000 | 1.000 |

### scenario = confidence（n=3）

| 臂 | nDCG@K | αnDCG@K | distinct@K | Kendallτ | firstMaxRank |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.4627 | 0.6956 | 1.000 | -0.0889 | 2.333 |
| mmr | 0.4231 | 0.6226 | 1.000 | -0.2222 | 2.667 |
| fusion | 1.0000 | 1.0000 | 1.000 | +0.9333 | 1.000 |

### scenario = control（n=2）

| 臂 | nDCG@K | αnDCG@K | distinct@K | Kendallτ | firstMaxRank |
| --- | --- | --- | --- | --- | --- |
| baseline | 1.0000 | 1.0000 | 3.000 | +1.0000 | 1.000 |
| mmr | 1.0000 | 1.0000 | 3.000 | +1.0000 | 1.000 |
| fusion | 0.9734 | 0.8827 | 3.000 | +0.8333 | 1.000 |

## 三、overall 三臂（@K=3 与 @5）

**@K=3**

| 臂 | nDCG@K | αnDCG@K | distinct@K | Kendallτ | firstMaxRank |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.5868 | 0.8286 | 1.364 | -0.0606 | 2.091 |
| mmr | 0.6887 | 0.8971 | 1.909 | +0.0727 | 2.182 |
| fusion | 0.8653 | 0.8903 | 1.364 | +0.6970 | 1.091 |

**@5**

| 臂 | nDCG@K | αnDCG@K | distinct@K | Kendallτ | firstMaxRank |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.7539 | 0.8746 | 2.091 | -0.0606 | 2.091 |
| mmr | 0.8016 | 0.9061 | 2.364 | +0.0727 | 2.182 |
| fusion | 0.9298 | 0.9478 | 2.091 | +0.6970 | 1.091 |

## 四、逐 case 明细（@K=3）

| case | scenario | 主指标 base→MMR→fusion | baseline 原序 | MMR 序 | fusion 序 |
| --- | --- | --- | --- | --- | --- |
| R-01 | redundancy | 0.676→1.000→0.676 | c1 c2 c3 c4 c5 c6 | c1 c5 c4 c6 c2 c3 | c1 c2 c3 c4 c5 c6 |
| R-02 | redundancy | 0.676→1.000→0.676 | c1 c2 c3 c4 c5 c6 | c1 c5 c6 c4 c2 c3 | c2 c1 c3 c4 c6 c5 |
| R-03 | redundancy | 0.676→1.000→0.676 | c1 c2 c3 c4 c5 c6 | c1 c5 c4 c6 c2 c3 | c1 c3 c2 c4 c5 c6 |
| F-01 | recency | 0.333→0.333→1.000 | c1 c2 c3 c4 | c1 c2 c3 c4 | c4 c3 c2 c1 |
| F-02 | recency | 0.333→0.333→1.000 | c1 c2 c3 c4 | c1 c2 c3 c4 | c4 c3 c2 c1 |
| F-03 | recency | 0.681→0.681→1.000 | c1 c2 c3 | c1 c2 c3 | c3 c2 c1 |
| C-01 | confidence | 0.496→0.378→1.000 | c1 c2 c3 c4 c5 | c1 c5 c2 c4 c3 | c2 c4 c3 c1 c5 |
| C-02 | confidence | 0.413→0.413→1.000 | c1 c2 c3 c4 | c1 c2 c3 c4 | c2 c4 c3 c1 |
| C-03 | confidence | 0.479→0.479→1.000 | c1 c2 c3 c4 | c1 c2 c3 c4 | c3 c4 c1 c2 |
| CT-01 | control | 1.000→1.000→1.000 | c1 c2 c3 c4 | c1 c2 c3 c4 | c1 c2 c3 c4 |
| CT-02 | control | 1.000→1.000→0.947 | c1 c2 c3 c4 | c1 c2 c3 c4 | c1 c2 c4 c3 |

## 五、鲁棒性：λ / 权重扫描（overall 主指标均值，@K=3）

看结论对超参是否稳健（软指标高方差 → 多点取势，同 D-0008/D-0009 纪律）。

**MMR λ 扫描 → redundancy αnDCG（越低 λ 越偏多样）**

| λ | redundancy αnDCG(MMR) | Δ vs baseline | control nDCG(MMR) | 备注 |
| --- | --- | --- | --- | --- |
| 0.3 | 1.0000 | +0.3240 | 0.9385 |  |
| 0.5 | 1.0000 | +0.3240 | 1.0000 |  |
| 0.7 | 1.0000 | +0.3240 | 1.0000 |  |
| 0.9 | 1.0000 | +0.3240 | 1.0000 |  |
| 1 | 0.6760 | +0.0000 | 1.0000 | λ=1 纯相关性 → MMR≡baseline |

**融合权重扫描 → recency+confidence nDCG(fusion)**

| wSim / wEff / wCred | recency nDCG(fusion) | confidence nDCG(fusion) | Δrec vs base | Δconf vs base |
| --- | --- | --- | --- | --- |
| 0.7 / 0.2 / 0.1 | 0.9907 | 1.0000 | +0.5416 | +0.5373 |
| 0.55 / 0.3 / 0.15 | 1.0000 | 1.0000 | +0.5509 | +0.5373 |
| 0.4 / 0.4 / 0.2 | 1.0000 | 1.0000 | +0.5509 | +0.5373 |

## 五点五、真实嵌入交叉验证（bge-m3，opt-in --real-embed）

用真实 **bge-m3**（@127.0.0.1:11435，dim=1024）替换 HashEmbedder 算候选两两冗余度复跑。
**只有 MMR 臂依赖嵌入**（baseline 按 score、fusion 按 score+元数据，均与嵌入无关）——
故本节验证的是「MMR 的多样性收益在真实语义向量下是否仍成立」。非确定（网络），仅供佐证。

| scenario | 主指标 | baseline | MMR(HashEmb) | MMR(bge-m3) | fusion |
| --- | --- | --- | --- | --- | --- |
| redundancy | αnDCG@K | 0.6760 | 1.0000 | 1.0000 | 0.6760 |
| recency | nDCG@K | 0.4491 | 0.4491 | 0.5927 | 1.0000 |
| confidence | nDCG@K | 0.4627 | 0.4231 | 0.4487 | 1.0000 |
| control | nDCG@K | 1.0000 | 1.0000 | 1.0000 | 0.9734 |

- **redundancy MMR 收益在真实向量下仍成立**：αnDCG@3 baseline 0.6760 → MMR(bge-m3) **1.0000**（Δ +0.3240）；HashEmbedder 版为 1.0000。真实语义向量下近重复条相似度更高（实测近重复 ~0.98、异话题 ~0.61），MMR 的冗余识别至少同样清晰。
- 结论：判别集的 MMR 多样性收益**不是 HashEmbedder 词面巧合**，真实嵌入下同样显著。

## 六、结论与 β 决策建议

### 这个判别集上，重排有可测收益吗？

- **有，且分工清晰**：在刻意构造的判别用例上，两种重排都在各自靶场对 baseline 取得**非零**增量：
  - **redundancy（去同话题冗余）→ A·MMR 赢**：αnDCG@3 0.6760 → **1.0000**（Δ +0.3240）；distinct@3 1.000 → **3.000**。fusion 在此≈baseline（元数据不含多样性信号）。
  - **recency（新近度应影响排序）→ B·fusion 赢**：nDCG@3 0.4491 → **1.0000**（Δ +0.5509）。MMR 在此=0.4491（≈baseline，同话题近同文本无从多样化）。
  - **confidence（可信度应影响排序）→ B·fusion 赢**：nDCG@3 0.4627 → **1.0000**（Δ +0.5373）。MMR 在此=0.4231（**不但不帮、还略降**，因 C-01 的异话题 gain=0 条被多样化目标错误上提）。
- **control（原序已理想）→ 记录副作用**：baseline nDCG@3=1.0000；本次 λ=0.7 下 MMR Δ=+0.0000、fusion Δ=-0.0266。**fusion 会把高置信但低相关的条上提而微损**（CT-02：stable 的无关偏好被 wEff/wCred 抬过低置信相关条），需 wSim 足够大压住。 **MMR 的过度多样化风险随 λ 降低而显现**：λ 扫描里 control nDCG(MMR) 在 λ=0.3 掉到 **0.9385**（把 gain=0 异话题条挤进前排）——故 MMR 的 λ 要偏相关性侧（≥0.7）。

### 关键判别设计点

- 每类 scenario **隔离单一信号**：redundancy 用例元数据同质（只有向量多样性能区分 → 只 MMR 赢）；recency/confidence 用例话题同质（只有元数据能区分 → 只 fusion 赢）。这保证「谁赢」可归因到策略机制，而非用例巧合。
- baseline 的「原序次优」是**刻意注入**的（score 手设为把陈旧/低置信/冗余项排前）——因为真实系统里检索器**是否真产出这种次优序，需真检索器 + 真嵌入复核**（见下）。本集测的是「**若**出现此类可修复缺陷，重排能修多少」，不等于「真实系统里重排一定有此收益」。

### 给 Integrator 的 β 建议

- **优先级：B·fusion 先行，A·MMR 缓行**。理由是**集成成本**（是否触 api-freeze）差异悬殊：
  - **B·fusion 不触 api-freeze**：它只重排 `recallCognitions` 产出的 `RecalledCognitionItem[]`——所需字段（`score`/`confidence`[已是 effConf]/`credStatus`/`contentType`）**全部已在项上**，零新数据。作为**纯内部函数**在 `recall.ts` 的 `out` 生成后插一段 `out.sort(...)` 即可（recall.ts:47 是 `retriever.search`，实际重排点在门控循环产出 `out` 之后）。若权重写成模块内常量 → **不新增 config 字段、不导出接口 → 公共 API 冻结面不动、api:check 保持绿**。
  - **A·MMR 大概率触 api-freeze**：MMR 的冗余度需要**候选向量的两两相似度**，而 `Retriever.search` 只回 `{id,score}`、`VectorRetriever` 的向量是内部私有、`recallCognitions` 手里只有 `Retriever` 没有 `Embedder`。要做 MMR 必须**新增 seam**——给 `Retriever` 加「回候选向量」的方法，或把 `Embedder` 注入 recall 重算嵌入——两者都改**公共接口/依赖形状 → 触 api-freeze，须走 D-xxxx**。且重算嵌入有额外算力/延迟成本。
- **若走 B**：建议先以模块常量落地（不触 api），dogfood 校准后再决定是否把 `wSim/wEff/wCred` 提升为 `config.retrieval.rerank*`（**那一步才触 api-freeze，须 D-xxxx**）。
- **诚实边界（防过度解读）**：本集是「能显差异」的合成判别集，Δ 是**上界性质**的存在性证据，不是真实语料的期望收益。§5.5 已用真实 bge-m3 交叉验证 MMR 臂、多样性收益非词面巧合；但进 β 前仍建议：①用真实 bge-m3（@127.0.0.1:11435）对**真实检索原序**复跑（本集 baseline 序是手设的，需确认真检索器确会产出冗余/陈旧靠前的次优序）；②在真实 `golden.json` / LoCoMo 上量 fusion 的端到端 Recall/nDCG 是否也有正向位移。若真实检索原序本就无这些缺陷（如 retrieval-after.md 显示的近最优），则按**铁律 4 不做**——不为一个真实系统不出现的问题加装置。

## 备注

- 全部离线确定：HashEmbedder（候选向量）+ 生产 effectiveConfidence（now 由 golden 固定），无网络、无随机、无系统时钟；每数字可由生成命令逐位复现。`--selftest` 自证（指标单测 + 判别不变量 + 两遍逐位相等）。
- **范围**：纯 bench/ 新增（rerank-golden.json + rerank-eval.mjs + 本报告），只读 import src/tests，未改 src/ / tests/ / api 快照 / DECISIONS / CHANGELOG。是否进 β 由 Integrator 守门，评测不代拍板。
- **真实嵌入交叉验证**：本次已跑（见 §5.5），MMR 多样性收益在真实 bge-m3 向量下同样显著。 更彻底的「真实检索原序」变体（用真嵌入定 baseline 序再重排）留作 β 前验证。
