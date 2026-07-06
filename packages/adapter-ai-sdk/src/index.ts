/**
 * @memoweft/adapter-ai-sdk · 公开面。
 *
 * 把 MemoWeft 的长期记忆接进 Vercel AI SDK（`ai`）：
 *   - 读：createMemoWeftMiddleware(core) → 塞进 wrapLanguageModel，召回记忆注入进 prompt。
 *   - 写：createPersistOnEnd(core, { userMessage, originId }) → 塞进 generateText 的 onEnd，
 *         对话结束后把【用户原话】沉淀成 spoken 证据（不存助手回话）。
 */
export {
  createMemoWeftMiddleware,
  buildKnowledgeBlock,
  getLastUserMessageText,
  addToLastUserMessage,
  type MemoWeftMiddlewareOptions,
} from './recallMiddleware.ts';

export {
  createPersistOnEnd,
  persistUserTurn,
  type PersistOnEndOptions,
  type PersistUserTurnInput,
} from './persistOnEnd.ts';
