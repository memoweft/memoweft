/**
 * persistToolResults 离线测试（tool-result-only ingestion）：不调用实际模型，直接提供消息数组。
 * Test coverage:
 *  - 只摄入 role==='tool' 消息的 tool-result 载荷（tool-result-only ingestion）：assistant 的 tool-call 意图/入参、
 *    assistant content 里混着的 tool-result（provider 执行）一概不读；
 *  - output.type 'text' 取原文、'json' 序列化；error-* / execution-denied / 空载荷不落库；
 *  - originIdPrefix + toolCallId 组合成幂等键透传；不传前缀 = 不去重（originId null）；
 *  - 不传任何上云授权位（隐私默认由 Core toolDefaults 兜底）；
 *  - 单条失败重试一次；仍失败 → logger 记 memory_degraded + onError，不外抛、不挡后续条目。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ToolResultInput, Evidence } from 'memoweft';
import type { ModelMessage } from 'ai';
import { persistToolResults } from '../src/persistOnEnd.ts';
import type { MemoWeftDegradedEvent } from '../src/degrade.ts';

/** 造一个只实现 ingestToolResult 的假 core：记录每次收到的入参，可按次序抛错。 */
function fakeCore(failPlan: boolean[] = []) {
  const ingested: ToolResultInput[] = [];
  let call = 0;
  return {
    ingested,
    core: {
      async ingestToolResult(input: ToolResultInput): Promise<Evidence> {
        if (failPlan[call++]) throw new Error('db down');
        ingested.push(input);
        return { id: `ev${ingested.length}`, ...input } as unknown as Evidence;
      },
    },
  };
}

/** 一轮典型消息：user 问天气 → assistant 发起 tool-call（意图+入参）→ tool 返回结果 → assistant 总结。 */
function weatherTurn(): ModelMessage[] {
  return [
    { role: 'user', content: [{ type: 'text', text: 'What is the weather in Xiamen?' }] },
    {
      role: 'assistant',
      content: [
        {
          type: 'tool-call',
          toolCallId: 'call-1',
          toolName: 'get_weather',
          input: { city: 'Xiamen' },
        },
      ],
    },
    {
      role: 'tool',
      content: [
        {
          type: 'tool-result',
          toolCallId: 'call-1',
          toolName: 'get_weather',
          output: { type: 'json', value: { city: 'Xiamen', tempC: 31, sky: 'sunny' } },
        },
      ],
    },
    { role: 'assistant', content: [{ type: 'text', text: 'It is 31°C and sunny in Xiamen.' }] },
  ] as ModelMessage[];
}

test('只摄入 tool 消息的 result 载荷：意图/入参与助手回话一概不进（tool-result-only ingestion）', async () => {
  const { core, ingested } = fakeCore();
  const stored = await persistToolResults(core, {
    messages: weatherTurn(),
    originIdPrefix: 'turn-7',
  });
  assert.equal(stored, 1, '恰好落一条');
  assert.equal(ingested.length, 1);
  const rec = ingested[0]!;
  assert.equal(
    rec.content,
    JSON.stringify({ city: 'Xiamen', tempC: 31, sky: 'sunny' }),
    'json 输出序列化后原样存',
  );
  assert.ok(!rec.content.includes('get_weather'), '工具名/调用意图不在载荷里');
  assert.equal(rec.originId, 'turn-7:call-1', '幂等键 = 前缀:toolCallId');
  // 写路径边界：不显式传授权位（由 Core toolDefaults 兜底，不进入内建云写模型 prompt）。
  assert.equal('allowCloudRead' in rec, false);
  assert.equal('allowLocalRead' in rec, false);
  assert.equal('allowInference' in rec, false);
});

test('assistant content 里混着的 tool-result（provider 执行）保守跳过', async () => {
  const { core, ingested } = fakeCore();
  const messages = [
    {
      role: 'assistant',
      content: [
        {
          type: 'tool-result',
          toolCallId: 'call-p',
          toolName: 'web_search',
          output: { type: 'text', value: 'provider ran this' },
        },
      ],
    },
  ] as ModelMessage[];
  const stored = await persistToolResults(core, { messages });
  assert.equal(stored, 0, 'assistant 消息永不摄入（宁可漏、不可错摄）');
  assert.equal(ingested.length, 0);
});

test('output 类型分流：text 取原文;error-*/execution-denied/空串不落库', async () => {
  const { core, ingested } = fakeCore();
  const messages = [
    {
      role: 'tool',
      content: [
        {
          type: 'tool-result',
          toolCallId: 'c1',
          toolName: 't',
          output: { type: 'text', value: 'plain result' },
        },
        {
          type: 'tool-result',
          toolCallId: 'c2',
          toolName: 't',
          output: { type: 'error-text', value: 'HTTP 404' },
        },
        {
          type: 'tool-result',
          toolCallId: 'c3',
          toolName: 't',
          output: { type: 'execution-denied', reason: 'user said no' },
        },
        {
          type: 'tool-result',
          toolCallId: 'c4',
          toolName: 't',
          output: { type: 'text', value: '   ' },
        },
      ],
    },
  ] as unknown as ModelMessage[];
  const stored = await persistToolResults(core, { messages, originIdPrefix: 'turn-8' });
  assert.equal(stored, 1, '只有真正的结果载荷落库');
  assert.equal(ingested[0]!.content, 'plain result');
});

