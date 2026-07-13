/**
 * mcp-server · adapter-kit 契约接入（AD-1…AD-6）。
 *
 * 一份 kit（../../../tests/adapter-kit）喂两个适配器；这里是 MCP 侧的薄驱动：
 *   - 写路径经真 in-memory core + InMemoryTransport 双工，调 memoweft_ingest_user_message；
 *   - 召回呈现走 memoweft_recall 的 structuredContent（AD-4 结构化 JSON golden）。
 * 不起真 stdio、不触网。AD 断言由 runAdapterContract 产出。
 */
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createMemoWeftCore, type ChatMessage, type MemoWeftCore, type RecalledCognition, type Retriever } from 'memoweft';
import { createMcpServer } from '../src/index.ts';
import { runAdapterContract } from '../../../tests/adapter-kit/contract.ts';
import type {
  AdapterDriver,
  FaultMode,
  FaultOutcome,
  MuteAndRecallResult,
  RecallFixtureItem,
  RecallSurface,
  ToolResultTurnResult,
  UserTurnResult,
} from '../../../tests/adapter-kit/spi.ts';
import { makeFaultyCore } from '../../../tests/adapter-kit/faultyCore.ts';

// ── 离线 core（同 server.test.ts：stub LLM + 空召回器，:memory: 库）──
function stubLLM(reply = 'ok') {
  return {
    callCount: 0,
    async chat(_messages: ChatMessage[]) {
      this.callCount++;
      return reply;
    },
  };
}
const nullRetriever = { async indexAll() {}, async search() { return []; } };
function makeCore() {
  return createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
}

// ── AD-7/8/9「B 用真 core」的种子机（真 createMemoWeftCore + 真 consolidation 落库,recall/mute 经 MCP tool 端到端）──
//   AD-4 用 fakeCore 直吐夹具(锁呈现格式);AD-7/8/9 要证 contentTypes/explain 过滤 + mute 真的落到 core 上,
//   故走真 core:词匹配 retriever + 消化桩把夹具沉成真认知(带真 id/证据链),再经 MCP tool 召回/静音。
const SUBJ = 'contract-subject';

/** 词匹配召回器（同 core tests/recallExplain）：updateProfile 时 indexAll 收 {id,text=content},search 按共享词打分。 */
function wordRetriever(): Retriever {
  let items: Array<{ id: string; text: string }> = [];
  const words = (s: string) => new Set(s.toLowerCase().split(/\W+/).filter(Boolean));
  return {
    async indexAll(next) {
      items = [...next];
    },
    async search(query, topK) {
      const q = words(query);
      return items
        .map((it) => ({ id: it.id, score: [...words(it.text)].filter((w) => q.has(w)).length }))
        .filter((h) => h.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, topK);
    },
  };
}

/** 从 consolidate prompt 正文抽出证据行 `    - [id] [来源标注]原文`,并按来源前缀分类(供桩挑 spoken/tool 支撑)。 */
interface SeededEvidence {
  id: string;
  kind: 'spoken' | 'tool' | 'other';
}
function evidenceLinesOf(body: string): SeededEvidence[] {
  return [...body.matchAll(/^[ \t]*-\s*\[([^\]]+)\]\s*(.*)$/gm)].map((m) => {
    const rest = m[2] ?? '';
    const kind = /\[tool result\]|\[工具返回\]/.test(rest)
      ? 'tool'
      : /\[user said\]|\[用户说\]/.test(rest)
        ? 'spoken'
        : 'other';
    return { id: m[1]!, kind };
  });
}

