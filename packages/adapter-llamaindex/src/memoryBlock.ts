/**
 * 读路径适配器：`MemoWeftMemoryBlock extends BaseMemoryBlock`——把 MemoWeft 的召回接进 LlamaIndex 的
 *   记忆块（memory-block）机制。
 *
 * 为什么召回走 memory-block（实测 `@llamaindex/core/memory` .d.ts 核对）：
 *   `createMemory({ memoryBlocks:[block] })` → `agent({ llm, tools, memory })`。每次模型调用前，Memory
 *   会调各 block 的 `get(messages)` 取「记忆上下文」拼进 prompt——这正是召回注入的缝。我们的 block 在
 *   `get()` 里做一次语义召回、把中性知识块作为 `role:'memory'` 的一条消息返回。
 *
 * 实测确认（override 哪个方法 / 返回什么形状）：
 *   - 抽象基类 `BaseMemoryBlock`：constructor 收 `{ id?, priority, isLongTerm? }`；须实现两抽象方法——
 *       · `get(messages?: MemoryMessage[]): Promise<MemoryMessage[]>`  ← 召回注入就 override 这个；
 *       · `put(messages: MemoryMessage[]): Promise<void>`             ← 写入钩子，本块【空实现】(见下)。
 *   - `MemoryMessage = ChatMessage & { id: string; createdAt?; annotations? }`；内建块（Fact/Vector）返回
 *       `[{ id: this.id, role:'memory', content:<text> }]`——本块逐字对齐这一形状（role 用一级 MessageType `'memory'`）。
 *   - `priority: 0` = 该块内容【总是】注入（Memory 侧 `filter(b=>b.priority===0)` 恒纳入）——召回块默认 0。
 *
 * 边界（遵循 MemoWeft「Core 无头」纪律）：注入文案只搬 Core `action.ts` 的中性措辞（见 knowledgeBlock.ts），
 *   适配器不添加专属角色指令。
 *
 * 隐私保证：`get()` 返回的 MemoryMessage.content【只】放 buildKnowledgeBlock 的产物
 *   （只用 content/confidence/credStatus）。provenance（证据原文 + 授权位）、contentType、id、score 一律【不】进
 *   block 输出文本——provenance 进 prompt = 绕过 tier 把未获当前 tier 内建写 prompt 资格的原文提供给模型；这些字段只经 `onRecall` 交宿主。
 *
 * 降级：`get()` 内 `withTimeout` 包 recall；超时/抛错 → 返回 `[]`（不注入）+ logger 记事件，
 *   读路径不重试，绝不向 Memory 抛（召回失败不阻塞对话）。
 *
 * 类型/值 import 自维护中的伞包 `llamaindex`（peer + dev 依赖；伞包 `export *` re-export 了 `@llamaindex/core/memory`
 *   与 `@llamaindex/core/llms`）：`extends BaseMemoryBlock` 需运行时值，故为值 import；
 *   `MemoryMessage` / `MessageType` 仅签名用，用 `import type`。
 */
