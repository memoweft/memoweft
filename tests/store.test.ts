/**
 * 共享连接 + 事务（写路径一致性）：一条连接三个 store，transaction() 能把跨表写一起提交/回滚。
 * 重点验证：consolidate 崩在中间时，认知写入与 markConsolidated 一起回滚（不留半拉画像、事件不被误标已消化）。
 * 用假 LLM，纯离线。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores } from '../src/store/openStores.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';

test('transaction：中途抛错 → 跨表写全回滚', () => {
  const s = openStores(':memory:');
  try {
    assert.throws(() => {
      s.transaction(() => {
        s.cognitionStore.put({ subjectId: 'owner', content: 'A', contentType: 'fact', formedBy: 'stated', confidence: 500, credStatus: 'limited' });
        s.eventStore.put({ subjectId: 'owner', summary: 'E', occurredAt: new Date().toISOString(), evidenceIds: [] });
        throw new Error('boom');
      });
    }, /boom/);
    assert.equal(s.cognitionStore.all('owner').length, 0, '认知回滚了');
    assert.equal(s.eventStore.all('owner').length, 0, '事件也回滚了（同一连接同一事务）');
  } finally {
    s.close();
  }
});

test('transaction：正常返回 → 跨表写一起提交', () => {
  const s = openStores(':memory:');
  try {
    const r = s.transaction(() => {
      s.cognitionStore.put({ subjectId: 'owner', content: 'A', contentType: 'fact', formedBy: 'stated', confidence: 500, credStatus: 'limited' });
      s.eventStore.put({ subjectId: 'owner', summary: 'E', occurredAt: new Date().toISOString(), evidenceIds: [] });
      return 42;
    });
    assert.equal(r, 42, '返回值透传');
    assert.equal(s.cognitionStore.all('owner').length, 1);
    assert.equal(s.eventStore.all('owner').length, 1);
  } finally {
    s.close();
  }
});

test('transaction：可重入——里层再调不报 "nested transaction"，随外层一起回滚', () => {
  const s = openStores(':memory:');
  try {
    assert.throws(() => {
      s.transaction(() => {
        s.cognitionStore.put({ subjectId: 'owner', content: 'A', contentType: 'fact', formedBy: 'stated', confidence: 500, credStatus: 'limited' });
        // 里层再包一次：不该抛"cannot start a transaction within a transaction"
        s.transaction(() => {
          s.cognitionStore.put({ subjectId: 'owner', content: 'B', contentType: 'fact', formedBy: 'stated', confidence: 500, credStatus: 'limited' });
        });
        throw new Error('boom');
      });
    }, /boom/);
    assert.equal(s.cognitionStore.all('owner').length, 0, '里外层写入都随最外层回滚');
  } finally {
    s.close();
  }
});

test('consolidate（共享连接 + 事务）：正常路径提交，认知落库 + 事件标已消化', async () => {
  const s = openStores(':memory:');
  try {
    const ev1 = s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 't', rawContent: '我喜欢喝茶' });
    const ev2 = s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 't', rawContent: '我在学吉他' });
    s.eventStore.put({ subjectId: 'owner', summary: '聊了茶和吉他', occurredAt: ev1.occurredAt, evidenceIds: [ev1.id, ev2.id] });

    const llm = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return `{"new":[
          {"content":"喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${ev1.id}"]},
          {"content":"在学吉他","content_type":"project","formed_by":"stated","support_evidence_ids":["${ev2.id}"]}
        ]}`;
      },
    };

    const r = await consolidate('owner', {
      eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore,
      llm, transaction: s.transaction,
    });
    assert.equal(r.created.length, 2, '两条认知都落库');
    assert.equal(s.eventStore.unconsolidated('owner').length, 0, '事件已标记消化');
  } finally {
    s.close();
  }
});

test('consolidate（共享连接 + 事务）：写到一半抛错 → 认知不落、事件不被标已消化（整段回滚）', async () => {
  const s = openStores(':memory:');
  try {
    const ev1 = s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 't', rawContent: '我喜欢喝茶' });
    const ev2 = s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 't', rawContent: '我在学吉他' });
    s.eventStore.put({ subjectId: 'owner', summary: '聊了茶和吉他', occurredAt: ev1.occurredAt, evidenceIds: [ev1.id, ev2.id] });

    // 模拟写路径中途崩：第 2 次 put 抛错（第 1 条已写入，靠事务回滚它）。
    const realPut = s.cognitionStore.put.bind(s.cognitionStore);
    let puts = 0;
    s.cognitionStore.put = (input) => {
      if (++puts === 2) throw new Error('boom mid-mutation');
      return realPut(input);
    };

    const llm = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return `{"new":[
          {"content":"喜欢喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${ev1.id}"]},
          {"content":"在学吉他","content_type":"project","formed_by":"stated","support_evidence_ids":["${ev2.id}"]}
        ]}`;
      },
    };

    await assert.rejects(
      () => consolidate('owner', {
        eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore,
        llm, transaction: s.transaction,
      }),
      /boom mid-mutation/,
    );

    // 关键断言：第 1 条认知也被回滚（不留半拉画像），事件没被误标已消化（下轮还能重新处理）。
    assert.equal(s.cognitionStore.all('owner').length, 0, '半途写入的认知已回滚');
    assert.equal(s.eventStore.unconsolidated('owner').length, 1, '事件未被标记消化 → 可安全重跑');
  } finally {
    s.close();
  }
});
