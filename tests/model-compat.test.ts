/**
 * 模型兼容：temperature 可配置，并兼容 reasoning 响应形状。
 *
 * 断言：
 *  - loadLLMConfig 按 prefix 读 *_TEMPERATURE（chat/write 自动分）；空/非法 → undefined。
 *  - OpenAICompatClient.chat 请求体 temperature = config.temperature ?? 0.3（含 0；不配=0.3 零行为变更）。
 *  - chat 剥掉成对 <think>…</think>（含花括号），无闭合不误剥。
 *  - extractJsonObject 括号配平：取第一个平衡对象，抗尾随文本/思考残留、跳过字符串内花括号。
 * 全离线（stub fetch / 直调），进 npm test 护栏。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { OpenAICompatClient, loadLLMConfig } from '../src/llm/client.ts';
import { extractJsonObject } from '../src/llm/jsonRepair.ts';

test('loadLLMConfig：按 prefix 读 *_TEMPERATURE；空/非法 → undefined；0 合法', () => {
  const P = 'T3TMP'; // 自定前缀，避开任何真 .env 干扰
  const keys = [
    `MEMOWEFT_${P}_BASE_URL`,
    `MEMOWEFT_${P}_API_KEY`,
    `MEMOWEFT_${P}_MODEL`,
    `MEMOWEFT_${P}_TEMPERATURE`,
  ];
  const saved = keys.map((k) => process.env[k]);
  try {
    process.env[`MEMOWEFT_${P}_BASE_URL`] = 'http://x';
    process.env[`MEMOWEFT_${P}_API_KEY`] = 'k';
    process.env[`MEMOWEFT_${P}_MODEL`] = 'm';
    process.env[`MEMOWEFT_${P}_TEMPERATURE`] = '0';
    assert.equal(loadLLMConfig(P).temperature, 0, '0 被读入（不回落）');
    process.env[`MEMOWEFT_${P}_TEMPERATURE`] = '0.9';
    assert.equal(loadLLMConfig(P).temperature, 0.9);
    process.env[`MEMOWEFT_${P}_TEMPERATURE`] = 'abc';
    assert.equal(loadLLMConfig(P).temperature, undefined, '非法 → undefined');
    delete process.env[`MEMOWEFT_${P}_TEMPERATURE`];
    assert.equal(loadLLMConfig(P).temperature, undefined, '未设 → undefined');
  } finally {
    keys.forEach((k, i) => {
      if (saved[i] === undefined) delete process.env[k];
      else process.env[k] = saved[i]!;
    });
  }
});

/** stub globalThis.fetch，捕获请求体、回一个带指定 content（可选 reasoning_content）的假响应。 */
async function captureBody(
  temperature: number | undefined,
  content = '{}',
  reasoningContent?: string,
): Promise<{ body: { temperature?: number }; out: string }> {
  const orig = globalThis.fetch;
  let body: { temperature?: number } = {};
  const stub = async (_url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    body = JSON.parse(init!.body as string) as { temperature?: number };
    const message =
      reasoningContent === undefined
        ? { content }
        : { content, reasoning_content: reasoningContent };
    return { ok: true, json: async () => ({ choices: [{ message }] }) } as unknown as Response;
  };
  globalThis.fetch = stub as unknown as typeof fetch;
  try {
    const c = new OpenAICompatClient({ baseUrl: 'http://x', apiKey: 'k', model: 'm', temperature });
    const out = await c.chat([{ role: 'user', content: 'hi' }]);
    return { body, out };
  } finally {
    globalThis.fetch = orig;
  }
}

test('chat body：temperature 缺省 0.3、设了用设的（含 0）', async () => {
  assert.equal((await captureBody(undefined)).body.temperature, 0.3, '不配 = 0.3（零行为变更）');
  assert.equal((await captureBody(0)).body.temperature, 0, 'write 可设 0（更稳）');
  assert.equal((await captureBody(0.8)).body.temperature, 0.8);
});

test('chat：剥掉成对 <think>…</think>（含花括号），无闭合不误剥', async () => {
  const closed = await captureBody(undefined, '<think>我先想想 {type:x}</think>{"ok":1}');
  assert.equal(closed.out, '{"ok":1}', '闭合 think 被剥、真 JSON 留下');
  const unclosed = await captureBody(undefined, '<think>没闭合就别乱剥 {"ok":1}');
  assert.ok(unclosed.out.includes('{"ok":1}'), '无闭合 </think> → 不剥，真答案保住');
});

/**
 * 推理模型的另一半兼容：答案整个跑进 reasoning_content、content 留空。
 *
 * 部分 OpenAI-compatible 推理模型会这样返回，且 finish_reason=stop、completion_tokens 正常。
 * 旧代码 `typeof '' === 'string'` 通过校验 →
 * chat() 静默返回空串 → 上游 JSON 解析失败 → consolidate 四类全空（`?? {}`）→
 * 整批证据 0 解析 0 认知、event 却被标 consolidated。
 */
test('chat：答案在 reasoning_content、content 空 → 回落读它（不再静默返回空串）', async () => {
  // Synthetic response matching the protocol shape returned by affected endpoints.
  const syntheticProtocolJson =
    '{"thought":"Need to preserve the structured result.","done":{"summary":"ok"}}';
  const empty = await captureBody(undefined, '', syntheticProtocolJson);
  assert.equal(empty.out, syntheticProtocolJson, 'content 为空串时回落 reasoning_content');

  const blank = await captureBody(undefined, '   \n ', syntheticProtocolJson);
  assert.equal(blank.out, syntheticProtocolJson, 'content 只有空白也算空');
});

test('chat：content 有内容时绝不掺进 reasoning_content（标准模型零行为变更）', async () => {
  const r = await captureBody(undefined, '{"ok":1}', '我先想想该怎么答');
  assert.equal(r.out, '{"ok":1}', 'content 有实质内容 → 用它，思考段不许掺进来');
});

test('chat：content 与 reasoning_content 皆空 → 保持空串（不改判、交上游）', async () => {
  const r = await captureBody(undefined, '', '   ');
  assert.equal(r.out, '', '两者皆空时保持既有行为，不在 client 层改判');
});

test('extractJsonObject：括号配平取第一个平衡对象，抗尾随文本/思考残留', () => {
  assert.equal(extractJsonObject('{"a":1} 后面还有 } 尾巴'), '{"a":1}', '尾随 } 不被贪进');
  assert.equal(extractJsonObject('前言 {"a":{"b":2}} 后语'), '{"a":{"b":2}}', '嵌套对象平衡闭合');
  assert.equal(
    extractJsonObject('{"s":"有个}在字符串里"}'),
    '{"s":"有个}在字符串里"}',
    '字符串内 } 不计数',
  );
  assert.equal(extractJsonObject('{半截没闭合'), null, '无平衡闭合 → null');
});
