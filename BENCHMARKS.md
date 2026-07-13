# BENCHMARKS — MemoWeft 公开基准成绩(Phase 6 · §19)

> 维护者账本,非营销页。原则:**每个数字都可复现、条件写清、不做不对等比较**。
> 数据许可:LoCoMo 为 **CC BY-NC 4.0**(仅研究、非商用)——数据文件**绝不入库**,本页只发**聚合分数**。
> 环境:本机 RTX 3090;答题/固化模型 = 小米 MiMo `mimo-v2.5-pro`(云端 OpenAI 兼容);嵌入 = 本地 `bge-m3`(1024 维,经 llama.cpp GPU)。
> 状态:两套公开基准(LoCoMo §1–3、LongMemEval_S §4)均已 ≥1 次完整跑;固化质量指标跨模型稳健性(§5·gpt-4o 交叉验证)已验;未打 `phase-6-done`(人类)。LoCoMo 部分小样本项(§2)标注为方向性。

---

## 1. LoCoMo-10 · §19.2 检索矩阵(Recall@15)

三种检索 × 两种嵌入,evidence 层,全 10 sample、1536 道有-gold 题(排除 category 5 adversarial);dry 跑(只量检索命中,不调答题 LLM)。

| 类别 | n | keyword | vector-hash | **vector-bge** | hybrid-hash | hybrid-bge |
|---|---|---|---|---|---|---|
| multi-hop | 282 | 39.4% | 18.1% | **77.0%** | 31.9% | 69.1% |
| temporal | 321 | 54.8% | 39.3% | **83.2%** | 57.0% | 81.0% |
| open-domain | 92 | 30.4% | 15.2% | **51.1%** | 27.2% | 48.9% |
| single-hop | 841 | 63.6% | 34.5% | 80.5% | 57.0% | **82.4%** |
| **overall** | **1536** | 55.3% | 31.3% | **78.6%** | 50.6% | 77.7% |

- **臂**:vector = VectorRetriever(余弦);keyword = KeywordRetriever(FTS5 trigram/BM25);hybrid = HybridRetriever(RRF 融合)。
- **嵌入**:`hash` = HashEmbedder(确定性 char-bigram,占位/基线);`bge-m3` = 真实语义嵌入。
- **结论**:① 真实 bge-m3 压倒性(+47pt vs 确定性向量);② **强嵌入下 hybrid≡vector**(77.7≈78.6)——坐实 D-0008「hybrid 不进公共 API」;③ **弱嵌入下 hybrid≫vector**(50.6 vs 31.3)、keyword-only 55.3% 也不弱——验证 D-0008 caveat「keyword 大语料被低估」。

## 2. LoCoMo-10 · 端到端 F1(evidence 层 vs cognition 层 · 会话日期 A/B)

单 sample(conv-26)· 全 419 轮 · 前 30 题;答题经 mimo,partial-match F1。**方向性小样本**,非最终分。

| 臂 | multi-hop | temporal | open-domain | 全程 token |
|---|---|---|---|---|
| evidence·关键词·日期开 | 0.133 | 0.613 | 0.263 | 45.4k |
| evidence·语义 bge-m3·日期开 | **0.407** | **0.658** | **0.277** | 39.8k |
| evidence·关键词·**日期关** | 0.133 | **0.131** | 0.222 | 31.9k |
| cognition·日期开 | 0.343 | 0.025 | 0.281 | 87.3k |

- **会话日期注入对 temporal 决定性**:F1 0.131→0.613(×4.7)——修了 temporal「已知偏差」。
- **cognition 层不适合逐句 episodic 题**:temporal F1 0.025、成本≈2×——消化丢细节,是「画像级 recall vs 逐句事实召回粒度落差」的量化(定位使然,非缺陷)。

## 3. 置信度参数敏感性 · §19.3(纯确定性网格)

底分 ×{0.8,1.0,1.2} × 半衰期 ×{0.5,1.0,2.0},零 LLM 重算(见 `bench/sensitivity-confidence.md`)。

