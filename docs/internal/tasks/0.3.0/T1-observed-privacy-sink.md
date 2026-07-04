# T1 · 隐私红线 B 下沉 core：observed 默认不上云，绑到 put 层

**对应五关**：隐私关。**作者已拍板**（2026-07-04 决策 2）：采用"下沉 core"方案，本任务书就是授权范围——这是唯一允许动授权默认语义的地方。

## 背景（审计结论）

红线 B（"observed 行为观察证据默认不上云"）目前只绑在 `ingestObservations` 一个入口上（`src/perception/ingest.ts:80-82`）。其他入口落 observed 证据走的是 put 的通用默认：`src/evidence/store.ts:146` 的 `allowCloudRead ?? cloudReadDefault(cfg)`，而 `cloudReadDefault = !privacyMode`、`privacyMode` 默认 false（`src/config.ts:90,131-134`）→ **observed 证据默认上云了**。实锤活例：testbench 的 `POST /api/observe`（`testbench/server.mjs:393-406`，已核实传了 `sourceKind:'observed'`）走 `perceive` + `store.put`，落出的观察证据 `allowCloudRead=true`，会被 distill 等云端写路径正常喂进 prompt。

## 不变式（改完后必须成立）

> 任何入口落库的 `sourceKind === 'observed'` 证据，凡未显式给授权位，一律套 `cfg.observedDefaults`（local ✓ / cloud ✗ / infer ✓）。显式传值仍然优先。`spoken` 与 `inferred` 行为完全不变。

## 改哪里

**唯一逻辑改动点**：`src/evidence/store.ts` 的 `put()`，第 145-147 行。现状：

```ts
allowLocalRead: input.allowLocalRead ?? this.cfg.evidenceDefaults.allowLocalRead,
allowCloudRead: input.allowCloudRead ?? cloudReadDefault(this.cfg),
allowInference: input.allowInference ?? this.cfg.evidenceDefaults.allowInference,
```

改成按 `input.sourceKind` 分流：`'observed'` → 三个默认取自 `this.cfg.observedDefaults`；其余（spoken/inferred）→ 维持现状。put 是所有正常落库的唯一漏斗（`insert()` 除外，见"不许动"），在这里立规矩 = 所有入口一次性兜住，包括 `core.ingestUserMessage` / `Conversation.handle` 传 `sourceKind:'observed'` 的路径和 testbench 那个端点——它们**不需要改代码**，自动被兜住。

**随任务必须联动改的测试 fixture**（校对实锤：不改这些，本任务改完至少 7 个现有测试变红，此处明确授权改法）：

库内既有测试把"无显式授权位的 observed 证据"当云可读数据在用，默认翻转后会被 `attribute.ts:149` 的 `filterCloudReadable` 滤掉 → 归因产不出假设 → 断言挂。**改法只有一种：给这些测试证据补显式 `allowCloudRead: true`**（口径 = "路线 A 手动授权上云的测试数据"，与 `tests/privacy.test.ts:102`、`tests/perception.test.ts:71` 既有写法一致，测试语义不变）。**不许**为让测试变绿去改断言或改归因逻辑。点位：

- `tests/attribution.test.ts`：`setupScenario`（第 30-33 行）以及第 74、142、166 行的 `ev.put({sourceKind:'observed', ...})`。
- `tests/cognition.test.ts`：第 191 行附近（183-206 行那组归因闭环用例）的 observed 证据。
- 跑全量测试后若还有同型红测试，按同一口径补显式授权位，并在 PR 里列全清单。

**随改的注释**（代码改了注释不改 = 留坑）：
- `src/config.ts:13-14`：observedDefaults 的注释写着"由摄入层 ingestObservations 套用并显式传给 put——不动 put 通用默认"，本任务后这句过时，改为"由 put 按 sourceKind 套用（最后防线）；ingestObservations 显式传值属双保险"。
- `src/perception/ingest.ts:79` 的注释同理更新。`ingestObservations` 的**代码不动**（显式传值留着当双保险，行为等价）。
- `src/evidence/model.ts:42-45` EvidenceInput 的 doc 注释补一句"observed 缺省授权 = observedDefaults"。

## 不许动

- `insert()`（`src/evidence/store.ts:104-105`）：导入/恢复按包内原授权位原样落库，是设计内的保真，**不套默认**。
- `filterCloudReadable` 及六条云端路径的过滤逻辑（红线 C 已全绿，别碰）。
- Host 的 `sanitizeObservation`（`apps/memoweft-host/src/server.ts:159-172`）：继续强制剥授权位，不因 put 兜底了就删——纵深防御。
- `'inferred'` 的默认行为（本次红线只管 observed；要收紧 inferred 属新决策，记"发现待办"）。
- 现有测试的断言与归因逻辑（fixture 补授权位是唯一允许的测试改动，见上）。

## 测试（新建 `tests/observedDefaults.test.ts`，全离线）

1. `put({sourceKind:'observed'})` 无显式授权 → `allowCloudRead=false`、`allowLocalRead=true`、`allowInference=true`（在 `privacyMode=false` 下）。
2. `put({sourceKind:'observed', allowCloudRead:true})` → 尊重显式 true。
3. 回归：`put({sourceKind:'spoken'})` 无显式 → `allowCloudRead=true`（privacyMode=false）；`privacyMode=true` 时 → false。
4. 从正门验证（真实签名是单入参对象，`src/core/createCore.ts:122`、UserMessageInput 见 56-66 行）：
   ```ts
   const ev = core.ingestUserMessage({ content: '窗口观察到…', sourceKind: 'observed' });
   assert.equal(ev.allowCloudRead, false);
   ```
5. 端到端堵漏验证：模拟 testbench 路径 `store.put(perceive(raw, {sourceKind:'observed'}))` → `allowCloudRead=false`。
6. 全量回归：`tests/perception.test.ts`、`tests/privacy.test.ts` 零改动通过；`tests/attribution.test.ts`、`tests/cognition.test.ts` 按上文授权补 fixture 后通过。

## 验收

- [ ] 上述测试全绿 + 三绿。
- [ ] `grep -rn "cloudReadDefault" src/` 复核：命中应为 config.ts 定义处、`src/index.ts:174` 的公共导出（**属公共 API，保留不动**，CONTRIBUTING 禁破坏 index.ts 导出）、store.ts 的 import 与 put 分流处——确认没有别的调用点拿它给 observed 证据算默认即可。
- [ ] CHANGELOG 记一条行为变化（方向是更保守）：**直接用 `SqliteEvidenceStore.put` 落 observed 证据的调用方，默认从"可上云"变为"不上云"；要上云需显式传 `allowCloudRead:true`**。
- [ ] `docs/internal/STATE.md`「当前可用接口」里证据层/观察摄入对应行**改写**（不追加，规矩见 `.agents/skills/docs-sync/SKILL.md`）：补一句"observed 缺省授权已下沉 put 层"。

## 影响面声明（AGENTS.md 要求）

行为变化只影响"经 put 落 observed 且没显式给授权位"的调用方。已知调用方盘点：`ingestObservations`（已显式传，无变化）、Host `/api/observe`（sanitize 后经 ingestObservation，无变化）、testbench `/api/observe`（**这就是要修的漏**，变化即修复）、库内测试 fixture（按上文补显式授权位）、外部第三方直连 put（保守方向变化，CHANGELOG 告知）。
