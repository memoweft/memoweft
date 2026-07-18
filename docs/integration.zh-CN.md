# MemoWeft 接入指南（Integration Guide）

[English](./integration.md) | **简体中文**

> 面向宿主（Host）开发者：如何把 MemoWeft 这层「用户认知框架」接进你的应用 / Agent。
>
> 默认接入路径是 **Cloud-first**：先用 OpenAI-compatible 云端端点跑通，再按数据敏感度升级到 Cloud-guarded 或 Hybrid / local-sensitive。

---

## 1. MemoWeft 和宿主的边界

MemoWeft 是一个**被宿主 import 的库**。它自己不做聊天界面、不做人设、不管 UI，也不替宿主决定隐私政策。

| MemoWeft                                 | 宿主应用                         |
| ---------------------------------------- | -------------------------------- |
| 存证据、生成事件、沉淀认知、计算把握度   | 负责聊天、人设、语气、UI         |
| 召回相关用户上下文                       | 决定什么时候用、怎么表达         |
| 提供 `allowCloudRead` 等 evidence 授权位 | 负责隐私政策、用户同意、可见设置 |
| 保持模型 / 嵌入器可替换                  | 决定云端、本地或混合部署         |

一句话：**MemoWeft 给“对用户的理解”，宿主决定“怎么使用这份理解”。**

---

## 2. 安装与导入

MemoWeft 已发布到 npm，宿主直接装：

```bash
npm install memoweft
```

（TypeScript 项目按常规装 `@types/node` 即可；Node 20/22 上另装可选驱动 `better-sqlite3`。）想从源码接（改库本身 / 跟最新提交）也行：

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

> 要求：**Node ≥ 24 开箱即用**（存储用内置 `node:sqlite`）；**Node 20/22** 上内置模块不可用，需装可选驱动 `better-sqlite3`（`npm i better-sqlite3`）。开发 / 跑 `.ts` 测试仍以 Node ≥24 为准（Node 22 需 22.18+ 才默认支持原生剥 `.ts` 类型，Node 20 不支持）。安装细节见 [`INSTALL.zh-CN.md`](./INSTALL.zh-CN.md)。

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

# 可选：语义召回。缺配则召回降级为关键词检索（FTS5）。
MEMOWEFT_EMBED_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_EMBED_API_KEY=sk-xxxx
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

旧前缀 `DLA_*` 仍兼容；新接入推荐使用 `MEMOWEFT_*`。

部署模式详见 [`deployment.md`](./deployment.md)：

- **Cloud-first**：适合 demo / 原型 / 普通开发者接入。
- **Cloud-guarded**：仍用云端模型，但 MemoWeft 写路径会把 `allowCloudRead=false` 的记录从其云端模型 prompt 中筛掉。其他转发、存储与访问控制仍由宿主负责。
- **Hybrid / local-sensitive**：敏感观察走本地模型或本地嵌入器，低风险调用可走云端。

---

## 4. 30 秒心智模型

| evidence（证据·来源记录）      | event（事件·情境化） | cognition（认知·判断）       |
| ------------------------------ | -------------------- | ---------------------------- |
| 说了、观察到或由工具返回的内容 | 一段对话的情境摘要   | 对用户的理解，带把握度和溯源 |

- **读路径**：一轮对话 → 存证据 → 召回相关认知 → 注入上下文。
- **写路径**：攒批整理证据 → 生成事件 → 更新认知 → 归因 → 重建索引。
- **读写解耦**：画像更新与对话路径分离；宿主可在后台或按需运行它。

---

## 5. 端到端最小接入

推荐经统一入口 `createMemoWeftCore` 调 Core：一行把三层 store + 召回器 + 模型池装好，环境配置从 `.env` 读取。未配置模型时仍可构造用于存储和管理；调用需要该模型的操作时会报告错误。

<!-- snippet:skip (needs a live model) -->

```ts
import { createMemoWeftCore } from 'memoweft';

// 一行装配：三层 store + 召回器 + 模型池；环境配置从 .env 读取。
// subjectId / hostId 不传则用默认（config.identity：'owner' / 'local'）。
const core = createMemoWeftCore({ dbPath: './my-app.db' });

// 自检：能否聊天 / 能否语义召回（决定要不要提示用户去配 .env）。
const { llmReady, embedReady } = core.health();

// 读路径：用户发来一轮消息 → 存证据 → 召回相关认知 → 注入上下文。
const turn = await core.handleConversationTurn({
  message: '我最近在赶一个副业项目，天天熬夜',
});
console.log(turn.reply);
console.log(turn.storedEvidence);
console.log(turn.recall);

// 写路径：攒批或手动触发画像更新（distill → consolidate → attribute → 重建索引）。
const upd = await core.updateProfile();
console.log(upd.consolidated.created);
console.log(upd.timings);

// 用完收口连接（注入的召回器归调用方管，不会被关）。
core.close();
```

