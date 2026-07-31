import assert from 'node:assert/strict';
import { test } from 'node:test';
import { exportBundle } from '../../src/portable/exportBundle.ts';
import { importBundle } from '../../src/portable/importBundle.ts';
import type { MemoryBundle } from '../../src/portable/model.ts';
import { validateBundle } from '../../src/portable/validateBundle.ts';
import { openStores } from '../../src/store/openStores.ts';

const T = '2026-07-31T00:00:00.000Z';

function minimalBundle(): MemoryBundle {
  return {
    format: 'memoweft-bundle',
    schemaVersion: 2,
    exportedAt: T,
    memoWeftVersion: '1.0.0-rc.1',
    subjectId: 'owner',
    source: { hostId: 'test', exportMode: 'full' },
    data: {
      evidence: [
        {
          id: 'ev-1',
          subjectId: 'owner',
          sourceKind: 'spoken',
          hostId: 'test',
          originId: null,
          occurredAt: T,
          recordedAt: T,
          rawContent: '用户原话',
          summary: '用户原话',
          allowLocalRead: true,
          allowCloudRead: false,
          allowInference: true,
          correctsEvidenceId: null,
        },
      ],
      events: [],
      eventEvidence: [],
      cognitions: [],
      cognitionEvidence: [],
      unconsolidatedEventIds: [],
    },
    metadata: { counts: { evidence: 1, events: 0, cognitions: 0 }, notes: [] },
  };
}

test('validateBundle：拒绝坏日期、非法 relation、孤儿和一证据多 semantic resolution', () => {
  const badDate = minimalBundle();
  badDate.data.evidence[0]!.occurredAt = 'not-a-date';
  assert.equal(validateBundle(badDate).valid, false);

  const badRelation = minimalBundle();
  badRelation.data.cognitions = [
    {
      id: 'cog-1',
      subjectId: 'owner',
      content: '判断',
      contentType: 'fact',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
      scope: null,
      validAt: null,
      invalidAt: null,
      askedAt: null,
      createdAt: T,
      updatedAt: T,
    },
  ];
  badRelation.data.cognitionEvidence = [
    { cognitionId: 'cog-1', evidenceId: 'ev-1', relation: 'neither' as never },
  ];
  assert.equal(validateBundle(badRelation).valid, false);

  const orphan = minimalBundle();
  orphan.data.semanticResolutions = [
    {
      id: 'sem-orphan',
      evidenceId: 'missing',
      resolvedContent: '解析',
      responseAct: null,
      promptAct: null,
      propositionOrigin: null,
      assertionStrength: null,
      requiredContext: null,
      resolverVersion: 'r1',
      createdAt: T,
    },
  ];
  assert.equal(validateBundle(orphan).valid, false);

  const duplicate = minimalBundle();
  duplicate.data.semanticResolutions = ['sem-1', 'sem-2'].map((id) => ({
    id,
    evidenceId: 'ev-1',
    resolvedContent: '解析',
    responseAct: null,
    promptAct: null,
    propositionOrigin: null,
    assertionStrength: null,
    requiredContext: null,
    resolverVersion: 'r1',
    createdAt: T,
  }));
  assert.equal(validateBundle(duplicate).valid, false);
});

test('importBundle：恶意 mixed-subject provenance 包致命拒绝且零写入', () => {
  const bundle = minimalBundle();
  bundle.data.evidence[0]!.subjectId = 'other-user';
  bundle.data.cognitions = [
    {
      id: 'cog-a',
      subjectId: 'owner',
      content: '试图泄露 other-user 摘要',
      contentType: 'fact',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
      scope: null,
      validAt: null,
      invalidAt: null,
      askedAt: null,
      createdAt: T,
      updatedAt: T,
    },
  ];
  bundle.data.cognitionEvidence = [
    { cognitionId: 'cog-a', evidenceId: 'ev-1', relation: 'support' },
  ];
  const target = openStores(':memory:');
  try {
    const validation = validateBundle(bundle);
    assert.equal(validation.valid, false);
    assert.ok(validation.errors.some((error) => error.includes('crosses subjects')));
    const plan = importBundle(bundle, target, { mode: 'merge' });
    assert.equal(plan.valid, false);
    assert.equal(target.evidenceStore.all().length, 0);
    assert.equal(target.cognitionStore.all('owner').length, 0);
  } finally {
    target.close();
  }
});

test('importBundle：重复 event/cognition join 致命拒绝且零写入', () => {
  const bundle = minimalBundle();
  bundle.data.events = [
    { id: 'evt-1', subjectId: 'owner', summary: '事件', occurredAt: T, createdAt: T },
  ];
  bundle.data.eventEvidence = [
    { eventId: 'evt-1', evidenceId: 'ev-1' },
    { eventId: 'evt-1', evidenceId: 'ev-1' },
  ];
  bundle.data.cognitions = [
    {
      id: 'cog-1',
      subjectId: 'owner',
      content: '判断',
      contentType: 'fact',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
      scope: null,
      validAt: null,
      invalidAt: null,
      askedAt: null,
      createdAt: T,
      updatedAt: T,
    },
  ];
  bundle.data.cognitionEvidence = [
    { cognitionId: 'cog-1', evidenceId: 'ev-1', relation: 'support' },
    { cognitionId: 'cog-1', evidenceId: 'ev-1', relation: 'support' },
  ];
  const target = openStores(':memory:');
  try {
    const validation = validateBundle(bundle);
    assert.equal(validation.valid, false);
    assert.ok(validation.errors.some((error) => error.includes('duplicate link')));
    const plan = importBundle(bundle, target, { mode: 'merge' });
    assert.equal(plan.valid, false);
    assert.equal(target.evidenceStore.all().length, 0);
    assert.equal(target.eventStore.all('owner').length, 0);
    assert.equal(target.cognitionStore.all('owner').length, 0);
  } finally {
    target.close();
  }
});

