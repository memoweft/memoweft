/**
 * MemoWeft MCP tools —— 白名单注册（外部 AI 客户端自主可调的协议面）。
 *
 * 安全硬约束（见 README 的 SECURITY 段 + 任务书 D3）：
 *   - 只暴露 6 个 tool：5 读 + 1 轻写（只存一句用户原话）。
 *   - 破坏性 / 改上云授权 / 整套消化改画像的 Core 方法【一律不注册】——
 *     invalidate、remove、merge、archive、reset、updateEvidenceAuthorization、
 *     handleConversationTurn、updateProfile、ingestObservation、portable 都不出现在这里。
 *   - tool description 用中性协议措辞，不复活人设（Core 无头）。
 *
 * 这一层只做"把 Core 门面翻译成 MCP tool"：取参 → 调门面 → 把结果包成
 * structuredContent + 一段可读 text。缺库/缺模型不崩：读 core.health() 给降级提示。
 */
import { z } from 'zod';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { MemoWeftCore } from 'memoweft';

/** 白名单 tool 名（snake_case + memoweft_ 前缀）。测试枚举它核对"多一个都不行"。 */
export const READ_TOOL_NAMES = [
  'memoweft_recall',
  'memoweft_list_cognitions',
  'memoweft_list_evidence',
  'memoweft_list_events',
  'memoweft_graph',
] as const;

export const WRITE_TOOL_NAMES = ['memoweft_ingest_user_message'] as const;

/** 全部会被注册的 tool 名（读 + 轻写）。测试断言 server 注册的 tool 集合 === 这个集合。 */
export const ALL_TOOL_NAMES = [...READ_TOOL_NAMES, ...WRITE_TOOL_NAMES] as const;

export type ToolName = (typeof ALL_TOOL_NAMES)[number];

/** 结果统一包成 { structuredContent, content:[text] }：结构给机器读，text 给人读/兜底。 */
function ok(payload: unknown): {
  structuredContent: { result: unknown };
  content: { type: 'text'; text: string }[];
} {
  return {
    structuredContent: { result: payload },
    content: [{ type: 'text', text: JSON.stringify(payload, null, 2) }],
  };
}

/**
 * 把白名单 tool 注册到给定 McpServer。
 * @param server 已建好的 McpServer（serverInfo/capabilities 由 createMcpServer 定）。
 * @param core   进程内的 MemoWeftCore 门面（读写都经它，绝不直接碰 store）。
 */
export function registerTools(server: McpServer, core: MemoWeftCore): void {
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
      },
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ query, subjectId }) => {
      const items = await core.recall({ query, subjectId });
      return ok(
        items.map((c) => ({
          id: c.id,
          content: c.content,
          confidence: c.confidence,
          credStatus: c.credStatus,
          score: c.score,
        })),
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
    async ({ subjectId }) => ok(core.memory.listCognitions({ subjectId })),
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
    async ({ subjectId }) => ok(core.memory.listEvidence({ subjectId })),
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
    async ({ subjectId }) => ok(core.memory.listEvents({ subjectId })),
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
      ok(core.graph.buildMemoryGraph({ subjectId, includeArchived })),
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
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ content, subjectId, originId }) => {
      const ev = await core.ingestUserMessage({ content, subjectId, originId });
      return ok({ id: ev.id, subjectId: ev.subjectId, sourceKind: ev.sourceKind, recordedAt: ev.recordedAt });
    },
  );
}
