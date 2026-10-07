import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ObservedEvidenceService } from '../src/evidence/observed.ts';
import type { ObservedEvidenceV1, ObservedPermissionsV1 } from '../src/evidence/observed.ts';
import { openStores } from '../src/store/openStores.ts';
import { config } from '../src/config.ts';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { exportBundle, importBundle } from '../src/portable/index.ts';
import { recallCognitions } from '../src/retrieval/recall.ts';
import type { ModelTier } from '../src/llm/client.ts';

const fixture = JSON.parse(
  readFileSync(new URL('../shared/parity/observed.json', import.meta.url), 'utf8'),
);
const clock = () => new Date('2026-10-07T00:00:00Z');

test('observed lifecycle matches shared Python parity including consent replay, withdrawal and restart', async () => {
  const root = mkdtempSync(join(tmpdir(), 'mw-observed-'));
  const file = join(root, 'memory.db');
  let stores = openStores(file);
  let service = new ObservedEvidenceService(stores, fixture.subject, fixture.host, clock);
  try {
    for (const [index, step] of fixture.steps.entries()) {
      const evidence = {
        source_key: fixture.source_key,
        ...fixture.evidence,
        ...(step.version ? { version: step.version } : {}),
        ...(step.content ? { content: step.content } : {}),
      };
      const params =
        step.operation === 'upsert_observed'
          ? { evidence }
          : step.operation === 'retract_observed'
            ? { source_key: fixture.source_key, withdrawn_through: step.version }
            : {
                source_key: fixture.source_key,
                permission_version: step.version,
                permissions: {
                  allow_local_read: step.local_read ?? true,
                  allow_cloud_read: step.cloud_read,
                  allow_inference: step.inference ?? true,
                },
              };
      if (step.error)
        assert.throws(() => service.execute(step.operation, params), { message: step.error });
      else
        assert.equal(
          service.execute(step.operation, params).result_state,
          step.state,
          `step ${index}`,
        );
      for (const tier of ['local', 'cloud'] as ModelTier[]) {
        const visible = stores.cognitionStore
          .all(fixture.subject)
          .some((c) => service.readable(c.id, tier));
        assert.equal(visible, step[tier], `${index}: ${tier}`);
      }
      stores.close();
      stores = openStores(file);
      service = new ObservedEvidenceService(stores, fixture.subject, fixture.host, clock);
    }
    const serialized = JSON.stringify(exportBundle(fixture.subject, stores));
    assert.doesNotMatch(serialized, /睡眠|HRV/);
    assert.doesNotMatch(readFileSync(file).toString(), /睡眠|HRV/);
  } finally {
    stores.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test('Core filters mixed provenance, preserves other memory, removes indexes and rejects old Portable restore', async () => {
  const stores = openStores(':memory:');
  const service = new ObservedEvidenceService(stores, fixture.subject, fixture.host, clock);
  try {
    const receipt = service.execute('upsert_observed', {
      evidence: { source_key: fixture.source_key, ...fixture.evidence },
    });
    const publicEvidence = stores.evidenceStore.put({
      subjectId: fixture.subject,
      hostId: fixture.host,
      sourceKind: 'spoken',
      rawContent: '睡眠前喜欢阅读',
      allowCloudRead: true,
    });
    const other = stores.cognitionStore.put({
      subjectId: fixture.subject,
      content: '睡眠前喜欢阅读',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 800,
      credStatus: 'stable',
      evidence: [{ evidenceId: publicEvidence.id, relation: 'support' }],
    });
    const mixed = stores.cognitionStore.put({
      subjectId: fixture.subject,
      content: '睡眠与阅读有关',
      contentType: 'hypothesis',
      formedBy: 'inferred',
      confidence: 500,
      credStatus: 'limited',
      evidence: [
        { evidenceId: receipt.evidence_id!, relation: 'support' },
        { evidenceId: publicEvidence.id, relation: 'support' },
      ],
    });
    assert.equal(service.readable(mixed.id, 'cloud'), false);
    assert.equal(service.readable(other.id, 'cloud'), true);
    const backup = exportBundle(fixture.subject, stores);
    stores.db.exec('CREATE VIRTUAL TABLE cognition_fts USING fts5(cognition_id UNINDEXED,text)');
    stores.db.prepare('INSERT INTO cognition_fts VALUES (?,?)').run(mixed.id, mixed.content);
    service.execute('retract_observed', {
      source_key: fixture.source_key,
      withdrawn_through: '2026-10-02T00:00:00Z',
    });
    assert.equal(stores.db.prepare('SELECT * FROM cognition_fts').all().length, 0);
    assert.equal(stores.cognitionStore.get(mixed.id), null);
    assert.ok(stores.cognitionStore.get(other.id));
    importBundle(backup, stores, { mode: 'merge' });
    assert.doesNotMatch(JSON.stringify(exportBundle(fixture.subject, stores)), /HRV|5 小时/);
    const recalled = await recallCognitions('睡眠', fixture.subject, {
      cognitionStore: stores.cognitionStore,
      retriever: {
        indexAll: async () => {},
        search: async () => [
          { id: mixed.id, score: 1 },
          { id: other.id, score: 1 },
        ],
      },
    });
    assert.deepEqual(
      recalled.map((c) => c.id),
      [other.id],
    );
  } finally {
    stores.close();
  }
});

test('public typed Core observed API recalls exact facts without calling any model or embedding', async () => {
  const cfg = structuredClone(config);
  cfg.identity.subjectId = fixture.subject;
  cfg.identity.hostId = fixture.host;
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    config: cfg,
    clock,
    embedder: {
      embed: async () => {
        throw new Error('must never embed observation');
      },
    },
  });
  try {
    const e: ObservedEvidenceV1 = { source_key: fixture.source_key, ...fixture.evidence };
    await core.observed.upsert(e);
    assert.equal((await core.recall({ query: '睡眠', modelTier: 'local' })).length, 1);
    assert.equal((await core.recall({ query: '睡眠', modelTier: 'cloud' })).length, 0);
    const permissions: ObservedPermissionsV1 = { ...e.permissions, allow_cloud_read: true };
    await core.observed.updatePermissions({
      source_key: e.source_key,
      permission_version: '2026-10-02T00:00:00Z',
      permissions,
    });
    assert.equal((await core.recall({ query: '睡眠', modelTier: 'cloud' })).length, 1);
    await core.observed.retract({
      source_key: e.source_key,
      withdrawn_through: '2026-10-03T00:00:00Z',
    });
    assert.equal((await core.recall({ query: '睡眠', modelTier: 'local' })).length, 0);
  } finally {
    core.close();
  }
});

test('index cleanup retry persists across restart and erases a separate vector database', async () => {
  const root = mkdtempSync(join(tmpdir(), 'mw-observed-index-'));
  const cfg = structuredClone(config);
  cfg.identity.subjectId = fixture.subject;
  cfg.identity.hostId = fixture.host;
  const file = join(root, 'core.db');
  let failed = true;
  const erased: string[] = [];
  const retriever = {
    indexAll: async () => {},
    search: async () => [],
    remove: async (ids: string[]) => {
      if (failed) throw new Error('test index unavailable');
      erased.push(...ids);
    },
  };
  let core = createMemoWeftCore({ dbPath: file, config: cfg, clock, retriever });
  try {
    const receipt = await core.observed.upsert({
      source_key: fixture.source_key,
      ...fixture.evidence,
    });
    const input = { source_key: fixture.source_key, withdrawn_through: '2026-10-03T00:00:00Z' };
    assert.equal((await core.observed.retract(input)).storage_cleanup?.state, 'pending');
    core.close();
    failed = false;
    core = createMemoWeftCore({ dbPath: file, config: cfg, clock, retriever });
    assert.equal((await core.observed.retract(input)).storage_cleanup?.state, 'complete');
    assert.deepEqual(erased, ['state:' + receipt.evidence_id]);
    assert.doesNotMatch(JSON.stringify(core.portable.exportBundle()), /HRV/);
  } finally {
    core.close();
    rmSync(root, { recursive: true, force: true });
  }
});