> 真实宿主里不建议每轮都调用 `updateProfile()`。写路径较重；Core 暴露一次性操作，攒批、空闲定时器、队列与按 subject 串行控制由宿主负责。
>
> 需要更细的手工装配（自己 `new Sqlite*Store` / 注入自定义召回器）？可参考 [`examples/minimal.ts`](../examples/minimal.ts) 与[可替换点](#8-可替换点)。

---

## 6. 摄入行为观察

除了对话，宿主也可以把桌面、设备、窗口等观察统一灌进 evidence 层。Core 只提供**通用观察摄入口** `core.ingestObservation({ observations })`：它把宿主标准化好的 `Observation` 落成 `observed` 证据（默认不上云、带 `originId` 幂等）。

<!-- snippet:skip (continues the snippet above; needs a live model) -->

```ts
import type { Observation } from 'memoweft';

// 宿主自己把外部信号标准化成一条 Observation（这里手工构造一条活动窗口观察）。
const obs: Observation = {
  kind: 'active_window',
  occurredAt: new Date().toISOString(),
  content: '在 VS Code（memoweft）停留约 40 分钟',
  originId: 'win-session-123', // 可选幂等键：同一窗口会话不重复落
  // 授权位不传 → 走 observed 保守默认（本地可读 / 不上云 / 可推画像）。
};

const stored = await core.ingestObservation({ observations: [obs] });
console.log(stored.length); // 本次新落库的 observed 证据条数
```

> **真采集器不在 Core 里。** 「怎么从操作系统抓活动窗口」是采集插件（Plugin 层）的活，不属于 Core；见[边界说明](./internals/boundaries.zh-CN.md)。真实数据流是：采集插件采窗口 → 映射成 `Observation` → POST 宿主 `/api/observe`（宿主审核采集总开关与授权策略）→ 宿主调 `core.ingestObservation` 落库。参考实现见 [`plugins/collector-active-window/README.md`](../plugins/collector-active-window/README.md)。

建议默认策略：

- 用户聊天 / 明确输入：可由宿主设置为 cloud-readable。
- 桌面 / 设备 / 健康 / 屏幕类观察：默认 `allowCloudRead=false`。
- 用户显式授权后，宿主再把对应证据标为可上云。

库内已设 busy_timeout=5000；仍不建议两个进程同时跑写路径。

---

## 7. 接入时别绕过的纪律

- 使用 Core 的**内置摄入方法**时，用户消息、观察与工具结果会成为 evidence 记录。
- 内置路径不会把助手回复持久化为 evidence。宿主若摄入或持久化其他内容，仍需自行维护这一区分。
- LLM 推测只能进入低置信候选 / hypothesis。
- 冲突先暴露，不自动覆盖。
- MemoWeft 写路径会在选择云端模型 prompt 的记录时使用 `allowCloudRead`；该标记不是访问控制或加密机制，宿主仍需为其他数据流实施自己的控制。
- 短期状态要走衰减 / 过期，不应永久注入。

---

## 8. 可替换点

统一入口 `createMemoWeftCore` 已把下面这些默认装好；需要换实现时，通过它的选项注入（如 `retriever` / `embedder` / `llm`），或从源码手工装配（见 [`examples/minimal.ts`](../examples/minimal.ts)）。

| 部分       | 默认实现                                                                                                        | 替换目的                        |
| ---------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| LLM client | `OpenAICompatClient`                                                                                            | 接不同云端 / 本地模型服务       |
| LLM pool   | `loadLLMPool()`                                                                                                 | 区分 chat / write 模型          |
| Embedder   | `OpenAICompatEmbedder`                                                                                          | 接云端或本地 embedding endpoint |
| Retriever  | 有 embedder 时 `VectorRetriever`；无 embedder 时 `KeywordRetriever`（FTS5）；仅 FTS5 不可用时为 `NullRetriever` | 后续可替换为混合检索、图检索等  |
| Stores     | SQLite stores                                                                                                   | 后续可迁移到其他存储后端        |

---

## 9. 常用导出

绝大多数宿主只需 `createMemoWeftCore` + 它返回的门面（`core.memory` / `core.portable` / `core.graph`）+ 领域类型。下面列出常用面。

| 类别                        | 导出                                                                                                               |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 统一入口                    | `createMemoWeftCore`, `MemoWeftCore`                                                                               |
| 证据层                      | `Evidence`, `EvidenceInput`, `SourceKind`                                                                          |
| 事件层                      | `Event`, `EventWithEvidence`                                                                                       |
| 认知层                      | `Cognition`, `CognitionWithSources`, `ContentType`, `CredStatus`                                                   |
| 观察摄入                    | `Observation`（配 `core.ingestObservation`）                                                                       |
| 会话返回形状                | `TurnOutcome`, `RecalledCognition`                                                                                 |
| 受控记忆管理                | `MemoryManagementAPI`（门面 `core.memory`）                                                                        |
| 便携记忆包                  | `MemoryBundle`, `ImportPlan`（门面 `core.portable`）                                                               |
| 图谱视图                    | `MemoryGraphPayload`（门面 `core.graph`）                                                                          |
| 模型 / 召回（可替换注入点） | `OpenAICompatClient`, `OpenAICompatEmbedder`, `loadLLMPool`, `loadEmbedConfig`, `VectorRetriever`, `NullRetriever` |
| 配置 / 版本                 | `config`, `MEMOWEFT_VERSION`                                                                                       |

真实导出以 [`src/index.ts`](../src/index.ts) 为准。

**便携记忆包**：`core.portable.exportBundle({ subjectId })` 把某用户的完整三层记忆导出成可校验 JSON；`core.portable.importBundle(bundle, { mode: 'dryRun' | 'merge' })` 保真导入（保留原 id 与时间戳、按 id/originId 幂等去重、非法包不写库、可选事务防污染）。向量索引不入包，导入后画像更新（`core.updateProfile`）会重建召回索引。可跑示例见 [`examples/portable-bundle.ts`](../examples/portable-bundle.ts)。

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
