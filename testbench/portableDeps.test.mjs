import assert from 'node:assert/strict';
import test from 'node:test';
import { exportBundle, importBundle } from '../src/portable/index.ts';
import { openStores } from '../src/store/openStores.ts';
import { portableDeps } from './portableDeps.mjs';

test('testbench portable dependency wiring includes v0.6 interaction stores for empty export and dry-run import', () => {
  const stores = openStores(':memory:');
  try {
    const deps = portableDeps(stores);
    assert.equal(deps.interactionContextStore, stores.interactionContextStore);
    assert.equal(deps.semanticResolutionStore, stores.semanticResolutionStore);
    assert.equal(deps.transaction, stores.transaction);

    const bundle = exportBundle('owner', deps, { now: '2026-07-19T00:00:00.000Z' });
    assert.deepEqual(bundle.data.interactionContexts, []);
    assert.deepEqual(bundle.data.semanticResolutions, []);

    const plan = importBundle(bundle, deps, { mode: 'dryRun' });
    assert.equal(plan.valid, true);
    assert.equal(plan.counts.evidence, 0);
    assert.equal(plan.counts.interactionContexts, 0);
    assert.equal(plan.counts.semanticResolutions, 0);
  } finally {
    stores.close();
  }
});
