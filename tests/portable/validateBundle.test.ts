/**
 * 便携记忆包 · 校验。纯离线。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores } from '../../src/store/openStores.ts';
import { exportBundle } from '../../src/portable/exportBundle.ts';
import { validateBundle } from '../../src/portable/validateBundle.ts';
import type { MemoryBundle } from '../../src/portable/model.ts';

function seedBundle(): MemoryBundle {
  const s = openStores(':memory:');
  try {
    const e1 = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '我喜欢喝茶',
    });
    s.eventStore.put({
      subjectId: 'owner',
      summary: 'E',
      occurredAt: e1.occurredAt,
      evidenceIds: [e1.id],
    });
    s.cognitionStore.put({
      subjectId: 'owner',
      content: 'C',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
      evidence: [{ evidenceId: e1.id, relation: 'support' }],
    });
    return exportBundle('owner', s, { now: '2026-07-02T00:00:00.000Z' });
  } finally {
    s.close();
  }
}

test('validateBundle：合法包 → valid、无 error', () => {
  const r = validateBundle(seedBundle());
  assert.equal(r.valid, true);
  assert.deepEqual(r.errors, []);
});

test('validateBundle：格式/版本/结构错 → 致命 error', () => {
  const b = seedBundle();
  assert.equal(validateBundle({ ...b, format: 'x' }).valid, false);
  assert.equal(validateBundle({ ...b, schemaVersion: 99 }).valid, false, '高于支持版本 → 拦');
  assert.equal(validateBundle(null).valid, false);
  assert.equal(validateBundle({ ...b, data: undefined }).valid, false);
});

test('validateBundle：溯源引用悬空 → 致命 error', () => {
  const b = seedBundle();
  const broken: MemoryBundle = {
    ...b,
    data: {
      ...b.data,
      cognitionEvidence: [
        { cognitionId: 'no-such', evidenceId: b.data.evidence[0]!.id, relation: 'support' },
      ],
    },
  };
  const r = validateBundle(broken);
  assert.equal(r.valid, false);
  assert.ok(
    r.errors.some((e) => e.includes('cognitionEvidence')),
    '报出悬空的溯源引用',
  );
});

test('validateBundle：subject 混入 → 软告警但仍 valid', () => {
  const b = seedBundle();
  const mixed: MemoryBundle = {
    ...b,
    data: { ...b.data, evidence: b.data.evidence.map((e) => ({ ...e, subjectId: 'intruder' })) },
  };
  const r = validateBundle(mixed);
  assert.equal(r.valid, true, '混入不致命');
  assert.ok(r.warnings.length > 0, '但要告警');
});

test('validateBundle：元素缺 id → 致命 error（不被 Set(undefined) 蒙混放行）', () => {
  const b = seedBundle();
  const noId: MemoryBundle = {
    ...b,
    data: {
      ...b.data,
      evidence: b.data.evidence.map((e, i) =>
        i === 0 ? { ...e, id: undefined as unknown as string } : e,
      ),
    },
  };
  assert.equal(validateBundle(noId).valid, false, '缺 id 的证据被拦');
});

test('validateBundle：包内重复 id → 致命 error（防 merge 撞主键）', () => {
  const b = seedBundle();
  const dup: MemoryBundle = {
    ...b,
    data: { ...b.data, evidence: [b.data.evidence[0]!, b.data.evidence[0]!] },
  };
  const r = validateBundle(dup);
  assert.equal(r.valid, false);
  assert.ok(
    r.errors.some((e) => e.includes('duplicate ids')),
    '报出重复 id（缺省 en 文案）',
  );
});
