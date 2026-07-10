# CURRENT — 当前状态(Integrator 每个工作段落结束更新)

更新于:2026-07-10 | 所在 Phase:**2 固化更可信(进行中)**(前置 tag `phase-1-done`)

> 总纲 `PROJECT_PLAN.md`;决策 `DECISIONS.md`;固化质量报告 `bench/consolidation-baseline.md`。

## 正在进行

- Phase 2 质量线已用于**真修了一个质量问题**(闲聊过度记忆,实测治好)。下一步:15.3 提示词版本化收尾 / 15.4 live+nightly,待人类定向。

## 刚完成(Phase 2,附证据)

- **2.1/2.2 质量线 + 基线**(`7b527c0`/`939695d`):42 场景语料 + 评测器(结构断言 + LLM-judge)+ 真实基线(v1:结构 88.8%)。
- **2.3 提示词 v2 治"闲聊过度记忆"**(D-0009):`consolidate.ts` 加"闲聊无信息→零新增"守卫(明确保留情绪/事实/偏好)。**实测前后对比(真实 mimo 全量 42 场景)**:
  - 总体结构断言 **88.8% → 94.2%**(198→210/223);全绿场景 25→30。
  - **chitchat-negative 21/35 → 33/35**(靶子,治好);correct gistRecall 0.43→0.71;overInferRate 0.01→0.00。
  - **无真实回退**:emotion 单跑软分 0.14 是噪声,复跑 34/35·0.57(>v1);结构硬指标全程稳。
  - 方法学:软判(gistRecall)单跑高方差,以结构硬指标为准(D-0009)。
  - 评测器加 `--discipline` 过滤(快速单类迭代)。

## 阻塞 / 环境

- 无阻塞。本地 Ollama(bge-m3 @ 11435)由本会话起着(可停)。固化评测慢(约 30s/场景),nightly/本地跑。

## 下一步(待人类定向)

- **A. Phase 2 收尾管道**:15.3 提示词进一步集中版本化(现已加版本注释 v2)+ 回归流程写文档;15.4 `test:live`/`fixtures:refresh`/nightly 接入。
- **B. 继续用质量线修**:conflict 不落行为认知(gistRecall 0)、no-over-inference 偶发过度推断——可继续迭代提示词。
- **C. 暂停**:Phase 2 已建质量线 + 出基线 + 真修一个问题,是很完整的一段。

## 本轮范围冻结(铁律 4)

host、采集插件、perception、asking、attribution、background、graph、portable、memory 管理 API —— 只在某 Phase 明确需要时才碰,否则进 ROADMAP Later。
