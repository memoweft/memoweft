<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/hero-light.svg">
  <img alt="MemoWeft — 面向 AI 应用的长期记忆" src="./assets/hero-dark.svg" width="100%">
</picture>

# MemoWeft

### 让 AI 记得你，同时永远分清：哪些是你说的，哪些只是它猜的。

MemoWeft 是面向 TypeScript AI 应用的开源长期记忆引擎。它把用户原话、行为观察、模型推断和未解决的冲突分别保存，让记忆可以追溯、纠正、管理和迁移，并存放在由你的应用控制的 SQLite 中。

[![npm stable](https://img.shields.io/npm/v/memoweft?style=flat-square&label=stable&labelColor=14110B&color=E2A75E)](https://www.npmjs.com/package/memoweft)
[![CI](https://img.shields.io/github/actions/workflow/status/memoweft/memoweft/ci.yml?style=flat-square&labelColor=14110B&label=CI)](https://github.com/memoweft/memoweft/actions/workflows/ci.yml)
[![Node](https://img.shields.io/badge/Node-20%20%7C%2022%20%7C%2024-4A4438?style=flat-square&labelColor=14110B)](./docs/INSTALL.zh-CN.md)
[![license](https://img.shields.io/badge/license-MIT-4A4438?style=flat-square&labelColor=14110B)](./LICENSE)

[为什么需要它](#为什么-ai-记忆需要一条可追溯的路径) · [离线演示](#无需-api-key-的离线体验) · [快速开始](#快速开始) · [生态集成](#生态集成) · [信任边界](#信任和隐私的本地边界) · [文档](#文档与社区)

[English](./README.md) · **简体中文**

</div>

> [!IMPORTANT]
> MemoWeft 是宿主应用直接导入的库——不是聊天产品、托管记忆服务、人设框架、向量数据库或 Agent 框架。

## 为什么 AI 记忆需要一条可追溯的路径

今天的 AI 已经很会对话，却未必能可靠地记住一个人。

跨越多次对话后，它可能忘记重要背景；遇到新信息时，它可能悄悄覆盖旧记录；一句模型猜测，也可能在后续对话里被当成用户亲口说过的事实。换一个模型或宿主，积累的记忆还可能无法带走。

MemoWeft 不替模型武断地决定“真相”。它保留信息从哪里来、何时出现、是否冲突，以及系统为什么形成某条记忆，让应用和用户都能看见从证据到召回的完整路径。

<table>
  <tr>
    <td width="33%" valign="top">
      <strong>证据始终是证据</strong><br><br>
      用户原话、外部观察、工具结果和模型推断保留不同来源。
    </td>
    <td width="33%" valign="top">
      <strong>冲突始终可见</strong><br><br>
      纠正会保留历史，未解决的矛盾不会被静默覆盖。
    </td>
    <td width="33%" valign="top">
      <strong>记忆仍由你掌握</strong><br><br>
      宿主可以检查、管理、导出、校验并导入带版本的记忆包。
    </td>
  </tr>
</table>

数值置信度由规则计算，不直接采用模型对自己的主观打分。临时状态可以比长期事实和偏好更快失效。内置摄入路径也不会因为助手自己说过一句话，就把它当成用户证据。

[了解六条记忆纪律](./docs/concepts/README.zh-CN.md) · [阅读架构说明](./docs/internals/architecture.zh-CN.md)

## 无需 API Key 的离线体验

准备好 Node 24 后运行：

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
node examples/no-key-demo.ts
```

依赖安装完成后，这个确定性演示不需要 API Key、不访问网络、使用内存数据库，也不会写入磁盘。

```text
[limited   ] conf  600/1000  The user lives in Osaka  — stated memory
[conflicted] conf  480/1000  The user lives in Tokyo  — conflict kept, not overwritten
[candidate ] conf  200/1000  The user probably works somewhere central  — guess (low confidence)

Summary: 3 cognitions, 1 in conflict-exposed state; inference remains labeled and rule-scored separately from stated memory.
Done. (in-memory database — nothing written to disk)
```

它展示用户亲口说过的内容、模型推断出的低置信猜测，以及不会被静默覆盖的冲突。这个演示验证的是 MemoWeft 的记忆规则，不是模型质量排行榜。要继续查看纠正历史和分型衰减，运行 `npm run demo`。

[阅读四幕演示说明](./docs/demo-script.zh-CN.md) · [查看演示源码](./examples/no-key-demo.ts)

## 放进真实产品后是什么样子

[WeftMate](https://www.weftmate.com/) 是一款使用 MemoWeft 构建的桌面产品。它把 Core 的记忆模型呈现为可见的用户画像、来源链路、冲突视图和用户控制。

下面展示的是 **WeftMate 的产品 UI**，不是 MemoWeft Core 自带的界面。MemoWeft 提供记忆层与可移植数据契约；实际产品体验仍由宿主应用负责。

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="./assets/weftmate-memory-profile.png" alt="WeftMate 记忆画像，显示记忆类型、把握度、来源话语与控制">
    </td>
    <td width="50%" valign="top">
      <img src="./assets/weftmate-chat.png" alt="WeftMate 对话界面，展示短回复在写入记忆前被解析">
    </td>
  </tr>
  <tr>
    <td valign="top"><strong>知道一条记忆从哪里来。</strong>每条内容都可以展示类型、把握度和来源链路。</td>
    <td valign="top"><strong>短回复先解释，再写入。</strong>一句简短确认不会把助手提出的内容直接升级为用户原话。</td>
  </tr>
</table>

[查看证据图谱](./assets/weftmate-memory-graph.png) · [查看便携数据控制](./assets/weftmate-data-portability.png) · [运行参考宿主](./docs/reference-host.zh-CN.md)

## 快速开始

Node 24+ 是最简单的使用方式：

```bash
npm install memoweft
```

Node 20 或 22 还需要安装可选 SQLite 驱动：

```bash
npm install better-sqlite3
```

保存为 `quickstart.mjs`：

```js
import { createMemoWeftCore } from 'memoweft';

const core = createMemoWeftCore({ dbPath: ':memory:' });

await core.ingestUserMessage({
  subjectId: 'alice',
  content: '下午三点后我只喝低因咖啡，咖啡因会影响睡眠。',
});

for (const item of core.memory.listEvidence({ subjectId: 'alice' })) {
  console.log(item.sourceKind, '·', item.rawContent);
}

core.close();
```

运行：

```bash
node quickstart.mjs
```

保存并读回原始证据不需要模型或网络。把证据整理成画像、区分猜测与事实并参与后续召回，需要配置一个 OpenAI-compatible 聊天模型；嵌入模型是可选项，没有它时 Core 通常使用本地 FTS5 关键词召回。

[继续阅读五分钟入门指南](./docs/getting-started.zh-CN.md)

## 它怎样工作

MemoWeft 让信息从来源到召回的路径始终明确：

```text
用户原话 · 外部观察 · 工具结果
                 │
                 ▼
               证据
                 │  保留来源
                 ▼
               事件
                 │
                 ▼
               认知  ◀── 纠正与冲突
                 │
                 ▼
               召回
```

应用接入时推荐使用 `createMemoWeftCore()` facade。底层导出用于高级组合，并分别标注 stable、experimental 或 internal 支持层级。

[API 接口面与支持层级](./docs/reference/api-surface.md) · [记忆接口契约](./docs/reference/memory-surface-contract.md)

## MemoWeft 适合你吗？

| 这些需求适合 MemoWeft                    | 这些需求应该选择其他层                         |
| ---------------------------------------- | ---------------------------------------------- |
| 跨对话、模型或宿主保留长期用户记忆       | 只需要短期聊天记录或通用文档 RAG               |
| 需要来源、纠正历史、冲突可见性和受控召回 | 需要开箱即用的聊天 UI、人设或消费级应用        |
| 需要由 SQLite 支撑的嵌入式 TypeScript 库 | 需要托管式多租户记忆 API 或现成多设备同步      |
| 记忆需要可检查、可管理、可导入导出       | 需要开箱即用的 PostgreSQL 或可替换生产存储后端 |
| 需要明确控制内置模型读取路径             | 需要库本身提供认证、同意界面、合规或磁盘加密   |

宿主仍需负责产品 UX、认证、授权、用户同意、加密、备份、日志策略与部署。

## 生态集成

| 生态                                                    | 接入方式                             | 当前公开状态                                 |
| ------------------------------------------------------- | ------------------------------------ | -------------------------------------------- |
| [Vercel AI SDK](./packages/adapter-ai-sdk)              | 中间件召回与受控持久化               | npm `0.2.3` 支持 Core `0.5.1` 至稳定版 `1.x` |
| [Model Context Protocol](./packages/mcp-server)         | stdio：5 个读工具、3 个受控写工具    | npm `0.2.3` 支持 Core `0.5.1` 至稳定版 `1.x` |
| [Claude Agent SDK](./packages/adapter-claude-agent-sdk) | 用户输入与工具结果 hooks             | 仓库源码预览                                 |
| [OpenAI Agents SDK](./packages/adapter-openai-agents)   | `run()` 包装与模型输入过滤           | 仓库源码预览                                 |
| [LangChain](./packages/adapter-langchain)               | v1 middleware、retriever 与 callback | 仓库源码预览                                 |
| [Mastra](./packages/adapter-mastra)                     | Processor 读写集成                   | 仓库源码预览                                 |
| [LlamaIndex.TS](./packages/adapter-llamaindex)          | Memory block 与 stream tap           | Legacy；上游已归档                           |

已发布包和仓库源码按照独立节奏推进。安装前请以 npm 上实际版本的元数据和对应 package README 为准；源码预览在正式发布前不会被描述成可直接 npm 安装。

[Vercel AI SDK 配方](./docs/recipes/vercel-ai-sdk.zh-CN.md) · [MCP 配方](./docs/recipes/mcp-server.zh-CN.md) · [集成指南](./docs/integration.zh-CN.md)

## 信任和隐私的本地边界

MemoWeft 的本地优先来自可检查的架构边界，而不是一句“数据永不离开设备”的口号。

- 记忆存放在应用指定的 SQLite 数据库中，不要求使用托管记忆服务。
- 保存和读取原始证据可以完全离线；仓库也提供无需 API Key 的确定性演示。
- 画像形成需要聊天模型。宿主可以连接云端或本地 OpenAI-compatible 端点。
- `allowCloudRead` 会限制 MemoWeft 内置云端写模型路径选择哪些证据，但它不是访问控制，也不会约束自定义代码、召回、MCP、Adapter、导出或日志。
- 外部观察与工具结果默认不会进入内置云端写模型提示，但宿主仍需实现同意、审阅和授权变更体验。
- SQLite 文件不会由 MemoWeft 自动加密。认证、租户隔离、磁盘加密、备份、日志与合规策略属于宿主职责。
- 强制移除单条证据时，Core 会移除依赖它的派生事件和认知，并留下审计墓碑；这不是逐行物理擦除。完整的主体级清理应使用 `resetSubject`，宿主仍需处理外部索引、日志和备份。

CI 持续验证离线回归、API 快照、可运行文档片段、构建与 Node 兼容性。公开评测会同时说明方法和不覆盖的范围。

[评测与复现协议](./BENCHMARKS.md) · [API 稳定性](./docs/STABILITY.md) · [部署与隐私](./docs/deployment.zh-CN.md) · [安全策略](./.github/SECURITY.md)

## 项目状态与路线图

MemoWeft 是 library-first 的库，Core 1.0 是其 TypeScript 支持门面与记忆契约的首个稳定版本。直接运行 `npm install memoweft` 会跟随稳定的 `latest` 版本线。

公共接口分别标注 stable、experimental 和 internal。1.0 之后，破坏 stable 符号需要主版本并提前弃用；experimental 接口仍可能在 minor 版本中带说明调整。Python 包仍是实验性的规则一致性实现，不是功能完整的稳定 SDK。

**当前重点：**维护 Core 1.x 契约，扩展带版本的集成，维持 Node 20/22/24 覆盖，增加可复现实验材料，并完善 TypeScript 与 Python 之间的便携包一致性。

[路线图](./ROADMAP.md) · [更新记录](./CHANGELOG.md) · [稳定性策略](./docs/STABILITY.md)

## 文档与社区

- [入门指南](./docs/getting-started.zh-CN.md) —— 从第一条证据到可召回画像
- [核心概念](./docs/concepts/README.zh-CN.md) —— 六条记忆纪律
- [示例](./examples) —— Core、管理、插件与便携包
- [文档索引](./docs/README.zh-CN.md) —— 参考、配方、部署与内部设计
- [GitHub Discussions](https://github.com/memoweft/memoweft/discussions) —— 使用帮助与设计讨论
- [Issues](https://github.com/memoweft/memoweft/issues) —— 可复现缺陷与具体功能建议
- [参与贡献](./CONTRIBUTING.zh-CN.md) —— 开发环境与评审预期
- [支持说明](./SUPPORT.md) —— 去哪里提问、需要提供哪些信息

贡献不只限于 Core 代码：更清楚的示例、新的框架集成、平台兼容性验证、能复现真实失败的评测用例，以及针对来源、冲突、删除和隐私边界的审查，都很重要。

如果你也相信 AI 记忆应该可追溯、可纠正、可带走，而不是一个看不见的黑箱，欢迎 **Star MemoWeft**、运行离线演示，或告诉我们你正在构建怎样的记忆体验。

## License

[MIT](./LICENSE) © 2026 MemoWeft contributors.
