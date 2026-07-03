/**
 * 跨会话趋势（阶段 4-B）：反复出现的状态 → 规则筛频率 + LLM 归纳 → 趋势认知。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { aggregateTrends } from '../src/background/trends.ts';

/** 造 n 条"负面状态"证据 + 各自的 state 认知（都在窗口内）。 */
function seedStates(ev: SqliteEvidenceStore, cog: SqliteCognitionStore, texts: string[]): string[] {
  const ids: string[] = [];
  texts.forEach((t, i) => {
    const e = ev.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: t, occurredAt: `2026-06-2${i}T08:00:00.000Z` });
    cog.put({ subjectId: 'u', content: `用户${t}`, contentType: 'state', formedBy: 'stated', confidence: 250, credStatus: 'low', evidence: [{ evidenceId: e.id, relation: 'support' }] });
    ids.push(e.id);
  });
  return ids;
}

test('趋势：窗口内状态够频 → LLM 归纳出趋势认知（ruled、挂证据）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const now = new Date('2026-06-30T00:00:00.000Z');
  try {
    const ids = seedStates(ev, cog, ['很烦', '又没睡好', '提不起劲', '还是很累']);
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"trends":[{"content":"用户最近这段时间持续情绪低落","based_on_evidence_ids":${JSON.stringify(ids)}}]}`; } };
    const r = await aggregateTrends('u', { evidenceStore: ev, cognitionStore: cog, llm: stub }, now);
    assert.equal(r.trends.length, 1, '聚出 1 条趋势');
    const t = r.trends[0]!;
    assert.equal(t.contentType, 'trend');
    assert.equal(t.formedBy, 'ruled', '规则聚出，比 inferred 可信');
    assert.ok(cog.sourcesOf(t.id).length >= 3, '挂着聚合的状态证据可溯源');
  } finally {
    ev.close(); cog.close();
  }
});

test('趋势：状态太少（不够频）→ 不聚、不调模型', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const now = new Date('2026-06-30T00:00:00.000Z');
  try {
    seedStates(ev, cog, ['有点烦']); // 只 1 条，< trendMinCount(3)
    const stub = { callCount: 0, async chat() { this.callCount++; return '{"trends":[]}'; } };
    const r = await aggregateTrends('u', { evidenceStore: ev, cognitionStore: cog, llm: stub }, now);
    assert.equal(r.trends.length, 0, '不够频不聚');
    assert.equal(stub.callCount, 0, '不够频根本不调模型');
  } finally {
    ev.close(); cog.close();
  }
});

test('趋势：同一批证据已聚过 → 不重复聚（dedup）', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const now = new Date('2026-06-30T00:00:00.000Z');
  try {
    const ids = seedStates(ev, cog, ['很烦', '又没睡好', '提不起劲']);
    const stub = { callCount: 0, async chat() { this.callCount++; return `{"trends":[{"content":"用户最近持续低落","based_on_evidence_ids":${JSON.stringify(ids)}}]}`; } };
    await aggregateTrends('u', { evidenceStore: ev, cognitionStore: cog, llm: stub }, now);
    const r2 = await aggregateTrends('u', { evidenceStore: ev, cognitionStore: cog, llm: stub }, now);
    assert.equal(r2.trends.length, 0, '这批已聚过不重复');
    assert.equal(stub.callCount, 1, '第二次不调模型');
  } finally {
    ev.close(); cog.close();
  }
});
