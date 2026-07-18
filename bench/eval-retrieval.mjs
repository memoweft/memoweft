/**
 * Retrieval regression runner for the repository's fully synthetic fixture.
 *
 * The default run is deterministic and offline. Pass --ablation to compare the
 * lexical-vector, FTS5 keyword, and hybrid RRF paths. Pass --real (or set
 * EVAL_REAL_ARM=1) to add model-backed embedding arms.
 *
 * Reports are written to ignored files under bench/runs/ unless --out <prefix>
 * is supplied. Node.js 24+ is required because this script imports TypeScript
 * source directly.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { HybridRetriever } from '../src/retrieval/hybridRetriever.ts';
import { KeywordRetriever } from '../src/retrieval/keywordRetriever.ts';
import { loadEmbedConfig, OpenAICompatEmbedder } from '../src/retrieval/embedder.ts';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { HashEmbedder } from '../tests/retrieval/hashEmbedder.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const GOLDEN_PATH = resolve(HERE, '../tests/retrieval/golden.json');
const RUNS_DIR = resolve(HERE, 'runs');
const TOP_K = 10;
const argv = process.argv.slice(2);
const ablation = argv.includes('--ablation');
const realRequested =
  argv.includes('--real') ||
  argv.includes('--require-real-arm') ||
  process.env.EVAL_REAL_ARM === '1';

function readFlagValue(flag) {
  const index = argv.indexOf(flag);
  if (index < 0) return null;
  const value = argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${flag} requires a value`);
  return value;
}

function percentile(values, p) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil((p / 100) * sorted.length) - 1)];
}

function aggregate(results) {
  if (results.length === 0) return { n: 0, recall5: 0, hit5: 0, mrr10: 0 };
  const mean = (field) => results.reduce((sum, row) => sum + row[field], 0) / results.length;
  return {
    n: results.length,
    recall5: mean('recall5'),
    hit5: mean('hit5'),
    mrr10: mean('rr10'),
  };
}

function grouped(results, key) {
  return Object.fromEntries(
    [...new Set(results.map((row) => row[key]))]
      .sort()
      .map((value) => [value, aggregate(results.filter((row) => row[key] === value))]),
  );
}

function signature(run) {
  return JSON.stringify(
    run.results.map(({ id, top5, recall5, hit5, rr10 }) => ({ id, top5, recall5, hit5, rr10 })),
  );
}

async function evaluateArm(definition, cognitions, cases) {
  const retriever = definition.create();
  const latencies = [];
  try {
    await retriever.indexAll(cognitions.map((item) => ({ id: item.id, text: item.content })));
    const results = [];
    for (const item of cases) {
      const started = performance.now();
      const hits = await retriever.search(item.query, TOP_K);
      latencies.push(performance.now() - started);
      const ids = hits.map((hit) => hit.id);
      const top5 = ids.slice(0, 5);
      const expected = [...new Set(item.expect)];
      const found = expected.filter((id) => top5.includes(id)).length;
      const firstIndex = ids.slice(0, TOP_K).findIndex((id) => expected.includes(id));
      results.push({
        id: item.id,
        kind: item.kind,
        lang: /\p{Script=Han}/u.test(item.query) ? 'zh' : 'en',
        query: item.query,
        expect: expected,
        top5,
        recall5: expected.length ? found / expected.length : 0,
        hit5: found > 0 ? 1 : 0,
        rr10: firstIndex >= 0 ? 1 / (firstIndex + 1) : 0,
      });
    }
    return {
      key: definition.key,
      label: definition.label,
      results,
      overall: aggregate(results),
      byKind: grouped(results, 'kind'),
      byLang: grouped(results, 'lang'),
      latencyMs: { p50: percentile(latencies, 50), p95: percentile(latencies, 95) },
    };
  } finally {
    retriever.close?.();
  }
}

const fmt = (value) => value.toFixed(4);

function metricTable(arms, select) {
  return [
    '| Arm | n | Recall@5 | Hit@5 | MRR@10 |',
    '| --- | ---: | ---: | ---: | ---: |',
    ...arms.map((arm) => {
      const metric = select(arm);
      return `| ${arm.label} | ${metric.n} | ${fmt(metric.recall5)} | ${fmt(metric.hit5)} | ${fmt(metric.mrr10)} |`;
    }),
  ];
}

function buildReport(meta, arms) {
  const lines = [
    '# Retrieval regression report',
    '',
    "This report uses the repository's fully synthetic retrieval fixture. It is a regression snapshot, not an estimate of real-world quality or a cross-system leaderboard.",
    '',
    '## Run manifest',
    '',
    '| Field | Value |',
    '| --- | --- |',
    `| Commit | \`${meta.commit}\` |`,
    `| Generated | ${meta.generatedAt} |`,
    `| Runtime | Node ${meta.node} · ${meta.platform}/${meta.arch} |`,
    `| Fixture | \`tests/retrieval/golden.json\` · ${meta.cognitionCount} cognitions · ${meta.caseCount} queries |`,
    `| Mode | ${meta.mode} |`,
    `| Command | \`${meta.command}\` |`,
    '',
    '## Overall',
    '',
    ...metricTable(arms, (arm) => arm.overall),
    '',
    'Latency is reported for local diagnostics only; it includes query embedding where applicable and is not comparable across machines or endpoints.',
    '',
    '| Arm | P50 ms | P95 ms | Determinism check |',
    '| --- | ---: | ---: | --- |',
    ...arms.map(
      (arm) =>
        `| ${arm.label} | ${arm.latencyMs.p50.toFixed(2)} | ${arm.latencyMs.p95.toFixed(2)} | ${arm.deterministic === null ? 'not asserted for model-backed embeddings' : arm.deterministic ? 'passed' : 'failed'} |`,
    ),
    '',
  ];

  for (const kind of [...new Set(arms.flatMap((arm) => Object.keys(arm.byKind)))].sort()) {
    lines.push(
      `## Query kind: ${kind}`,
      '',
      ...metricTable(arms, (arm) => arm.byKind[kind] ?? aggregate([])),
      '',
    );
  }
  lines.push('## Language split', '');
  for (const lang of ['en', 'zh']) {
    lines.push(
      `### ${lang}`,
      '',
      ...metricTable(arms, (arm) => arm.byLang[lang] ?? aggregate([])),
      '',
    );
  }
  lines.push(
    '## Interpretation limits',
    '',
    '- The fixture is small, synthetic, and hand-labeled.',
    '- Hash embeddings measure deterministic lexical behavior, not production semantic quality.',
    '- Model-backed embedding results depend on an external endpoint and are not asserted to be deterministic.',
    '- Changing the fixture, embedding model, tokenizer, fusion settings, or top-k starts a new result series.',
    '',
  );
  return lines.join('\n');
}

function armDefinitions(embedConfig) {
  const vector = (embedderFactory, key, label) => ({
    key,
    label,
    create: () => new VectorRetriever(':memory:', embedderFactory()),
  });
  const keyword = {
    key: 'keyword',
    label: 'Keyword (FTS5)',
    create: () => new KeywordRetriever(':memory:'),
  };
  const hybrid = (embedderFactory, key, label) => ({
    key,
    label,
    create: () =>
      new HybridRetriever([
        new VectorRetriever(':memory:', embedderFactory()),
        new KeywordRetriever(':memory:'),
      ]),
  });
  const definitions = [
    vector(() => new HashEmbedder(), 'hash-vector', 'Vector (deterministic hash)'),
  ];
  if (ablation)
    definitions.push(
      keyword,
      hybrid(() => new HashEmbedder(), 'hash-hybrid', 'Hybrid (hash + FTS5)'),
    );
  if (embedConfig) {
    definitions.push(
      vector(
        () => new OpenAICompatEmbedder(embedConfig),
        'model-vector',
        `Vector (${embedConfig.model})`,
      ),
    );
    if (ablation)
      definitions.push(
        hybrid(
          () => new OpenAICompatEmbedder(embedConfig),
          'model-hybrid',
          `Hybrid (${embedConfig.model} + FTS5)`,
        ),
      );
  }
  return definitions;
}

async function main() {
  const golden = JSON.parse(readFileSync(GOLDEN_PATH, 'utf8'));
  const embedConfig = realRequested ? loadEmbedConfig() : null;
  if (realRequested && !embedConfig)
    throw new Error('A model-backed arm was requested, but MEMOWEFT_EMBED_* is not configured.');

  const arms = [];
  for (const definition of armDefinitions(embedConfig)) {
    const first = await evaluateArm(definition, golden.cognitions, golden.cases);
    if (definition.key.startsWith('model-')) {
      first.deterministic = null;
    } else {
      const second = await evaluateArm(definition, golden.cognitions, golden.cases);
      first.deterministic = signature(first) === signature(second);
      if (!first.deterministic) throw new Error(`Determinism check failed for ${definition.label}`);
    }
    arms.push(first);
  }

  const generatedAt = new Date().toISOString();
  const commit = (() => {
    try {
      return execSync('git rev-parse --short HEAD').toString().trim();
    } catch {
      return 'nogit';
    }
  })();
  const meta = {
    commit,
    generatedAt,
    node: process.versions.node,
    platform: process.platform,
    arch: process.arch,
    cognitionCount: golden.cognitions.length,
    caseCount: golden.cases.length,
    mode: `${ablation ? 'ablation' : 'vector'}${embedConfig ? ' + model-backed embeddings' : ' · offline'}`,
    command: `node bench/eval-retrieval.mjs${argv.length ? ` ${argv.join(' ')}` : ''}`,
    fixtureProvenance: golden._provenance,
    embeddingModel: embedConfig?.model ?? null,
  };

  mkdirSync(RUNS_DIR, { recursive: true });
  const defaultPrefix = resolve(
    RUNS_DIR,
    `${generatedAt.slice(0, 10)}-${commit}-retrieval-${ablation ? 'ablation' : 'vector'}`,
  );
  const prefix = readFlagValue('--out') ?? defaultPrefix;
  mkdirSync(dirname(resolve(prefix)), { recursive: true });
  writeFileSync(`${prefix}.md`, `${buildReport(meta, arms)}\n`, 'utf8');
  writeFileSync(`${prefix}.json`, `${JSON.stringify({ meta, arms }, null, 2)}\n`, 'utf8');

  console.log(
    `Retrieval regression complete: ${arms.map((arm) => `${arm.label} Recall@5=${fmt(arm.overall.recall5)}`).join(' · ')}`,
  );
  console.log(`Reports: ${prefix}.md and ${prefix}.json`);
}

main().catch((error) => {
  console.error(`[eval-retrieval] ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
