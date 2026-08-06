import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeConsolidationShards } from '../scripts/merge-consolidation-shards.mjs';

const corpus = {
  _meta: { corpusVersion: 'test-v1' },
  scenarios: [
    { id: 'A-1', discipline: 'alpha' },
    { id: 'B-1', discipline: 'beta' },
  ],
};

function shard(discipline, id, overrides = {}) {
  const summary = {
    id,
    discipline,
    error: null,
    structPass: 1,
    structTotal: 1,
    gistRecall: 1,
    overInferRate: 0,
  };
  return {
    meta: {
      commit: 'abc123',
      model: 'subject-model',
      judgeModel: 'judge-model',
      judgePromptVersion: 'judge-v1',
      gistScoringVersion: 'gist-v2',
      promptVersions: { consolidate: 'v1' },
      subjectEnv: null,
      scenarioCount: 1,
      totalScenarios: 2,
      judgeCalls: 3,
      partial: true,
      filter: { limit: null, discipline },
      ...overrides.meta,
    },
    agg: { errored: 0, ...overrides.agg },
    summaries: overrides.summaries ?? [summary],
  };
}

test('merges exact discipline shards into one full-corpus aggregate', () => {
  const merged = mergeConsolidationShards(corpus, [shard('alpha', 'A-1'), shard('beta', 'B-1')]);
  assert.equal(merged.meta.partial, false);
  assert.equal(merged.meta.scenarioCount, 2);
  assert.equal(merged.meta.corpusVersion, 'test-v1');
  assert.deepEqual(
    merged.summaries.map((summary) => summary.id),
    ['A-1', 'B-1'],
  );
  assert.equal(merged.agg.errored, 0);
});

test('rejects a missing discipline shard', () => {
  assert.throws(
    () => mergeConsolidationShards(corpus, [shard('alpha', 'A-1')]),
    /Discipline coverage mismatch/,
  );
});

test('rejects shards produced from different commits', () => {
  assert.throws(
    () =>
      mergeConsolidationShards(corpus, [
        shard('alpha', 'A-1'),
        shard('beta', 'B-1', { meta: { commit: 'different' } }),
      ]),
    /metadata mismatch for commit/i,
  );
});

test('rejects a shard whose aggregate hides a scenario error', () => {
  const failedSummary = {
    id: 'B-1',
    discipline: 'beta',
    error: 'judge failed',
    structPass: 1,
    structTotal: 1,
    gistRecall: null,
    overInferRate: null,
  };
  assert.throws(
    () =>
      mergeConsolidationShards(corpus, [
        shard('alpha', 'A-1'),
        shard('beta', 'B-1', { summaries: [failedSummary] }),
      ]),
    /errored count mismatch/,
  );
});