- **底分 ±20%**:credStatus 翻转率 28.1%,但**跨 >1 档的野翻转 = 0**——全是相邻档边界跨越,集中在 `stated` 底分(600 恰在 limited/stable 阈值中点)。分档系统固有特性,系统有序无突变。
- **半衰期**:召回保留窗口随半衰期**线性**伸缩(×0.5/1/2 → 窗口 ×0.5/1/2),无悬崖。
- **结论**:未发现更优默认参数,默认值行为有序可预测,不触发「改默认→D-xxxx」或「改 eval 断言→铁律1」。

## 4. LongMemEval_S · accuracy(judge = gpt-4o,标准口径)

**全 500 题**;答题 mimo · **judge = `gpt-4o-2024-11-20`**(gpt-4o 快照,标准口径)· evidence 层 keyword 检索 · 只摄入 user 回合(铁律 3a)。

| question_type | n | 正确率 |
|---|---|---|
| single-session-user | 70 | **71.4%** |
| knowledge-update | 78 | **69.3%** |
| temporal-reasoning | 133 | 58.6% |
| multi-session | 133 | 45.9% |
| single-session-assistant | 56 | 19.6% |
| single-session-preference | 30 | 10.0% |
| **overall** | **500** | **51.3%** |

- 答题 token(mimo)≈ **1.70M**;judge(gpt-4o)成本 **<$1**。
- **强项**:single-session-user 71.4% / knowledge-update 69.3%(事实性记忆召回好)。
- **两处结构性低分,信息量大**:
  - **single-session-assistant 19.6%**:铁律 3a 只摄入 user 回合、不存助手输出 → 问「助手说过什么」结构性偏低,**定位使然、非弱点**。
  - **single-session-preference 10.0%(evidence)→ cognition 层实证显著更好**:偏好正是 cognition 层消化的东西。**增量消化 A/B(`--layer cognition --consolidate-every 50`,同 3 题)实证:evidence-keyword 0% → cognition 66.7%**——preference 类确实该走 cognition 层(召回"这人偏好 X"的消化结论,胜过在 500 原始回合里 keyword 搜)。
    - 边界+caveat:一次性 `updateProfile` 撑爆 120s LLM 超时(MemoWeft 是**增量消化**设计 batchSize=5,非一口气消化),须边摄入边周期消化;mimo 慢、部分 50 条 chunk 仍超时 → 消化**不完整**,cognition 仍大胜(效应强)。样本小(3 题),方向性;放大样本/更小 chunk/更快消化模型可进一步坐实。
- 跑法:per-batch 进程隔离(避 node:sqlite 累积 native 崩)+ `--merge`;50 批中 2 批崩溃已用 limit-5 补跑,凑齐 500。数据 278MB 经 `LONGMEMEVAL_PATH`,不入库。

## 5. 固化质量 · 多模型分差(§15.5:指标对被测模型的依赖度)

把 §15.2 固化评测的**被测模型**从 mimo 换成 **gpt-4o**(judge **固定** = mimo 温度 0,只动一个自变量 → 结构硬指标跨臂可比),量化"这套指标有多依赖 mimo 这个具体模型"。全 42 场景,一次完整跑。

| discipline | n | mimo 结构 | gpt-4o 结构 | Δ |
|---|---|---|---|---|
| chitchat-negative | 7 | 35/35 | 35/35 | 0 |
| conflict | 7 | 40/42 | 42/42 | +2 |
| correct | 7 | 42/42 | 41/42 | −1 |
| emotion-cap | 7 | 31/35 | 34/35 | +3 |
| fact-vs-belief | 7 | 34/35 | 34/35 | 0 |
| no-over-inference | 7 | 28/34 | 28/34 | 0 |
| **overall** | 42 | **210/223(94.2%)** | **214/223(96.0%)** | **+4(+1.8pp)** |

