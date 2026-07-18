/**
 * 读路径适配器：`MemoWeftRetriever extends BaseRetriever`——把 MemoWeft 的召回接进 LangChain。
 *
 * 为什么召回走 retriever 而非 callback（框架行为）：
 *   LangChain 的 callbacks 是【仅观察】——CallbackManager 丢弃 handler 的返回值，
 *   handler 无法把内容【注入】进模型输入。故「召回注入」不能走 callback，必须走
 *   BaseRetriever（Runnable）：宿主获取 Document[] 后自行拼进 prompt（用 formatMemoWeftDocs）。
 *
 * 边界（遵循 MemoWeft「Core 无头」纪律）：注入文案只搬 Core `action.ts` 的中性措辞（见 knowledgeBlock.ts），
 *   适配器不添加专属角色指令。
 *
 * 写路径隐私保证：provenance（证据【原文】+ 授权位，含默认不进入内建云写模型 prompt 的 observed/tool）
 *   【绝不】进 `pageContent`（会被宿主拼进 prompt = 绕过 tier 将受限原文暴露给云模型），
 *   也【不】进 Document.metadata——只经 `onRecall` 交宿主，宿主转发云模型前据 allowCloudRead/allowInference 自筛。
 *   `pageContent` 只放 `content`；`metadata` 只放 host-facing 的 confidence/credStatus/id/contentType/score。
 *
 * 降级：`_getRelevantDocuments` 内 `withTimeout` 包 recall；超时/抛错 → 返回 `[]` + logger 记事件，
 *   读路径不重试，绝不向链抛（召回失败不阻塞对话）。
 *
 * 类型/值 import 自 `@langchain/core`（peer + dev 依赖）：`extends BaseRetriever` / `new Document(...)`
 *   需运行时值，故为值 import（非 `import type`）；仅签名用的 `CallbackManagerForRetrieverRun` 用 `import type`。
 */
import { BaseRetriever, type BaseRetrieverInput } from '@langchain/core/retrievers';
import { Document, type DocumentInterface } from '@langchain/core/documents';
import type { CallbackManagerForRetrieverRun } from '@langchain/core/callbacks/manager';
import type { MemoWeftCore, RecalledCognition, ContentType } from 'memoweft';
import {
  DEFAULT_RECALL_TIMEOUT_MS,
  RecallTimeoutError,
  withTimeout,
  type MemoWeftLogger,
} from './degrade.ts';
import { buildKnowledgeBlock, type RecalledLike } from './knowledgeBlock.ts';

/** 只依赖 recall 一个方法——测试可传最小 stub。 */
type RetrieverCore = Pick<MemoWeftCore, 'recall'>;

/**
 * 映射进 Document.metadata 的字段（召回 v2 面，//）：全是 host-facing 元信息。
 * 隐私保证：【不含】provenance——它只经 onRecall 透传，绝不进 metadata / pageContent。
 */
export interface MemoWeftDocMetadata {
  /** 把握度（0..1000）：注入块用它标可信度。 */
  confidence: number;
  /** 可信状态（credStatus）：注入块措辞用。 */
  credStatus: string;
  /** 认知 id：供宿主管理/透视反查。 */
  id?: string;
  /** 认知类型：供宿主看类型 / 过滤观测。 */
  contentType?: ContentType;
  /** 相似度分：供宿主观测排序。 */
  score?: number;
  /** 兼容 LangChain `Metadata extends Record<string, any>` 约束（宿主可挂自有键）。 */
  [key: string]: unknown;
}

export interface MemoWeftRetrieverOptions {
  /** 召回归属的 subject；缺省交给 Core（config.identity.subjectId）。 */
  subjectId?: string;
  /**
   * `formatMemoWeftDocs` 拼注入块的语言（措辞沿用 Core action.ts 的 knowledgeBlock 双语口径）。缺省 'en'。
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
   * 隐私保证：provenance【绝不】进 Document（pageContent/metadata）——只经 onRecall 交宿主自筛。
   */
  explain?: boolean;
  /** 每次成功召回后的回调（可选，便于宿主观测/日志/自筛 provenance）；召回为空也会以空数组触发。
   *  仅在 recall 成功返回后调用——空 query 或 recall 抛错/超时（降级）时不触发。
   *  透传召回 v2 面：items 带 id/contentType/score，explain 时还带 provenance（含授权位）——宿主据此自筛/透视。 */
  onRecall?: (items: RecalledLike[]) => void;
  /**
   * recall 超时阈值（毫秒，降级契约）。缺省 200ms。超时即视为召回失败 → 降级为返回 `[]`。
   * 读路径不重试（超时/抛错直接降级），呼应 Core「召回失败不阻塞对话」纪律。
   */
  recallTimeoutMs?: number;
  /**
   * 注入式 logger（可选，降级契约）：召回超时/抛错降级时记一条【结构化事件】
   *   （`{ event:'memory_degraded', op:'recall', reason }`）。缺省不注入 = 静默降级。
   * 认知纪律 + 隐私：只记事件/原因，绝不记用户内容 / 原话 / 密钥。
   */
  logger?: MemoWeftLogger;
}

/**
 * 把一条召回认知映射成 LangChain `Document`。
 *
 * 隐私保证：`pageContent` 只放 `content`（会被宿主拼进 prompt）；
 *   provenance（证据原文 + 授权位）不得进入 pageContent / metadata，只能通过 onRecall 返回给宿主。
 */
