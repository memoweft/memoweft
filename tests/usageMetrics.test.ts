/**
 * token 用量观测（0.5.0·用量统计「宿主能算钱」）：
 *  - OpenAICompatClient.chat 接 OpenAI 兼容 usage（snake_case），读到才加、读不到不崩。
 *  - total_tokens 缺失回退 prompt+completion；多次调用累加；callsWithUsage ≤ callCount。
 *  - OpenAICompatEmbedder.embed 同款接 usage（无 completion）；空输入不计数。
 *  - core.usage() 累计总账：chat/write 同实例去重不重复计；llm/embed 分桶 + 合计。
 * 全离线（stub fetch / 注入假件），进 npm test 护栏。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { OpenAICompatClient, type LLMClient } from '../src/llm/client.ts';
import { OpenAICompatEmbedder, type Embedder } from '../src/retrieval/embedder.ts';
import { createMemoWeftCore } from '../src/core/createCore.ts';

/** stub globalThis.fetch 依次吐给定 chat 响应（末个之后重复用最后一个）；返回构造好的 client。 */
function chatClientWith(responses: Array<Record<string, unknown>>): OpenAICompatClient {
  let i = 0;
  const stub = async (): Promise<Response> => {
    const payload = responses[Math.min(i, responses.length - 1)];
    i++;
    return { ok: true, json: async () => payload } as unknown as Response;
  };
  globalThis.fetch = stub as unknown as typeof fetch;
  return new OpenAICompatClient({ baseUrl: 'http://x', apiKey: 'k', model: 'm' });
}

test('OpenAICompatClient.chat：接 usage（snake_case）、多次累加、callsWithUsage 计数', async () => {
  const orig = globalThis.fetch;
  try {
    const c = chatClientWith([
      {
        choices: [{ message: { content: 'a' } }],
        usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
      },
      {
        choices: [{ message: { content: 'b' } }],
        usage: { prompt_tokens: 20, completion_tokens: 8, total_tokens: 28 },
      },
    ]);
    await c.chat([{ role: 'user', content: 'x' }]);
    await c.chat([{ role: 'user', content: 'y' }]);
    assert.deepEqual(c.usage, {
      promptTokens: 30,
      completionTokens: 13,
      totalTokens: 43,
      callsWithUsage: 2,
    });
    assert.equal(c.callCount, 2);
  } finally {
    globalThis.fetch = orig;
  }
});

test('OpenAICompatClient.chat：端点不回 usage → 计数保持 0、不崩（本地模型常见）', async () => {
  const orig = globalThis.fetch;
  try {
    const c = chatClientWith([{ choices: [{ message: { content: 'a' } }] }]);
    const out = await c.chat([{ role: 'user', content: 'x' }]);
    assert.equal(out, 'a', '回话照常返回');
    assert.deepEqual(c.usage, {
      promptTokens: 0,
      completionTokens: 0,
      totalTokens: 0,
      callsWithUsage: 0,
    });
    assert.equal(c.callCount, 1, 'callCount 照常 +1（usage 缺不影响调用计数）');
  } finally {
    globalThis.fetch = orig;
  }
});

test('OpenAICompatClient.chat：total_tokens 缺失 → 回退 prompt+completion', async () => {
  const orig = globalThis.fetch;
  try {
    const c = chatClientWith([
      {
        choices: [{ message: { content: 'a' } }],
        usage: { prompt_tokens: 7, completion_tokens: 3 },
      },
    ]);
    await c.chat([{ role: 'user', content: 'x' }]);
    assert.equal(c.usage.totalTokens, 10, 'total 缺 → 用 p+c 补');
    assert.equal(c.usage.callsWithUsage, 1);
  } finally {
    globalThis.fetch = orig;
  }
});

test('OpenAICompatEmbedder.embed：接 usage（无 completion）、空输入不计数', async () => {
  const orig = globalThis.fetch;
  try {
    const stub = async (): Promise<Response> =>
      ({
        ok: true,
        json: async () => ({
          data: [{ embedding: [0.1, 0.2] }],
          usage: { prompt_tokens: 12, total_tokens: 12 },
        }),
      }) as unknown as Response;
    globalThis.fetch = stub as unknown as typeof fetch;
    const e = new OpenAICompatEmbedder({ baseUrl: 'http://x', apiKey: 'k', model: 'm' });
    assert.deepEqual(await e.embed([]), [], '空输入直接返回');
    assert.equal(e.callCount, 0, '空输入不计数（未打网络）');
    await e.embed(['hi']);
    assert.equal(e.callCount, 1);
    assert.deepEqual(
      e.usage,
      { promptTokens: 12, completionTokens: 0, totalTokens: 12, callsWithUsage: 1 },
      '嵌入无 completion',
    );
  } finally {
    globalThis.fetch = orig;
  }
});

test('core.usage()：chat/write 同实例去重不重复计；llm/embed 分桶 + 合计', () => {
  const llm: LLMClient = {
    callCount: 3,
    usage: { promptTokens: 100, completionTokens: 40, totalTokens: 140, callsWithUsage: 3 },
    async chat() {
      return '';
    },
  };
  const embedder: Embedder = {
    callCount: 2,
    usage: { promptTokens: 50, completionTokens: 0, totalTokens: 50, callsWithUsage: 2 },
    async embed() {
      return [];
    },
  };
  // 单 client 注入 → asPool 让 chat/write 两用途同一实例；core.usage() 去重后 llm 只算一次（不是两倍）。
  const core = createMemoWeftCore({ dbPath: ':memory:', llm, embedder });
  try {
    const u = core.usage();
    assert.deepEqual(
      u.llm,
      { promptTokens: 100, completionTokens: 40, totalTokens: 140, callsWithUsage: 3 },
      'chat/write 同实例去重',
    );
    assert.deepEqual(u.embed, {
      promptTokens: 50,
      completionTokens: 0,
      totalTokens: 50,
      callsWithUsage: 2,
    });
    assert.deepEqual(
      u.total,
      { promptTokens: 150, completionTokens: 40, totalTokens: 190, callsWithUsage: 5 },
      'llm + embed 合计',
    );
  } finally {
    core.close();
  }
});
