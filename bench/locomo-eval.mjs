/**
 * LoCoMo-10 evaluation runner. Manual and model-backed runs are not CI quality gates.
 *
 * LoCoMo (Maharana et al., ACL 2024, arXiv:2402.17753) evaluates memory over long multi-session conversations.
 * This limited evaluation exercises the conversation → retrieval → answer path and makes its retrieval granularity explicit:
 *   - The default path retrieves from the **evidence layer** (listEvidence + keyword-overlap top-k), without updateProfile or an embedder.
 *     This preserves episodic details that may not appear in profile-level cognitions.
 *   - A configured answer model synthesizes answers from retrieved evidence; the Core library itself does not answer questions.
 *   - Answers are scored with normalized token-overlap F1 and grouped by category.
 *
 * LoCoMo is licensed CC BY-NC 4.0. Use LOCOMO_PATH to point at a local copy;
 * dataset rows are not written to reports or included in the package.
 *
 * Imports TypeScript source directly and requires Node.js 24+.
 *
 * Usage (`LOCOMO_PATH=/path/to/locomo10.json node bench/locomo-eval.mjs …`):
 *   --dry                                # loader and evidence-retrieval metrics without an answer model
 *   --limit 1 --qa 5                     # run 1 sample / 5 QA with a configured answer model (evidence-layer keyword retrieval)
 *   --retriever semantic --limit 1       # evidence-layer semantic retrieval
 *   --layer cognition --limit 1 --qa 5   # updateProfile + core.recall; requires a model
 *   --no-dates …                         # omit session dates
 *   --matrix --limit 1                   # retrieval matrix; embedding endpoint required
 *   （无 flag）                           # full run (slow; requires a configured answer model)
 *
 * Category 5 adversarial questions are excluded. Session dates are included by
 * default and can be disabled with `--no-dates`.
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { OpenAICompatClient } from '../src/llm/client.ts';
import { loadEmbedConfig, OpenAICompatEmbedder } from '../src/retrieval/embedder.ts';
// Retrieval-matrix implementations and the deterministic HashEmbedder fixture.
import { KeywordRetriever } from '../src/retrieval/keywordRetriever.ts';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { HybridRetriever } from '../src/retrieval/hybridRetriever.ts';
import { HashEmbedder } from '../tests/retrieval/hashEmbedder.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUNS_DIR = resolve(HERE, 'runs');
const LOCOMO_PATH = process.env.LOCOMO_PATH || resolve(HERE, 'data/locomo10.json');
const TOP_K = 15;

// LoCoMo category labels; category 5 is excluded.
const CAT_NAME = {
  1: 'multi-hop',
  2: 'temporal',
  3: 'open-domain',
  4: 'single-hop',
  5: 'adversarial',
};

// ── CLI ─────────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
const DRY = argv.includes('--dry');
const getNum = (flag, def) => {
  const i = argv.indexOf(flag);
  return i >= 0 && argv[i + 1] ? Number(argv[i + 1]) : def;
};
const LIMIT = getNum('--limit', Infinity);
const QA_LIMIT = getNum('--qa', Infinity);
const RETRIEVER = (() => {
  const i = argv.indexOf('--retriever');
  return i >= 0 && argv[i + 1] ? argv[i + 1] : 'keyword';
})(); // keyword | semantic
const MAXTURNS = getNum('--max-turns', Infinity); // Limits replay length; use the same value when comparing retrievers.
const LAYER = (() => {
  const i = argv.indexOf('--layer');
  return i >= 0 && argv[i + 1] ? argv[i + 1] : 'evidence';
})(); // evidence | cognition
const DATES = !argv.includes('--no-dates');
const MATRIX = argv.includes('--matrix');
const OFFSET = getNum('--offset', 0);
const MERGE_MATRIX = argv.includes('--merge-matrix');

// Normalized token-overlap F1
const STOP = new Set([
  'a',
  'an',
  'the',
  'of',
  'to',
  'in',
  'on',
  'at',
  'is',
  'was',
  'were',
  'and',
  'or',
  'for',
]);
function tokenize(s) {
  return String(s)
    .toLowerCase()
    .replace(/[^\w\s]/g, ' ')
    .split(/\s+/)
    .filter((w) => w && !STOP.has(w));
}
function f1(pred, gold) {
  const p = tokenize(pred),
    g = tokenize(gold);
  if (!p.length || !g.length) return p.length === g.length ? 1 : 0;
  const gc = new Map();
  g.forEach((w) => gc.set(w, (gc.get(w) || 0) + 1));
  let common = 0;
  for (const w of p)
    if (gc.get(w) > 0) {
      common++;
      gc.set(w, gc.get(w) - 1);
    }
  if (!common) return 0;
  const prec = common / p.length,
    rec = common / g.length;
  return (2 * prec * rec) / (prec + rec);
}

// Parses dates such as "1:56 pm on 8 May, 2023" for occurredAt.
function parseLocomoDate(raw) {
  if (!raw) return null;
  const m = String(raw).match(/(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})/);
  if (!m) return null;
  const d = new Date(`${m[1]} ${m[2]} ${m[3]} UTC`);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

// Sample loader
function loadSample(s) {
  const conv = s.conversation || {};
  const turns = [];
  const sessKeys = Object.keys(conv)
    .filter((k) => /^session_\d+$/.test(k))
    .sort((a, b) => Number(a.split('_')[1]) - Number(b.split('_')[1]));
  for (const k of sessKeys) {
    const date = conv[`${k}_date_time`] || '';
    for (const t of conv[k])
      turns.push({ speaker: t.speaker, diaId: t.dia_id, text: t.text, date });
  }
  const qa = (s.qa || [])
    .filter((q) => q.category !== 5)
    .map((q) => ({
      question: q.question,
      answer: String(q.answer ?? ''),
      evidence: q.evidence || [],
      category: q.category,
    }));
  return { id: s.sample_id, turns, qa };
}

// Evidence-layer keyword-overlap retrieval
function retrieveTopK(evidences, question, k) {
  const qt = new Set(tokenize(question));
  const scored = evidences.map((e) => {
    const et = tokenize(e.rawContent);
    let hit = 0;
    for (const w of et) if (qt.has(w)) hit++;
    return { e, score: hit };
  });
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, k).map((x) => x.e);
}

// Embed in bounded batches and compute cosine similarity locally.
async function embedAll(embedder, texts, batch = 1) {
  const out = [];
  for (let i = 0; i < texts.length; i += batch)
    out.push(...(await embedder.embed(texts.slice(i, i + batch))));
  return out;
}
function cosine(a, b) {
  let d = 0,
    na = 0,
    nb = 0;
  for (let i = 0; i < a.length; i++) {
    d += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  return d / (Math.sqrt(na) * Math.sqrt(nb) + 1e-9);
}

// Configured answer model
async function answer(llm, excerpts, question) {
  const ctx = excerpts.map((e) => e.rawContent).join('\n');
  const messages = [
    {
      role: 'system',
      content:
        'You answer a question using ONLY the conversation excerpts. Reply with a short phrase or a few words — no full sentences, no explanation. If the excerpts do not contain the answer, reply exactly: No information available.',
    },
    { role: 'user', content: `Conversation excerpts:\n${ctx}\n\nQuestion: ${question}\nAnswer:` },
  ];
  const out = await llm.chat(messages);
  return String(out).trim();
}

// Sample runner
async function runSample(sample, llm) {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  const subjectId = sample.id;
  for (const t of sample.turns.slice(0, MAXTURNS)) {
    // Preserve speaker labels and optionally include session dates.
    const content =
      DATES && t.date ? `[${t.date}] ${t.speaker}: ${t.text}` : `${t.speaker}: ${t.text}`;
    const occurredAt = DATES ? parseLocomoDate(t.date) : null;
    await core.ingestUserMessage({
      subjectId,
      content,
      originId: t.diaId,
      occurredAt: occurredAt ?? undefined,
    });
  }
  const allEv = core.memory.listEvidence({ subjectId });

  // Mutually exclusive retrieval modes: cognition, semantic evidence, or
  // keyword evidence.
  let sem = null,
    cog = null;
  if (LAYER === 'cognition') {
    if (DRY) {
      console.error('--layer cognition 需 LLM(updateProfile 消化证据),不能 --dry');
      process.exit(1);
    }
    await core.updateProfile({ subjectId });
    // Map recalled cognition provenance back to LoCoMo dialogue identifiers.
    const evById = new Map(allEv.map((e) => [e.id, e.originId]));
    const cogSources = new Map(
      core.memory.listCognitions({ subjectId }).map((c) => [c.id, c.sources || []]),
    );
    cog = { evById, cogSources };
  } else if (RETRIEVER === 'semantic') {
    const cfg = loadEmbedConfig();
    if (!cfg) {
      console.error('--retriever semantic 需配 embedder（MEMOWEFT_EMBED_* / DLA_EMBED_*）');
      process.exit(1);
    }
    const embedder = new OpenAICompatEmbedder(cfg);
    const evVecs = await embedAll(
      embedder,
      allEv.map((e) => e.rawContent),
    );
    sem = { embedder, evVecs };
  }

  const rows = [];
  const qaList = sample.qa.slice(0, QA_LIMIT);
  for (const q of qaList) {
    // A retrieval hit requires a gold dialogue identifier in the top-k result.
    let top, evHit;
    if (cog) {
      const recalled = await core.recall({ subjectId, query: q.question });
      top = recalled.map((r) => ({ rawContent: r.content }));
      const hitDia = new Set();
      for (const r of recalled)
        for (const s of cog.cogSources.get(r.id) || []) {
          const oid = cog.evById.get(s.evidenceId);
          if (oid) hitDia.add(oid);
        }
      evHit = q.evidence.length ? q.evidence.some((id) => hitDia.has(id)) : null;
    } else if (sem) {
      const [qv] = await sem.embedder.embed([q.question]);
      const scored = allEv.map((e, i) => ({ e, s: cosine(qv, sem.evVecs[i]) }));
      scored.sort((a, b) => b.s - a.s);
      top = scored.slice(0, TOP_K).map((x) => x.e);
      const topIds = new Set(top.map((e) => e.originId));
      evHit = q.evidence.length ? q.evidence.some((id) => topIds.has(id)) : null;
    } else {
      top = retrieveTopK(allEv, q.question, TOP_K);
      const topIds = new Set(top.map((e) => e.originId));
      evHit = q.evidence.length ? q.evidence.some((id) => topIds.has(id)) : null;
    }
    let pred, score;
    if (DRY) {
      pred = '(dry)';
      score = null;
    } else {
      pred = await answer(llm, top, q.question);
      score = f1(pred, q.answer);
    }
    rows.push({
      category: q.category,
      question: q.question,
      gold: q.answer,
      pred,
      f1: score,
      evHit,
    });
  }
  // Capture Core-side model and embedding usage for cognition-layer runs.
  const coreUsage = core.usage();
  core.close();
  return { id: sample.id, evidenceCount: allEv.length, rows, coreUsage };
}

// Aggregation
function summarize(sampleResults) {
  const all = sampleResults.flatMap((s) => s.rows);
  const byCat = {};
  for (const r of all) {
    const c = r.category;
    byCat[c] ??= { n: 0, f1: 0, evHitN: 0, evHitYes: 0 };
    byCat[c].n++;
    if (r.f1 != null) byCat[c].f1 += r.f1;
    if (r.evHit != null) {
      byCat[c].evHitN++;
      if (r.evHit) byCat[c].evHitYes++;
    }
  }
  return { total: all.length, byCat };
}

// Evidence-layer retrieval matrix. Hybrid arms reuse vector and keyword
// instances; matrix runs measure retrieval hits without an answer model.
async function runMatrix(sample) {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  const subjectId = sample.id;
  for (const t of sample.turns.slice(0, MAXTURNS)) {
    const content =
      DATES && t.date ? `[${t.date}] ${t.speaker}: ${t.text}` : `${t.speaker}: ${t.text}`;
    const occurredAt = DATES ? parseLocomoDate(t.date) : null;
    await core.ingestUserMessage({
      subjectId,
      content,
      originId: t.diaId,
      occurredAt: occurredAt ?? undefined,
    });
  }
  const allEv = core.memory.listEvidence({ subjectId });
  const items = allEv.map((e) => ({ id: e.originId, text: e.rawContent }));

  const cfg = loadEmbedConfig();
  if (!cfg) {
    console.error('--matrix 需 embedder（MEMOWEFT_EMBED_* / DLA_EMBED_*）');
    process.exit(1);
  }
  const bge = new OpenAICompatEmbedder(cfg);
  const kw = new KeywordRetriever(':memory:');
  const vHash = new VectorRetriever(':memory:', new HashEmbedder());
  const vBge = new VectorRetriever(':memory:', bge);
  await kw.indexAll(items);
  await vHash.indexAll(items);
  await vBge.indexAll(items);
  const arms = {
    keyword: kw,
    'vector-hash': vHash,
    'vector-bge': vBge,
    'hybrid-hash': new HybridRetriever([vHash, kw]),
    'hybrid-bge': new HybridRetriever([vBge, kw]),
  };
  const armNames = Object.keys(arms);

  const rows = [];
  for (const q of sample.qa.slice(0, QA_LIMIT)) {
    if (!q.evidence.length) continue;
    const perArm = {};
    for (const name of armNames) {
      const hits = await arms[name].search(q.question, TOP_K);
      const ids = new Set(hits.map((h) => h.id));
      perArm[name] = q.evidence.some((id) => ids.has(id));
    }
    rows.push({ category: q.category, perArm });
  }
  const embedUsage = bge.usage;
  kw.close();
  vHash.close();
  vBge.close();
  core.close();
  return { id: sample.id, evidenceCount: allEv.length, rows, armNames, embedUsage };
}

function summarizeMatrix(sampleResults) {
  const armNames = sampleResults.find((s) => s.armNames)?.armNames || [];
  const all = sampleResults.flatMap((s) => s.rows);
  const byCat = {};
  for (const r of all) {
    byCat[r.category] ??= { n: 0, hits: Object.fromEntries(armNames.map((a) => [a, 0])) };
    byCat[r.category].n++;
    for (const a of armNames) if (r.perArm[a]) byCat[r.category].hits[a]++;
  }
  return { total: all.length, armNames, byCat };
}

// CLI
async function main() {
  // Merge per-sample matrix reports without loading the dataset.
  if (MERGE_MATRIX) {
    const commit = (() => {
      try {
        return execSync('git rev-parse --short HEAD').toString().trim();
      } catch {
        return 'nogit';
      }
    })();
    const files = readdirSync(RUNS_DIR)
      .filter((f) => new RegExp(`-${commit}-locomo-matrix-s\\d+n1\\.json$`).test(f))
      .sort();
    if (!files.length) {
      console.error(`无匹配矩阵分片（先跑 per-sample --matrix;commit ${commit}）`);
      process.exit(1);
    }
    let armNames = null;
    const agg = {};
    let embTot = 0,
      embCalls = 0;
    const ids = [];
    for (const f of files) {
      const j = JSON.parse(readFileSync(resolve(RUNS_DIR, f), 'utf8'));
      armNames ??= j.armNames;
      embTot += j.embedTokens || 0;
      embCalls += j.embedCalls || 0;
      for (const s of j.samples) ids.push(s.id + (s.failed ? '(FAIL)' : ''));
      for (const [c, b] of Object.entries(j.byCat)) {
        agg[c] ??= { n: 0, hits: Object.fromEntries(armNames.map((a) => [a, 0])) };
        agg[c].n += b.n;
        for (const a of armNames) agg[c].hits[a] += b.hits[a];
      }
    }
    const cols = armNames;
    const lines = [
      `# LoCoMo retrieval matrix (Recall@${TOP_K}, dry, ${files.length} merged samples)`,
      '',
    ];
    lines.push(`- commit: \`${commit}\` · samples: ${ids.length} (${ids.join(', ')})`);
    lines.push(
      `- 臂:vector / keyword / hybrid × HashEmbedder(确定性) / bge-m3(真实);evidence 层;会话日期${DATES ? '已注入' : '未注入'}`,
    );
    lines.push(
      `- bge-m3 embed tokens: ${embTot} (calls: ${embCalls}) · merged from per-sample reports`,
    );
    lines.push(
      '',
      `| category | n | ${cols.join(' | ')} |`,
      `|---|---|${cols.map(() => '---').join('|')}|`,
    );
    const overall = { n: 0, hits: Object.fromEntries(cols.map((a) => [a, 0])) };
    for (const c of Object.keys(agg).sort()) {
      const b = agg[c];
      overall.n += b.n;
      for (const a of cols) overall.hits[a] += b.hits[a];
      lines.push(
        `| ${c} ${CAT_NAME[c] || ''} | ${b.n} | ${cols.map((a) => ((b.hits[a] / b.n) * 100).toFixed(1) + '%').join(' | ')} |`,
      );
    }
    lines.push(
      `| **overall** | ${overall.n} | ${cols.map((a) => `**${((overall.hits[a] / overall.n) * 100).toFixed(1)}%**`).join(' | ')} |`,
    );
    const report = lines.join('\n') + '\n';
    console.log('\n' + report);
    if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
    const date = new Date().toISOString().slice(0, 10);
    writeFileSync(resolve(RUNS_DIR, `${date}-${commit}-locomo-matrix-merged.md`), report);
    console.log('written:', resolve(RUNS_DIR, `${date}-${commit}-locomo-matrix-merged.md`));
    return;
  }
  if (!existsSync(LOCOMO_PATH)) {
    console.error(
      `LoCoMo dataset not found: ${LOCOMO_PATH}\nSet LOCOMO_PATH to a local copy obtained under the upstream CC BY-NC 4.0 license.`,
    );
    process.exit(1);
  }
  const data = JSON.parse(readFileSync(LOCOMO_PATH, 'utf8'));
  const samples = data.slice(OFFSET, OFFSET + LIMIT).map(loadSample);
  const commitOf = () => {
    try {
      return execSync('git rev-parse --short HEAD').toString().trim();
    } catch {
      return 'nogit';
    }
  };

  // Retrieval matrix: no answer-model calls.
  if (MATRIX) {
    const mResults = [];
    for (const s of samples) {
      process.stderr.write(
        `  [matrix] sample ${s.id}: ${s.turns.length} turns, ${Math.min(s.qa.length, QA_LIMIT)} QA…\n`,
      );
      try {
        mResults.push(await runMatrix(s));
      } catch (e) {
        process.stderr.write(`  [matrix] sample ${s.id} FAILED (excluded): ${e?.message || e}\n`);
        mResults.push({ id: s.id, failed: true, rows: [], armNames: null });
      }
    }
    const msum = summarizeMatrix(mResults);
    const commit = commitOf();
    const embTot = mResults.reduce((a, r) => a + (r.embedUsage?.totalTokens || 0), 0);
    const embCalls = mResults.reduce((a, r) => a + (r.embedUsage?.callsWithUsage || 0), 0);
    const cols = msum.armNames;
    const lines = [];
    lines.push(`# LoCoMo retrieval matrix (Recall@${TOP_K}, dry)`);
    lines.push('');
    lines.push(
      `- commit: \`${commit}\` · samples: ${samples.length} · QA(有 gold evidence): ${msum.total} (已排除 category 5)`,
    );
    lines.push(
      `- 臂:vector / keyword / hybrid × HashEmbedder(确定性) / bge-m3(真实);evidence 层;会话日期${DATES ? '已注入' : '未注入'}`,
    );
    lines.push(`- bge-m3 embed tokens: ${embTot} (calls with usage: ${embCalls})`);
    lines.push('');
    lines.push(`| category | n | ${cols.join(' | ')} |`);
    lines.push(`|---|---|${cols.map(() => '---').join('|')}|`);
    const overall = { n: 0, hits: Object.fromEntries(cols.map((a) => [a, 0])) };
    for (const c of Object.keys(msum.byCat).sort()) {
      const b = msum.byCat[c];
      overall.n += b.n;
      for (const a of cols) overall.hits[a] += b.hits[a];
      const cells = cols.map((a) => (b.n ? ((b.hits[a] / b.n) * 100).toFixed(1) + '%' : 'n/a'));
      lines.push(`| ${c} ${CAT_NAME[c] || ''} | ${b.n} | ${cells.join(' | ')} |`);
    }
    const ocells = cols.map((a) =>
      overall.n ? ((overall.hits[a] / overall.n) * 100).toFixed(1) + '%' : 'n/a',
    );
    lines.push(`| **overall** | ${overall.n} | ${ocells.map((x) => `**${x}**`).join(' | ')} |`);
    const failed = mResults.filter((r) => r.failed).map((r) => r.id);
    if (failed.length) lines.push('', `- ⚠ 跳过(失败)sample: ${failed.join(', ')}`);
    const report = lines.join('\n') + '\n';
    console.log('\n' + report);
    if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
    const date = new Date().toISOString().slice(0, 10);
    // Bounded batches include their sample range in the output name.
    const batched = OFFSET > 0 || Number.isFinite(LIMIT);
    const base = `${date}-${commit}-locomo-matrix${DATES ? '' : '-nodates'}${batched ? `-s${OFFSET}n${samples.length}` : ''}`;
    writeFileSync(resolve(RUNS_DIR, `${base}.md`), report);
    writeFileSync(
      resolve(RUNS_DIR, `${base}.json`),
      JSON.stringify(
        {
          commit,
          date,
          topK: TOP_K,
          dates: DATES,
          offset: OFFSET,
          armNames: cols,
          byCat: msum.byCat,
          samples: mResults.map((r) => ({
            id: r.id,
            evidenceCount: r.evidenceCount ?? null,
            failed: !!r.failed,
            qaCounted: r.rows?.length ?? 0,
          })),
          embedTokens: embTot,
          embedCalls: embCalls,
        },
        null,
        2,
      ),
    );
    console.log('written:', resolve(RUNS_DIR, `${base}.md`), '(+ .json)');
    return;
  }

  let llm = null;
  if (!DRY) {
    llm = new OpenAICompatClient();
    console.log(
      'answer model:',
      process.env.MEMOWEFT_LLM_MODEL || process.env.DLA_LLM_MODEL || '(env 未设)',
    );
  }

  const results = [];
  for (const s of samples) {
    process.stderr.write(
      `  sample ${s.id}: ${s.turns.length} turns, ${Math.min(s.qa.length, QA_LIMIT)} QA…\n`,
    );
    results.push(await runSample(s, llm));
  }

  const sum = summarize(results);
  const commit = (() => {
    try {
      return execSync('git rev-parse --short HEAD').toString().trim();
    } catch {
      return 'nogit';
    }
  })();
  const usage = llm?.usage;
  // Aggregate Core-side usage across samples.
  const coreTot = results.reduce(
    (a, r) => {
      const u = r.coreUsage;
      if (!u) return a;
      return {
        llm: a.llm + (u.llm?.totalTokens || 0),
        embed: a.embed + (u.embed?.totalTokens || 0),
      };
    },
    { llm: 0, embed: 0 },
  );

  // Report
  const lines = [];
  lines.push(`# LoCoMo-10 evaluation (${DRY ? 'DRY structure check' : 'F1'})`);
  lines.push('');
  lines.push(
    `- commit: \`${commit}\` · samples: ${samples.length} · QA: ${sum.total} (已排除 category 5 adversarial)`,
  );
  if (!DRY)
    lines.push(
      `- answer model: ${process.env.MEMOWEFT_LLM_MODEL || process.env.DLA_LLM_MODEL || '?'} · 答题 tokens: ${usage ? usage.totalTokens : 'n/a'} (calls with usage: ${usage ? usage.callsWithUsage : 'n/a'})`,
    );
  if (!DRY && (coreTot.llm || coreTot.embed))
    lines.push(
      `- core 侧（updateProfile+recall）: llm ${coreTot.llm} + embed ${coreTot.embed} tokens · 全程合计: ${(usage?.totalTokens || 0) + coreTot.llm + coreTot.embed}`,
    );
  const recallDesc =
    LAYER === 'cognition'
      ? `cognition 层 core.recall top-5（updateProfile 消化后·env bge-m3）`
      : `evidence 层 ${RETRIEVER === 'semantic' ? 'bge-m3 语义检索' : '关键词'} top-${TOP_K}（未 updateProfile）`;
  const hitColName = LAYER === 'cognition' ? 'cognition 溯源命中率' : 'evidence 层命中率';
  lines.push(`- 召回:${recallDesc} · 会话日期${DATES ? '已注入' : '未注入(--no-dates)'}`);
  lines.push('');
  lines.push(`| category | n | ${hitColName} | ` + (DRY ? '' : '平均 F1 |'));
  lines.push('|---|---|---|' + (DRY ? '' : '---|'));
  for (const c of Object.keys(sum.byCat).sort()) {
    const b = sum.byCat[c];
    const evHit = b.evHitN ? ((b.evHitYes / b.evHitN) * 100).toFixed(1) + '%' : 'n/a';
    const meanF1 = b.f1 != null && b.n ? (b.f1 / b.n).toFixed(3) : 'n/a';
    lines.push(`| ${c} ${CAT_NAME[c] || ''} | ${b.n} | ${evHit} | ` + (DRY ? '' : `${meanF1} |`));
  }
  const report = lines.join('\n') + '\n';

  console.log('\n' + report);

  if (!DRY) {
    if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
    const date = new Date().toISOString().slice(0, 10);
    const armTag =
      (LAYER === 'cognition' ? 'cognition' : `evidence-${RETRIEVER}`) + (DATES ? '' : '-nodates');
    const out = resolve(RUNS_DIR, `${date}-${commit}-locomo-${armTag}.md`);
    writeFileSync(out, report);
    console.log('written:', out);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
