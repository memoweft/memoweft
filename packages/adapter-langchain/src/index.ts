/**
 * @memoweft/adapter-langchain · 公开面。
 *
 * 把 MemoWeft 的长期记忆接进 LangChain（`@langchain/core`）。
 *
 * 三条路径 / 两种载体（硬事实：LangChain callbacks 是【观察-only】，不能注入）：
 *   ① 召回注入（读）= `MemoWeftRetriever extends BaseRetriever`：`retriever.invoke(query)` → `Document[]`；
 *      宿主用 `formatMemoWeftDocs`（= `formatKnowledge`）把它拼成中性块塞进 prompt。
 *      隐私（D-0024）：provenance 绝不进 pageContent/metadata——只经 `onRecall` 交宿主。
 *   ② 用户原话（写）= 宿主闭包 `persistUserTurn(core, { text, originId })` → ingestUserMessage(spoken)；
 *      原话由宿主在调用点（召回注入前）持有并显式传入（不从事件回捞）。
 *   ③ 工具结果（写）= `MemoWeftWriteCallback extends BaseCallbackHandler`：【只】实现 `handleToolEnd`
 *      → ingestToolResult；【绝不】实现 `handleToolStart`——调用意图物理上进不来（铁律 3a·by-construction）。
 *
 * 用法（工厂）：
 *   const mw = createMemoWeftLangChain(core, { subjectId, lang });
 *   const docs = await mw.retriever.invoke(userText);           // ① 读
 *   const knowledge = mw.formatKnowledge(docs);                 // 拼中性块（宿主自行塞进 prompt）
 *   await mw.persistUserTurn({ text: userText, originId });      // ② 写原话
 *   await chain.invoke(input, { callbacks: [mw.writeCallback] }); // ③ 写工具结果（自动）
 */
import type { MemoWeftCore } from 'memoweft';
import type { ContentType } from 'memoweft';
import type { DocumentInterface } from '@langchain/core/documents';
import { MemoWeftRetriever, type MemoWeftRetrieverOptions } from './retriever.ts';
import { formatMemoWeftDocs } from './retriever.ts';
import {
  MemoWeftWriteCallback,
  persistUserTurn,
  type MemoWeftWriteCallbackOptions,
  type PersistUserTurnInput,
} from './writeCallback.ts';
import type { RecalledLike } from './knowledgeBlock.ts';
import type { MemoWeftLogger } from './degrade.ts';

// ── 类：可单独 new 出用（自驱动宿主不走工厂时）──
export { MemoWeftRetriever, formatMemoWeftDocs, type MemoWeftRetrieverOptions } from './retriever.ts';
export { type MemoWeftDocMetadata } from './retriever.ts';
export {
  MemoWeftWriteCallback,
  persistUserTurn,
  toolOutputText,
  type MemoWeftWriteCallbackOptions,
  type PersistUserTurnInput,
} from './writeCallback.ts';

// ── v1 Agent Middleware 入口（LangChain v1 正门；读写一体，需可选 peer `langchain` 伞包）──
//   现有 retriever+callback 是 v0 载体；middleware 是 v1 createAgent 的主推面，且 afterAgent 能接 0.6 recordAssistantReply。
export {
  createMemoWeftMiddleware,
  buildMemoWeftHooks,
  type MemoWeftMiddlewareOptions,
} from './middleware.ts';

