# MemoWeft 接入指南（Integration Guide）

> 面向宿主（Host）开发者：如何把 MemoWeft 这层「用户认知框架」接进你的应用 / Agent。
>
> 默认接入路径是 **Cloud-first**：先用 OpenAI-compatible 云端端点跑通，再按数据敏感度升级到 Cloud-guarded 或 Hybrid / local-sensitive。

---

## 1. MemoWeft 和宿主的边界

MemoWeft 是一个**被宿主 import 的库**。它自己不做聊天界面、不做人设、不管 UI，也不替宿主决定隐私政策。

| MemoWeft | 宿主应用 |
| --- | --- |
| 存证据、生成事件、沉淀认知、计算把握度 | 负责聊天、人设、语气、UI |
| 召回相关用户上下文 | 决定什么时候用、怎么表达 |
| 提供 `allowCloudRead` 等 evidence 授权位 | 负责隐私政策、用户同意、可见设置 |
| 保持模型 / 嵌入器可替换 | 决定云端、本地或混合部署 |

一句话：**MemoWeft 给“对用户的理解”，宿主决定“怎么使用这份理解”。**

---

## 2. 安装与导入

MemoWeft 已发布到 npm，宿主直接装：

```bash
npm install memoweft
```

（TypeScript 项目另需 `@types/node@^24`——库的公开类型里有 `node:sqlite`。）想从源码接（改库本身 / 跟最新提交）也行：

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm install
npm run typecheck && npm test && npm run build
```

从源码接时，宿主可用：

- git submodule；
- `npm install <本地路径>`；
- 直接引用 `../memoweft/src/index.ts`；
- 或引用 build 后的 `dist/index.js`。

> 要求：Node ≥ 24。项目使用 `node:sqlite` 等 Node 内置能力，且当前开发 / 测试以 Node 24 为准。

---

## 3. Cloud-first 配置

MemoWeft 读取 `.env`。推荐先用云端 OpenAI-compatible endpoint 跑通：

```ini
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# 可选：写路径小快模型。缺配会回退对话模型。
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model

# 可选：语义召回。缺配则召回降级为空。
MEMOWEFT_EMBED_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_EMBED_API_KEY=sk-xxxx
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

旧前缀 `DLA_*` 仍兼容；新接入一律推荐 `MEMOWEFT_*`。

部署模式详见 [`deployment.md`](./deployment.md)：

- **Cloud-first**：最快跑通，适合 demo / 原型 / 普通开发者接入。
- **Cloud-guarded**：仍用云端模型，但 `allowCloudRead=false` 的证据不进云端 prompt。
- **Hybrid / local-sensitive**：敏感观察走本地模型或本地嵌入器，低风险调用可走云端。

---

## 4. 30 秒心智模型

```txt
evidence（证据·原始事实） → event（事件·情境化） → cognition（认知·判断）
用户说了/做了什么          一段对话的情境摘要       对用户的理解，带把握度和溯源
```

- **读路径**：一轮对话 → 存证据 → 召回相关认知 → 注入回话。
- **写路径**：攒批整理证据 → 生成事件 → 更新认知 → 归因 → 重建索引。
- **读写解耦**：聊天时不等画像更新；画像更新在后台或手动触发。

---

## 5. 端到端最小接入

```ts
import {
  SqliteEvidenceStore,
  SqliteEventStore,
  SqliteCognitionStore,
  Conversation,
  updateProfile,
  loadLLMPool,
  loadEmbedConfig,
  OpenAICompatEmbedder,
  VectorRetriever,
  NullRetriever,
  config,
} from 'memoweft';

const DB = './my-app.db';

const evidenceStore = new SqliteEvidenceStore(DB);
const eventStore = new SqliteEventStore(DB);
const cognitionStore = new SqliteCognitionStore(DB);

const llmPool = loadLLMPool();
const chatLLM = llmPool.for('chat');
const writeLLM = llmPool.for('write');

const embedConfig = loadEmbedConfig();
const retriever = embedConfig
  ? new VectorRetriever(DB, new OpenAICompatEmbedder(embedConfig))
  : new NullRetriever();

const subjectId = config.identity.subjectId;

const convo = new Conversation({
  store: evidenceStore,
  retriever,
  cognitionStore,
  llm: chatLLM,
});

// 读路径：用户发来一轮消息。
const outcome = await convo.handle('我最近在赶一个副业项目，天天熬夜', {
  subjectId,
});

console.log(outcome.reply);
console.log(outcome.storedEvidence);
console.log(outcome.recall);

// 写路径：攒批或手动触发画像更新。
const upd = await updateProfile(subjectId, {
  evidenceStore,
  eventStore,
  cognitionStore,
  retriever,
  llm: writeLLM,
});

console.log(upd.consolidated.created);
console.log(upd.attributed.hypotheses);
console.log(upd.timings);
```

