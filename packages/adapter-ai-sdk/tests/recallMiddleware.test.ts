/**
 * 读适配器离线护栏：不打真模型，直接调 middleware.transformParams。
 * 验收：
 *  - transformParams 把召回文本真注进了 params.prompt 的最后一条 user 消息；
 *  - 注入文案照 Core knowledgeBlock 中性口径（含 "only guesses" 低置信标注），不含自造人设；
 *  - 空召回 / 无 user 文本 / recall 抛错 → 原样透传不注入（召回失败不挡回话）；
 *  - subjectId 透传给 core.recall；query 取的是最后一条 user 文本；不原地改调用方的 prompt。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { LanguageModelMiddleware } from 'ai';
import type { RecalledCognition, RecallInput } from 'memoweft';
import {
  createMemoWeftMiddleware,
  getLastUserMessageText,
  buildKnowledgeBlock,
} from '../src/recallMiddleware.ts';

// transformParams 的入参类型（从 ai 的 middleware 契约提取，避免 any）。
type TransformArg = Parameters<NonNullable<LanguageModelMiddleware['transformParams']>>[0];
/** SDK prompt 消息的最小结构（本测试只碰 role + text part）。 */
type TextMsg = { role: string; content: Array<{ type: string; text: string }> };

const MODEL = {} as unknown as TransformArg['model'];

/** 造一个只实现 recall 的假 core：记录收到的入参，返回预设召回。 */
function fakeCore(recalled: RecalledCognition[], opts: { throwOnRecall?: boolean } = {}) {
  const calls: RecallInput[] = [];
  return {
    calls,
    core: {
      async recall(input: RecallInput): Promise<RecalledCognition[]> {
        calls.push(input);
        if (opts.throwOnRecall) throw new Error('boom');
        return recalled;
      },
    },
  };
}

/** 造一个最小 SDK 风格 params：一条 user 消息，单个 text part。 */
function paramsWith(userText: string): TransformArg['params'] {
  return {
    prompt: [
      { role: 'system', content: 'You are helpful.' },
      { role: 'user', content: [{ type: 'text', text: userText }] },
    ],
  } as unknown as TransformArg['params'];
}

// credStatus 用【真实枚举】(src/cognition/model.ts:29 = candidate|low|limited|stable|conflicted)。
//   c2 用 'conflicted' 跑通冲突路径(冲突经 credStatus 隐式带出),别把非法形状锁进测试。
const RECALLED = [
  { id: 'c1', content: 'Prefers concise answers', confidence: 820, credStatus: 'stable', score: 0.9 },
  { id: 'c2', content: 'Might be learning Rust', confidence: 220, credStatus: 'conflicted', score: 0.7 },
] as unknown as RecalledCognition[];

test('transformParams 把召回注进最后一条 user 消息，且用 Core knowledgeBlock 中性口径', async () => {
  const { core, calls } = fakeCore(RECALLED);
  const mw = createMemoWeftMiddleware(core);
  const params = paramsWith('How should I phrase this?');

  const out = await mw.transformParams!({ type: 'generate', params, model: MODEL });

  // 召回按最后一条 user 文本发起
  assert.equal(calls.length, 1);
  assert.equal(calls[0]!.query, 'How should I phrase this?');

  // 找到被注入的 user 消息，拼出全部文本
  const userMsg = (out.prompt as unknown as TextMsg[]).find((m) => m.role === 'user')!;
  const injectedText = userMsg.content.map((p) => p.text).join('');
  assert.ok(injectedText.includes('Prefers concise answers'), '召回内容进了 prompt');
  assert.ok(injectedText.includes('Might be learning Rust'), '第二条召回也进了');
  assert.ok(injectedText.includes('confidence 820/1000'), '带把握度');
  // 中性口径 + 低置信标注（照 action.ts）
  assert.ok(injectedText.includes('only guesses'), '低置信中性标注在');
  // 原用户问题仍在
  assert.ok(injectedText.includes('How should I phrase this?'), '用户原问题保留');
});

test('不原地改调用方持有的 prompt 对象', async () => {
  const { core } = fakeCore(RECALLED);
  const mw = createMemoWeftMiddleware(core);
  const params = paramsWith('hi');
  const before = JSON.stringify(params.prompt);

  const out = await mw.transformParams!({ type: 'generate', params, model: MODEL });

  assert.equal(JSON.stringify(params.prompt), before, '原 params.prompt 未被改');
  assert.notEqual(out.prompt, params.prompt, '返回的是新 prompt');
});

test('空召回 → 原样透传不注入', async () => {
  const { core } = fakeCore([]);
  const mw = createMemoWeftMiddleware(core);
  const params = paramsWith('anything');
  const out = await mw.transformParams!({ type: 'generate', params, model: MODEL });
  assert.equal(out, params, '无召回时返回原 params');
});

test('无 user 文本 → 不调 recall，原样透传', async () => {
  const { core, calls } = fakeCore(RECALLED);
  const mw = createMemoWeftMiddleware(core);
  const params = { prompt: [{ role: 'system', content: 'sys only' }] } as unknown as TransformArg['params'];
  const out = await mw.transformParams!({ type: 'generate', params, model: MODEL });
  assert.equal(calls.length, 0, '没 user 文本就别召回');
  assert.equal(out, params);
});

test('recall 抛错 → 降级不注入（召回失败不挡回话）', async () => {
  const { core } = fakeCore(RECALLED, { throwOnRecall: true });
  const mw = createMemoWeftMiddleware(core);
  const params = paramsWith('q');
  const out = await mw.transformParams!({ type: 'generate', params, model: MODEL });
  assert.equal(out, params, '召回抛错时原样返回');
});

test('subjectId 透传给 core.recall', async () => {
  const { core, calls } = fakeCore(RECALLED);
  const mw = createMemoWeftMiddleware(core, { subjectId: 'alice' });
  await mw.transformParams!({ type: 'generate', params: paramsWith('q'), model: MODEL });
  assert.equal(calls[0]!.subjectId, 'alice');
});

test('getLastUserMessageText 取最后一条 user、多 text part 换行拼', () => {
  const prompt = [
    { role: 'user', content: [{ type: 'text', text: 'first' }] },
    { role: 'assistant', content: [{ type: 'text', text: 'reply' }] },
    { role: 'user', content: [{ type: 'text', text: 'a' }, { type: 'text', text: 'b' }] },
  ];
  assert.equal(getLastUserMessageText(prompt), 'a\nb');
});

test('buildKnowledgeBlock 空输入返回空串', () => {
  assert.equal(buildKnowledgeBlock([]), '');
});

test('buildKnowledgeBlock 中文口径也对齐 action.ts', () => {
  const zh = buildKnowledgeBlock(RECALLED, 'zh');
  assert.ok(zh.includes('把握度 820/1000'));
  assert.ok(zh.includes('低置信的只是假设'));
});