// 召回注入块拼装 + 召回项形状（对外也当独立工具用；隐私口径见 knowledgeBlock.ts 注释）。
export { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';

// 降级语义（§16.2）公开件：供宿主为注入的 logger 标类型。
export {
  DEFAULT_RECALL_TIMEOUT_MS,
  type MemoWeftLogger,
  type MemoWeftDegradedEvent,
} from './degrade.ts';

/** 只依赖读写三方法——测试可传最小 stub。 */
type LangChainCore = Pick<MemoWeftCore, 'recall' | 'ingestUserMessage' | 'ingestToolResult'>;

/** `createMemoWeftLangChain` 的选项（工厂把它拆给 retriever / writeCallback / persistUserTurn 三件）。 */
export interface CreateMemoWeftLangChainOptions {
  /** 召回/摄入归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /** `formatKnowledge` 拼块的语言（措辞照 Core action.ts 的 knowledgeBlock 双语口径）。缺省 'en'。 */
  lang?: 'en' | 'zh';
  /** 召回按认知类型过滤（D-0022/D-0024）：透传进 `core.recall` 的 `contentTypes`。不传/空 = 全类型。 */
  contentTypes?: ContentType[];
  /** 召回解释（D-0021/D-0024）：透传进 `core.recall` 的 `explain`；true → onRecall 每项带 provenance（含授权位）。 */
  explain?: boolean;
  /** 每次成功召回后的回调（可选）；透传召回 v2 面（id/contentType/score，explain 时带 provenance）供宿主自筛/透视。
   *  隐私硬约束（D-0024）：provenance【只】经此回调交宿主，绝不进 Document.pageContent/metadata。 */
  onRecall?: (items: RecalledLike[]) => void;
  /** recall 超时阈值（毫秒，§16.2）。缺省 200ms。超时/抛错 → 检索降级为 []。 */
  recallTimeoutMs?: number;
  /** ingest 单次尝试超时（毫秒，可选）。传正数则每次尝试套超时；超时按失败计入「重试一次」（超时不重试）。 */
  ingestTimeoutMs?: number;
  /** 注入式 logger（§16.2）：召回/摄入降级时记结构化事件。缺省不注入 = 静默降级；只记事件/原因，不记内容。 */
  logger?: MemoWeftLogger;
}

/** 工厂返回：读检索器 + 写回调 + 拼块函数 + 用户原话摄入闭包（后二者已绑 core + 选项）。 */
export interface MemoWeftLangChain {
  /** ① 召回注入（读）：`retriever.invoke(query)` → `Document[]`。塞进链 / 手动 invoke 皆可。 */
  retriever: MemoWeftRetriever;
  /** ③ 工具结果摄入（写）：塞进 LangChain `callbacks`；只落工具返回结果（铁律 3a）。 */
  writeCallback: MemoWeftWriteCallback;
  /** 把召回结果（`Document[]` 或 `RecalledLike[]`）拼成中性注入块文本（供宿主拼进 prompt）。 */
  formatKnowledge(input: ReadonlyArray<DocumentInterface> | ReadonlyArray<RecalledLike>): string;
  /** ② 用户原话摄入（写）：宿主在调用点（注入前）持有原话，显式传入 → spoken 证据。已绑 core + subjectId/logger 等。 */
  persistUserTurn(
    input: Pick<PersistUserTurnInput, 'text' | 'originId' | 'subjectId' | 'hostId' | 'occurredAt'>,
  ): Promise<void>;
}

/**
 * 造一组 MemoWeft × LangChain 读写适配器件（retriever + writeCallback + formatKnowledge + persistUserTurn）。
 *
 * @param core 只需持有 recall / ingestUserMessage / ingestToolResult 三方法的 Core（或其最小实现）。
 * @param opts subjectId / lang / contentTypes / explain / onRecall / recallTimeoutMs / ingestTimeoutMs / logger。
 * @returns `{ retriever, writeCallback, formatKnowledge, persistUserTurn }`。
 */
export function createMemoWeftLangChain(
  core: LangChainCore,
  opts: CreateMemoWeftLangChainOptions = {},
): MemoWeftLangChain {
  const {
    subjectId,
    lang = 'en',
    contentTypes,
    explain,
    onRecall,
    recallTimeoutMs,
    ingestTimeoutMs,
    logger,
  } = opts;

  const retrieverOpts: MemoWeftRetrieverOptions = {
    subjectId,
    lang,
    contentTypes,
    explain,
    onRecall,
    recallTimeoutMs,
    logger,
  };
  const writeOpts: MemoWeftWriteCallbackOptions = { subjectId, ingestTimeoutMs, logger };

  const retriever = new MemoWeftRetriever(core, retrieverOpts);
  const writeCallback = new MemoWeftWriteCallback(core, writeOpts);

  return {
    retriever,
    writeCallback,
    formatKnowledge: (input) => formatMemoWeftDocs(input, lang),
    persistUserTurn: (input) =>
      persistUserTurn(core, {
        text: input.text,
        originId: input.originId,
        subjectId: input.subjectId ?? subjectId,
        hostId: input.hostId,
        occurredAt: input.occurredAt,
        ingestTimeoutMs,
        logger,
      }),
  };
}
