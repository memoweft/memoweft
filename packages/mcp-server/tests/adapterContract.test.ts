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
import { createMemoWeftCore, type ChatMessage, type MemoWeftCore, type RecalledCognition } from 'memoweft';
import { createMcpServer } from '../src/index.ts';
import { runAdapterContract } from '../../../tests/adapter-kit/contract.ts';
import type {
  AdapterDriver,
  FaultMode,
  FaultOutcome,
  RecallFixtureItem,
  RecallSurface,
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

/** 建 server + 连好 in-memory client。core 的关闭由调用方负责（fake core 无需关）。 */
async function connect(core: MemoWeftCore) {
  const server = createMcpServer(core);
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

  // AD-1：MCP 客户端驱动 —— 注册面只有 memoweft_ingest_user_message（收 verbatim 用户原话），
  //   无任何助手摄入 tool。没有可调的助手落库入口 → 助手消息流经产生零证据（by-construction）。
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
      assert.deepEqual(writeTools, ['memoweft_ingest_user_message'], 'AD-1：唯一写 tool 是用户原话摄入，无助手摄入入口');
      return core.memory.listEvidence({}).length - before;
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

  // AD-6：故障 core → 读 tool。handler 无 try/catch → 抛错以协议错误(isError)上浮，不降级为空注入。
  //   本轮 ad6=na（下方 applicability），套件不真跑此路径；实现留真以备后续启用。
  async runWithFaultyCore(_mode: FaultMode): Promise<FaultOutcome> {
    const faulty = makeFaultyCore('throw') as unknown as MemoWeftCore;
    const { client, close } = await connect(faulty);
    try {
      const res = await client.callTool({ name: 'memoweft_recall', arguments: { query: 'q' } });
      return { degraded: res.isError !== true, logged: false };
    } finally {
      await close();
    }
  },

  applicability: {
    ad3: {
      status: 'na',
      reason: 'AD-3 na(mcp)：无工具结果摄入 tool；SourceKind 无 "tool" 值（契约冻结，不碰）',
    },
    ad5: {
      status: 'na',
      reason: 'AD-5 na(mcp)：写 tool 仅收 verbatim content、无 evidenceId 入参，无 LLM 输出→落库回捞',
    },
    ad6: {
      status: 'na',
      reason: 'AD-6 na(mcp)：handler 无 try/catch，故障以协议错误(isError)上浮；适配器层降级+logger 属后续契约(§21.3)',
    },
  },
};

runAdapterContract(driver, { goldenDir: fileURLToPath(new URL('./golden', import.meta.url)) });
