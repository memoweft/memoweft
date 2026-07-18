/**
 * @memoweft/adapter-llamaindex · 公开面。
 *
 * 把 MemoWeft 的长期记忆接进 LlamaIndex（`@llamaindex/core` 记忆块 + `@llamaindex/workflow` agent 流）。
 *
 * 三条路径 / 两种载体：
 *   ① 召回注入（读）= `MemoWeftMemoryBlock extends BaseMemoryBlock`：传入 `createMemory({ memoryBlocks:[block] })`
 *      → `agent({ llm, tools, memory })`。每次模型调用前 Memory 调 `block.get(messages)`，本块做语义召回、把中性
 *      知识块作为一条 `role:'memory'` 消息注入。隐私：provenance 绝不进 block 输出——只经 `onRecall` 交宿主。
 *   ② 用户原话（写）= `persistFromAgentStream` 内摄（extras.userMessage，注入前持有的原话）；或宿主闭包 `persistUserTurn`。
 *   ③ 工具结果（写）= `persistFromAgentStream(core, stream, extras)` 透传式 async generator：原样 re-yield
 *      `agent.runStream(userMsg)` 全部事件，【只认】`agentToolCallResultEvent` → ingestToolResult；
 *      `agentToolCallEvent`（调用意图）只 re-yield 不摄（tool-result-only ingestion boundary·by-construction）。
 *
 * 用法（工厂）：
 *   const mw = createMemoWeftLlamaIndex(core, { subjectId, lang });
 *   const memory = createMemory({ memoryBlocks: [mw.memoryBlock] });          // ① 读（注入）
 *   const myAgent = agent({ llm, tools, memory });
 *   for await (const ev of mw.persistFromAgentStream(                          // ②③ 写（透传式摄入）
 *     myAgent.runStream(userText), { userMessage: userText, originId })) {
 *     // …正常消费 ev（事件被原样透传）…
 *   }
 */