interface NewCogSpec {
  content: string;
  contentType: string;
  support: string[];
}
/** 消化桩:distill(系统提示不含 JSON)回平 summary;consolidate(含 JSON)按 body 证据行产 new 认知(全 formed_by=stated)。 */
function seedStub(buildNew: (ev: SeededEvidence[]) => NewCogSpec[], tier?: 'local' | 'cloud') {
  return {
    callCount: 0,
    tier,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      if (!/JSON/.test(sys)) return 'User material summary.';
      const specs = buildNew(evidenceLinesOf(body)).filter((s) => s.support.length > 0);
      return JSON.stringify({
        new: specs.map((s) => ({
          content: s.content,
          content_type: s.contentType,
          formed_by: 'stated',
          support_evidence_ids: s.support,
        })),
        reinforce: [],
        correct: [],
        conflict: [],
      });
    },
  };
}

/** 建真 core + 种下认知(一条 spoken 证据,可选一条 tool 证据),再 updateProfile 沉成真画像。core 关由调用方负责。 */
async function seedCore(
  buildNew: (ev: SeededEvidence[]) => NewCogSpec[],
  opts: { withTool?: boolean } = {},
): Promise<MemoWeftCore> {
  const withTool = opts.withTool ?? false;
  // 带 tool 证据时写模型 tier 必须是 'local'——tool 证据默认 allowCloudRead=false,cloud tier 会把它挡在消化门外。
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: seedStub(buildNew, withTool ? 'local' : undefined),
    retriever: wordRetriever(),
  });
  await core.ingestUserMessage({ content: 'I prefer concise answers, might be learning Rust, home timezone matters.', subjectId: SUBJ });
  if (withTool) await core.ingestToolResult({ content: 'external activity log: request at 03:00 local time', subjectId: SUBJ });
  await core.updateProfile({ subjectId: SUBJ });
  return core;
}

/** 召回查询:含全部夹具内容的词,让词匹配 retriever 能召回每条种下的认知。 */
const recallQuery = (fixture: RecallFixtureItem[]) => fixture.map((f) => f.content).join(' ');

/** 一条 MCP recall tool 输出项(含召回 v2 面:contentType + 可选 provenance,受限项 provenance 无 summary)。 */
interface RawRecallItem {
  id: string;
  content: string;
  confidence: number;
  credStatus: string;
  score: number;
  contentType?: string;
  provenance?: Array<{
    evidenceId: string;
    relation: string;
    sourceKind: string;
    allowCloudRead: boolean;
    allowInference: boolean;
    summary?: string;
  }>;
}

/** 建 server + 连好 in-memory client。core 的关闭由调用方负责（fake core 无需关）。可注入降级 logger（AD-6）。 */
async function connect(core: MemoWeftCore, opts: Parameters<typeof createMcpServer>[1] = {}) {
  const server = createMcpServer(core, opts);
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'contract-client', version: '0.0.0' });
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  return {
    client,
    async close() {
      await client.close();
      await server.close();
    },
  };
}

