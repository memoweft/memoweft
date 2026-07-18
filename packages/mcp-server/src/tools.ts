/**
 * MemoWeft MCP tools —— 白名单注册（外部 AI 客户端自主可调的协议面）。
 *
 * Security boundary (see the package README):
 *   - 只暴露 8 个 tool：5 读 + 3 轻写（存一句用户原话 / 存一条工具返回结果 / 静音一条认知）。
 *     两个 ingest 写工具都只【存一条原料证据】——不改画像、不消化、不改上云授权，落库均默认不上云；
 *     mute 写工具只翻转某条认知的召回可见性（ 召回负反馈）——仅从召回雪藏、认知仍 active、
 *     仍参与 consolidation/画像演化，与置信度正交（mute semantics）、不删不改上云授权。
 *   - 破坏性 / 改上云授权 / 整套消化改画像的 Core 方法【一律不注册】——
 *     invalidate、remove、merge、archive、reset、updateEvidenceAuthorization、
 *     handleConversationTurn、updateProfile、ingestObservation、portable 都不出现在这里。
 *     （muteCognition 现已注册为 memoweft_mute_cognition：它只从召回雪藏、不删、不改上云授权、不动置信度，
 *      属可控轻写、非破坏面——故从"不注册黑名单"移出、进白名单。）
 *   - memoweft_ingest_tool_result 只摄入工具执行的【返回结果】（外部返回数据），
 *     不摄入 LLM 的工具调用意图/入参(那是助手输出，禁摄入，tool-result-only ingestion)。
 *   - tool description 使用中性协议措辞，不引入角色设定（Core 保持无头）。
 *
 * 这一层只做"把 Core 门面翻译成 MCP tool"：取参 → 调门面 → 把结果包成
 * structuredContent + 一段可读 text。
 *
 * Degradation contract: memory-layer failures and timeouts do not crash the process or surface as protocol errors.
 *   Handlers contain errors and timeouts from core.*:
 *     · 读工具（recall / list_* / graph）→ 返回空结果 + isError:false，对话不中断；recall 另包 200ms 超时；
 *     · 写工具（ingest / mute）→ 一次重试后仍失败则返回未落库/未变更标记 + isError:false；
 *     · 降级都经【注入的 logger】记一条结构化事件（缺省无 logger = 静默）。
 *   边界：只有 core.* 记忆层故障才降级；参数非法（zod inputSchema 在 handler 之前校验）等
 *   "调用方的错"仍以协议错误上浮，不被吞（降级 vs 真错分清）。
 */
import { z } from 'zod';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { MemoWeftCore, RecalledCognition } from 'memoweft';
import {
  DEFAULT_RECALL_TIMEOUT_MS,
  RecallTimeoutError,
  retryOnce,
  withTimeout,
  type McpServerLogger,
} from './degrade.ts';

/** 白名单 tool 名（snake_case + memoweft_ 前缀）。测试枚举它核对"多一个都不行"。 */
export const READ_TOOL_NAMES = [
  'memoweft_recall',
  'memoweft_list_cognitions',
  'memoweft_list_evidence',
  'memoweft_list_events',
  'memoweft_graph',
] as const;

export const WRITE_TOOL_NAMES = [
  'memoweft_ingest_user_message',
  'memoweft_ingest_tool_result',
  // 可控轻写（ 召回负反馈）：静音/取消静音一条认知——仅从召回雪藏、认知仍 active、与置信度正交（mute semantics）。
  'memoweft_mute_cognition',
] as const;

/** 全部会被注册的 tool 名（读 + 轻写）。测试断言 server 注册的 tool 集合 === 这个集合。 */
export const ALL_TOOL_NAMES = [...READ_TOOL_NAMES, ...WRITE_TOOL_NAMES] as const;

export type ToolName = (typeof ALL_TOOL_NAMES)[number];

/**
 * ContentType 8 值（逐字对齐 src/cognition/model.ts:15-23）：供 memoweft_recall 的 contentTypes 过滤。
 * 与 core.recall 的 RecallInput.contentTypes（ContentType[]）同源——多/少一个都会与 core 契约错位，故就地对齐。
 */
const CONTENT_TYPE_VALUES = [
  'fact',
  'preference',
  'goal',
  'project',
  'state',
  'trait',
  'hypothesis',
  'trend',
] as const;

/** 统一返回结构化结果与等价文本；前者供协议客户端解析，后者供显示及兼容性回退。 */
function ok(payload: unknown): {
  structuredContent: { result: unknown };
  content: { type: 'text'; text: string }[];
} {
  return {
    structuredContent: { result: payload },
    content: [{ type: 'text', text: JSON.stringify(payload, null, 2) }],
  };
}

