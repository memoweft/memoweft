/**
 * @memoweft/mcp-server 离线测试。
 *
 * 全程不起真 stdio、不连真模型：
 *   - core 用 :memory: 库 + 注入假 LLMClient（stub，避免真网络），参考 tests/core.test.ts。
 *   - server ↔ client 走 InMemoryTransport 双工（SDK 自带），跑真实 MCP 协议 tools/list + tools/call。
 *
 * 验证两项安全保证：
 *   ① 白名单 8 个 tool 都注册了、handler 调用返回结构合理；
 *   ② 破坏性 / 改上云授权 / 整套消化改画像的方法【确实没有】被注册成 tool
 *     （枚举 server 实际注册的 tool 名逐个核对，多一个都算失败）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createMemoWeftCore, type ChatMessage } from 'memoweft';
import { createMcpServer, ALL_TOOL_NAMES } from '../src/index.ts';

/** Stub LLM：不访问网络，并返回固定响应（与 tests/core.test.ts 的测试策略一致）。 */
function stubLLM(replyText = 'ok') {
  return {
    callCount: 0,
    async chat(_messages: ChatMessage[]) {
      this.callCount++;
      return replyText;
    },
  };
}

/** 空召回器：不触网、召回恒空（避免建 VectorRetriever 需要真嵌入端点）。 */
const nullRetriever = {
  async indexAll() {},
  async search() {
    return [];
  },
};

/** 建一个离线 core + 连好 in-memory client 的 server。返回 client 与清理函数。 */
async function connectClient() {
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
  const server = createMcpServer(core);
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '0.0.0' });
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  return {
    client,
    async close() {
      await client.close();
      await server.close();
      core.close();
    },
  };
}

/** 破坏性 / 隐私敏感 / 消化改画像的方法名——它们【绝不该】出现在注册的 tool 里。 */
const FORBIDDEN_SUBSTRINGS = [
  'invalidate',
  'update_evidence_authorization',
  'authorization',
  'remove_evidence',
  'remove_cognition',
  'merge',
  'archive',
  'reset',
  'handle_conversation',
  'conversation_turn',
  'update_profile',
  'ingest_observation',
  'export',
  'import',
  'portable',
];

test('tools/list：恰好注册白名单 8 个 tool，一个不多', async () => {
  const { client, close } = await connectClient();
  try {
    const { tools } = await client.listTools();
    const names = tools.map((t) => t.name).sort();
    assert.deepEqual(names, [...ALL_TOOL_NAMES].sort(), '注册的 tool 集合 === 白名单集合');
    assert.equal(names.length, 8, '恰好 8 个（5 读 + 3 轻写）');
  } finally {
    await close();
  }
});

test('破坏性 / 改授权 / 消化改画像的方法确实没有被注册成 tool', async () => {
  const { client, close } = await connectClient();
  try {
    const { tools } = await client.listTools();
    const names = tools.map((t) => t.name);
    for (const name of names) {
      for (const bad of FORBIDDEN_SUBSTRINGS) {
        assert.ok(
          !name.includes(bad),
          `tool '${name}' 命中禁用子串 '${bad}'——破坏性/隐私面不许暴露`,
        );
      }
    }
    // 正向再钉一遍：这些具体危险名一个都不在。
    for (const forbidden of [
      'memoweft_invalidate_cognition',
      'memoweft_update_evidence_authorization',
      'memoweft_remove_evidence',
      'memoweft_remove_cognition',
      'memoweft_merge_cognition',
      'memoweft_archive_cognition',
      'memoweft_reset_subject',
      'memoweft_handle_conversation_turn',
      'memoweft_update_profile',
      'memoweft_ingest_observation',
      'memoweft_export_bundle',
      'memoweft_import_bundle',
    ]) {
      assert.ok(!names.includes(forbidden), `${forbidden} 不该被注册`);
    }
  } finally {
    await close();
  }
});

test('每个 tool 声明了非人设的中性 description', async () => {
  const { client, close } = await connectClient();
  try {
    const { tools } = await client.listTools();
    for (const t of tools) {
      assert.ok(t.description && t.description.length > 0, `${t.name} 有 description`);
      // 中性协议措辞检查：不出现拟人/人设词（第一人称回忆、"about you" 之类）。
      const lowered = t.description.toLowerCase();
      for (const persona of ['i remember', 'about you', '关于你', '回忆起']) {
        assert.ok(!lowered.includes(persona), `${t.name} description 不该有人设措辞 '${persona}'`);
      }
    }
  } finally {
    await close();
  }
});

