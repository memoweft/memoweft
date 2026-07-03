# 交接 · 公开仓加固批次（一次性交付）· 给 Claude Code

> **用法**：本文件是编排任务书。主 agent（编排者）通读全文，按 §5 波次分派子 agent；
> 每个子 agent 领自己的任务卡（§4）+ 公共约束（§2、§3），不读他人的卡。
> 全部任务完成后由 A5 收口验收，**一次性交付单个 PR**，中途不向人提问（见卡壳协议）。

日期：2026-07-02 ｜ 目标仓：`kestercarroll702-gif/memoweft` ｜ 基线：`main`（typecheck ✅ / 108 tests ✅ / build ✅，Node 22.22 实测三绿）

---

## 0. 使命与交付形态

本批次 = **公开仓门面债 + 隐私止血 + 两项低风险性能/健壮性修复 + 两条债的登记**。
不做的（明确排除）：consolidate 相关性限定注入、防重网、Retriever 接口多 subject 化、npm 发布、G2 图谱前端。

交付形态：单分支 `chore/hardening-batch-202607` → 按任务分 commit → 单 PR → 附交付报告（§7 模板）。

---

## 1. 开工前置（主 agent 执行一次）

1. 读 `AGENTS.md` 与 `STATE.md`（守既有契约；本文件与 AGENTS.md 冲突时以 AGENTS.md 为准并触发卡壳协议）。
2. `git checkout -b chore/hardening-batch-202607`。
3. 跑基线三绿并记录测试总数（应为 108）：`npm run typecheck && npm test && npm run build`。基线不绿 → 整批中止，报告退出。

---

## 2. 已拍板决策（人已确认，子 agent 不得改判、不得"顺手优化"）

| # | 决策 | 内容 |
|---|------|------|
| D1 | 多 subject 边界 | 走**方案 A**：契约化"一个 subject 一个 Retriever 实例（独立向量库文件）"，写进项目地图；`Conversation` 注入点加 subjectId 硬过滤兜底（T2）。**不给** vectors 表加 subject 列、**不改** Retriever 接口签名（那是 B 方案，升级前提见 T6 地图文案）。 |
| D2 | CI / engines | **一律不动**。`.github/workflows/ci.yml` 已存在且注释明示锁 Node 24 是有意决策（"别随手放宽"）。本批仅把 README 的静态 tests badge 换接该 workflow（T1）。不加 Node 22 矩阵腿、不改 `package.json` engines。 |
| D3 | reference/ | 留在仓内，新增 `reference/README.md` 只读说明（T1）。不删、不挪分支。 |
| D4 | 写路径膨胀 | 本批**只装仪表**（T5：profileSize / promptChars 落盘），不做限定注入与防重网；后者作为债登记进 11-A（T6）。 |

---

## 3. 全局铁律与卡壳协议

**禁止清单（任何 agent）**
- 不新增运行时依赖（`dependencies` 保持空；node:crypto 等内置模块可用）。
- 不改 `Retriever` / 各 Store 的**公共接口签名**（新增返回值字段属允许的增量，删改参数/方法不允许）。
- 不动 `ci.yml`、`package.json` 的 engines、`config.ts` 的参数默认值。
- 不删/不挪 `reference/`；不动 `src/index.ts`（本批无新导出符号；类型字段新增不需要它）。
- 不为让测试通过而修改**既有**测试断言。
- `STATE.md` 保持 ≤40 行红线。
- 不触碰他人独占文件（§5 所有权表）；确需跨界 → 走卡壳协议。

**卡壳协议（一次性交付版）**：任务卡未覆盖的情形、或发现必须突破上述禁止项才能完成 → **跳过该子项**，其余照做；在交付报告"待人拍板"区写明：卡号、卡点、你建议的两个选项与权衡。不即兴、不追问、不空转。

---

## 4. 任务卡

### T1 · 门面同步（agent A1）

**目标**：README 与 STATE.md 对齐，消灭一切手工维护的易变数字；reference/ 加只读说明。

**改动**
1. `README.md` + `README.zh-CN.md`（两语言逐条同步，语义一致）：
   - 删静态 tests badge（当前写死 "87 passing"），换为已存在 workflow 的状态 badge：
     `[![CI](https://github.com/kestercarroll702-gif/memoweft/actions/workflows/ci.yml/badge.svg)](https://github.com/kestercarroll702-gif/memoweft/actions/workflows/ci.yml)`
   - 正文所有 "87 passing / 87 个通过" 文案改为不写死数字的表述（如 "all tests green in CI / 测试在 CI 全绿"）。
   - **以 `STATE.md` 为唯一事实源**重写 Done / Not yet 区块。对齐要点：5-B 测试台导入导出、6-A 记忆管理页、6-B G1 后端、7-A Cloud Guard、8-A 真采集器 → **Done**；Not yet 只剩：G2 图谱前端、9-A 星瑶最小宿主、10-A 插件契约、11-A 稳定性/迁移、12-A npm 发布、召回精化后续。
   - README 其余段落（定位、Quick start、Configuration 等）**不动**。
