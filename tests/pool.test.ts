/**
 * LLM 池离线契约：按 chat/write 用途选择模型并保持缺省回退行为。
 * smoke——装配 + for(purpose) 返回 client 形状；不打网络（构造 client 不发请求）。
 * 回退语义（缺 DLA_WRITE_LLM_* → write 用 chat）见 pool.ts 注释，靠 server 运行 + code review 保证。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadLLMPool, type LLMPurpose } from '../src/llm/pool.ts';

test('LLMPool：装配后 for(chat/write) 都返回 client；缺配延迟到模型请求时报告', () => {
  const pool = loadLLMPool();
  for (const purpose of ['chat', 'write'] as LLMPurpose[]) {
    const c = pool.for(purpose);
    assert.equal(typeof c.chat, 'function', `${purpose} 有 chat 方法`);
    assert.equal(typeof c.callCount, 'number', `${purpose} 有 callCount`);
  }
});