> 真实宿主里不建议每轮都立刻 `updateProfile()`。写路径较重，应该攒批、空闲、定时或手动触发。

---

## 6. 摄入行为观察

除了对话，宿主也可以把桌面、设备、窗口等观察统一灌进 evidence 层。

```ts
import { ingestObservations, activeWindowToObservation } from 'memoweft';

const obs = activeWindowToObservation({
  app: 'VS Code',
  title: 'memoweft',
  durationSec: 2400,
  occurredAt: new Date().toISOString(),
});

const res = ingestObservations(subjectId, [obs], {
  evidenceStore,
  hostId: 'my-desktop-host',
});

console.log(res.stored, res.skipped);
```

建议默认策略：

- 用户聊天 / 明确输入：可由宿主设置为 cloud-readable。
- 桌面 / 设备 / 健康 / 屏幕类观察：默认 `allowCloudRead=false`。
- 用户显式授权后，宿主再把对应证据标为可上云。

库内已设 busy_timeout=5000；仍不建议两个进程同时跑写路径。

---

## 7. 接入时别绕过的纪律

- 只有**用户消息 / 用户授权观察**进入 evidence。
- **助手回话不应当作证据**，避免系统自证。
- LLM 推测只能进入低置信候选 / hypothesis。
- 冲突先暴露，不自动覆盖。
- 写路径喂云端模型前必须尊重 `allowCloudRead`。
- 短期状态要走衰减 / 过期，不应永久注入。

---

## 8. 可替换点

| 部分 | 默认实现 | 替换目的 |
| --- | --- | --- |
| LLM client | `OpenAICompatClient` | 接不同云端 / 本地模型服务 |
| LLM pool | `loadLLMPool()` | 区分 chat / write 模型 |
| Embedder | `OpenAICompatEmbedder` | 接云端或本地 embedding endpoint |
| Retriever | `VectorRetriever` / `NullRetriever` | 后续可替换为混合检索、图检索等 |
| Stores | SQLite stores | 后续可迁移到其他存储后端 |

---

## 9. 常用导出

| 类别 | 导出 |
| --- | --- |
| 证据层 | `SqliteEvidenceStore`, `Evidence`, `EvidenceInput`, `ingestObservations` |
| 事件层 | `SqliteEventStore`, `distill` |
| 认知层 | `SqliteCognitionStore`, `Cognition`, `computeConfidence` |
| 写路径 | `updateProfile`, `consolidate`, `attribute`, `aggregateTrends`, `expire` |
| 读路径 | `Conversation`, `WorkingMemory`, `VectorRetriever`, `NullRetriever` |
| 主动询问 | `proposeAsk`, `revisitConflicts`, `AskProposal` |
| 模型 | `loadLLMPool`, `OpenAICompatClient`, `OpenAICompatEmbedder`, `loadEmbedConfig` |
| 便携记忆包 | `exportBundle`, `validateBundle`, `importBundle`, `MemoryBundle`, `ImportPlan` |
| 配置 | `config`, `MEMOWEFT_VERSION` |

真实导出以 [`src/index.ts`](../src/index.ts) 为准。

**便携记忆包（Phase 5-A）**：`exportBundle(subjectId, { evidenceStore, eventStore, cognitionStore })` 把某用户的完整三层记忆导出成可校验 JSON；`importBundle(bundle, { ...三个 store, transaction }, { mode: 'dryRun' | 'merge' })` 保真导入（保留原 id 与时间戳、按 id/originId 幂等去重、非法包不写库、可选事务防污染）。向量索引不入包，导入后调 `retriever.indexAll()` 重建召回。

---

## 10. 宿主最小责任清单

宿主至少要决定：

1. 哪些用户输入要存成 evidence；
2. 哪些观察数据允许摄入；
3. 哪些 evidence 可上云；
4. 什么时候触发 `updateProfile()`；
5. 如何展示 / 使用召回上下文；
6. 用户如何查看、纠正、删除、导出自己的记忆。

MemoWeft 只提供底层认知能力，最终用户体验由宿主完成。