2. 新建 `reference/README.md`（≤6 行，中文）：说明这是 DLA→MemoWeft 迁移时的旧代码**只读基线快照**，不参与构建与测试、不再维护，仅供对照历史实现。

**验收**
- `grep -n "87" README.md README.zh-CN.md` 无测试计数残留。
- 两语言 status 区块条目一一对应。
- badge URL 与 `.github/workflows/ci.yml` 的实际文件名/工作流一致（动手前先 `cat` 确认）。

---

### T2 · 召回越界止血（agent A2）

**目标**：多 subject 共用 retriever 时，他人认知**不可能**被注入回话。

**现状锚点**（`src/pipeline/conversation.ts`，handle 内召回循环）：
```ts
const c = cognitionStore.get(h.id);
if (!c || c.invalidAt) continue; // 失效的不注入（……）
```
`stored` 变量（本轮落库证据）在同一作用域，`stored.subjectId` 即本轮 subject。

**改动**：上述两行之后插入一行硬过滤：
```ts
if (c.subjectId !== stored.subjectId) continue; // 越界召回硬过滤（多 subject 隐私止血）：索引可能混入其他 subject 的条目，不是本人的认知绝不注入。契约见地图「召回边界」。
```

**新增测试** `tests/recallSubjectGuard.test.ts`（新文件，不动既有 conversation.test.ts；stub LLM / 假 Retriever 的写法参照 `tests/conversation.test.ts` 既有 fixture）：
- 场景：cognitionStore 内放 subject A 与 subject B 各一条认知；B 的 content 含独特标记串（如 `SUBJECT_B_SECRET`）；假 Retriever 的 `search` 固定返回**两条**认知的 id（B 排前、分数 0.99）。
- **防假绿关键**：两条认知都要能通过既有门槛——`contentType: 'preference'`、`formedBy: 'stated'`、`confidence: 900`、`credStatus: 'stable'`（preference 默认不衰减，900 ≫ `minEffectiveConfidence=80`）。否则 B 被衰减/门槛挡掉，测不出泄漏。
- 断言 1（隔离）：以 subject A 调 `handle`，`turn.recall` 中**不含** B 标记串。
- 断言 2（阳性对照）：A 自己的认知**在** recall 里——证明过滤没有误伤本人召回。
- **红→绿留证**：先写测试、注释掉过滤行跑一次确认断言 1 失败，再启用过滤行转绿；把这一验证过程一句话写进交付报告。

---

### T3 · 嵌入器超时（agent A2，与 T2 同人，文件不同）

**目标**：`OpenAICompatEmbedder.embed` 的 fetch 不再裸奔（现状无 signal，上游 LLM client 已有 120s 超时）。

**改动**（`src/retrieval/embedder.ts`）：
- `embed()` 内、fetch 前读超时（env 双前缀写法与本文件 `loadEmbedConfig` 保持一致）：
  ```ts
  const timeoutMs = Number(process.env.MEMOWEFT_EMBED_TIMEOUT_MS ?? process.env.DLA_EMBED_TIMEOUT_MS) || 60000;
  ```
- fetch options 加 `signal: AbortSignal.timeout(timeoutMs)`。
- 超时错误包装：先读 `src/llm/client.ts` 第 ~81–96 行的既有写法，同样式捕获 TimeoutError 并抛 `嵌入请求超时（超过 ${timeoutMs}ms）`。
- 顶部文件注释补一行：超时可经 `MEMOWEFT_EMBED_TIMEOUT_MS` 配置，默认 60s；失败由上游容错（召回失败不挡回话、indexError 不回滚画像）。

**验收**：typecheck 过；既有 retrieval/嵌入相关测试不回归。（超时行为本身可不写网络测试；若要写，用 AbortSignal + 假 fetch，禁止真网络调用。）

---

### T4 · 向量索引增量化（agent A3）

**目标**：`indexAll` 对外语义不变（替换式：调用后索引 = 传入集合），内部改增量——只 embed 新增/变更条目，嵌入调用从 O(N) 降到 O(Δ)。