/** registerTools 选项（降级语义，降级契约）。 */
export interface RegisterToolsOptions {
  /**
   * 注入式 logger（可选）：记忆层故障/超时降级时记一条【结构化事件】
   *   （`{ event:'memory_degraded', tool, op, reason }`）。缺省不注入 = 静默降级。
   * 认知纪律 + 隐私：只记事件元信息，绝不记用户内容 / 原话 / 密钥。
   */
  logger?: McpServerLogger;
  /** recall 超时阈值（毫秒）。缺省 200ms（降级契约）。 */
  recallTimeoutMs?: number;
}

/**
 * 把白名单 tool 注册到给定 McpServer。
 * @param server 已建好的 McpServer（serverInfo/capabilities 由 createMcpServer 定）。
 * @param core   进程内的 MemoWeftCore 门面（读写都经它，绝不直接碰 store）。
 * @param opts   降级语义选项（logger / recallTimeoutMs，降级契约）。
 */
export function registerTools(
  server: McpServer,
  core: MemoWeftCore,
  opts: RegisterToolsOptions = {},
): void {
  const { logger, recallTimeoutMs = DEFAULT_RECALL_TIMEOUT_MS } = opts;

  /**
   * 读工具降级包裹：跑 core 读操作 → 成功回真结果；记忆层抛错/超时 → 记一条 + 返回 emptyValue（降级）。
   * recall 另包 recallTimeoutMs 超时（op==='recall'）；list_* 与 graph 只兜抛错（op==='read'）。
   */
  async function guardRead<T>(
    tool: string,
    op: 'recall' | 'read',
    empty: T,
    run: () => Promise<T>,
  ): Promise<T> {
    try {
      return op === 'recall' ? await withTimeout(run(), recallTimeoutMs) : await run();
    } catch (err) {
      logger?.({
        event: 'memory_degraded',
        tool,
        op,
        reason: err instanceof RecallTimeoutError ? 'timeout' : 'error',
      });
      return empty;
    }
  }
  // ── 读 1：召回相关认知 ───────────────────────────────────────────────
  server.registerTool(
    'memoweft_recall',
    {
      title: 'Recall memory',
      description:
        'Recall stored knowledge relevant to a query. Returns cognitions with confidence and credibility status; low-credibility items are guesses, not established facts.',
      inputSchema: {
        query: z.string().min(1).describe('The query to recall knowledge for.'),
        subjectId: z
          .string()
          .optional()
          .describe('Subject to recall for; defaults to the configured subject.'),
        // 召回 v2 面（additive，/）：按认知类型过滤（允许名单）；不传/空 = 全类型（行为不变）。
        contentTypes: z
          .array(z.enum(CONTENT_TYPE_VALUES))
          .optional()
          .describe(
            'Only recall cognitions whose content type is in this allow-list (e.g. "preference", "goal"). Omit for all types.',
          ),
        // 召回 v2 面（additive，/）：带出每条认知的支撑/反证证据链（provenance）。
        explain: z
          .boolean()
          .optional()
          .describe(
            'Include a provenance chain (supporting/contradicting evidence) per cognition. ' +
              'Cloud-restricted evidence keeps only its authorization metadata; its summary is withheld.',
          ),
      },
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ query, subjectId, contentTypes, explain }) => {
      // 降级：召回超时（200ms）/ 抛错 → 记一条 + 返回空召回（无记忆），isError:false 不中断。
      // 召回 v2 透传（additive）：contentTypes / explain 原样交 core.recall，过滤/解释端到端由 core 一处实现。
      const items = await guardRead('memoweft_recall', 'recall', [] as RecalledCognition[], () =>
        core.recall({ query, subjectId, contentTypes, explain }),
      );
      return ok(
        items.map((c) => {
          const base = {
            id: c.id,
            content: c.content,
            confidence: c.confidence,
            credStatus: c.credStatus,
            score: c.score,
            // 认知类型：召回结果逐字带回，供客户端看类型 + 印证 contentTypes 过滤已透传。
            contentType: c.contentType,
          };
          if (!explain || !c.provenance) return base;
          // tier privacy filter：provenance 的 summary 是证据【原文】（observed/tool 默认
          //   allowCloudRead=false，比派生认知更敏感）。根据隐私保证，仅 allowCloudRead=true 的证据
          //   保留 summary；allowCloudRead=false 的受限项隐去 summary、只回 { evidenceId, relation, sourceKind }
          //   + 授权位元数据（授权位是 metadata 非敏感载荷，留着让宿主转发云模型前仍能按 tier 自筛——与
          //   mcp-server "tool 证据默认 local-only" 姿态一致）。
          return {
            ...base,
            provenance: c.provenance.map((p) =>
              p.allowCloudRead
                ? {
                    evidenceId: p.evidenceId,
                    relation: p.relation,
                    summary: p.summary,
                    sourceKind: p.sourceKind,
                    allowCloudRead: p.allowCloudRead,
                    allowInference: p.allowInference,
                  }
                : {
                    evidenceId: p.evidenceId,
                    relation: p.relation,
                    sourceKind: p.sourceKind,
                    allowCloudRead: p.allowCloudRead,
                    allowInference: p.allowInference,
                  },
            ),
          };
        }),
      );
    },
  );

  // ── 读 2：列取认知（画像条目 + 溯源链 + 有效置信）────────────────────
  server.registerTool(
    'memoweft_list_cognitions',
    {
      title: 'List cognitions',
      description:
        'List all stored cognitions for a subject, each with its evidence links and a read-time effective confidence. Read-only.',
      inputSchema: {
        subjectId: z
          .string()
          .optional()
          .describe('Subject to list for; defaults to the configured subject.'),
      },
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ subjectId }) =>
      ok(
        await guardRead('memoweft_list_cognitions', 'read', [], async () =>
          core.memory.listCognitions({ subjectId }),
        ),
      ),
  );

  // ── 读 3：列取证据（原始来源）────────────────────────────────────────
  server.registerTool(
    'memoweft_list_evidence',
    {
      title: 'List evidence',
      description:
        'List all stored evidence (raw sources) for a subject. Read-only; does not expose or change authorization bits.',
      inputSchema: {
        subjectId: z
          .string()
          .optional()
          .describe('Subject to list for; defaults to the configured subject.'),
      },
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ subjectId }) =>
      ok(
        await guardRead('memoweft_list_evidence', 'read', [], async () =>
          core.memory.listEvidence({ subjectId }),
        ),
      ),
  );

  // ── 读 4：列取事件（证据聚合）───────────────────────────────────────
  server.registerTool(
    'memoweft_list_events',
    {
      title: 'List events',
      description:
        'List all stored events for a subject, each with the ids of the evidence it covers. Read-only.',
      inputSchema: {
        subjectId: z
          .string()
          .optional()
          .describe('Subject to list for; defaults to the configured subject.'),
      },
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ subjectId }) =>
      ok(
        await guardRead('memoweft_list_events', 'read', [], async () =>
          core.memory.listEvents({ subjectId }),
        ),
      ),
  );

  // ── 读 5：记忆图谱 payload（nodes/edges/stats）──────────────────────
  server.registerTool(
    'memoweft_graph',
    {
      title: 'Build memory graph',
      description:
        'Build a memory graph payload (nodes, edges, stats) for a subject. Read-only. Archived cognitions are excluded unless includeArchived is true.',
      inputSchema: {
        subjectId: z
          .string()
          .optional()
          .describe('Subject to build the graph for; defaults to the configured subject.'),
        includeArchived: z
          .boolean()
          .optional()
          .describe('Include archived cognitions in the graph. Defaults to false.'),
      },
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ subjectId, includeArchived }) =>
      ok(
        await guardRead(
          'memoweft_graph',
          'read',
          // 降级空图（best-effort「无记忆」）：只保空 nodes/edges，形状对齐 payload。
          { nodes: [], edges: [] } as unknown as ReturnType<typeof core.graph.buildMemoryGraph>,
          async () => core.graph.buildMemoryGraph({ subjectId, includeArchived }),
        ),
      ),
  );

  // ── 写·轻：存一句用户原话为 spoken 证据（不改画像、不做消化）──────────
  server.registerTool(
    'memoweft_ingest_user_message',
    {
      title: 'Ingest user message',
      description:
        'Store a single verbatim user message as spoken evidence. This only records the raw message; it does not update the profile, run consolidation, or grant any cloud-read authorization.',
      inputSchema: {
        content: z.string().min(1).describe('The verbatim user message to store.'),
        subjectId: z
          .string()
          .optional()
          .describe('Subject the message belongs to; defaults to the configured subject.'),
        originId: z
          .string()
          .optional()
          .describe('Idempotency key: repeated ingests with the same originId store only once.'),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    },
    async ({ content, subjectId, originId }) => {
      // 降级（降级契约）：写路径失败重试一次；仍失败 → 记一条 + 返回未落库标记，isError:false 不中断。
      try {
        const ev = await retryOnce(() => core.ingestUserMessage({ content, subjectId, originId }));
        return ok({
          id: ev.id,
          subjectId: ev.subjectId,
          sourceKind: ev.sourceKind,
          recordedAt: ev.recordedAt,
        });
      } catch {
        logger?.({
          event: 'memory_degraded',
          tool: 'memoweft_ingest_user_message',
          op: 'ingest',
          reason: 'error',
        });
        return ok({ stored: false, degraded: true });
      }
    },
  );

  // ── 写·轻：存一条工具执行【返回结果】为 tool 证据（默认不上云、不改画像）──
  server.registerTool(
    'memoweft_ingest_tool_result',
    {
      title: 'Ingest tool result',
      description:
        'Store the verbatim result payload returned by a tool execution as evidence (source kind: tool). ' +
        "This records only the tool's returned output as an external data point; it does NOT record the model's " +
        'tool-call arguments or intent, does not update the profile, run consolidation, or grant any cloud-read ' +
        'authorization. Tool-result evidence defaults to local-only (not cloud-readable).',
      inputSchema: {
        content: z
          .string()
          .min(1)
          .describe('The verbatim result payload returned by the tool execution.'),
        subjectId: z
          .string()
          .optional()
          .describe('Subject the result belongs to; defaults to the configured subject.'),
        originId: z
          .string()
          .optional()
          .describe(
            'Idempotency key (e.g. the toolCallId): repeated ingests with the same originId store only once.',
          ),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    },
    async ({ content, subjectId, originId }) => {
      // 降级（降级契约）：写路径失败重试一次；仍失败 → 记一条 + 返回未落库标记，isError:false 不中断。
      try {
        const ev = await retryOnce(() => core.ingestToolResult({ content, subjectId, originId }));
        return ok({
          id: ev.id,
          subjectId: ev.subjectId,
          sourceKind: ev.sourceKind,
          recordedAt: ev.recordedAt,
        });
      } catch {
        logger?.({
          event: 'memory_degraded',
          tool: 'memoweft_ingest_tool_result',
          op: 'ingest',
          reason: 'error',
        });
        return ok({ stored: false, degraded: true });
      }
    },
  );

  // ── 写·轻：静音/取消静音一条认知（ 召回负反馈，可控轻写）──────────────
  //   只翻转 mutedAt：muted:true → 仅从召回雪藏（认知仍 active、仍参与 consolidation/画像演化，
  //   区别于 archive 的全面隐藏、invalidate 的不再有效）；muted:false → 恢复召回。
  //   与置信度正交（mute semantics：不碰 confidence 自算）、不改上云授权、不删。
  server.registerTool(
    'memoweft_mute_cognition',
    {
      title: 'Mute cognition',
      description:
        'Toggle whether a single cognition is excluded from recall (recall negative feedback). ' +
        'Muting only hides the cognition from recall; it stays active and still participates in consolidation. ' +
        'This does NOT change its confidence, does not delete it, and does not grant or change any cloud-read authorization.',
      inputSchema: {
        cognitionId: z.string().min(1).describe('The id of the cognition to mute or unmute.'),
        muted: z
          .boolean()
          .describe(
            'true = exclude the cognition from recall (mute); false = restore it to recall (unmute).',
          ),
        reason: z
          .string()
          .optional()
          .describe(
            'Optional reason recorded in the management audit log; a neutral default is used when omitted.',
          ),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ cognitionId, muted, reason }) => {
      // 降级（降级契约）：写路径失败重试一次；仍失败 → 记一条 + 返回未变更标记，isError:false 不中断。
      //   muteCognition 是同步门面方法，包进 retryOnce 以照现有写 tool 的降级范式（稳定 cognitionId → 重试幂等）。
      try {
        const cog = await retryOnce(async () =>
          core.memory.muteCognition({
            cognitionId,
            muted,
            reason: reason ?? 'mute toggled via mcp tool',
          }),
        );
        // 不存在的认知 → core 返回 null（不是记忆层故障）：如实回 not-found，不吞成降级。
        if (!cog) return ok({ muted: false, cognition: null });
        return ok({
          muted: cog.mutedAt != null,
          cognition: {
            id: cog.id,
            content: cog.content,
            contentType: cog.contentType,
            confidence: cog.confidence,
            credStatus: cog.credStatus,
            mutedAt: cog.mutedAt ?? null,
          },
        });
      } catch {
        logger?.({
          event: 'memory_degraded',
          tool: 'memoweft_mute_cognition',
          op: 'ingest',
          reason: 'error',
        });
        return ok({ muted: false, degraded: true });
      }
    },
  );
}
