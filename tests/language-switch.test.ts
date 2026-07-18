/**
 * 双语层：语言开关生效，并验证中英文认知约束语义一致。
 *
 * 断言：
 *  - resolveLang 缺省 en / 显式 zh / undefined 回落 en。
 *  - consolidate 的 system prompt 按 config.language 切 en/zh；correct/conflict 纪律文本在两语都在；
 *    en 侧无残留中文；user 骨架标签（【新材料】↔ [New material]）同切。
 *  - reply（走单例 resolveLang）缺省英文。
 * 测试保持离线，通过直接构造 store 与确定性 LLM stub 验证语言切换。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { config, resolveLang, type Lang, type MemoWeftConfig } from '../src/config.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { reply } from '../src/pipeline/action.ts';
import type { ChatMessage } from '../src/llm/client.ts';

const CJK = /[一-鿿]/;

/** 捕获发给模型的 messages 的假 LLM；回固定文本。 */
function capturingLLM(replyText: string) {
  const calls: ChatMessage[][] = [];
  return {
    calls,
    callCount: 0,
    async chat(messages: ChatMessage[]): Promise<string> {
      this.callCount++;
      calls.push(messages);
      return replyText;
    },
  };
}

/** 单例配置 + 覆盖 language（不改全局单例）。 */
function withLang(lang: Lang | undefined): MemoWeftConfig {
  return { ...config, language: lang };
}

test('resolveLang：缺省 en / 显式 zh / undefined 回落 en', () => {
  assert.equal(resolveLang(), 'en', '未设 → en（进英文市场缺省）');
  assert.equal(resolveLang(withLang('zh')), 'zh');
  assert.equal(resolveLang(withLang('en')), 'en');
  assert.equal(resolveLang(withLang(undefined)), 'en', 'language 缺省回落 en');
});

/** 建一条待消化事件，让 consolidate 走到 LLM 调用，捕获它的 system/user prompt。 */
async function captureConsolidatePrompt(lang: Lang): Promise<{ system: string; user: string }> {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const e = ev.put({
      subjectId: 'owner',
      sourceKind: 'observed',
      hostId: 'h',
      rawContent: 'hello',
      allowCloudRead: true,
    });
    evt.put({
      subjectId: 'owner',
      summary: 'said hello',
      occurredAt: e.occurredAt,
      evidenceIds: [e.id],
    });
    const llm = capturingLLM('{}');
    await consolidate('owner', {
      eventStore: evt,
      evidenceStore: ev,
      cognitionStore: cog,
      llm,
      config: withLang(lang),
    });
    assert.ok(llm.calls.length >= 1, 'consolidate 应调到 LLM');
    const msgs = llm.calls[0]!;
    return { system: msgs[0]!.content, user: msgs[1]!.content };
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
}

test('consolidate system prompt：en 缺省下为英文、correct/conflict 纪律等义在、无残留中文', async () => {
  const { system, user } = await captureConsolidatePrompt('en');
  assert.match(system, /You maintain a cognitive profile/, 'en system 开头');
  assert.match(
    system,
    /only flag the conflict, do not replace/,
    'conflict 纪律：只标不替换（英译等义）',
  );
  assert.match(system, /explicitly corrected\/negated/, 'correct 纪律：明确纠正（英译等义）');
  assert.equal(CJK.test(system), false, 'en system 无残留中文');
  assert.match(user, /\[New material\]/, 'en user 骨架标签英文');
});

test('consolidate system prompt：config.language=zh → 中文、纪律原文在', async () => {
  const { system, user } = await captureConsolidatePrompt('zh');
  assert.match(system, /你在维护对用户的认知画像/, 'zh system 开头');
  assert.match(system, /只标冲突，不替换/, 'zh conflict 纪律原文');
  assert.match(user, /【新材料】/, 'zh user 骨架标签中文');
});

test('reply（走单例 resolveLang）：缺省 system prompt 为英文', async () => {
  const llm = capturingLLM('ok');
  await reply('hi', [], [], llm);
  const sys = llm.calls[0]![0]!.content;
  assert.match(sys, /Respond to the user naturally/, '缺省英文回话 system');
});