function toDocument(c: RecalledCognition): Document<MemoWeftDocMetadata> {
  const metadata: MemoWeftDocMetadata = {
    confidence: c.confidence,
    credStatus: c.credStatus,
    id: c.id,
    contentType: c.contentType,
    score: c.score,
  };
  return new Document<MemoWeftDocMetadata>({
    id: c.id,
    pageContent: c.content, // 只放 content——provenance 绝不进这里
    metadata,
  });
}

/**
 * MemoWeft 召回检索器：可通过 `retriever.invoke(query)` 调用或集成到 LangChain 链，返回 `Document[]`。
 *
 * 必填抽象成员（实测 `@langchain/core` .d.ts 核对，见文件头）：
 *   - `lc_namespace: string[]`——`Serializable` 上是抽象属性，`BaseRetriever` 未实现，故本类须给；
 *   - `_getRelevantDocuments(query, runManager?)`——检索主体（`BaseRetriever` 里是待子类实现的占位方法）。
 */
export class MemoWeftRetriever extends BaseRetriever<MemoWeftDocMetadata> {
  /** `Serializable` 抽象成员（BaseRetriever 未实现）：序列化命名空间。本适配器的 retriever 命名。 */
  lc_namespace = ['memoweft', 'retrievers'];

  private readonly core: RetrieverCore;
  private readonly opts: MemoWeftRetrieverOptions;

  /**
   * @param core 只需持有 `recall` 方法的 Core（或其最小实现）。
   * @param opts subjectId / lang / contentTypes / explain / onRecall / recallTimeoutMs / logger。
   * @param fields 透传给 BaseRetriever 的基础配置（callbacks/tags/metadata/verbose，可选）。
   */
  constructor(
    core: RetrieverCore,
    opts: MemoWeftRetrieverOptions = {},
    fields?: BaseRetrieverInput,
  ) {
    super(fields);
    this.core = core;
    this.opts = opts;
  }

  /**
   * 检索主体：query → `core.recall` → `Document[]`。降级逻辑覆盖整个路径，绝不向链抛。
   * `runManager` 参数（BaseRetriever 传入）本适配器不使用——召回不需要链上下文。
   */
  async _getRelevantDocuments(
    query: string,
    _runManager?: CallbackManagerForRetrieverRun,
  ): Promise<DocumentInterface<MemoWeftDocMetadata>[]> {
    const {
      subjectId,
      contentTypes,
      explain,
      onRecall,
      recallTimeoutMs = DEFAULT_RECALL_TIMEOUT_MS,
      logger,
    } = this.opts;

    // 空 query 不发起召回（无检索意图）。
    if (typeof query !== 'string' || query.trim() === '') return [];

    // 契约 ：withTimeout 包 recallTimeoutMs；读路径不重试，超时/抛错即降级为返回 []。
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

    // 观测回调【独立】兜底：onRecall 是宿主的仅观察 回调，它抛错只记一条、【不连累已成功的召回】——
    //   Document[] 是 retriever 的主输出(宿主拿去做 RAG),不该因一个观测 bug 被丢成 []。
    try {
      onRecall?.(recalled);
    } catch {
      logger?.({ event: 'memory_degraded', op: 'recall', reason: 'error' });
    }
    // 映射本身极简(几乎不会抛),仍纳入兜底返 [] 以守「绝不向链抛」；此路径不代表召回失败、仅防御。
    try {
      return recalled.map(toDocument);
    } catch {
      logger?.({ event: 'memory_degraded', op: 'recall', reason: 'error' });
      return [];
    }
  }
}

/**
 * 把召回结果（`Document[]` 或 `RecalledLike[]`）拼成中性注入块文本，供宿主拼进 prompt。
 *
 * 两种入参都收：
 *   - `Document[]`（`retriever.invoke(query)` 的产物）——从 `pageContent` + `metadata.confidence/credStatus` 还原；
 *   - `RecalledLike[]`（直接调 `core.recall` 的产物 / onRecall 收到的）——直接用。
 *
 * 隐私保证：拼块只用 content/confidence/credStatus（`buildKnowledgeBlock` 强制），
 *   provenance 等一律不进——即便 Document.metadata 里没放 provenance，这里也不碰。
 */
export function formatMemoWeftDocs(
  input: ReadonlyArray<DocumentInterface> | ReadonlyArray<RecalledLike>,
  lang: 'en' | 'zh' = 'en',
): string {
  const recalled: RecalledLike[] = input.map((item) => {
    if (isDocumentLike(item)) {
      const m = (item.metadata ?? {}) as { confidence?: unknown; credStatus?: unknown };
      return {
        content: item.pageContent,
        confidence: typeof m.confidence === 'number' ? m.confidence : 0,
        credStatus: typeof m.credStatus === 'string' ? m.credStatus : '',
      };
    }
    return item;
  });
  return buildKnowledgeBlock(recalled, lang);
}

/** 区分 Document（有 `pageContent`）与 RecalledLike（有 `content`）。 */
function isDocumentLike(item: DocumentInterface | RecalledLike): item is DocumentInterface {
  return typeof (item as { pageContent?: unknown }).pageContent === 'string';
}
