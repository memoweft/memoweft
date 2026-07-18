# @memoweft/adapter-mastra

> [!IMPORTANT]
> **尚未发布的源码预览。** 此适配器尚未发布到 npm；文中的包名目前仅能在本仓库的 npm workspace 中解析。

给 [Mastra](https://mastra.ai) 智能体接上 [MemoWeft](https://github.com/memoweft/memoweft) 的长期记忆——这个可移植记忆库**把事实和猜测分开**（置信度由规则算、冲突只暴露不裁决）。

一个 `Processor` 打通读写两路：

- **读**（`processInput`，模型调用【前】）：为用户这轮召回相关记忆，注入进 **system 通道**——绝不修改 user 消息，所以注入内容不会被重新摄入为「用户说的话」。
- **写**（`processOutputResult`，模型答【完】后）：
  - 用户这轮原话 → 一条 `spoken` 证据（在注入前捕获）；
  - 每个工具**返回结果** → 一条 `tool` 证据（证据边界只接受 `payload.result`，不读取调用入参）；
  - AI 回复 → `recordAssistantReply`（MemoWeft 0.6 会话上下文——只作【下一轮】的上文，永不落证据）。

## 从源码检出试用

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-mastra
```

`memoweft` `^0.6.0` 与 `@mastra/core` 是 peer 依赖。适配器使用 0.6 的 `recordAssistantReply` 面处理 AI 回复与前文上下文。

## 用法

```ts
import { Agent } from '@mastra/core/agent';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftProcessor } from '@memoweft/adapter-mastra';

const core = createMemoWeftCore({ dbPath: './memory.db', llm, embedder });

// 同一实例服务读写两路——注册进【两个】数组。
const memory = createMemoWeftProcessor(core, { lang: 'zh' });

const agent = new Agent({
  name: 'assistant',
  instructions: '你是一个乐于助人的助手。',
  model,
  inputProcessors: [memory], // processInput  → 召回 + 注入
  outputProcessors: [memory], // processOutputResult → 落库
});
```

想启用 0.6 的会话上下文（让一句「是的」能对着 AI 上一句问题被理解），给你的 Mastra 消息带上稳定的 `threadId`——适配器用它作 MemoWeft 的 `conversationId`。

## 选项

`createMemoWeftProcessor(core, options)`：

| 选项              | 缺省                | 含义                                                                                                           |
| ----------------- | ------------------- | -------------------------------------------------------------------------------------------------------------- |
| `processorId`     | `'memoweft-memory'` | Mastra processor 的 id。                                                                                       |
| `subjectId`       | Core 缺省           | 召回 / 写入归属的 subject。                                                                                    |
| `lang`            | `'en'`              | 注入知识块的语言（`'en'` \| `'zh'`）。只影响措辞，不改 Core 行为。                                             |
| `contentTypes`    | 全类型              | 按认知类型过滤召回（允许名单）；透传进 `core.recall`。                                                         |
| `explain`         | `false`             | 让 Core 附上每条召回认知的 provenance；**只**经 `onRecall` 交出，绝不注入。                                    |
| `onRecall`        | —                   | 每次成功召回后带召回项回调（id / contentType / score，`explain` 时含 provenance）。供观测 / 转发云模型前自筛。 |
| `recallTimeoutMs` | `200`               | 召回超时上限。超时/出错这轮降级为**不注入**；读路径不重试。                                                    |
| `logger`          | —                   | 结构化降级事件 `{ event, op, reason }`。绝不收用户内容 / 原话 / 密钥。                                         |

## 保证

- **绝不注入进 user 消息。** 召回落 system 通道，捕获到的用户原话永远是干净输入。
- **来源角色边界。** 只有工具**返回结果**成为证据；工具调用入参不会成为证据，AI 回复只作上下文。
- **隐私。** `provenance`（证据原文 + 上云/推断授权位）、`contentType`、`id`、`score` 绝不进注入 prompt——只经 `onRecall` 流转。
- **绝不阻塞对话。** 召回有超时上界、失败降级为不注入；写路径失败重试一次即放弃。记忆层故障永不中断一次生成。

## 与 Mastra 内建记忆共存

Mastra 自带 working / semantic / observational 记忆。MemoWeft **替换其语义召回层**为「事实 vs 猜测」记忆。若你同时开着 Mastra 的内建 semantic recall，会有两套记忆并行注入——请关掉 Mastra 的 semantic recall（message history / working memory 可按需保留），让召回保持一致。

## 许可

MIT
