<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="assets/hero-light.svg">
  <img alt="MemoWeft —— 给 AI 装一块长期记忆，记住用户是谁，分得清事实和猜测，换模型也带得走。" src="assets/hero-dark.svg" width="100%">
</picture>

# MemoWeft

**给 AI 装一块长期记忆——记住用户是谁，还分得清哪些是事实、哪些只是猜的，换个模型也带得走。**

*把一条条零散的记忆线索，织成一张「这个人是谁」的布——但不假装每根线都一样可信。*

[![npm](https://img.shields.io/npm/v/memoweft?style=flat-square&labelColor=14110B&color=E2A75E)](https://www.npmjs.com/package/memoweft)
[![CI](https://img.shields.io/github/actions/workflow/status/memoweft/memoweft/ci.yml?style=flat-square&labelColor=14110B&label=CI)](https://github.com/memoweft/memoweft/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/badge/coverage-97.42%25-4A4438?style=flat-square&labelColor=14110B)](#项目状态)
[![runtime deps](https://img.shields.io/badge/运行时依赖-零-4A4438?style=flat-square&labelColor=14110B)](#它有什么)
[![Node](https://img.shields.io/badge/Node-%E2%89%A520-4A4438?style=flat-square&labelColor=14110B)](#当库用)
[![license](https://img.shields.io/badge/license-MIT-4A4438?style=flat-square&labelColor=14110B)](LICENSE)

[先跑起来](#一条命令跑起来) · [凭什么不一样](#它凭什么不是又一个向量记忆库) · [当库用](#当库用) · [看看宿主](#参考宿主长什么样) · [文档](#文档)

[English](./README.md) · **简体中文**

</div>

---

## 换个模型，AI 就把你忘光了

你跟一个 AI 助手聊了三个月，它慢慢摸清了你的作息、口味、脾气。然后你把底层模型一换——它两眼一抹黑，重新问你"你是谁"。

把上下文一股脑塞进 prompt 也不是办法：查不到来源（它凭什么这么认为？）、搬不走（下个模型用不了）、还越塞越长越贵。

**MemoWeft 把「对一个人的了解」当成一份能长期攒、能追溯、能搬家的资产**，而不是一段用完就丢的 prompt。

它是一个你 `import` 的库，不是一个应用：**不聊天、不装人设、不做界面**——那些是宿主的活。它只干一件事：把记忆织好、存住，需要时递给你。

---

## 一条命令跑起来

不想看文档？直接跑，两分钟见分晓：

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm install
npm run build
npm start -w @memoweft/host        # → http://localhost:7788
```

打开 `http://localhost:7788`，跟它聊几句家常。聊一会儿、等它在后台整理一下，**顶栏那颗「它记住我 N 件事」就会跟着往上跳数**——那是它悄悄攒下的、对你的理解。点开就能看它到底记住了些什么。

然后是最好玩的一步：顶栏一键，把「普通助手」切成「**星瑶**」（一个陪伴型人设）——**同一份记忆，换一张脸，记忆还在**。

记忆是底座，人设只是插在上面、随时能换的一张脸——星瑶是自带的一张，你也可以换成自己的。

> 想先配好模型再玩？第一次打开会有个配置引导，填一个 OpenAI 兼容的接口就行，云端本地都成。只想当库用、几行代码接进自己的应用？往下翻到 [当库用](#当库用) 那一节。

---

## 参考宿主长什么样

库本身不带界面——但自带的参考宿主（`@memoweft/host`）能让你看到「记忆优先」的应用是什么手感。下面每个界面都只走 Core 公开面，绝不直接碰底层存储。

**聊天时，记忆看得见地长出来。** 你一边聊，MemoWeft 一边把「记住了：……」的小气泡织进对话流，顶栏「它记住我 N 件事」跟着跳数——记忆随着你说话实时形成，而且始终归你掌控。

![MemoWeft 参考宿主 —— 聊天界面，内嵌记忆气泡、左侧会话列表、顶栏「它记住我 N 件事」计数](assets/screenshot-chat.png)

**记忆是张图，不是一条流水账。** 手搓的 canvas 力导向图，把主体、证据、事件、认知连起来——类型化的彩色边（支持 / 矛盾）、可拖拽缩放、点节点看详情，零依赖。

![MemoWeft 记忆图谱 —— 手搓 canvas 力导向图，把证据、事件、认知用类型化彩色边连起来](assets/screenshot-memory-graph.png)

**它记住了什么，凭什么。** 每条理解都是一张卡，带大白话的把握度档，能展开溯源到「根据你哪句话看出来的」——还能标失效或安全删除。

![MemoWeft 记忆管理页 —— 「对你的理解」卡片，每张带把握度档和可展开的溯源](assets/screenshot-memory-manage.png)

**云端优先，但守得住隐私。** 每条记忆线索都标了来源（亲口说的 / 观察到的 / 推测的），带一个「能不能给云端模型用」的单条开关——观察类数据默认只留本地。

![MemoWeft 记忆线索列表 —— 每条线索带来源类型和单条的本地/云端授权开关](assets/screenshot-evidence-authorize.png)

**你的记忆归你。** 一键把整份记忆包导出到本地文件（不含密钥和聊天记录），或用 dry-run 预览合并导入，或在打字确认后清空重来。

![MemoWeft 数据与备份 —— 导出完整记忆包、dry-run 预览合并导入、打字确认的恢复出厂](assets/screenshot-data-backup.png)

---

## 它凭什么不是「又一个向量记忆库」

普通记忆库的逻辑是：存进去 = 当真，来了新的就覆盖旧的。MemoWeft 不这么干——它对**「允许相信什么」很较真**。这套「认知纪律」才是它真正的不一样：

- **记 ≠ 信。** 用户亲口说的，和大模型猜的，不是一回事。模型推出来的先当**低把握度**的候选，绝不直接混成事实。
- **矛盾先摊开，不偷偷合并。** 你上周说爱喝咖啡、这周说戒了，它不会默默选一个——而是把冲突标出来，等确认。
- **把握度它自己算，不听模型自报。** 一条理解有多可信，由证据强度和反复印证程度决定，不是让大模型拍脑袋打个分。
- **情绪会淡，偏好留得住。** "今天心情差"这种会随时间衰减；"我不吃香菜"这种明确偏好不会被自动忘掉。
- **禁止自证。** 助手自己说过的话、用户的沉默，都不算证据——不然它会越聊越信自己编的。

| | 普通向量 / 记忆库 | MemoWeft | eval 背书 |
| --- | --- | --- | --- |
| 遇到矛盾 | 覆盖 / 取最新 | **暴露冲突**，不偷偷合并 | `EVAL-C01`–`C07` |
| 采信 | 存了就当真 | **记 ≠ 信** | `EVAL-T01`、`T02` |
| 模型猜测 | 可能混成事实 | **低把握度假设** | `EVAL-T03`–`T05` |
| 过期 | 永久有效 | **分型过期**（情绪快忘、偏好留住） | `EVAL-M01`–`M07` |

*上表每一行都有带编号的 eval 用例支撑*——断言写在
[`tests/eval/cognition-discipline.eval.test.ts`](./tests/eval/cognition-discipline.eval.test.ts)，
随 `npm test` 一起跑，所以这些不是嘴上说说，是能跑的检查。

一句话：别人是「记得住」，MemoWeft 想做到的是「**记得住，还不乱用**」。

---

## 它有什么

- **认知纪律**——记 ≠ 信、冲突暴露、把握度自算、分型过期（上面那套）。
- **换模型不丢记忆**——认知层是 SQLite 里的普通数据，不焊死在模型权重里。换 GPT、换 Claude、换本地模型，记忆照样在。
- **每条判断都可追溯**——它为什么这么认为？一路能回溯到形成它的那条原始证据。
- **一套记忆，多张脸**——体验插件决定语气和人设（自带普通助手 + 星瑶两张脸），底层记忆共用。
- **云端优先，但不无脑上云**——模型调用可以走云端，但每条证据能单独控制「能不能上云」；桌面/行为观察默认不上云。
- **能感知，不只会聊**——除了对话，还能吃「行为观察」（比如活动窗口采集插件），当作证据沉淀。
- **零运行时依赖**——存储 / HTTP / 向量全用 Node 内置的 `node:sqlite` / `node:http` / `node:fs`，一个第三方包都不装，`npm install memoweft` 什么传递依赖都不拖。**Node ≥ 24 开箱即用**（`node:sqlite` 到 24 才转正）；**Node 20/22** 上内置模块不可用，装个可选驱动 `better-sqlite3`（`npm i better-sqlite3`）即可——它是可选 peer 依赖，不算进零依赖基线。

---

## 三层记忆，怎么织的

```mermaid
flowchart LR
  subgraph 写路径 [写路径 · 织布]
    E["证据 evidence<br/>(原始事实)"] --> V["事件 event<br/>(放进情境)"] --> C["认知 cognition<br/>(判断 · 画像)"]
  end
  subgraph 读路径 [读路径 · 从布上取一块]
    Q["用户消息"] --> S["召回相关认知"] --> INJ["注入回话"]
  end
  C -. 建索引 .-> S
```

| 层 | 大白话 |
| --- | --- |
| **证据 evidence** | 唯一真相：用户说了什么、观察到了什么。这层只存事实，不存判断。 |
| **事件 event** | 把证据放进情境：当时发生了什么。 |
| **认知 cognition** | 判断层：一条带把握度、能溯源的用户画像。 |

读写是**解耦**的：读路径轻、同步；写路径攒批、异步——所以整理记忆不会卡住回话。

---

## 当库用

**1. 装**（Node ≥ 24 开箱即用；Node 20/22 另跑 `npm i better-sqlite3`）：

```bash
npm install memoweft
```

**2. 配个对话模型**——项目根建 `.env`，填任意 OpenAI 兼容端点：

```bash
MEMOWEFT_LLM_BASE_URL=https://你的端点/v1
MEMOWEFT_LLM_API_KEY=sk-...
MEMOWEFT_LLM_MODEL=gpt-4o-mini
```

**3. 存成 `demo.mjs`，`node --env-file=.env demo.mjs` 跑**——统一入口 `createMemoWeftCore` 一行装配好三层存储、召回器、模型池（都从 `.env` 读，没配就自动降级、不崩）：

```ts
import { createMemoWeftCore } from 'memoweft';

// 一行装配：三层 store + 召回器 + 模型池全从 .env 读。
const core = createMemoWeftCore({ dbPath: './memoweft.db' });

const subjectId = 'user-42';

// 1）把用户原话存成证据。
await core.ingestUserMessage({
  subjectId,
  content: '我下午三点后只喝无咖啡因的，咖啡因毁我睡眠。',
});

// 2）整理成带把握度的画像（攒批写路径）。
await core.updateProfile({ subjectId });

// 3）回话时召回相关用户上下文并注入。
const turn = await core.handleConversationTurn({
  subjectId,
  message: '下午推荐我喝点什么？',
});
console.log(turn.reply);   // 回话里会带上"你下午不喝含咖啡因的"
console.log(turn.recall);  // 这轮召回并注入了哪些理解

core.close();
```

> TypeScript 项目按常规装 `@types/node` 即可。Node 20/22 上另装可选驱动 `better-sqlite3`（`npm i better-sqlite3`）。没配嵌入器也能跑：召回自动降级为空，证据照写，只是回话不做语义召回。仓库内的可跑版本见 [`examples/minimal.ts`](./examples/minimal.ts)；想直接用底层部件（`openStores` / `Conversation` / `updateProfile` / 召回器）见 [`docs/integration.zh-CN.md`](./docs/integration.zh-CN.md)。

---

## 生态：接进 MCP 和 Vercel AI SDK

核心库 `import` 就能用，但你不必自己接线。两个独立发布的轻适配器，让 MemoWeft 藏到你可能已经在用的接口后面：

| 包 | 给你什么 |
| --- | --- |
| [`@memoweft/mcp-server`](./packages/mcp-server) | 一个 Model Context Protocol 服务器，把 MemoWeft 用 **6 个工具**（5 读 + 1 受控写）暴露出去——任何支持 MCP 的客户端（Claude Desktop、IDE、Agent）都能召回和记录记忆。 |
| [`@memoweft/adapter-ai-sdk`](./packages/adapter-ai-sdk) | **Vercel AI SDK**（`ai` v7）的中间件：模型调用前注入召回，流结束时落库新证据——几行代码接上记忆，不用写胶水。需 Node ≥ 22。 |

两个都建在同一个核心上，遵守同一套认知纪律和 Cloud Guard 规则。核心包本身保持**零运行时依赖**。

---

## 模型部署：云端优先，但不是无脑上云

默认接入体验是**云端友好**：填个 OpenAI 兼容的云端接口就能先跑起来，不用一上来就装本地模型。但这不等于所有原始证据都能直接发云端——边界是：

- **模型调用可以云端优先。** 对话、写路径、归因、趋势、嵌入都能指向云端 OpenAI 兼容接口。
- **证据决定能不能上云。** 每条 evidence 带 `allowCloudRead` 之类的授权位。
- **行为观察默认保守。** 桌面窗口、屏幕、剪贴板、文件、健康/睡眠等观察，默认**不上云**，除非宿主明确征得同意。
- **同意权在宿主。** MemoWeft 只给模型开关和过滤钩子；隐私政策、同意 UI 归宿主。

| 模式 | 适合谁 | 说明 |
| --- | --- | --- |
| **Cloud-first** | Demo、原型、日常开发接入 | 对话 / 写路径 / 嵌入都走云端，最快跑起来 |
| **Cloud-guarded** | 用云端模型的真实应用 | 仍用云端模型，但 `allowCloudRead=false` 的证据会被过滤掉 |
| **Hybrid / 本地敏感** | 隐私敏感的桌面助手 | 敏感观察留本地，写路径还能跑在**本地模型档**（`MEMOWEFT_WRITE_LLM_TIER=local`），观察类证据不出机器就被消化 |

完整说明见 [`docs/deployment.md`](./docs/deployment.md)。

---

## 配置

从环境变量读模型。推荐 `MEMOWEFT_*` 前缀；旧的 `DLA_*` 仍兼容。

| 用途 | 变量 |
| --- | --- |
| 对话模型 | `MEMOWEFT_LLM_BASE_URL` · `MEMOWEFT_LLM_API_KEY` · `MEMOWEFT_LLM_MODEL` |
| 写路径模型 | `MEMOWEFT_WRITE_LLM_BASE_URL` · `MEMOWEFT_WRITE_LLM_API_KEY` · `MEMOWEFT_WRITE_LLM_MODEL` · `MEMOWEFT_WRITE_LLM_TIER` |
| 嵌入器 | `MEMOWEFT_EMBED_BASE_URL` · `MEMOWEFT_EMBED_API_KEY` · `MEMOWEFT_EMBED_MODEL` |

三组都接受 OpenAI 兼容接口。云端最省事；Ollama、LM Studio 等本地端点也支持。输出语言默认英文、可配置（`config.language` / `MEMOWEFT_LANG`）。完整 env 说明见 [`docs/INSTALL.zh-CN.md`](./docs/INSTALL.zh-CN.md)。

---

## 它做什么 / 不做什么

| MemoWeft（库） | 宿主应用 |
| --- | --- |
| 摄入证据、织三层、算把握度、提供可溯源的用户上下文 | 聊天、人设、语气、界面、什么时候开口 |
| 保留模型可切换，记录 evidence 级授权 | 隐私政策、同意 UI、到底存不存 |
| 按请求把相关用户上下文递回去 | 决定怎么用（回话 / 工具调用 / 桌面助手 / Agent） |

主要导出见 [`src/index.ts`](./src/index.ts)，接入说明见 [`docs/integration.zh-CN.md`](./docs/integration.zh-CN.md)；稳定性分层与破坏性变更政策见 [`docs/memory-surface-contract.md`](./docs/memory-surface-contract.md)。

---

## 性能

老实数字，不设阈值。基准测试把 **1 万条证据**灌进一个用完即弃的内存库，跑一次完整的 `updateProfile` 写路径（用内置的 `result.timings` 测）、以及经公开入口的平均 `recall` 延迟——用一个离线桩模型，让你看到的就是存储 + 编排的成本。不设 CI 门（基准慢且抖）。

**1 万条证据：** `updateProfile` ≈ **462 ms** · `recall` ≈ **0 ms**（`NullRetriever` 路径——真实召回延迟是你嵌入器的成本）
· 测于 Node `24.15.0` · `win32/x64`，模型打桩——本机数字，不是保证。完整拆解见 [`docs/perf.md`](./docs/perf.md)。

```bash
npm run build && npm run bench   # 先 build：脚本从 dist/ 导入，不是 src
```

细节和旋钮见 [`docs/perf.md`](./docs/perf.md)。

---

## 项目状态

**早期 alpha，但在稳定推进。** Core、一个参考宿主、一个 MCP 服务器、一个 AI-SDK 适配器、两个插件都已就位并有测试；算法和认知纪律是真的。接口在 1.0 前仍会动——1.0 前的破坏性变更走 minor 号。

最新版本：**0.5.0**——`npm install memoweft`。

**已经能用**

- **认知内核**——证据 → 事件 → 认知三层、画像 + 召回、纠正闭环、归因 + 主动询问、周期后台（衰减、分型过期、召回门控、冲突复看、趋势）。
- **统一入口**——`createMemoWeftCore` + 受控记忆管理 API（标失效 / 授权 / 安全删除 / 合并 / 归档 / 完整性检查），宿主不直接碰底层存储。
- **可迁移与图谱**——便携记忆包（导入 / 导出 / 校验，保真 + 幂等）+ 图谱后端，**外加**参考宿主里手搓的 canvas 图谱前端。
- **Cloud Guard 与模型档**——写 / 趋势 / 归因路径上云过滤，外加写路径的本地/云端模型档，让观察类证据能用本地模型消化。
- **Token 计量**——`core.usage()` 提供原始 LLM token 计数（不带价目表）。
- **参考宿主**（`apps/memoweft-host`）——聊天、配置向导、记忆管理页、记忆图谱、多会话、备份 / 恢复、恢复出厂，全走 Core 公开面。
- **体验与插件契约 v2**——同一 core 上可换人设（普通助手 + 星瑶），外加只观察的 Core 钩子（`onLoad` / `onUserMessage` / `onObservation`）+ `PluginContext`。
- **生态包**——[`@memoweft/mcp-server`](./packages/mcp-server)（MCP）和 [`@memoweft/adapter-ai-sdk`](./packages/adapter-ai-sdk)（Vercel AI SDK）。
- **采集插件**——活动窗口采集器作为仓库内独立插件（`@memoweft/collector-active-window`，未发布到 npm），经宿主 `/api/observe` 落库。
- **Schema 版本化 + 迁移器**——`PRAGMA user_version` + 迁移运行器（事务化、自动备份、dry-run、防降级）；老库无损打开。
- **已发布到 npm**——`memoweft` 现为 `0.5.0`，Node 20/22 经可选驱动 `better-sqlite3` 支持（engines 放宽到 `>=20`）。

**还没做 / 接下来**

- 召回质量 v2——相似度阈值门控、按用途/范围/内容类型过滤、召回可解释、负反馈。
- 更多采集器和体验插件；更多框架适配器（比如 LangChain）。

往哪走、以及为什么"库为主、Host 当演示"，见 [`ROADMAP.md`](./ROADMAP.md)；当前在做什么见 [`CURRENT.md`](./CURRENT.md)。

> **永远全开源。** 核心库现在是、将来也是 MIT 全开源——没有隐藏的企业版，也不会把功能拆成"开源一套、付费一套"。将来若有托管服务，卖的只会是省事，不会是被扣下的功能。

> **怎么维护的。** MemoWeft 由**单人 + AI 协作维护**，以 **best-effort（尽力而为）** 的节奏推进——**没有 SLA**、不承诺固定响应时间。唯一插队的一类：**安全问题优先分诊**。怎么报安全问题见 [`SECURITY.md`](./.github/SECURITY.md)。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/INSTALL.zh-CN.md`](./docs/INSTALL.zh-CN.md) | 安装、配 `.env`、跑测试、起宿主 / 测试台 |
| [`docs/deployment.md`](./docs/deployment.md) | 云端 / 云守护 / 混合部署与隐私模式 |
| [`docs/architecture.md`](./docs/architecture.md) | 三层数据、读写解耦、认知纪律、可替换点 |
| [`docs/integration.zh-CN.md`](./docs/integration.zh-CN.md) | 宿主接入指南 + 导出表 |
| [`docs/memory-surface-contract.md`](./docs/memory-surface-contract.md) | 记忆面契约 v1：稳定性分层 + 对宿主的破坏性变更政策 |
| [`docs/plugin-contract.md`](./docs/plugin-contract.md) | 插件契约 v2：只观察的钩子 + `PluginContext` |
| [`docs/naming.md`](./docs/naming.md) | 双语命名与定位口径 |
| [`docs/perf.md`](./docs/perf.md) | 基准（1 万证据）：实测 `updateProfile` / `recall` 数字 + 怎么复现 |
| [`plugins/collector-active-window/README.md`](./plugins/collector-active-window/README.md) | 活动窗口采集插件（采集 → 宿主 → core 数据流） |
| [`docs/PUBLISHING.md`](./docs/PUBLISHING.md) | 打包和 npm 发布流程 |
| [`examples/minimal.ts`](./examples/minimal.ts) | 最小写→读闭环（需对话模型） |
| [`examples/memory-management.ts`](./examples/memory-management.ts) | 受控记忆管理（`core.memory.*`，需对话模型） |
| [`examples/portable-bundle.ts`](./examples/portable-bundle.ts) | 导出/导入便携记忆包（无需模型即可跑） |

内部设计笔记与历史白板（项目地图、`STATE`）在 [`docs/internal/`](./docs/internal/)——是「项目当初怎么造的」历史背景，用库或改代码都不需要读。

---

## 参与

任何代码改动都要保持三绿：

```bash
npm run typecheck && npm test && npm run build
```

刚接手（AI 或人）？先读 [`AGENTS.md`](./AGENTS.md) 和 [`CURRENT.md`](./CURRENT.md)；硬规矩在 [`CONTRIBUTING.md`](./CONTRIBUTING.md)。

## License

[MIT](./LICENSE) © 2026 MemoWeft contributors.

## 致谢

独立构建，借鉴了 **Mem0** 和 **Graphiti** 的思路；接口保持隔离，方便后续替换。
