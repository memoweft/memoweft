/**
 * 冲突复看：把 conflicted 认知拿出来、并排亮正反证据主动问、去重。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { revisitConflicts } from '../src/asking/revisitConflicts.ts';

test('冲突复看：conflicted 认知 → 带正反两面证据的提问 + 标已问去重', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const eTea = ev.put({
      subjectId: 'u',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '我爱喝茶',
    });
    const eCoffee = ev.put({
      subjectId: 'u',
      sourceKind: 'observed',
      hostId: 'h',
      rawContent: '这周一直在点咖啡',
    });
    const conflict = cog.put({
      subjectId: 'u',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'conflicted',
      evidence: [
        { evidenceId: eTea.id, relation: 'support' },
        { evidenceId: eCoffee.id, relation: 'contradict' },
      ],
    });
    // 一条正常认知不该被复看
    cog.put({
      subjectId: 'u',
      content: '用户周末参加陶艺课',
      contentType: 'fact',
      formedBy: 'stated',
      confidence: 700,
      credStatus: 'stable',
    });

    const r = await revisitConflicts('u', { cognitionStore: cog, evidenceStore: ev });
    assert.equal(r.proposals.length, 1, '只复看那条冲突');
    const p = r.proposals[0]!;
    assert.equal(p.kind, 'conflict');
    assert.ok(p.question.includes('喝茶'), '问法点到这条认知');
    assert.ok(
      p.evidence.some((e) => e.summary.includes('茶')),
      '亮支撑侧证据',
    );
    assert.ok(
      (p.contradictEvidence || []).some((e) => e.summary.includes('咖啡')),
      '并排亮反对侧证据',
    );
    assert.ok(cog.get(conflict.id)?.askedAt, '标已问');

    const r2 = await revisitConflicts('u', { cognitionStore: cog, evidenceStore: ev });
    assert.equal(r2.proposals.length, 0, '问过的不再复看');
  } finally {
    ev.close();
    cog.close();
  }
});
