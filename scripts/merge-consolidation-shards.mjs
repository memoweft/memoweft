#!/usr/bin/env node
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, extname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { aggregate, buildReport, commitSummarySingle } from '../bench/eval-consolidation.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const CORPUS_PATH = resolve(ROOT, 'tests/consolidation-corpus/corpus.json');

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, child]) => [key, canonical(child)]),
    );
  }
  return value;
}

const stableJson = (value) => JSON.stringify(canonical(value));

function assertSameMeta(runs, field) {
  const expected = stableJson(runs[0]?.meta?.[field] ?? null);
  for (const run of runs.slice(1)) {
    const actual = stableJson(run?.meta?.[field] ?? null);
    if (actual !== expected) {
      throw new Error(`Shard metadata mismatch for ${field}: ${expected} !== ${actual}`);
    }
  }
}

function exactSet(label, expected, actual) {
  const want = [...expected].sort();
  const got = [...actual].sort();
  if (stableJson(want) !== stableJson(got)) {
    throw new Error(`${label} mismatch: expected ${want.join(', ')}, got ${got.join(', ')}`);
  }
}

/**
 * Combines one independently executed run per discipline into the same full-corpus
 * aggregate that a serial run would produce. It rejects missing, duplicate, stale,
 * or differently configured shards before reporting success.
 */
export function mergeConsolidationShards(corpus, runs) {
  if (!Array.isArray(corpus?.scenarios) || corpus.scenarios.length === 0) {
    throw new Error('Corpus must contain at least one scenario.');
  }
  if (!Array.isArray(runs) || runs.length === 0) {
    throw new Error('No consolidation shard runs were provided.');
  }

  const expectedByDiscipline = new Map();
  for (const scenario of corpus.scenarios) {
    const ids = expectedByDiscipline.get(scenario.discipline) ?? [];
    ids.push(scenario.id);
    expectedByDiscipline.set(scenario.discipline, ids);
  }

  const runByDiscipline = new Map();
  for (const run of runs) {
    const discipline = run?.meta?.filter?.discipline;
    if (!discipline || typeof discipline !== 'string') {
      throw new Error('Every shard must identify meta.filter.discipline.');
    }
    if (runByDiscipline.has(discipline)) {
      throw new Error(`Duplicate consolidation shard for discipline: ${discipline}`);
    }
    if (!Array.isArray(run.summaries)) {
      throw new Error(`Shard ${discipline} has no summaries array.`);
    }
    if (run.meta?.partial !== true) {
      throw new Error(`Shard ${discipline} is not marked as a partial run.`);
    }
    if (run.meta?.scenarioCount !== run.summaries.length) {
      throw new Error(
        `Shard ${discipline} scenarioCount=${run.meta?.scenarioCount} but has ${run.summaries.length} summaries.`,
      );
    }
    if (run.meta?.totalScenarios !== corpus.scenarios.length) {
      throw new Error(
        `Shard ${discipline} targets ${run.meta?.totalScenarios} total scenarios; corpus has ${corpus.scenarios.length}.`,
      );
    }
    const computedErrors = run.summaries.filter((summary) => summary.error).length;
    if (run.agg?.errored !== computedErrors) {
      throw new Error(
        `Shard ${discipline} errored count mismatch: aggregate=${run.agg?.errored}, summaries=${computedErrors}.`,
      );
    }
    runByDiscipline.set(discipline, run);
  }

  exactSet('Discipline coverage', expectedByDiscipline.keys(), runByDiscipline.keys());
  for (const field of [
    'commit',
    'model',
    'judgeModel',
    'judgePromptVersion',
    'gistScoringVersion',
    'promptVersions',
    'subjectEnv',
  ]) {
    assertSameMeta(runs, field);
  }

  const summaryById = new Map();
  for (const [discipline, expectedIds] of expectedByDiscipline) {
    const run = runByDiscipline.get(discipline);
    exactSet(
      `Scenario coverage for ${discipline}`,
      expectedIds,
      run.summaries.map((summary) => summary.id),
    );
    for (const summary of run.summaries) {
      if (summary.discipline !== discipline) {
        throw new Error(
          `Scenario ${summary.id} reports discipline ${summary.discipline}; expected ${discipline}.`,
        );
      }
      if (summaryById.has(summary.id)) throw new Error(`Duplicate scenario summary: ${summary.id}`);
      summaryById.set(summary.id, summary);
    }
  }

  const summaries = corpus.scenarios.map((scenario) => summaryById.get(scenario.id));
  if (summaries.some((summary) => !summary)) {
    throw new Error('At least one corpus scenario has no summary after shard merge.');
  }

  const agg = aggregate(summaries);
  const firstMeta = runs[0].meta;
  const meta = {
    ...firstMeta,
    generatedAt: new Date().toISOString(),
    scenarioCount: summaries.length,
    totalScenarios: corpus.scenarios.length,
    judgeCalls: runs.reduce((sum, run) => sum + (run.meta?.judgeCalls ?? 0), 0),
    partial: false,
    filter: { limit: null, discipline: null },
    corpusVersion: corpus._meta?.corpusVersion ?? null,
    shardCount: runs.length,
    shardDisciplines: [...runByDiscipline.keys()].sort(),
  };
  return { meta, agg, summaries };
}

function walkJsonFiles(root) {
  const files = [];
  const visit = (entry) => {
    if (statSync(entry).isDirectory()) {
      for (const child of readdirSync(entry)) visit(resolve(entry, child));
    } else if (extname(entry).toLowerCase() === '.json') {
      files.push(entry);
    }
  };
  visit(root);
  return files.sort();
}

function option(args, name) {
  const index = args.indexOf(name);
  if (index < 0 || !args[index + 1] || args[index + 1].startsWith('--')) {
    throw new Error(`${name} requires a value.`);
  }
  return args[index + 1];
}

function main() {
  const args = process.argv.slice(2);
  const inputDir = resolve(option(args, '--input'));
  const outPrefix = resolve(option(args, '--out'));
  if (!existsSync(inputDir)) throw new Error(`Shard input directory does not exist: ${inputDir}`);

  const corpus = JSON.parse(readFileSync(CORPUS_PATH, 'utf8'));
  const files = walkJsonFiles(inputDir);
  const runs = files.map((path) => JSON.parse(readFileSync(path, 'utf8')));
  const merged = mergeConsolidationShards(corpus, runs);

  mkdirSync(dirname(outPrefix), { recursive: true });
  writeFileSync(`${outPrefix}.json`, JSON.stringify(merged, null, 2), 'utf8');
  writeFileSync(`${outPrefix}.md`, buildReport(merged.summaries, merged.agg, merged.meta), 'utf8');
  console.log(
    `[merge-consolidation-shards] merged ${merged.meta.shardCount} shards and ${merged.meta.scenarioCount}/${merged.meta.totalScenarios} scenarios`,
  );
  console.log(commitSummarySingle(merged.agg, merged.meta));
  console.log(`[merge-consolidation-shards] wrote ${outPrefix}.json and ${outPrefix}.md`);

  if (merged.agg.errored > 0) {
    console.error(`[merge-consolidation-shards] crash gate failed: errored=${merged.agg.errored}`);
    process.exit(1);
  }
  console.log('[merge-consolidation-shards] full-corpus crash gate passed: errored=0');
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try {
    main();
  } catch (error) {
    console.error(
      `[merge-consolidation-shards] failed: ${error instanceof Error ? error.message : String(error)}`,
    );
    process.exit(1);
  }
}