import { BaseMemoryBlock } from 'llamaindex';
import type { MemoryMessage } from 'llamaindex';
import type { MessageType } from 'llamaindex';
import type { MemoWeftCore, RecalledCognition, ContentType } from 'memoweft';
import {
  DEFAULT_RECALL_TIMEOUT_MS,
  RecallTimeoutError,
  withTimeout,
  type MemoWeftLogger,
} from './degrade.ts';
import { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';

/** 只依赖 recall 一个方法——测试可传最小 stub。 */
type MemoryBlockCore = Pick<MemoWeftCore, 'recall'>;

export interface MemoWeftMemoryBlockOptions {
  /** 召回归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /**
   * 注入块的语言（措辞沿用 Core action.ts 的 knowledgeBlock 双语口径）。缺省 'en'。
   * 只影响适配器拼的说明文字，不改 Core / 召回行为。
   */
  lang?: 'en' | 'zh';
  /**
   * 召回按认知类型过滤：透传进 `core.recall` 的 `contentTypes`（允许名单）。
   * 不传/空 = 全类型（行为不变）。过滤在 Core 侧做（后过滤，可能欠填），适配器只负责透传。
   */
  contentTypes?: ContentType[];
  /**
   * 召回解释：透传进 `core.recall` 的 `explain`。true → onRecall 收到的每项带 provenance
   *   （其支撑/反证证据链，每条含 allowCloudRead/allowInference 授权位）。缺省 false = 不做额外查询、行为不变。
   * 隐私保证：provenance【绝不】进 block 输出文本——只经 onRecall 交宿主自筛。
   */
  explain?: boolean;
  /** 每次成功召回后的回调（可选，便于宿主观测/日志/自筛 provenance）；召回为空也会以空数组触发。
   *  仅在 recall 成功返回后调用——无 query 或 recall 抛错/超时（降级）时不触发。
   *  透传召回 v2 面：items 带 id/contentType/score，explain 时还带 provenance（含授权位）——宿主据此自筛/透视。 */
  onRecall?: (items: RecalledLike[]) => void;
  /**
   * recall 超时阈值（毫秒，降级契约）。缺省 200ms。超时即视为召回失败 → 降级为不注入（返回 `[]`）。
   * 读路径不重试（超时/抛错直接降级），呼应 Core「召回失败不阻塞对话」纪律。
   */
  recallTimeoutMs?: number;
  /**
   * 注入式 logger（可选，降级契约）：召回超时/抛错降级时记一条【结构化事件】
   *   （`{ event:'memory_degraded', op:'recall', reason }`）。缺省不注入 = 静默降级。
   * 认知纪律 + 隐私：只记事件/原因，绝不记用户内容 / 原话 / 密钥。
   */
  logger?: MemoWeftLogger;
  /** block id（`BaseMemoryBlock` 需要；也当返回消息的 id 用）。缺省 'memoweft-recall'。 */
  id?: string;
  /**
   * block 优先级（`BaseMemoryBlock`）。0 = 该块内容【总是】注入（Memory 侧对 priority===0 恒纳入）——
   * 召回块默认 0（总注入）。非 0 则参与长期记忆的 token 预算竞争。
   */
  priority?: number;
  /**
   * 返回消息的 role（`MessageType`）。缺省 'memory'——内建 Fact/Vector 块的注入内容都用这个一级 role。
   */
  messageRole?: MessageType;
}

/**
 * 把一批（会话）消息里【最后一条 user 消息】的纯文本提出来当召回 query。
 *   - content 为 string：原样（去空白后为空 → null）；
 *   - content 为 detail 数组：拼其中 `type:'text'` 部分（无文本 → null）。
 * 无 user 消息 / 无文本 → null（不发起召回）。按 unknown 防御解析，形状不合静默跳过。
 */
export function queryFromMessages(messages: readonly MemoryMessage[] | undefined): string | null {
  if (!Array.isArray(messages)) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as { role?: unknown; content?: unknown } | undefined;
    if (!m || m.role !== 'user') continue;
    const content = m.content;
    if (typeof content === 'string') return content.trim() === '' ? null : content;
    if (!Array.isArray(content)) return null;
    const texts = content
      .filter((p): p is { type: 'text'; text: string } => {
        const part = p as { type?: unknown; text?: unknown };
        return part?.type === 'text' && typeof part.text === 'string';
      })
      .map((p) => p.text);
    if (texts.length === 0) return null;
    const joined = texts.join('\n');
    return joined.trim() === '' ? null : joined;
  }
  return null;
}

/**
 * MemoWeft 召回记忆块：传入 `createMemory({ memoryBlocks:[block] })`，再用于 `agent({ llm, tools, memory })`。
 * 每次模型调用前 Memory 调 `get(messages)` → 本块做一次语义召回、返回一条 `role:'memory'` 的中性知识消息。
 */
export class MemoWeftMemoryBlock extends BaseMemoryBlock {
  private readonly core: MemoryBlockCore;
  private readonly opts: MemoWeftMemoryBlockOptions;

  /**
   * @param core 只需持有 `recall` 方法的 Core（或其最小实现）。
   * @param opts subjectId / lang / contentTypes / explain / onRecall / recallTimeoutMs / logger / id / priority / messageRole。
   */
  constructor(core: MemoryBlockCore, opts: MemoWeftMemoryBlockOptions = {}) {
    // priority 缺省 0（总注入）；id 缺省 'memoweft-recall'。isLongTerm 交给基类缺省（true，本块 put 空实现无副作用）。
    super({ id: opts.id ?? 'memoweft-recall', priority: opts.priority ?? 0 });
    this.core = core;
    this.opts = opts;
  }