test('memoweft_ingest_user_message：写一句用户原话，返回结构合理', async () => {
  const { client, close } = await connectClient();
  try {
    const res = await client.callTool({
      name: 'memoweft_ingest_user_message',
      arguments: { content: '我喜欢喝茶', originId: 'msg-1' },
    });
    assert.equal(res.isError, undefined, 'ingest 不报错');
    const payload = (res.structuredContent as { result: { id: string; sourceKind: string } })
      .result;
    assert.ok(payload.id, '返回落库证据 id');
    assert.equal(payload.sourceKind, 'spoken', '用户消息存为 spoken 证据');
  } finally {
    await close();
  }
});

test('memoweft_ingest_tool_result：存一条工具返回结果为 tool 证据（tool-result-only ingestion）', async () => {
  const { client, close } = await connectClient();
  try {
    const res = await client.callTool({
      name: 'memoweft_ingest_tool_result',
      arguments: { content: '{"city":"Xiamen","tempC":31}', originId: 'call-1' },
    });
    assert.equal(res.isError, undefined, 'ingest 不报错');
    const payload = (res.structuredContent as { result: { id: string; sourceKind: string } })
      .result;
    assert.ok(payload.id, '返回落库证据 id');
    assert.equal(
      payload.sourceKind,
      'tool',
      '工具结果存为 tool 证据（隐私默认不上云由 Core toolDefaults 兜底）',
    );

    // 幂等：同 originId 再存一次，list_evidence 仍只有这一条 tool 证据。
    await client.callTool({
      name: 'memoweft_ingest_tool_result',
      arguments: { content: '{"city":"Xiamen","tempC":31}', originId: 'call-1' },
    });
    const listed = await client.callTool({ name: 'memoweft_list_evidence', arguments: {} });
    const evs = (listed.structuredContent as { result: Array<{ sourceKind: string }> }).result;
    assert.equal(
      evs.filter((e) => e.sourceKind === 'tool').length,
      1,
      '同 originId 幂等：只落一条 tool 证据',
    );
  } finally {
    await close();
  }
});

test('memoweft_mute_cognition：不存在的认知 → not-found（muted:false, cognition:null），不吞成降级', async () => {
  const { client, close } = await connectClient();
  try {
    const res = await client.callTool({
      name: 'memoweft_mute_cognition',
      arguments: { cognitionId: 'no-such-cognition', muted: true, reason: '召回没用' },
    });
    assert.equal(res.isError, undefined, 'mute 不报错（not-found 也不上浮协议错）');
    const payload = (res.structuredContent as { result: { muted: boolean; cognition: unknown } })
      .result;
    assert.equal(payload.muted, false, '不存在的认知 → muted:false');
    assert.equal(payload.cognition, null, '不存在的认知 → cognition:null');
  } finally {
    await close();
  }
});

test('memoweft_recall / list_* / graph：读 tool 调用返回结构合理（空库不崩）', async () => {
  const { client, close } = await connectClient();
  try {
    // 先写一句，让 list_evidence 有东西。
    await client.callTool({
      name: 'memoweft_ingest_user_message',
      arguments: { content: '我在学吉他' },
    });

    const recall = await client.callTool({ name: 'memoweft_recall', arguments: { query: '爱好' } });
    assert.equal(recall.isError, undefined);
    assert.ok(
      Array.isArray((recall.structuredContent as { result: unknown[] }).result),
      'recall 返回数组',
    );

    const listEvidence = await client.callTool({ name: 'memoweft_list_evidence', arguments: {} });
    const evList = (listEvidence.structuredContent as { result: unknown[] }).result;
    assert.ok(Array.isArray(evList) && evList.length >= 1, 'list_evidence 至少含刚写的一条');

    const listCog = await client.callTool({ name: 'memoweft_list_cognitions', arguments: {} });
    assert.ok(
      Array.isArray((listCog.structuredContent as { result: unknown[] }).result),
      'list_cognitions 返回数组',
    );

    const listEvents = await client.callTool({ name: 'memoweft_list_events', arguments: {} });
    assert.ok(
      Array.isArray((listEvents.structuredContent as { result: unknown[] }).result),
      'list_events 返回数组',
    );

    const graph = await client.callTool({ name: 'memoweft_graph', arguments: {} });
    const g = (graph.structuredContent as { result: { nodes: unknown[]; edges: unknown[] } })
      .result;
    assert.ok(Array.isArray(g.nodes) && Array.isArray(g.edges), 'graph 返回 nodes/edges');
  } finally {
    await close();
  }
});
