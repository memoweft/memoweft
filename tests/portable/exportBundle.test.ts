/**
 * 便携记忆包 · 导出（Phase 5-A）。纯离线，用内存库。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores } from '../../src/store/openStores.ts';
import { exportBundle } from '../../src/portable/exportBundle.ts';
import { BUNDLE_FORMAT, BUNDLE_SCHEMA_VERSION } from '../../src/portable/model.ts';

test('exportBundle：导出完整三层 + 溯源关系，保真 id/时间戳', () => {
  const s = openStores(':memory:');
  try {
    const e1 = s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶' });
    const e2 = s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '游戏开到3:30' });
    const ev = s.eventStore.put({ subjectId: 'owner', summary: '聊了茶+游戏', occurredAt: e1.occurredAt, evidenceIds: [e1.id, e2.id] });
    const c = s.cognitionStore.put({
      subjectId: 'owner', content: '用户喜欢喝茶', contentType: 'preference', formedBy: 'stated',
      confidence: 600, credStatus: 'limited', evidence: [{ evidenceId: e1.id, relation: 'support' }],
    });

    const bundle = exportBundle('owner', s, { now: '2026-07-02T00:00:00.000Z', hostId: 'testbench' });

    assert.equal(bundle.format, BUNDLE_FORMAT);
    assert.equal(bundle.schemaVersion, BUNDLE_SCHEMA_VERSION);
    assert.equal(bundle.exportedAt, '2026-07-02T00:00:00.000Z');
    assert.equal(bundle.subjectId, 'owner');
    assert.equal(bundle.source.hostId, 'testbench');
    assert.equal(bundle.source.exportMode, 'full');

    assert.equal(bundle.data.evidence.length, 2);
    assert.equal(bundle.data.events.length, 1);
    assert.equal(bundle.data.cognitions.length, 1);
    assert.equal(bundle.data.eventEvidence.length, 2, '事件覆盖两条证据');
    assert.deepEqual(bundle.data.cognitionEvidence, [{ cognitionId: c.id, evidenceId: e1.id, relation: 'support' }]);

    // 保真：导出的行 id / 时间戳与库内一致
    const gotE1 = bundle.data.evidence.find((e) => e.id === e1.id)!;
    assert.equal(gotE1.recordedAt, e1.recordedAt, 'recordedAt 保真');
    assert.equal(bundle.data.events[0]!.id, ev.id);
    assert.equal(bundle.data.cognitions[0]!.id, c.id);
    assert.equal(bundle.data.cognitions[0]!.createdAt, c.createdAt);

    assert.deepEqual(bundle.metadata.counts, { evidence: 2, events: 1, cognitions: 1 });
  } finally {
    s.close();
  }
});

test('exportBundle：只导出指定 subject，不夹带别的 subject', () => {
  const s = openStores(':memory:');
  try {
    s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: 'A' });
    s.evidenceStore.put({ subjectId: 'other', sourceKind: 'spoken', hostId: 'h', rawContent: 'B' });
    s.cognitionStore.put({ subjectId: 'other', content: 'x', contentType: 'fact', formedBy: 'stated', confidence: 500, credStatus: 'limited' });

    const bundle = exportBundle('owner', s);
    assert.equal(bundle.data.evidence.length, 1);
    assert.equal(bundle.data.evidence[0]!.subjectId, 'owner');
    assert.equal(bundle.data.cognitions.length, 0, 'other 的认知不导出');
  } finally {
    s.close();
  }
});