  /**
   * ① 召回注入：Memory 调 `get(messages)` 取记忆上下文。取末条 user 文本当 query → `core.recall` →
   * 中性知识块拼成一条 `role:'memory'` 消息返回。空召回 / 无 query / 降级 → 返回 `[]`（不注入）。
   * 降级逻辑覆盖整个路径，绝不向 Memory 抛。
   *
   * 隐私保证：返回消息的 content 只放 buildKnowledgeBlock（只用 content/confidence/credStatus）；
   *   provenance/contentType/id/score 绝不进这里——只经 onRecall 交宿主。
   */
  async get(messages?: MemoryMessage[]): Promise<MemoryMessage[]> {
    const {
      subjectId,
      contentTypes,
      explain,
      onRecall,
      lang = 'en',
      recallTimeoutMs = DEFAULT_RECALL_TIMEOUT_MS,
      logger,
      messageRole = 'memory',
    } = this.opts;

    const query = queryFromMessages(messages);
    if (query == null) return []; // 无检索意图 → 不注入

    // 契约 ：withTimeout 包 recallTimeoutMs；读路径不重试，超时/抛错即降级为不注入。
    let recalled: RecalledCognition[];
    try {
      recalled = await withTimeout(
        this.core.recall({ query, subjectId, contentTypes, explain }),
        recallTimeoutMs,
      );
    } catch (err) {
      logger?.({
        event: 'memory_degraded',
        op: 'recall',
        reason: err instanceof RecallTimeoutError ? 'timeout' : 'error',
      });
      return [];
    }

    // 观测回调 + 拼块纳入降级 guard：宿主 onRecall 或拼块如果抛错，绝不向 Memory 抛 / 中断本轮对话。
    try {
      onRecall?.(recalled);
      // 隐私保证：buildKnowledgeBlock 只用 content/confidence/credStatus——provenance 等绝不进 block 输出。
      const block = buildKnowledgeBlock(recalled, lang);
      if (block === '') return [];
      // 内建 Fact/Vector 块同形状：{ id: this.id, role:'memory', content }。去掉块首换行（buildKnowledgeBlock 前导 \n\n）。
      const message: MemoryMessage = {
        id: this.id,
        role: messageRole,
        content: block.replace(/^\n+/, ''),
      };
      return [message];
    } catch {
      logger?.({ event: 'memory_degraded', op: 'recall', reason: 'error' });
      return [];
    }
  }

  /**
   * `BaseMemoryBlock` 抽象成员：写入钩子。本块【空实现】(no-op)——写路径全走 streamTap 的 `persistFromAgentStream`。
   *
   * 为什么这里不摄入（设计约束）：Memory 会将【整段会话消息】(含助手回话 / 已被召回注入过的上下文) 传给
   *   put()。若在此落库 = 把助手输出、甚至注入的记忆当"用户/工具证据"存回去（脏数据 + 违反「只存用户原话/工具结果」）。
   *   故用户原话由宿主在调用点显式传给 streamTap（注入前持有）、工具结果只认 agentToolCallResultEvent——都不经 put。
   */
  async put(_messages: MemoryMessage[]): Promise<void> {
    // no-op：本块只做召回注入（读），不承担写入。写走 persistFromAgentStream。
  }
}

/**
 * 创建 MemoWeft 召回记忆块（薄工厂，等价 `new MemoWeftMemoryBlock(core, opts)`）。
 * 返回值可传入 `createMemory({ memoryBlocks:[block] })`，再用于 `agent({ llm, tools, memory })`。
 *
 * @param core 只需持有 `recall` 方法的 Core（或其最小实现）。
 * @param opts 见 `MemoWeftMemoryBlockOptions`。
 */
export function createMemoWeftMemoryBlock(
  core: MemoryBlockCore,
  opts: MemoWeftMemoryBlockOptions = {},
): MemoWeftMemoryBlock {
  return new MemoWeftMemoryBlock(core, opts);
}