**改动**（`src/retrieval/vectorRetriever.ts`，`Retriever` 接口一字不改）：
1. Schema 升级：`CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, hash TEXT NOT NULL, vec TEXT NOT NULL)`。
2. 迁移：构造函数里 `PRAGMA table_info(vectors)` 检查；缺 `hash` 列 → `DROP TABLE vectors` 后重建，注释写明理由：**索引是可重建资产，宁可重建不带病迁移**（下次 `indexAll` 自然回填）。
3. `indexAll(items)` 改 diff 逻辑：
   - `hash = createHash('sha256').update(text).digest('hex')`（`node:crypto`，零依赖）。
   - 读库中现有 `(id, hash)`；分三集：新增（库无此 id）、变更（id 同 hash 异）、删除（库有但 items 无）。
   - 仅对 新增+变更 调 `embedder.embed`（保持一次批量调用）；删除集执行 DELETE；变更集 UPDATE（或 DELETE+INSERT）。
   - **`indexAll([])` 必须仍清空全表**（替换式语义的边界情形，现有行为如此）。
4. 文件头注释更新：对外仍是"替换式重建"，内部增量实现 + hash 判变。

**新增测试**（写入 `tests/retrieval.test.ts`，沿用该文件既有假 embedder 风格，改造成**可计数** stub：记录 `embed` 被调用次数与收到的文本数）：
- (a) 首次 `indexAll` N 条 → embed 收到 N 条文本；search 可命中。
- (b) 相同 items 再 `indexAll` → embed 收到 0 条文本（或未被调用）。
- (c) 改 1 条内容 + 删 1 条 + 增 1 条 → embed 恰收到 2 条文本；被删 id 不再出现在 search 结果。
- (d) `indexAll([])` → search 返回空。
- (e) 迁移：手工建旧 schema 库（无 hash 列），构造 VectorRetriever 不抛错，随后 indexAll/search 正常。

**禁止**：不加 subject 列（D1）；不改接口；不引第三方库。

---

### T5 · 写路径仪表（agent A4）

**目标**：让"画像多大 / prompt 多大"进入落盘日志，为 11-A 的膨胀债提供 dogfood 曲线（D4：只观测，不动刀）。

**改动**
1. `src/consolidation/consolidate.ts`：
   - `ConsolidateResult` 增两个必有字段：`profileSize: number`（本轮注入 prompt 的 active 认知条数 = `existing.length`）、`promptChars: number`（`buildMessages` 产物全部 `content` 长度之和）。
   - 无新事件早退分支：两值均为 `0`，字段 jsdoc 注明「0 = 本轮未执行整理」。行为零变化，只加计量。
2. `src/consolidation/updateProfile.ts`：`UpdateProfileResult` 增 `metrics: { profileSize: number; promptChars: number }`，从 consolidate 结果透传。
3. `src/obs/runLog.ts`：`ProfileUpdateRecord.summary` 增**可选**字段 `profileSize?: number; promptChars?: number`（可选是为旧日志读取兼容）。
4. `testbench/server.mjs`：grep 定位构造 `kind: 'profile_update'` 记录处，把 `result.metrics` 两值透传进 summary。找不到落盘点或接线复杂 → 步骤 4 走卡壳协议跳过，前三步照做。

**新增测试** `tests/writePathMetrics.test.ts`（新文件；fixture 先 `grep -rn "consolidate(" tests/` 找到既有 consolidate 测试文件并沿用其 stub LLM / store 搭建方式）：
- 预置 ≥1 条 active 认知 + 1 个未消化事件，stub LLM 返回合法空产出 JSON → 断言 `profileSize` 等于预置 active 条数、`promptChars > 0`。
- 无未消化事件 → 断言两值均为 0。
- 经 `updateProfile` 走一遍 → 断言 `result.metrics` 与 consolidate 值一致。

---

### T6 · 文档收口与债务登记（agent A5，Wave 2）

**改动**（全部为文档，代码零改）
1. `docs/项目地图.md` 新增/修订两条（文案已成文，按地图既有格式就近落格、可微调措辞不改语义）：
   - **召回边界（V1 契约，2026-07 定）**：一个 subject 对应一个 Retriever 实例（独立向量库文件，如 `memoweft-vectors.<subjectId>.db`）；`Conversation` 注入点有 subjectId 硬过滤兜底（conversation.ts）。**非死规则，升级前提**：出现"单进程需服务多 subject 的宿主"时，升级为 vectors 表加 subject 列、Retriever 接口带 subjectId（B 方案）。
   - **11-A 债 · 写路径膨胀**：consolidate 全量注入 active 画像，prompt 随画像线性涨。修复方向 = 相关性限定注入（retriever 对新事件文本检索 top-K + 全部 conflicted + 最近 N 条）**且必须配防重网**（new 候选落库前对 active 认知相似度查重，高相似降级 reinforce 或丢弃）——代价：限定注入漏召回会致重复创建，故防重网先行、可独立落地。**触发条件**：dogfood 观察 T5 落盘的 profileSize/promptChars 曲线，到疼点由人拍板。