const driver: AdapterDriver = {
  name: 'mcp',

  // AD-2：走 memoweft_ingest_user_message 落一句用户原话 → 前后计数恰好 +1、spoken。
  async ingestUserTurn(text: string): Promise<UserTurnResult> {
    const core = makeCore();
    const { client, close } = await connect(core);
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const res = await client.callTool({
        name: 'memoweft_ingest_user_message',
        arguments: { content: text, originId: 'turn-1' },
      });
      const payload = (res.structuredContent as { result: { sourceKind: string } }).result;
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
      return { delta: after.length - before.length, sourceKind: payload.sourceKind, content: added[0]?.rawContent };
    } finally {
      await close();
      core.close();
    }
  },

  // AD-1：MCP 客户端驱动 —— 写 tool 只有「用户原话」与「工具返回结果」两个摄入面，
  //   无任何【助手输出】摄入 tool。没有可调的助手落库入口 → 助手消息流经产生零证据（by-construction）。
  async ingestAssistantTurn(_text: string): Promise<number> {
    const core = makeCore();
    const { client, close } = await connect(core);
    try {
      const before = core.memory.listEvidence({}).length;
      const { tools } = await client.listTools();
      const writeTools = tools
        .filter((t) => (t.annotations as { readOnlyHint?: boolean } | undefined)?.readOnlyHint !== true)
        .map((t) => t.name)
        .sort();
      assert.deepEqual(
        writeTools,
        ['memoweft_ingest_tool_result', 'memoweft_ingest_user_message', 'memoweft_mute_cognition'],
        'AD-1：写 tool 仅摄入用户原话 / 工具返回结果 + 静音一条认知，无【助手输出】摄入入口',
      );
      return core.memory.listEvidence({}).length - before;
    } finally {
      await close();
      core.close();
    }
  },

  // AD-3：外部客户端调 memoweft_ingest_tool_result 存工具返回结果 → +1 tool 证据。
  //   MCP 注册面【无】摄入 assistant/tool-call 的 tool，故 LLM 的调用意图/入参无渠道落库（铁律 3a，by-construction）。
  async ingestToolResult(resultPayload: string, callIntent: string): Promise<ToolResultTurnResult> {
    const core = makeCore();
    const { client, close } = await connect(core);
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const res = await client.callTool({
        name: 'memoweft_ingest_tool_result',
        arguments: { content: resultPayload, originId: 'call-1' },
      });
      const payload = (res.structuredContent as { result: { sourceKind: string } }).result;
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
      // 铁律 3a：落库证据里无一条等于/含调用意图（callIntent 含 'get_weather'，result 不含）。
      const callIntentExcluded = added.every((e) => e.rawContent !== callIntent && !e.rawContent.includes('get_weather'));
      return { delta: after.length - before.length, sourceKind: payload.sourceKind, content: added[0]?.rawContent, callIntentExcluded };
    } finally {
      await close();
      core.close();
    }
  },

  // AD-4：memoweft_recall 的 structuredContent 即结构化呈现面。用 fake-recall core 注入夹具。
  async recallSurface(fixture: RecallFixtureItem[]): Promise<RecallSurface> {
    const fakeCore = { recall: async (): Promise<RecalledCognition[]> => fixture as unknown as RecalledCognition[] } as unknown as MemoWeftCore;
    const { client, close } = await connect(fakeCore);
    try {
      const res = await client.callTool({ name: 'memoweft_recall', arguments: { query: 'anything' } });
      const items = (res.structuredContent as { result: RecallSurface['items'] }).result;
      return { kind: 'structured-json', rendered: JSON.stringify(res.structuredContent, null, 2), items };
    } finally {
      await close();
    }
  },

  // AD-6：故障 core → 读 tool（recall）。handler 兜 core.* 抛错/超时 → 降级为空召回 + isError:false（不崩、不中断），
  //   经注入 logger 记一条结构化事件。throw / timeout 两模式都真跑（timeout 由 handler 200ms 超时器有界赢下）。
  async runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome> {
    const faulty = makeFaultyCore(mode) as unknown as MemoWeftCore;
    const events: unknown[] = [];
    const { client, close } = await connect(faulty, { logger: () => events.push(1) });
    try {
      const res = await client.callTool({ name: 'memoweft_recall', arguments: { query: 'q' } });
      const result = (res.structuredContent as { result?: unknown[] } | undefined)?.result;
      // 降级 = 不以协议错误上浮（isError 非真）且返回空召回（无记忆）。
      const degraded = res.isError !== true && Array.isArray(result) && result.length === 0;
      return { degraded, logged: events.length > 0 };
    } finally {
      await close();
    }
  },

  // AD-7：带 contentTypes 调 memoweft_recall → 真 core 后过滤，返回项只含请求类型（证明过滤端到端透传）。
  async recallSurfaceFiltered(fixture: RecallFixtureItem[], contentTypes: string[]): Promise<RecallSurface> {
    // 种下每条夹具认知（各带其 contentType），走真 consolidation 落库。
    const core = await seedCore((ev) => {
      const spoken = ev.find((e) => e.kind === 'spoken') ?? ev[0];
      return spoken
        ? fixture.map((f) => ({ content: f.content, contentType: f.contentType ?? 'fact', support: [spoken.id] }))
        : [];
    });
    const { client, close } = await connect(core);
    try {
      const res = await client.callTool({
        name: 'memoweft_recall',
        arguments: { query: recallQuery(fixture), subjectId: SUBJ, contentTypes },
      });
      const raw = (res.structuredContent as { result: RawRecallItem[] }).result;
      return {
        kind: 'structured-json',
        rendered: JSON.stringify(res.structuredContent, null, 2),
        items: raw.map((h) => ({
          id: h.id,
          content: h.content,
          confidence: h.confidence,
          credStatus: h.credStatus,
          score: h.score,
          contentType: h.contentType,
        })),
      };
    } finally {
      await close();
      core.close();
    }
  },

  // AD-8：带 explain 调 memoweft_recall → 返回项带 provenance（每条含授权位）；岔口②按 tier 预筛：
  //   云受限证据(allowCloudRead=false)隐去 summary、只留授权位元数据。此驱动内先验预筛已生效，再交契约断言授权位。
  async recallSurfaceExplained(fixture: RecallFixtureItem[]): Promise<RecallSurface> {
    // 种一条 fact 认知，支撑证据 = spoken(allowCloudRead=true)+ tool(allowCloudRead=false) → provenance 混合授权。
    const factContent = (fixture.find((f) => (f.contentType ?? 'fact') === 'fact') ?? fixture[0]!).content;
    const core = await seedCore(
      (ev) => {
        const spoken = ev.find((e) => e.kind === 'spoken');
        const tool = ev.find((e) => e.kind === 'tool');
        const support = [spoken?.id, tool?.id].filter((x): x is string => !!x);
        return [{ content: factContent, contentType: 'fact', support }];
      },
      { withTool: true },
    );
    const { client, close } = await connect(core);
    try {
      const res = await client.callTool({
        name: 'memoweft_recall',
        arguments: { query: recallQuery(fixture), subjectId: SUBJ, explain: true },
      });
      const raw = (res.structuredContent as { result: RawRecallItem[] }).result;

      // 驱动内自验（任务 item4「验 provenance 带授权位 + 受限项已预筛」）：
      const provs = raw.flatMap((h) => h.provenance ?? []);
      assert.ok(provs.length > 0, 'AD-8：explain 至少一条认知带 provenance 证据链');
      const restricted = provs.filter((p) => p.allowCloudRead === false);
      const allowed = provs.filter((p) => p.allowCloudRead === true);
      assert.ok(restricted.length > 0, 'AD-8 预筛前提：种子里含一条云受限(tool, allowCloudRead=false)证据');
      for (const p of restricted) {
        // tier 预筛②（隐私硬约束）：云受限项隐去 summary（敏感原文），只留授权位元数据 + 关系。
        assert.equal(p.summary, undefined, 'AD-8/岔口②：云受限证据的 summary 已被 tier 预筛隐去');
        assert.equal(typeof p.allowCloudRead, 'boolean', 'AD-8：受限项仍带授权位(metadata,非敏感载荷)');
        assert.equal(typeof p.allowInference, 'boolean', 'AD-8：受限项仍带 allowInference 授权位');
      }
      for (const p of allowed) {
        assert.ok(typeof p.summary === 'string' && p.summary.length > 0, 'AD-8：云可读证据保留 summary（未被预筛掉）');
      }

      return {
        kind: 'structured-json',
        rendered: JSON.stringify(res.structuredContent, null, 2),
        items: raw.map((h) => ({
          id: h.id,
          content: h.content,
          confidence: h.confidence,
          credStatus: h.credStatus,
          score: h.score,
          contentType: h.contentType,
          // 受限项 summary 已隐去 → 回填 '' 占位（ProvenanceFixtureItem.summary 为 string；契约只断言授权位）。
          provenance: h.provenance?.map((p) => ({
            evidenceId: p.evidenceId,
            relation: p.relation,
            summary: p.summary ?? '',
            sourceKind: p.sourceKind,
            allowCloudRead: p.allowCloudRead,
            allowInference: p.allowInference,
          })),
        })),
      };
    } finally {
      await close();
      core.close();
    }
  },

  // AD-9：经 memoweft_mute_cognition 静音某认知 → 再召回该 id 消失、其它仍在；mute 与 confidence 正交（铁律 3b）。
  async muteAndRecall(fixture: RecallFixtureItem[], muteId: string): Promise<MuteAndRecallResult> {
    const core = await seedCore((ev) => {
      const spoken = ev.find((e) => e.kind === 'spoken') ?? ev[0];
      return spoken
        ? fixture.map((f) => ({ content: f.content, contentType: f.contentType ?? 'fact', support: [spoken.id] }))
        : [];
    });
    const { client, close } = await connect(core);
    try {
      // 真 id ↔ 夹具 id 映射（按 content 唯一）：真 core 生成真 id，需映回夹具 id 供契约断言。
      const listed = core.memory.listCognitions({ subjectId: SUBJ });
      const realIdByContent = new Map(listed.map((c) => [c.content, c.id]));
      const fixtureIdByContent = new Map(fixture.map((f) => [f.content, f.id]));
      const target = fixture.find((f) => f.id === muteId)!;
      const realMuteId = realIdByContent.get(target.content)!;
      const before = listed.find((c) => c.id === realMuteId)!.confidence;

      // 经新 mute tool 静音（端到端）。
      const muteRes = await client.callTool({
        name: 'memoweft_mute_cognition',
        arguments: { cognitionId: realMuteId, muted: true, reason: '这条召回没用' },
      });
      const mutePayload = (muteRes.structuredContent as { result: { muted: boolean; cognition: { confidence: number } | null } }).result;
      const after = mutePayload.cognition?.confidence;

      // 静音后再召回：muted 项应从结果消失，其它仍在。
      const recallRes = await client.callTool({
        name: 'memoweft_recall',
        arguments: { query: recallQuery(fixture), subjectId: SUBJ },
      });
      const hits = (recallRes.structuredContent as { result: Array<{ content: string }> }).result;
      const recalledIds = hits
        .map((h) => fixtureIdByContent.get(h.content))
        .filter((x): x is string => x !== undefined);

      return { recalledIds, mutedConfidenceBefore: before, mutedConfidenceAfter: after };
    } finally {
      await close();
      core.close();
    }
  },

  applicability: {
    ad3: {
      status: 'applicable',
      reason: 'memoweft_ingest_tool_result 存工具返回结果 → +1 tool 证据；无 assistant/tool-call 摄入 tool，调用意图不落库（铁律 3a，AD-3/D-0013）',
    },
    ad5: {
      status: 'na',
      reason: 'AD-5 na(mcp)：写 tool 仅收 verbatim content、无 evidenceId 入参，无 LLM 输出→落库回捞',
    },
    ad6: {
      status: 'applicable',
      reason: 'handler 兜 core.* 抛错/超时 → 读工具降级空召回 + isError:false，经注入 logger 记一条（契约 §16.2）',
    },
    ad7: {
      status: 'applicable',
      reason: 'memoweft_recall 透传 contentTypes 进 core.recall → 真 core 后过滤，返回项只含请求类型（端到端透传，D-0022/D-0024）',
    },
    ad8: {
      status: 'applicable',
      reason: 'memoweft_recall 透传 explain → provenance 带 allowCloudRead/allowInference 授权位；岔口②按 tier 预筛：云受限项隐 summary、留授权位（D-0021/D-0024）',
    },
    ad9: {
      status: 'applicable',
      reason: 'memoweft_mute_cognition 静音一条认知 → 召回消失、其它仍在；mute 与 confidence 正交、前后相等（铁律 3b，D-0023）',
    },
  },
};

runAdapterContract(driver, { goldenDir: fileURLToPath(new URL('./golden', import.meta.url)) });
