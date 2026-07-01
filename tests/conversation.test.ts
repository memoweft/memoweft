/**
 * 召回门控（阶段 4-B 衰减门控）：失效的、有效置信太低的认知，不该被塞进回话。
 * 用伪 retriever / 伪 llm，不依赖网络与嵌入器。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { Conversation } from '../src/pipeline/conversation.ts';

test('召回门控：失效 / 有效置信过低的认知不注入回话', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 三条认知：正常 / 置信过低 / 已失效
    const keep = cog.put({ subjectId: 'owner', content: '用户喜欢喝茶', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    const tooLow = cog.put({ subjectId: 'owner', content: '用户此刻有点烦', contentType: 'preference', formedBy: 'inferred', confidence: 60, credStatus: 'candidate' });
    const dead = cog.put({ subjectId: 'owner', content: '用户喜欢咖啡（已被纠正）', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' });
    cog.update(dead.id, { invalidAt: new Date().toISOString() });

    // 伪 retriever：三条都"召回"到（高相似度），交给门控去筛
    const retriever = {
      async indexAll() {},
      async search() {
        return [
          { id: keep.id, score: 0.9 },
          { id: tooLow.id, score: 0.9 },
          { id: dead.id, score: 0.9 },
        ];
      },
    };
    const llm = { callCount: 0, async chat() { this.callCount++; return '好的。'; } };

    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm });
    const outcome = await convo.handle('喝点什么好');

    const ids = outcome.recall.map((r) => r.content);
    assert.equal(outcome.recall.length, 1, '只注入 1 条（其余被门控）');
    assert.ok(ids.includes('用户喜欢喝茶'), '正常认知留下');
    assert.ok(!ids.some((c) => c.includes('有点烦')), '有效置信过低 → 不注入');
    assert.ok(!ids.some((c) => c.includes('咖啡')), '已失效 → 不注入');
  } finally {
    store.close();
    cog.close();
  }
});