2. `STATE.md`（改写对应行，守 ≤40 行）：
   - 阶段行追加「公开仓加固批次 ✅（2026-07-XX）」。
   - 命令行 `npm test` 后的括号数字更新为实测总数。
   - 可用接口区补两处短句：VectorRetriever 内部增量化（hash diff，语义不变）；`updateProfile` 返回 `metrics{profileSize,promptChars}` 并落盘；Conversation 注入点 subject 硬过滤。
3. `LOG.md` 追加一条批次记录：先读文件末尾 3 条模仿既有格式，内容覆盖：README 同步接 CI badge ｜ 召回 subject 硬过滤+红绿测试 ｜ 嵌入器超时 ｜ 向量索引增量化 ｜ 写路径 metrics 落盘 ｜ 债登记两条 ｜ 三绿 N tests。
4. `.agents/skills/docs-sync/SKILL.md` 检查单追加一行：「对外 README（含 zh-CN）与 STATE.md 状态一致，README 不得含手工维护的测试数字」。

---

## 5. 波次与文件所有权（独占，越界即卡壳协议）

**Wave 1（四个 agent 可并行）**

| Agent | 任务卡 | 独占文件 |
|-------|--------|----------|
| A1 门面 | T1 | `README.md`、`README.zh-CN.md`、`reference/README.md`（新） |
| A2 加固 | T2+T3 | `src/pipeline/conversation.ts`、`src/retrieval/embedder.ts`、`tests/recallSubjectGuard.test.ts`（新） |
| A3 索引 | T4 | `src/retrieval/vectorRetriever.ts`、`tests/retrieval.test.ts` |
| A4 仪表 | T5 | `src/consolidation/consolidate.ts`、`src/consolidation/updateProfile.ts`、`src/obs/runLog.ts`、`testbench/server.mjs`、`tests/writePathMetrics.test.ts`（新） |

**Wave 2（Wave 1 全部完成并各自局部测试通过后）**

| Agent | 任务卡 | 独占文件 |
|-------|--------|----------|
| A5 收口 | T6 + §6 集成验收 | `STATE.md`、`LOG.md`、`docs/项目地图.md`、`.agents/skills/docs-sync/SKILL.md` |

每个 Wave 1 agent 完成后按任务提交 commit（风格沿用仓内惯例，建议）：
`docs(readme): 门面同步——badge 接 CI、状态对齐 STATE、reference 只读说明`
`fix(pipeline): 召回 subject 硬过滤（多 subject 隐私止血）+ 红绿测试`
`fix(retrieval): 嵌入器请求加超时（默认 60s，可 env 配）`
`perf(retrieval): 向量索引增量化——hash diff，嵌入调用 O(N)→O(Δ)，语义不变`
`feat(obs): 写路径仪表——profileSize/promptChars 进 metrics 并落盘`
`docs: 加固批次收口——STATE/LOG/地图债登记 + docs-sync 检查单`

---

## 6. 集成验收（A5，全部通过才算交付）

1. 全仓三绿：`npm run typecheck && npm test && npm run build`；测试总数 **> 108** 且既有 108 条零回归。
2. testbench 冒烟（无 `.env` 也应能起，首启门兜底）：`npm run testbench` 后 `curl -s http://127.0.0.1:7888/api/health` 返回 200，随后关闭进程。
3. §3 禁止清单逐条自查（特别是：ci.yml/engines/config 默认值未被动过、STATE ≤40 行、reference 未删）。
4. `git log` 检查 commit 与任务对应、无混杂改动。
5. 产出交付报告（§7），随 PR 描述提交。

---

## 7. 交付报告模板

```
# 交付报告 · 公开仓加固批次
基线：main @ <sha>，108 tests ｜ 交付分支：chore/hardening-batch-202607

## ✅ 完成
- [T1] …（一行结果）
- [T2] …（含红→绿验证说明）
- [T3] …
- [T4] …（嵌入调用计数测试结果摘要）
- [T5] …（落盘是否接通 server.mjs）
- [T6] …

## ⏭ 跳过（卡壳协议触发项）
- 卡号 / 卡点 / 建议选项 A、B 与权衡（无则写"无"）

## 🔶 待人拍板（预填两条，如有新增追加）
1. 首轮 CI 跑完后确认 badge 显示正常（badge 在 Actions 首次运行前可能显示 no status，属预期）。
2. dogfood 一段时间后看 profileSize/promptChars 曲线，决定 11-A 写路径债的启动时机。

## 三绿证据
<粘贴 typecheck / test（含总数 before→after）/ build 输出末尾>
```
