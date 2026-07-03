/**
 * 治慢③离线护栏：LLM 池（"可切换模型"架构第一块）。
 * smoke——装配 + for(purpose) 返回 client 形状；不打网络（构造 client 不发请求）。
 * 回退语义（缺 DLA_WRITE_LLM_* → write 用 chat）见 pool.ts 注释，靠 server 运行 + code review 保证。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadLLMPool, type LLMPurpose } from '../src/llm/pool.ts';

test('LLMPool：装配后 for(chat/write) 都返回可用 client 形状（缺配也不崩，真调用才报）', () => {
  const pool = loadLLMPool();
  for (const purpose of ['chat', 'write'] as LLMPurpose[]) {
    const c = pool.for(purpose);
    assert.equal(typeof c.chat, 'function', `${purpose} 有 chat 方法`);
    assert.equal(typeof c.callCount, 'number', `${purpose} 有 callCount`);
  }
});