- **指标对模型依赖小**:两个前沿模型总体只差 1.8pp;**3/6 盘逐检查完全相同**(chitchat-negative / fact-vs-belief / no-over-inference),`overInferRate=0.00` 两模型全盘一致。评测器量的是**认知纪律本身**,不是 mimo 的怪癖 → 指标**跨模型稳健、可迁移**,非"只对 mimo 成立"的过拟合。
- **no-over-inference 28/34 两模型一模一样** → **跨模型印证 D-0019**:fact-vs-state 灰区(一次性事件被标 fact/goal/preference)在 gpt-4o 上原样复现,坐实这是 **ContentType 缺「事件」型**的定义局限、**非 mimo 缺陷**。
- **有分差处 gpt-4o 略"干净"**:emotion-cap +3(情绪封顶更稳)、conflict +2(更会标矛盾);mimo 在 1 条 correct 上略强。均 ≤3 检查,幅度小。
- **软判(gistRecall)**:judge 固定 mimo → 非-conflict 盘可比,delta 多为 0(correct −0.14 属单跑方差 D-0009);conflict 的 0→1.00 是 **gist 评分口径 v1→v2**(度量清理①的确定性硬判,`--compare` 已高声告警),**非模型差异**。
- 被测=gpt-4o(用户自配 `MEMOWEFT_GPT4O_*`,裸 `gpt-4o` 别名)、judge=mimo 固定。gpt-4o 臂产物在 `bench/runs/`(gitignore),本页只发聚合分。

## 6. 复现命令

```bash
# 数据(CC BY-NC,不入库):把 locomo10.json 放本地,LOCOMO_PATH 指向它
export LOCOMO_PATH=bench/data/locomo10.json

# §19.2 完整矩阵:逐 sample 独立进程(规避 node:sqlite 全量单进程 native 崩)+ 合并
for i in $(seq 0 9); do node bench/locomo-eval.mjs --matrix --offset $i --limit 1; done
node bench/locomo-eval.mjs --merge-matrix          # → bench/runs/<date>-<commit>-locomo-matrix-merged.md

# §2 端到端 F1 对比(接 mimo)
node bench/locomo-eval.mjs --limit 1 --qa 30                     # evidence·关键词
node bench/locomo-eval.mjs --limit 1 --qa 30 --retriever semantic # evidence·bge-m3
node bench/locomo-eval.mjs --limit 1 --qa 30 --layer cognition    # cognition
node bench/locomo-eval.mjs --limit 1 --qa 30 --no-dates           # 日期 A/B

# §19.3 参数敏感性(零 LLM)
node bench/sensitivity-confidence.mjs              # → bench/sensitivity-confidence.md

# LongMemEval_S(全 500 题·标准 gpt-4o judge):数据经 LONGMEMEVAL_PATH(278MB,不入库)
node bench/longmemeval-eval.mjs --selftest        # 离线验管线(无数据/无 key)
export LONGMEMEVAL_PATH=bench/data/longmemeval_s.json
export MEMOWEFT_JUDGE_BASE_URL=https://api.openai.com/v1  # judge=gpt-4o(标准);key 只经 env
export MEMOWEFT_JUDGE_API_KEY=sk-...  MEMOWEFT_JUDGE_MODEL=gpt-4o-2024-11-20
for off in $(seq 0 10 490); do node --max-old-space-size=4096 bench/longmemeval-eval.mjs --offset $off --limit 10; done
node bench/longmemeval-eval.mjs --merge           # → bench/runs/<date>-<commit>-longmemeval-merged.md

# §15.5 多模型分差:换被测模型(judge 固定 mimo),写 runs/ 不碰基线;再 --compare 出逐 discipline 分差
# 先在 .env 配 MEMOWEFT_GPT4O_BASE_URL / _API_KEY / _MODEL(key 只经 env)
node bench/eval-consolidation.mjs --subject-env GPT4O
node bench/eval-consolidation.mjs --compare bench/consolidation-baseline.json \
  bench/runs/<date>-<sha>-consolidation-subject-gpt-4o.json   # 会高声提示「被测模型变了」
```

嵌入端点(bge-m3 @ 127.0.0.1:11435)需在跑语义/cognition 臂前起(本机经 llama.cpp GPU)。

## 7. 条件与对比纪律

- **不做不对等比较**:与 Mem0 / 其他记忆库的公开数字对照前,须对齐 top-k、检索层(evidence vs cognition)、嵌入模型、judge 模型、是否含 adversarial。本页数字的条件已在各节写明。
- **模型非确定**:mimo 是推理模型、有输出漂移;F1/judge 数字是某次快照,不做 CI 断言。
- **摄入纪律差异**:MemoWeft 只把 user 亲口当证据(铁律 3a),不摄入助手输出——这会让「问助手说过什么」类题结构性偏低,是定位差异而非弱点。

---

*本页随 Phase 6 推进更新;历史快照见 `bench/runs/`(gitignore,仅本地)。*