import type { MemoWeftCore } from 'memoweft';
import type { ContentType } from 'memoweft';
import type { WorkflowEventData } from '@llamaindex/workflow';
import {
  MemoWeftMemoryBlock,
  createMemoWeftMemoryBlock,
  type MemoWeftMemoryBlockOptions,
} from './memoryBlock.ts';
import {
  persistFromAgentStream,
  persistUserTurn,
  type PersistFromAgentStreamExtras,
  type PersistUserTurnInput,
} from './streamTap.ts';
import { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';
import type { MemoWeftLogger } from './degrade.ts';

// ── 件：可单独用（不走工厂时）──
export {
  MemoWeftMemoryBlock,
  createMemoWeftMemoryBlock,
  queryFromMessages,
  type MemoWeftMemoryBlockOptions,
} from './memoryBlock.ts';
export {
  persistFromAgentStream,
  persistUserTurn,
  toolOutputText,
  type PersistFromAgentStreamExtras,
  type PersistUserTurnInput,
} from './streamTap.ts';

// 召回注入块拼装 + 召回项形状（对外也当独立工具用；隐私口径见 knowledgeBlock.ts 注释）。
export { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';

// 降级语义公开类型：供宿主为注入的 logger 标注类型。
export {
  DEFAULT_RECALL_TIMEOUT_MS,
  type MemoWeftLogger,
  type MemoWeftDegradedEvent,
} from './degrade.ts';

/** 只依赖读写三方法——测试可传最小 stub。 */
type LlamaIndexCore = Pick<MemoWeftCore, 'recall' | 'ingestUserMessage' | 'ingestToolResult'>;

/** `createMemoWeftLlamaIndex` 的选项（工厂把它拆给 memoryBlock / persistFromAgentStream / persistUserTurn 三件）。 */
export interface CreateMemoWeftLlamaIndexOptions {
  /** 召回/摄入归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /** memoryBlock 注入块的语言（措辞沿用 Core action.ts 的 knowledgeBlock 双语口径）。缺省 'en'。 */
  lang?: 'en' | 'zh';
  /** 召回按认知类型过滤：透传进 `core.recall` 的 `contentTypes`。不传/空 = 全类型。 */
  contentTypes?: ContentType[];
  /** 召回解释：透传进 `core.recall` 的 `explain`；true → onRecall 每项带 provenance（含授权位）。 */
  explain?: boolean;
  /** 每次成功召回后的回调（可选）；透传召回 v2 面（id/contentType/score，explain 时带 provenance）供宿主自筛/透视。
   *  隐私保证：provenance【只】经此回调交宿主，绝不进 memoryBlock 输出文本。 */
  onRecall?: (items: RecalledLike[]) => void;
  /** recall 超时阈值（毫秒，）。缺省 200ms。超时/抛错 → 召回降级为不注入。 */
  recallTimeoutMs?: number;
  /** ingest 单次尝试超时（毫秒，可选）。传正数则每次尝试套超时；超时按失败计入「重试一次」（超时不重试）。 */
  ingestTimeoutMs?: number;
  /** 注入式 logger：召回/摄入降级时记结构化事件。缺省不注入 = 静默降级；只记事件/原因，不记内容。 */
  logger?: MemoWeftLogger;
  /** memoryBlock 的 block id（`BaseMemoryBlock` 需要）。缺省 'memoweft-recall'。 */
  memoryBlockId?: string;
  /** memoryBlock 优先级。0 = 总注入（缺省）。见 `MemoWeftMemoryBlockOptions.priority`。 */
  memoryBlockPriority?: number;
}

/** `persistFromAgentStream` 的工厂绑定版每轮附加配置（core + subjectId/ingestTimeoutMs/logger 已绑）。 */
export type BoundStreamTapExtras = Pick<
  PersistFromAgentStreamExtras,
  'userMessage' | 'originId' | 'subjectId'
>;

/** 工厂返回：召回记忆块 + 透传式摄入 generator + 用户原话闭包 + 拼块函数（均已绑 core + 选项）。 */
export interface MemoWeftLlamaIndex {
  /** ① 召回注入（读）：传入 `createMemory({ memoryBlocks:[memoryBlock] })`。 */
  memoryBlock: MemoWeftMemoryBlock;
  /**
   * ②③ 写（透传式）：包住 `agent.runStream(userMsg)` 事件流，原样 re-yield 全部事件、顺路摄原话 + 工具结果。
   * 已绑 core + subjectId/ingestTimeoutMs/logger；每轮传 { userMessage（注入前原话）, originId?, subjectId? 覆盖 }。
   */
  persistFromAgentStream<T extends WorkflowEventData<unknown>>(
    stream: AsyncIterable<T>,
    extras: BoundStreamTapExtras,
  ): AsyncGenerator<T, void, unknown>;
  /** ② 用户原话摄入（写）：宿主不走透传 generator 时的单独入口 → spoken 证据。已绑 core + subjectId/ingestTimeoutMs/logger。 */
  persistUserTurn(
    input: Pick<PersistUserTurnInput, 'text' | 'originId' | 'subjectId' | 'hostId' | 'occurredAt'>,
  ): Promise<void>;
  /** 把召回结果（`RecalledLike[]`，如 onRecall 收到的）拼成中性注入块文本（供宿主自行拼进 prompt）。 */
  formatKnowledge(items: ReadonlyArray<RecalledLike>): string;
}

/**
 * 造一组 MemoWeft × LlamaIndex 读写适配器件（memoryBlock + persistFromAgentStream + persistUserTurn + formatKnowledge）。
 *
 * @param core 只需持有 recall / ingestUserMessage / ingestToolResult 三方法的 Core（或其最小实现）。
 * @param opts subjectId / lang / contentTypes / explain / onRecall / recallTimeoutMs / ingestTimeoutMs / logger / memoryBlockId / memoryBlockPriority。
 * @returns `{ memoryBlock, persistFromAgentStream, persistUserTurn, formatKnowledge }`。
 */
export function createMemoWeftLlamaIndex(
  core: LlamaIndexCore,
  opts: CreateMemoWeftLlamaIndexOptions = {},
): MemoWeftLlamaIndex {
  const {
    subjectId,
    lang = 'en',
    contentTypes,
    explain,
    onRecall,
    recallTimeoutMs,
    ingestTimeoutMs,
    logger,
    memoryBlockId,
    memoryBlockPriority,
  } = opts;

  const blockOpts: MemoWeftMemoryBlockOptions = {
    subjectId,
    lang,
    contentTypes,
    explain,
    onRecall,
    recallTimeoutMs,
    logger,
    id: memoryBlockId,
    priority: memoryBlockPriority,
  };

  const memoryBlock = createMemoWeftMemoryBlock(core, blockOpts);

  return {
    memoryBlock,
    persistFromAgentStream: (stream, extras) =>
      persistFromAgentStream(core, stream, {
        userMessage: extras.userMessage,
        originId: extras.originId,
        subjectId: extras.subjectId ?? subjectId,
        ingestTimeoutMs,
        logger,
      }),
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
    formatKnowledge: (items) => buildKnowledgeBlock([...items], lang),
  };
}
