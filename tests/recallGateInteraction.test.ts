/**
 * 召回门控 × topK 的【交互】覆盖。
 *
 * 既有测试覆盖的是各道门的机械行为（archived 的不返回、跨 subject 的不返回……），
 * bench/eval-retrieval.mjs 覆盖的是 Retriever 层的排序质量（recall@5 / mrr@10）。
 * 两者之间有一条缝，产品每轮对话都走它：
 *
 *   召回改「超取再截断」后 —— `search(query, topK×overfetchFactor)` 先拿回一个更大的候选池，
 *   逐条过六道门、凑够 topK 即停。门控挡掉的名额由池里更靠后的合格认知补上，不再欠填
 *   （旧实现只取 topK 条再过门，前 K 名被挡就不补位、库里合格认知取不到）。
 *
 * 本文件覆盖「超取补位」与六道门的交互。全离线、确定性（词序检索器 + 注入 clock）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { recallCognitions } from '../src/retrieval/recall.ts';
import { config } from '../src/config.ts';
import type { Retriever } from '../src/retrieval/retriever.ts';
import type { Cognition } from '../src/cognition/model.ts';

/** 确定性检索器：按插入顺序返回前 topK，score 递减。不做真检索——本文件测的是【门控】不是排序。 */
function orderedRetriever(ids: string[]): Retriever {
  return {
    async indexAll() {},
    async search(_query, topK) {
      return ids.slice(0, topK).map((id, i) => ({ id, score: 1 - i * 0.01 }));
    },
  };
}

interface SeedSpec {
  content: string;
  confidence?: number;
  credStatus?: Cognition['credStatus'];
  contentType?: Cognition['contentType'];
  subjectId?: string;
  invalid?: boolean;
  archived?: boolean;
  muted?: boolean;
}

function seed(store: SqliteCognitionStore, specs: SeedSpec[]): string[] {
  const ids: string[] = [];
  for (const s of specs) {
    const c = store.put({
      subjectId: s.subjectId ?? 'owner',
      content: s.content,
      contentType: s.contentType ?? 'preference',
      formedBy: 'stated',
      confidence: s.confidence ?? 900,
      credStatus: s.credStatus ?? 'stable',
    });
    if (s.invalid) store.update(c.id, { invalidAt: new Date().toISOString() });
    if (s.archived) store.update(c.id, { archivedAt: new Date().toISOString() });
    if (s.muted) store.update(c.id, { mutedAt: new Date().toISOString() });
    ids.push(c.id);
  }
  return ids;
}

test('超取补位：前 K 名全被门控挡掉时，从库里剩下的合格认知补满 topK', async () => {
  const store = new SqliteCognitionStore(':memory:');
  try {
    const topK = config.retrieval.topK;
    // 前 topK 条全部会被门控挡掉，其后紧跟着 topK 条完全合格的认知。
    const blocked: SeedSpec[] = Array.from({ length: topK }, (_, i) => ({
      content: `blocked ${i}`,
      archived: true,
    }));
    const healthy: SeedSpec[] = Array.from({ length: topK }, (_, i) => ({
      content: `healthy ${i}`,
    }));
    const ids = seed(store, [...blocked, ...healthy]);

    const got = await recallCognitions('q', 'owner', {
      retriever: orderedRetriever(ids),
      cognitionStore: store,
    });

    // 超取后：候选池覆盖到后面的 healthy，前 K 名被挡的名额被补满（旧实现这里会返回空）。
    assert.equal(got.length, topK, '前 K 名全被挡 → 从库里合格认知补满 topK');
    assert.ok(
      got.every((g) => g.content.startsWith('healthy ')),
      '补进来的都是合格认知',
    );
  } finally {
    store.close();
  }
});

test('超取补位：门控挡掉几条，就从后面的候选补几条，凑满 topK', async () => {
  const store = new SqliteCognitionStore(':memory:');
  try {
    const topK = config.retrieval.topK;
    // 前 K 名里交替放"会被挡"和"合格"的，其后再放一批合格的。
    const head: SeedSpec[] = Array.from({ length: topK }, (_, i) =>
      i % 2 === 0 ? { content: `muted ${i}`, muted: true } : { content: `ok ${i}` },
    );
    const tail: SeedSpec[] = Array.from({ length: topK }, (_, i) => ({ content: `tail ${i}` }));
    const ids = seed(store, [...head, ...tail]);

    const got = await recallCognitions('q', 'owner', {
      retriever: orderedRetriever(ids),
      cognitionStore: store,
    });

    // 超取后：head 里被 muted 挡掉的名额，由 tail 的合格认知补上，凑满 topK（旧实现只会返回 head 里合格的那几条）。
    assert.equal(got.length, topK, 'head 里被挡的名额由 tail 补上 → 凑满 topK');
    const okFromHead = head.filter((s) => !s.muted).length;
    assert.equal(
      got.filter((g) => g.content.startsWith('ok ')).length,
      okFromHead,
      'head 里合格的都在',
    );
    assert.ok(
      got.some((g) => g.content.startsWith('tail ')),
      'tail 里的合格认知被用来补位',
    );
  } finally {
    store.close();
  }
});

test('六道门逐一生效，且互不遮蔽（同一批里各挡各的）', async () => {
  const store = new SqliteCognitionStore(':memory:');
  try {
    const ids = seed(store, [
      { content: 'healthy' },
      { content: 'invalid one', invalid: true },
      { content: 'archived one', archived: true },
      { content: 'muted one', muted: true },
      { content: 'other subject', subjectId: 'someone-else' },
    ]);

    const got = await recallCognitions('q', 'owner', {
      retriever: orderedRetriever(ids),
      cognitionStore: store,
    });

    assert.deepEqual(
      got.map((g) => g.content),
      ['healthy'],
      'invalid / archived / muted / 跨 subject 四道门各挡各的，只剩健康的那条',
    );
  } finally {
    store.close();
  }
});

test('衰减门控：同一条认知随 clock 前进跌出召回（有效置信低于门槛）', async () => {
  const store = new SqliteCognitionStore(':memory:');
  try {
    // state 是 transient 类型（半衰期短），置信刚过门槛 → 前进时间后应跌出。
    const ids = seed(store, [
      { content: 'fleeting mood', contentType: 'state', confidence: 300, credStatus: 'low' },
    ]);
    const deps = { retriever: orderedRetriever(ids), cognitionStore: store };

    const now = new Date();
    const fresh = await recallCognitions('q', 'owner', deps, config, now);
    assert.equal(fresh.length, 1, '刚落库时能召回');

    const later = new Date(now.getTime() + 90 * 86400000); // 90 天后
    const decayed = await recallCognitions('q', 'owner', deps, config, later);
    assert.equal(decayed.length, 0, '90 天后有效置信跌破 minEffectiveConfidence → 不再注入');
  } finally {
    store.close();
  }
});

test('相似度门控在最前：低于 minSimilarity 的连认知都不取', async () => {
  const store = new SqliteCognitionStore(':memory:');
  try {
    const ids = seed(store, [{ content: 'a' }, { content: 'b' }]);
    const lowScore: Retriever = {
      async indexAll() {},
      async search() {
        return ids.map((id) => ({ id, score: -1 })); // 恒低于任何阈值
      },
    };
    const cfg = { ...config, retrieval: { ...config.retrieval, minSimilarity: 0.5 } };
    const got = await recallCognitions(
      'q',
      'owner',
      { retriever: lowScore, cognitionStore: store },
      cfg,
    );
    assert.equal(got.length, 0, '相似度不足 → 一条都不注入');
  } finally {
    store.close();
  }
});