test('畸形 json 输出（value 序列化为 undefined）静默跳过、绝不向外抛（回归护栏）', async () => {
  // serialization guard:JSON.stringify(undefined | function | symbol) === undefined（非 string、非抛错）。
  //   若 toolOutputText 直接返回它，extractToolResults 的 text.trim() 会抛 TypeError 逃逸出函数、崩宿主 turn。
  //   真实可达:自定义 tool.toModelOutput 返回 { type:'json', value: obj.missingField } → value:undefined（SDK 不再归一化）。
  const { core, ingested } = fakeCore();
  const messages = [
    {
      role: 'tool',
      content: [
        {
          type: 'tool-result',
          toolCallId: 'c1',
          toolName: 't',
          output: { type: 'json', value: undefined },
        },
        { type: 'tool-result', toolCallId: 'c2', toolName: 't', output: { type: 'json' } }, // value 键缺失
        {
          type: 'tool-result',
          toolCallId: 'c3',
          toolName: 't',
          output: { type: 'json', value: () => 1 },
        }, // function → stringify 得 undefined
        {
          type: 'tool-result',
          toolCallId: 'c4',
          toolName: 't',
          output: { type: 'json', value: { ok: true } },
        },
      ],
    },
  ] as unknown as ModelMessage[];
  // 不得 reject（契约「绝不向外抛」/「形状不合静默跳过」）：前三条无诚实文本载荷 → 跳过；第四条正常落库。
  const stored = await persistToolResults(core, { messages });
  assert.equal(stored, 1, '只有可序列化的 json 结果落库;value 序列化为 undefined 的畸形项静默跳过');
  assert.equal(ingested[0]!.content, JSON.stringify({ ok: true }));
});

test('不传 originIdPrefix → originId 为 null（不去重，同 Core 语义）', async () => {
  const { core, ingested } = fakeCore();
  await persistToolResults(core, { messages: weatherTurn() });
  assert.equal(ingested[0]!.originId, null);
});

test('单条失败重试一次成功 → 不降级;两次都失败 → logger+onError、后续条目照走', async () => {
  // 两条结果:第一条第 1 次失败、第 2 次(重试)成功;第二条直接成功。
  const retryOk = fakeCore([true, false, false]);
  const okEvents: MemoWeftDegradedEvent[] = [];
  const storedA = await persistToolResults(retryOk.core, {
    messages: [
      {
        role: 'tool',
        content: [
          {
            type: 'tool-result',
            toolCallId: 'c1',
            toolName: 't',
            output: { type: 'text', value: 'r1' },
          },
          {
            type: 'tool-result',
            toolCallId: 'c2',
            toolName: 't',
            output: { type: 'text', value: 'r2' },
          },
        ],
      },
    ] as unknown as ModelMessage[],
    logger: (e) => okEvents.push(e),
  });
  assert.equal(storedA, 2, '重试一次后两条都落了');
  assert.equal(okEvents.length, 0, '重试成功不算降级');

  // 第一条两次都失败 → 降级一条;第二条照常落。
  const retryFail = fakeCore([true, true, false]);
  const events: MemoWeftDegradedEvent[] = [];
  const errs: unknown[] = [];
  const storedB = await persistToolResults(retryFail.core, {
    messages: [
      {
        role: 'tool',
        content: [
          {
            type: 'tool-result',
            toolCallId: 'c1',
            toolName: 't',
            output: { type: 'text', value: 'r1' },
          },
          {
            type: 'tool-result',
            toolCallId: 'c2',
            toolName: 't',
            output: { type: 'text', value: 'r2' },
          },
        ],
      },
    ] as unknown as ModelMessage[],
    logger: (e) => events.push(e),
    onError: (e) => errs.push(e),
  });
  assert.equal(storedB, 1, '失败那条放弃,后续条目不受影响');
  assert.deepEqual(
    events,
    [{ event: 'memory_degraded', op: 'ingest', reason: 'error' }],
    '恰好一条结构化降级事件',
  );
  assert.equal(errs.length, 1, 'onError 收到原始错误');
});

test('形状防御：非法消息/part 静默跳过，不抛', async () => {
  const { core, ingested } = fakeCore();
  const messages = [
    null,
    { role: 'tool' }, // 无 content
    {
      role: 'tool',
      content: [null, { type: 'tool-result', toolCallId: 'c1', toolName: 't', output: null }],
    },
    {
      role: 'tool',
      content: [{ type: 'tool-approval-response', approvalId: 'a1', approved: true }],
    },
  ] as unknown as ModelMessage[];
  const stored = await persistToolResults(core, { messages });
  assert.equal(stored, 0);
  assert.equal(ingested.length, 0);
});
