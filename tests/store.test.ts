/**
 * 共享连接 + 事务（写路径一致性）：一条连接三个 store，transaction() 能把跨表写一起提交/回滚。
 * 重点验证：consolidate 中途失败时，认知写入与 markConsolidated 一起回滚，不留下部分画像，也不误标事件。
 * 用假 LLM，纯离线。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores } from '../src/store/openStores.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';

/** Node 24 实测：PRAGMA busy_timeout 的结果列名是 `timeout`，取 .timeout、别断言裸数字。 */
function busyTimeout(db: { prepare(sql: string): { get(): unknown } }): number {
  const row = db.prepare('PRAGMA busy_timeout').get() as { timeout: number };
  return row.timeout;
}

test('busy_timeout：openStores 的共享连接设成 5000（多进程写冲突先等待再报告）', () => {
  const s = openStores(':memory:');
  try {
    assert.equal(busyTimeout(s.db), 5000);
  } finally {
    s.close();
  }
});

test('busy_timeout：三个 store 自开连接分支（传字符串路径）各自设成 5000', () => {
  // 传字符串 → ownsDb=true → 自开连接、设 pragma；':memory:' 走的正是这条自开分支。
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // @ts-expect-error 测试内探 private db（只读 pragma，不改行为）
    assert.equal(busyTimeout(ev.db), 5000, 'evidence 自开连接');
    // @ts-expect-error Access the private database handle for pragma verification.
    assert.equal(busyTimeout(evt.db), 5000, 'event 自开连接');
    // @ts-expect-error Access the private database handle for pragma verification.
    assert.equal(busyTimeout(cog.db), 5000, 'cognition 自开连接');
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
});

test('transaction：中途抛错 → 跨表写全回滚', () => {
  const s = openStores(':memory:');
  try {
    assert.throws(() => {
      s.transaction(() => {
        s.cognitionStore.put({
          subjectId: 'owner',
          content: 'A',
          contentType: 'fact',
          formedBy: 'stated',
          confidence: 500,
          credStatus: 'limited',
        });
        s.eventStore.put({
          subjectId: 'owner',
          summary: 'E',
          occurredAt: new Date().toISOString(),
          evidenceIds: [],
        });
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
      s.cognitionStore.put({
        subjectId: 'owner',
        content: 'A',
        contentType: 'fact',
        formedBy: 'stated',
        confidence: 500,
        credStatus: 'limited',
      });
      s.eventStore.put({
        subjectId: 'owner',
        summary: 'E',
        occurredAt: new Date().toISOString(),
        evidenceIds: [],
      });
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
        s.cognitionStore.put({
          subjectId: 'owner',
          content: 'A',
          contentType: 'fact',
          formedBy: 'stated',
          confidence: 500,
          credStatus: 'limited',
        });
        // 里层再包一次：不该抛"cannot start a transaction within a transaction"
        s.transaction(() => {
          s.cognitionStore.put({
            subjectId: 'owner',
            content: 'B',
            contentType: 'fact',
            formedBy: 'stated',
            confidence: 500,
            credStatus: 'limited',
          });
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
    const ev1 = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 't',
      rawContent: '我喜欢喝茶',
    });
    const ev2 = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 't',
      rawContent: '我在学吉他',
    });
    s.eventStore.put({
      subjectId: 'owner',
      summary: '聊了茶和吉他',
      occurredAt: ev1.occurredAt,
      evidenceIds: [ev1.id, ev2.id],
    });

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
      eventStore: s.eventStore,
      evidenceStore: s.evidenceStore,
      cognitionStore: s.cognitionStore,
      llm,
      transaction: s.transaction,
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
    const ev1 = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 't',
      rawContent: '我喜欢喝茶',
    });
    const ev2 = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 't',
      rawContent: '我在学吉他',
    });
    s.eventStore.put({
      subjectId: 'owner',
      summary: '聊了茶和吉他',
      occurredAt: ev1.occurredAt,
      evidenceIds: [ev1.id, ev2.id],
    });

    // 模拟写路径中途失败：第 2 次 put 抛错（第 1 条已写入，依靠事务回滚）。
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
      () =>
        consolidate('owner', {
          eventStore: s.eventStore,
          evidenceStore: s.evidenceStore,
          cognitionStore: s.cognitionStore,
          llm,
          transaction: s.transaction,
        }),
      /boom mid-mutation/,
    );

    // 关键断言：第 1 条认知也被回滚，事件未被误标为已消化，因此下轮仍可重新处理。
    assert.equal(s.cognitionStore.all('owner').length, 0, '半途写入的认知已回滚');
    assert.equal(s.eventStore.unconsolidated('owner').length, 1, '事件未被标记消化 → 可安全重跑');
  } finally {
    s.close();
  }
});
