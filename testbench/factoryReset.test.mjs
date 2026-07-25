import assert from 'node:assert/strict';
import test from 'node:test';
import { createMemoryManagementAPI } from '../src/memory/managementApi.ts';
import { exportBundle } from '../src/portable/index.ts';
import { openStores } from '../src/store/openStores.ts';
import { resetTestbenchSubject } from './factoryReset.mjs';
import { portableDeps } from './portableDeps.mjs';

test('testbench factory reset clears interaction and semantic data from a complete portable export', async () => {
  const stores = openStores(':memory:');
  let indexed = null;
  try {
    const subjectId = 'owner';
    const evidence = stores.evidenceStore.put({
      subjectId,
      sourceKind: 'spoken',
      hostId: 'testbench',
      rawContent: '我想部署这个项目。',
    });
    stores.interactionContextStore.record({
      subjectId,
      conversationId: 'conversation-1',
      episodeId: 'episode-1',
      context: [{ role: 'user', content: '我想部署这个项目。' }],
    });
    stores.semanticResolutionStore.put({
      evidenceId: evidence.id,
      resolvedContent: '用户表达了部署意图。',
      resolverVersion: 'test@1',
    });

    const before = exportBundle(subjectId, portableDeps(stores));
    assert.equal(before.data.interactionContexts.length, 1);
    assert.equal(before.data.semanticResolutions.length, 1);

    const counts = await resetTestbenchSubject({
      memoryApi: createMemoryManagementAPI(stores),
      retriever: {
        async indexAll(items) {
          indexed = items;
        },
      },
      subjectId,
    });

    const after = exportBundle(subjectId, portableDeps(stores));
    assert.equal(counts.evidenceRemoved, 1);
    assert.deepEqual(indexed, []);
    assert.deepEqual(after.data.evidence, []);
    assert.deepEqual(after.data.interactionContexts, []);
    assert.deepEqual(after.data.semanticResolutions, []);
  } finally {
    stores.close();
  }
});
