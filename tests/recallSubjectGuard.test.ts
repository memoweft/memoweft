/**
 * 召回边界（多 subject 隐私止血）：共用 retriever 时，索引可能混入其他 subject 的条目，
 * Conversation 注入点必须按 subjectId 硬过滤——不是本人的认知绝不注入回话。
 * 用伪 retriever / 伪 llm，不依赖网络与嵌入器；数据库全用 :memory:，无运行时残留。
 *
 * 防假绿设计：两条认知都能通过既有门槛（preference + stated + 置信 900 + stable，
 * preference 默认不衰减，900 远高于 minEffectiveConfidence=80），确保 B 若泄漏一定测得出来。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { Conversation } from '../src/pipeline/conversation.ts';

test('召回边界：他人 subject 的认知不注入，本人认知正常召回', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // subject A（本轮说话人）与 subject B（他人）各一条认知；两条都过得了所有既有门槛。
    const mine = cog.put({
      subjectId: 'subject-a',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });
    const other = cog.put({
      subjectId: 'subject-b',
      content: 'SUBJECT_B_SECRET 只属于 B 的隐私偏好',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });

    // 伪 retriever：模拟共用索引越界——B 的条目也被召回，且排在最前、分数最高。
    const retriever = {
      async indexAll() {},
      async search() {
        return [
          { id: other.id, score: 0.99 },
          { id: mine.id, score: 0.9 },
        ];
      },
    };
    const llm = { callCount: 0, async chat() { this.callCount++; return '好的。'; } };

    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm });
    // 以 subject A 的身份说话（stored.subjectId = 'subject-a'）。
    const outcome = await convo.handle('喝点什么好', { subjectId: 'subject-a' });

    const contents = outcome.recall.map((r) => r.content);
    // 断言 1（隔离）：B 的标记串绝不出现在注入内容里。
    assert.ok(
      !contents.some((c) => c.includes('SUBJECT_B_SECRET')),
      '他人 subject 的认知泄漏进了 recall（越界召回未被过滤）',
    );
    // 断言 2（阳性对照）：A 自己的认知正常召回——证明过滤没有误伤本人。
    assert.ok(contents.includes('用户喜欢喝茶'), '本人认知应正常召回（过滤误伤了本人）');
    assert.equal(outcome.recall.length, 1, '只注入本人的那 1 条');
  } finally {
    store.close();
    cog.close();
  }
});