test('validateBundle：稳定写路径导出的空 evidence 可校验并 dry-run 导入', () => {
  const source = openStores(':memory:');
  const target = openStores(':memory:');
  try {
    source.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'test',
      rawContent: '',
      summary: '',
    });
    const bundle = exportBundle('owner', source, { now: T });
    assert.equal(bundle.data.evidence[0]!.rawContent, '');
    assert.equal(bundle.data.evidence[0]!.summary, '');
    assert.equal(validateBundle(bundle).valid, true);
    const plan = importBundle(bundle, target, { mode: 'dryRun' });
    assert.equal(plan.valid, true);
    assert.equal(plan.counts.evidence, 1);
  } finally {
    source.close();
    target.close();
  }
});

test('importBundle：墓碑阻止旧包复活 evidence、event summary 与派生 cognition', () => {
  const source = openStores(':memory:');
  const target = openStores(':memory:');
  try {
    const evidence = source.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'test',
      rawContent: '应被删除的原话',
    });
    const event = source.eventStore.put({
      subjectId: 'owner',
      summary: '不应复活的事件摘要',
      occurredAt: evidence.occurredAt,
      evidenceIds: [evidence.id],
    });
    const cognition = source.cognitionStore.put({
      subjectId: 'owner',
      content: '不应复活的派生画像',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
      evidence: [{ evidenceId: evidence.id, relation: 'support' }],
    });
    const bundle = exportBundle('owner', source, { now: T });
    assert.equal(importBundle(bundle, target, { mode: 'merge' }).valid, true);
    assert.equal(target.evidenceStore.remove(evidence.id), true);
    assert.equal(target.eventStore.remove(event.id), true, '模拟删除流程已清理派生 event');
    assert.equal(
      target.cognitionStore.remove(cognition.id),
      true,
      '模拟删除流程已清理派生 cognition',
    );

    const plan = importBundle(bundle, target, { mode: 'merge' });
    assert.equal(plan.valid, true);
    assert.equal(plan.counts.evidence, 0);
    assert.equal(plan.counts.events, 0);
    assert.equal(plan.counts.cognitions, 0);
    assert.equal(plan.counts.eventEvidence, 0);
    assert.equal(plan.counts.cognitionEvidence, 0);
    assert.equal(plan.duplicates.evidence, 1);
    assert.equal(target.evidenceStore.get(evidence.id), null, '墓碑仍不可见');
    assert.equal(target.eventStore.get(event.id), null, '旧 event.summary 没有复活');
    assert.equal(target.cognitionStore.get(cognition.id), null, '旧派生 cognition 没有复活');
    const exported = exportBundle('owner', target, { now: T });
    assert.equal(
      exported.data.events.some((value) => value.summary === event.summary),
      false,
    );
    assert.equal(
      exported.data.cognitions.some((value) => value.content === cognition.content),
      false,
    );
    assert.ok(
      target.db
        .prepare('SELECT 1 AS present FROM evidence WHERE id = ? AND deleted_at IS NOT NULL')
        .get(evidence.id),
      '墓碑仍在',
    );
    assert.ok(plan.warnings.some((warning) => warning.includes('tombstoned')));
    assert.ok(plan.warnings.some((warning) => warning.includes('derived summary revival')));
    assert.ok(plan.warnings.some((warning) => warning.includes('derived content revival')));
  } finally {
    source.close();
    target.close();
  }
});

test('importBundle：目标库已有解析时不插入第二条 semantic resolution', () => {
  const target = openStores(':memory:');
  try {
    const bundle = minimalBundle();
    assert.equal(importBundle(bundle, target, { mode: 'merge' }).valid, true);
    target.semanticResolutionStore.put({
      evidenceId: 'ev-1',
      resolvedContent: '既有解析',
      resolverVersion: 'existing',
    });
    bundle.data.semanticResolutions = [
      {
        id: 'sem-import',
        evidenceId: 'ev-1',
        resolvedContent: '备份解析',
        responseAct: null,
        promptAct: null,
        propositionOrigin: null,
        assertionStrength: null,
        requiredContext: null,
        resolverVersion: 'backup',
        createdAt: T,
      },
    ];

    const plan = importBundle(bundle, target, { mode: 'merge' });
    assert.equal(plan.valid, true);
    assert.equal(plan.counts.semanticResolutions, 0);
    assert.equal(target.semanticResolutionStore.forEvidenceIds(['ev-1']).length, 1);
    assert.ok(plan.warnings.some((warning) => warning.includes('one resolution per evidence')));
  } finally {
    target.close();
  }
});
