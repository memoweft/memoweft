/**
 * @memoweft/adapter-mastra · 最小示例（读写一体 Processor）。
 *
 * 说明：本文件是【文档级示例】，展示接线形状，不在 CI 里跑（需真实 model / embedder / DB）。
 * 关键点：同一个 processor 实例同时进 inputProcessors（读·召回注入）与 outputProcessors（写·落库）。
 */
import { Agent } from '@mastra/core/agent';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftProcessor } from '@memoweft/adapter-mastra';

// 1) 造 MemoWeft Core（真实用法请传你的 llm / embedder；dbPath 用 ':memory:' 只作示例）。
declare const llm: Parameters<typeof createMemoWeftCore>[0]['llm'];
declare const embedder: Parameters<typeof createMemoWeftCore>[0]['embedder'];
declare const model: unknown; // 你的 Mastra model（如 openai('gpt-4o')）

const core = createMemoWeftCore({ dbPath: './memory.db', llm, embedder });

// 2) 造 MemoWeft Processor（读写一体）。
const memory = createMemoWeftProcessor(core, {
  lang: 'en',
  // 可选：观测每次召回（provenance 仅 explain 时带，且只到这里、绝不进 prompt）。
  onRecall: (items) => console.log(`[memoweft] recalled ${items.length} cognition(s)`),
  // 可选：观测降级（记忆层超时/出错时；绝不含用户内容）。
  logger: (e) => console.warn('[memoweft] degraded:', e),
});

// 3) 同一实例注册进两路。
const agent = new Agent({
  name: 'assistant',
  instructions: 'You are a helpful assistant with long-term memory.',
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- 示例占位，真实用法传具体 model
  model: model as any,
  inputProcessors: [memory],
  outputProcessors: [memory],
});

// 4) 之后正常使用 agent。给消息带稳定 threadId 即启用 0.6 会话上下文（一句「是的」能对着上一句被理解）。
export { agent };
