/**
 * 最小可跑示例：把 MemoWeft 长期记忆接进 Claude Agent SDK 的一轮对话。
 *
 * 一个工厂造两个 hook，覆盖读写三条路径（摊进 query 的 options.hooks）：
 *   读 + 写（同一 UserPromptSubmit hook）：先存【用户原话】（spoken），再召回相关记忆 → 经返回值 additionalContext 注入本轮。
 *   写（PostToolUse hook）：把每条【工具结果】沉淀成 tool 证据（只读 tool_response，绝不碰 tool_input——tool-result-only ingestion）。
 *
 * 从源码检出运行（query 会调用 Claude Agent SDK，需配置相应鉴权）：
 *   git clone https://github.com/memoweft/memoweft.git && cd memoweft
 *   npm ci && npm run build && npm run build --workspace @memoweft/adapter-claude-agent-sdk
 *   node --experimental-strip-types packages/adapter-claude-agent-sdk/examples/basic.ts
 * 只想看接线、不连模型：把下面的 for-await 段注释掉即可——createMemoWeftAgentHooks 的返回值是纯离线数据。
 */
import { query } from '@anthropic-ai/claude-agent-sdk';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftAgentHooks } from '@memoweft/adapter-claude-agent-sdk';

// 1) 起一个 Core（这里用一次性内存库；真实宿主传自己的 dbPath）。
const core = createMemoWeftCore({ dbPath: ':memory:' });

// 2) 造 MemoWeft 读写 hooks。lang 只影响注入块的说明文字（照 Core 中性措辞），不改 Core 行为。
//    可选：onRecall 拿到完整召回面（带 id/contentType/score，explain 时还带 provenance 授权位）供宿主观测/自筛。
const { hooks } = createMemoWeftAgentHooks(core, {
  lang: 'en',
  onRecall: (items) => console.log(`[memoweft] recalled ${items.length} cognition(s)`),
});

// 3) 一整轮对话：把 hooks 摊进 query 的 options.hooks。
//    UserPromptSubmit 会先存这轮用户原话、再把召回记忆注入 prompt；PostToolUse 会存工具结果。
async function chatTurn(prompt: string) {
  for await (const message of query({
    prompt,
    options: { hooks: { ...hooks } },
  })) {
    if (message.type === 'result') return message;
  }
  return undefined;
}

// 用两轮演示"这一轮存进去、下一轮召回出来"（第二轮的 prompt 会被注入第一轮沉淀的记忆）。
await chatTurn('I strongly prefer short, direct answers.');
await chatTurn('Now explain recursion.');

core.close();
