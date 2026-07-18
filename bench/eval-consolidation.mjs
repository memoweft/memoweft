/**
 * Consolidation-discipline evaluator. This is a manual, model-backed evaluation,
 * not a CI quality gate.
 *
 * Each scenario runs updateProfile (distill -> consolidate -> attribute) and records:
 *   1) deterministic structural checks derived from the scenario expectations;
 *   2) semantic gist checks using a three-vote, temperature-zero LLM judge.
 * Conflict formation uses a deterministic `conflicted`-status check because
 * exposing a conflict does not create a separate cognition for text matching.
 * Reports are written to ignored, commit-stamped files under bench/runs/ unless --out is supplied.
 *
 * The runner imports TypeScript source directly and requires Node.js 24+.
 *
 * Usage:
 *   node bench/eval-consolidation.mjs                        # full model-backed run (slow and billable)
 *   node bench/eval-consolidation.mjs --limit N              # partial model-backed run
 *   node bench/eval-consolidation.mjs --discipline <name>    # one discipline only
 *   node bench/eval-consolidation.mjs --out <prefix>         # write <prefix>.md and <prefix>.json
 *   node bench/eval-consolidation.mjs --subject-env ALT      # subject model from MEMOWEFT_ALT_*; judge remains the default model
 *   node bench/eval-consolidation.mjs --compare a.json b.json# compare two run files offline
 *   node bench/eval-consolidation.mjs --selftest             # offline checks with mock clients
 *
 * The default subject model comes from MEMOWEFT_LLM_*; the judge uses the same endpoint at temperature 0.
 * Confidence is computed by MemoWeft rules rather than supplied by the fixture.
 * Model outputs are stochastic; full-run metrics are point-in-time observations.
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { SqliteSemanticResolutionStore } from '../src/interaction/semanticResolutionStore.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';
import { updateProfile } from '../src/consolidation/updateProfile.ts';
import { OpenAICompatClient, loadLLMConfig } from '../src/llm/client.ts';
import { config } from '../src/config.ts';
import { promptVersions } from '../src/prompts/registry.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const CORPUS_PATH = resolve(HERE, '../tests/consolidation-corpus/corpus.json');
const RUNS_DIR = resolve(HERE, 'runs');
const GEN_CMD = 'node bench/eval-consolidation.mjs';
/** Number of temperature-zero judge votes per semantic check. */
const JUDGE_RUNS = 3;
/**
 * Version of the gist-scoring method. Version 2 checks conflict formation via
 * active `conflicted` status; other gist checks remain model-judged. Scores
 * from different method or judge-prompt versions are not directly comparable.
 */
const GIST_SCORING_VERSION = 'v2';

// ══════════════════════════════════════════════════════════════════════════
// Versioned judge prompt.
// Changing this requires a version bump and a fresh full run because judge wording changes the scores.
// Reports record this version because prompt wording affects scores.
// ══════════════════════════════════════════════════════════════════════════
const JUDGE_PROMPT_V1 = {
  version: 'v1',
  system: {
    zh: '你是严格的语义匹配判官。只回答一个词：YES 或 NO。不要解释、不要任何多余文字。',
    en: 'You are a strict semantic-match judge. Answer with exactly one word: YES or NO. No explanation, no extra text.',
  },
  /** Tests whether a formed cognition matches a required gist. */
  form(contents, gist, lang) {
    const list = renderCognitionList(contents, lang);
    return lang === 'zh'
      ? `已形成的认知如下：\n${list}\n\n其中是否有一条在语义上匹配这个要点：『${gist}』？只答 YES 或 NO。`
      : `The formed cognitions are:\n${list}\n\nIs there one among them that semantically matches this point: "${gist}"? Answer only YES or NO.`;
  },
  /** Tests whether a cognition asserts a prohibited over-inference. */
  not(contents, gist, lang) {
    const list = renderCognitionList(contents, lang);
    return lang === 'zh'
      ? `已形成的认知如下：\n${list}\n\n其中是否有一条断言了『${gist}』（这属于过度推断）？只答 YES 或 NO。`
      : `The formed cognitions are:\n${list}\n\nDoes any one of them assert "${gist}" (which would be an over-inference)? Answer only YES or NO.`;
  },
};

function renderCognitionList(contents, lang) {
  if (!contents.length)
    return lang === 'zh' ? '（无，没有形成任何认知）' : '(none, no cognition was formed)';
  return contents.map((c, i) => `${i + 1}. ${c}`).join('\n');
}

// ══════════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════════

/** Parses YES/NO answers; ambiguous responses default to NO. */
export function parseYesNo(ans) {
  const t = String(ans).trim().toUpperCase();
  const yi = t.search(/\bYES\b/);
  const ni = t.search(/\bNO\b/);
  const hasYes = yi >= 0;
  const hasNo = ni >= 0;
  if (hasYes && !hasNo) return true;
  if (hasNo && !hasYes) return false;
  if (hasYes && hasNo) return yi < ni;
  return false;
}

const UUID_RE = /\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]/gi;
/** Extracts bracketed UUID references. */
function uuidsIn(text) {
  return [...(text ?? '').matchAll(UUID_RE)].map((m) => m[1]);
}

/** Extract the prompt references used for evidence. Production prompts use
 * short `[eN]` labels; accept UUIDs too so this fixture remains compatible
 * with older prompt manifests. */
function evidenceRefsIn(text) {
  const shortRefs = [...(text ?? '').matchAll(/\[\s*(e\d+)\s*\]/gi)].map((m) => m[1]);
  return shortRefs.length > 0 ? shortRefs : uuidsIn(text);
}

/** Splits the profile and new-material sections for the mock client. */
function splitProfileMaterial(user) {
  for (const marker of ['【新材料】', '[New material]']) {
    const idx = user.indexOf(marker);
    if (idx >= 0) return { profilePart: user.slice(0, idx), materialPart: user.slice(idx) };
  }
  return { profilePart: '', materialPart: user };
}

// ══════════════════════════════════════════════════════════════════════════
// Judge voting
// ══════════════════════════════════════════════════════════════════════════

/** Returns all judge votes and their strict-majority result. */
async function judgeMajority(judge, lang, question) {
  const votes = [];
  for (let i = 0; i < JUDGE_RUNS; i++) {
    const ans = await judge.chat([
      { role: 'system', content: JUDGE_PROMPT_V1.system[lang] ?? JUDGE_PROMPT_V1.system.en },
      { role: 'user', content: question },
    ]);
    votes.push(parseYesNo(ans));
  }
  const yesCount = votes.filter(Boolean).length;
  return { votes, yes: yesCount * 2 > JUDGE_RUNS };
}

/**
 * Scores required and prohibited gists for one scenario. Conflict formation is
 * detected by active `conflicted` status; all other checks use the model judge.
 * The conflict signal assumes one required gist per conflict scenario and no
 * cognition pre-seeded with `conflicted` status.
 */
async function scoreGists(scenario, run, judge) {
  const contents = run.active.map((c) => c.content);
  const lang = scenario.lang === 'zh' ? 'zh' : 'en';
  const forms = scenario.expect?.shouldFormGists ?? [];
  const nots = scenario.expect?.shouldNotFormGists ?? [];
  const isConflict = scenario.discipline === 'conflict';
  // Scenario-level deterministic signal for conflict formation.
  const conflictSurfaced = run.active.some((c) => c.credStatus === 'conflicted');

  const formResults = [];
  for (const gist of forms) {
    if (isConflict) {
      formResults.push({
        gist,
        hit: conflictSurfaced,
        deterministic: true,
        signal: 'conflicted-status',
      });
    } else {
      const { votes, yes } = await judgeMajority(
        judge,
        lang,
        JUDGE_PROMPT_V1.form(contents, gist, lang),
      );
      formResults.push({ gist, votes, hit: yes });
    }
  }
  const notResults = [];
  for (const gist of nots) {
    const { votes, yes } = await judgeMajority(
      judge,
      lang,
      JUDGE_PROMPT_V1.not(contents, gist, lang),
    );
    notResults.push({ gist, votes, overInferred: yes });
  }
  const gistRecall = forms.length ? formResults.filter((r) => r.hit).length / forms.length : null;
  const overInferRate = nots.length
    ? notResults.filter((r) => r.overInferred).length / nots.length
    : null;
  return { formResults, notResults, gistRecall, overInferRate };
}

// ══════════════════════════════════════════════════════════════════════════
// Scenario execution
// ══════════════════════════════════════════════════════════════════════════

/**
 * Runs one scenario against in-memory stores and returns persisted results.
 * Evidence is explicitly marked `allowCloudRead=true` so a configured cloud-tier
 * subject model receives every fixture item. This keeps the run focused on
 * consolidation behavior rather than the privacy filter.
 * @param llm Subject-model client or offline mock.
 */
async function runScenario(scenario, llm) {
  config.language = scenario.lang === 'zh' ? 'zh' : 'en';
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  // Semantic resolutions are persisted so the evaluator can inspect them.
  const sem = new SqliteSemanticResolutionStore(':memory:');
  try {
    for (const s of scenario.seed ?? []) {
      cog.put({
        subjectId: 'owner',
        content: s.content,
        contentType: s.contentType,
        formedBy: s.formedBy,
        confidence: s.confidence,
        credStatus: s.credStatus,
      });
    }
    // Preserve message order within the attribution window.
    const base = Date.now() - 3600_000;
    /** Metadata needed to evaluate resolution coverage and source handling. */
    const evidenceMeta = [];
    scenario.messages.forEach((m, i) => {
      const e = ev.put({
        subjectId: 'owner',
        sourceKind: m.sourceKind,
        hostId: 'local',
        rawContent: m.rawContent,
        // Optional context used to resolve short replies without treating the
        // assistant turn as evidence.
        precedingAiContext: m.precedingAiContext,
        occurredAt: new Date(base + i * 1000).toISOString(),
        allowCloudRead: true,
      });
      evidenceMeta.push({
        id: e.id,
        sourceKind: m.sourceKind,
        hasAiContext: !!(m.precedingAiContext ?? '').trim(),
      });
    });

    const result = await updateProfile('owner', {
      evidenceStore: ev,
      eventStore: evt,
      cognitionStore: cog,
      semanticResolutionStore: sem,
      retriever: new NullRetriever(),
      llm,
    });

    const active = cog.active('owner').map((c) => ({
      id: c.id,
      content: c.content,
      contentType: c.contentType,
      credStatus: c.credStatus,
      confidence: c.confidence,
      formedBy: c.formedBy,
    }));
    const cogSources = cog
      .all('owner')
      .map((c) => ({ id: c.id, contentType: c.contentType, sources: cog.sourcesOf(c.id) }));
    const evidenceIds = new Set(ev.all().map((e) => e.id));

    return {
      error: null,
      consolidated: {
        created: result.consolidated.created.map((c) => ({
          content: c.content,
          contentType: c.contentType,
          credStatus: c.credStatus,
          confidence: c.confidence,
          formedBy: c.formedBy,
        })),
        createdCount: result.consolidated.created.length,
        reinforced: result.consolidated.reinforced,
        corrected: result.consolidated.corrected,
        conflicted: result.consolidated.conflicted,
        processedEvents: result.consolidated.processedEvents,
      },
      active,
      cogSources,
      evidenceIds,
      // Pair every evidence item with its persisted semantic resolution.
      resolutions: evidenceMeta.map((x) => {
        const r = sem.ofEvidence(x.id);
        return {
          ...x,
          res: r
            ? {
                resolvedContent: r.resolvedContent,
                responseAct: r.responseAct,
                promptAct: r.promptAct,
                propositionOrigin: r.propositionOrigin,
                assertionStrength: r.assertionStrength,
              }
            : null,
        };
      }),
      timings: result.timings,
    };
  } catch (e) {
    return {
      error: e instanceof Error ? e.message : String(e),
      consolidated: null,
      active: [],
      cogSources: [],
      evidenceIds: new Set(),
      resolutions: [],
      timings: null,
    };
  } finally {
    ev.close();
    evt.close();
    cog.close();
    sem.close();
  }
}

/**
 * Applies deterministic structural checks. Every scenario validates confidence,
 * transient-state status, and evidence references. Expectations may add checks
 * for conflict, correction, creation, provenance, chitchat, and short-reply
 * resolution. Optional checks affect the denominator only when declared by the
 * fixture, which preserves comparability with runs using the same fixture schema.
 */
export function checkStructural(scenario, run) {
  if (run.error) return [{ name: 'run', pass: false, detail: `updateProfile 抛错: ${run.error}` }];
  const c = run.consolidated;
  const ex = scenario.expect ?? {};
  const checks = [];

  if (ex.conflict)
    checks.push({
      name: 'conflicted≥1',
      pass: c.conflicted >= 1,
      detail: `conflicted=${c.conflicted}`,
    });
  if (ex.correct)
    checks.push({
      name: 'corrected≥1',
      pass: c.corrected >= 1,
      detail: `corrected=${c.corrected}`,
    });
  if (ex.newCognitions) {
    const { min, max, types, formedBy } = ex.newCognitions;
    checks.push({
      name: `created∈[${min},${max}]`,
      pass: c.createdCount >= min && c.createdCount <= max,
      detail: `created=${c.createdCount}`,
    });
    if (types) {
      // Content-type mismatches are reported separately from semantic
      // over-inference so callers can interpret the two metrics independently.
      const set = new Set(types);
      const bad = [
        ...new Set(c.created.filter((x) => !set.has(x.contentType)).map((x) => x.contentType)),
      ];
      checks.push({
        name: `created类型⊆{${types.join(',')}}`,
        pass: bad.length === 0,
        detail: bad.length ? `越界类型: ${bad.join(',')}` : 'ok',
      });
    }
    // Optional provenance constraint, used by context-dependent reply cases.
    if (formedBy) {
      const set = new Set(formedBy);
      const bad = [
        ...new Set(c.created.filter((x) => !set.has(x.formedBy)).map((x) => x.formedBy)),
      ];
      checks.push({
        name: `created来源⊆{${formedBy.join(',')}}`,
        pass: bad.length === 0,
        detail: bad.length ? `越界来源: ${bad.join(',')}` : 'ok',
      });
    }
  }
  if (scenario.discipline === 'chitchat-negative') {
    checks.push({
      name: 'chitchat→created===0',
      pass: c.createdCount === 0,
      detail: `created=${c.createdCount}`,
    });
  }
  if (scenario.discipline === 'short-reply') {
    // Context-dependent spoken replies require a persisted resolution.
    const need = run.resolutions.filter((x) => x.hasAiContext && x.sourceKind === 'spoken');
    const missing = need.filter((x) => !x.res);
    checks.push({
      name: '带AI上文的原话都落了解析',
      pass: need.length > 0 && missing.length === 0,
      detail:
        need.length === 0
          ? '评测语料缺少带 AI 上文的 spoken 原话'
          : missing.length
            ? `${missing.length}/${need.length} 条缺解析`
            : `${need.length}/${need.length} 条有解析`,
    });
    // Optional response-act constraint for the persisted resolutions.
    if (ex.resolutions?.responseAct) {
      const allow = new Set(ex.resolutions.responseAct);
      const acts = need.map((x) => x.res?.responseAct).filter((a) => a != null);
      const bad = [...new Set(acts.filter((a) => !allow.has(a)))];
      checks.push({
        name: `resolution.responseAct⊆{${ex.resolutions.responseAct.join(',')}}`,
        pass: acts.length > 0 && bad.length === 0,
        detail:
          acts.length === 0
            ? '无可评估的解析（覆盖检查未通过）'
            : bad.length
              ? `越界: ${bad.join(',')}`
              : acts.join(','),
      });
    }
  }

  // Every active cognition has a bounded positive confidence.
  const confBad = run.active.filter((a) => !(a.confidence > 0 && a.confidence <= 1000));
  checks.push({
    name: '不变量·confidence∈(0,1000]',
    pass: confBad.length === 0,
    detail: confBad.length
      ? `越界: ${confBad.map((a) => a.confidence).join(',')}`
      : `${run.active.length}条active合规`,
  });
  // Transient state cognitions remain candidate or low confidence.
  const stateBad = run.active.filter(
    (a) => a.contentType === 'state' && !(a.credStatus === 'candidate' || a.credStatus === 'low'),
  );
  checks.push({
    name: '不变量·state封顶∈{candidate,low}',
    pass: stateBad.length === 0,
    detail: stateBad.length ? `越界档: ${stateBad.map((a) => a.credStatus).join(',')}` : 'ok',
  });
  // Every provenance reference resolves to stored evidence.
  const chainBad = [];
  for (const cs of run.cogSources)
    for (const s of cs.sources) if (!run.evidenceIds.has(s.evidenceId)) chainBad.push(s.evidenceId);
  checks.push({
    name: '不变量·证据链引用真实存在',
    pass: chainBad.length === 0,
    detail: chainBad.length ? `虚构evidenceId ${chainBad.length}个` : 'ok',
  });

  return checks;
}

// ══════════════════════════════════════════════════════════════════════════
// Aggregation
// ══════════════════════════════════════════════════════════════════════════

/**
 * Describes semantic-resolution coverage without contributing to the score.
 * Coverage is retained in run JSON for provenance diagnostics.
 */
function buildResolutionProbe(resolutions) {
  const spoken = resolutions.filter((x) => x.sourceKind === 'spoken');
  const withRes = spoken.filter((x) => x.res);
  const dist = (key) => {
    const out = {};
    for (const x of withRes) {
      const v = x.res[key] ?? 'null';
      out[v] = (out[v] ?? 0) + 1;
    }
    return out;
  };
  return {
    spokenCount: spoken.length,
    withResolution: withRes.length,
    coverage: spoken.length ? withRes.length / spoken.length : null,
    /** Resolutions attached to non-spoken evidence. Expected to remain zero. */
    nonSpokenWithResolution: resolutions.filter((x) => x.sourceKind !== 'spoken' && x.res).length,
    responseAct: dist('responseAct'),
    propositionOrigin: dist('propositionOrigin'),
    assertionStrength: dist('assertionStrength'),
  };
}

function buildSummary(sc, run, checks, gist) {
  return {
    id: sc.id,
    discipline: sc.discipline,
    lang: sc.lang,
    title: sc.title,
    error: run.error,
    checks,
    structPass: checks.filter((c) => c.pass).length,
    structTotal: checks.length,
    gistRecall: gist.gistRecall,
    overInferRate: gist.overInferRate,
    formResults: gist.formResults,
    notResults: gist.notResults,
    consolidated: run.consolidated,
    resolutionProbe: buildResolutionProbe(run.resolutions ?? []),
  };
}

const mean = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null);

function aggregate(summaries) {
  const structPass = summaries.reduce((a, s) => a + s.structPass, 0);
  const structTotal = summaries.reduce((a, s) => a + s.structTotal, 0);
  const recalls = summaries.map((s) => s.gistRecall).filter((v) => v !== null);
  const overs = summaries.map((s) => s.overInferRate).filter((v) => v !== null);

  const byDiscipline = {};
  for (const s of summaries) (byDiscipline[s.discipline] ??= []).push(s);
  const groups = Object.entries(byDiscipline).map(([discipline, arr]) => ({
    discipline,
    n: arr.length,
    structPass: arr.reduce((a, s) => a + s.structPass, 0),
    structTotal: arr.reduce((a, s) => a + s.structTotal, 0),
    gistRecall: mean(arr.map((s) => s.gistRecall).filter((v) => v !== null)),
    overInferRate: mean(arr.map((s) => s.overInferRate).filter((v) => v !== null)),
  }));

  return {
    structPass,
    structTotal,
    structRate: structTotal ? structPass / structTotal : null,
    avgGistRecall: mean(recalls),
    avgOverInferRate: mean(overs),
    scenariosPassed: summaries.filter((s) => !s.error && s.structPass === s.structTotal).length,
    errored: summaries.filter((s) => s.error).length,
    groups,
  };
}

function collectMeta(corpus, scenarios, cfgs, filter) {
  const { subjectCfg, judgeCfg, subjectEnv } = cfgs;
  // Conflict formation does not consume judge calls.
  const judgeCalls = scenarios.reduce((a, s) => {
    const formCalls = s.discipline === 'conflict' ? 0 : (s.expect?.shouldFormGists ?? []).length;
    const notCalls = (s.expect?.shouldNotFormGists ?? []).length;
    return a + JUDGE_RUNS * (formCalls + notCalls);
  }, 0);
  const limit = filter?.limit ?? null;
  const discipline = filter?.discipline ?? null;
  return {
    commit: (() => {
      try {
        return execSync('git rev-parse --short HEAD', { cwd: HERE }).toString().trim();
      } catch {
        return 'unknown';
      }
    })(),
    node: process.versions.node,
    platform: process.platform,
    arch: process.arch,
    generatedAt: new Date().toISOString(),
    model: subjectCfg.model,
    scenarioCount: scenarios.length,
    totalScenarios: corpus.scenarios.length,
    judgeCalls,
    promptVersions: promptVersions(),
    judgePromptVersion: JUDGE_PROMPT_V1.version,
    gistScoringVersion: GIST_SCORING_VERSION,

    judgeModel: judgeCfg.model,
    subjectEnv: subjectEnv ?? null, // Non-null means the subject model came from a named environment prefix.
    partial: Boolean(limit) || Boolean(discipline),
    filter: { limit, discipline },
  };
}

// Report formatting
const pct = (n) => (n === null ? 'n/a' : (n * 100).toFixed(1) + '%');
const f2 = (n) => (n === null ? 'n/a' : n.toFixed(2));
const checksInline = (checks) => checks.map((c) => `${c.pass ? '✓' : '✗'}${c.name}`).join(' · ');
/** Formats prompt versions in stable identifier order. */
const formatPromptVersions = (pv) =>
  Object.keys(pv ?? {})
    .sort()
    .map((k) => `${k}@${pv[k]}`)
    .join(' · ');
/** Formats an optional scenario filter. */
function describeFilter(filter) {
  const p = [];
  if (filter?.discipline) p.push(`discipline=${filter.discipline}`);
  if (filter?.limit) p.push(`limit=${filter.limit}`);
  return p.length ? p.join(', ') : '无';
}
/** Formats a signed delta. */
const signed = (n, digits) =>
  n === null || n === undefined ? 'n/a' : (n >= 0 ? '+' : '') + n.toFixed(digits);

function buildReport(summaries, agg, meta) {
  const L = [];
  L.push('# Consolidation discipline report');
  L.push('');
  if (meta.subjectEnv) {
    L.push(
      `> Subject model = \`${meta.model}\` (--subject-env ${meta.subjectEnv}); judge = \`${meta.judgeModel}\`.`,
    );
    L.push(
      '> This run uses a non-default subject model. Compare it only with a run that uses the same corpus, prompts, judge, and scoring versions.',
    );
    L.push('');
  }
  if (meta.partial) {
    L.push(
      `> ⚠ **PARTIAL RUN：只跑了 ${meta.scenarioCount}/${meta.totalScenarios} 场景（filter=${describeFilter(meta.filter)}）。**`,
    );
    L.push('> This is a partial run. Do not compare its aggregate score with a full-corpus run.');
    L.push('');
  }
  L.push(
    '> Each scenario runs updateProfile with the configured subject model. Results include deterministic structural checks and semantic checks from a three-vote, temperature-zero judge.',
  );
  L.push(
    '> This is a point-in-time model-backed observation, not a CI assertion or a fixed reproducible score.',
  );
  L.push('');
  L.push('## 生成环境');
  L.push('');
  L.push('| 项 | 值 |');
  L.push('| --- | --- |');
  const cmdSuffix = `${meta.filter?.discipline ? ` --discipline ${meta.filter.discipline}` : ''}${meta.filter?.limit ? ` --limit ${meta.filter.limit}` : ''}`;
  L.push(`| 生成命令 | \`${GEN_CMD}${cmdSuffix}\` |`);
  L.push(`| commit | \`${meta.commit}\` |`);
  L.push(`| Node | ${meta.node} |`);
  L.push(`| 平台 | ${meta.platform}/${meta.arch} |`);
  L.push(`| 生成时间 | ${meta.generatedAt} |`);
  L.push(`| Subject model | ${meta.model} |`);
  L.push(`| judge model | ${meta.judgeModel}（复用同端点，温度 0 覆写） |`);
  L.push(`| judge 提示词版本 | ${meta.judgePromptVersion}（每要点 ${JUDGE_RUNS} 次取多数） |`);
  L.push(
    `| gist 评分口径版本 | ${meta.gistScoringVersion ?? 'v1'}（v2: conflict shouldForm uses persisted status; cross-version gistRecall is not comparable） |`,
  );
  L.push(`| 被测提示词版本 | ${formatPromptVersions(meta.promptVersions)} |`);
  L.push(
    `| 语料 | tests/consolidation-corpus/corpus.json（跑 ${meta.scenarioCount}/${meta.totalScenarios} 场景） |`,
  );
  L.push('');
  L.push('## 总分');
  L.push('');
  L.push('| 指标 | 值 |');
  L.push('| --- | --- |');
  L.push(`| 结构断言通过率 | ${agg.structPass}/${agg.structTotal} = ${pct(agg.structRate)} |`);
  L.push(
    `| 场景全部通过（结构断言通过且无执行错误） | ${agg.scenariosPassed}/${meta.scenarioCount} |`,
  );
  L.push(`| 平均 gistRecall（越高越好） | ${f2(agg.avgGistRecall)} |`);
  L.push(`| 平均 overInferRate（越低越好） | ${f2(agg.avgOverInferRate)} |`);
  L.push(`| 执行失败场景（LLM/网络错误） | ${agg.errored} |`);
  L.push('');
  L.push('## 按 discipline 分组');
  L.push('');
  L.push('| discipline | 场景数 | 结构通过率 | 平均 gistRecall | 平均 overInferRate |');
  L.push('| --- | --- | --- | --- | --- |');
  for (const g of agg.groups) {
    L.push(
      `| ${g.discipline} | ${g.n} | ${g.structPass}/${g.structTotal} = ${pct(g.structTotal ? g.structPass / g.structTotal : null)} | ${f2(g.gistRecall)} | ${f2(g.overInferRate)} |`,
    );
  }
  L.push('');
  L.push('## 逐场景明细');
  L.push('');
  L.push('| id | discipline | lang | 结构 | gistRecall | overInferRate | 备注 |');
  L.push('| --- | --- | --- | --- | --- | --- | --- |');
  for (const s of summaries) {
    const note = s.error ? `错误: ${s.error.slice(0, 60)}` : s.title;
    L.push(
      `| ${s.id} | ${s.discipline} | ${s.lang} | ${s.structPass}/${s.structTotal} | ${f2(s.gistRecall)} | ${f2(s.overInferRate)} | ${note} |`,
    );
  }
  L.push('');
  L.push('## 逐场景结构断言逐项');
  L.push('');
  for (const s of summaries) {
    L.push(`- **${s.id}** (${s.discipline}/${s.lang}): ${checksInline(s.checks)}`);
  }
  L.push('');
  L.push('## 逐场景要点判分明细');
  L.push('');
  for (const s of summaries) {
    if (!s.formResults.length && !s.notResults.length) continue;
    L.push(`### ${s.id} — ${s.title}`);
    L.push('');
    for (const r of s.formResults) {
      const basis = r.deterministic
        ? `确定性·${r.signal}`
        : `票 ${r.votes.map((v) => (v ? 'Y' : 'N')).join('')}`;
      L.push(`- shouldForm ${r.hit ? '✓ matched' : '✗ not matched'}（${basis}）：${r.gist}`);
    }
    for (const r of s.notResults)
      L.push(
        `- shouldNot ${r.overInferred ? '✗ over-inference detected' : '✓ not detected'}（票 ${r.votes.map((v) => (v ? 'Y' : 'N')).join('')}）：${r.gist}`,
      );
    L.push('');
  }
  L.push('## 备注');
  L.push('');
  L.push(
    '- **Model outputs are stochastic**: repeated subject-model and judge calls can produce different scores even when the judge uses temperature 0 and majority voting.',
  );
  L.push(
    '- **Model-backed and billable**: each scenario can require multiple subject-model and judge calls. Runtime and cost depend on the configured endpoints; inspect the run manifest before comparing results.',
  );
  L.push(
    '- **Structural checks are deterministic.** Semantic gist checks are model-judged and must not be compared across judge-prompt versions.',
  );
  L.push(
    '- **conflict 场景的 gistRecall 使用确定性检查**：存在 credStatus=`conflicted` 的在册认知表示冲突已暴露且旧认知仍留档。该路径不产生适合文本匹配的独立认知，因此 shouldNotFormGists（不删/不覆盖/不裁决）仍使用 LLM 判分。',
  );
  L.push(
    '- **Confidence is rule-computed.** Fixtures do not supply expected confidence values; structural checks validate bounds, transient-state caps, and evidence references.',
  );
  L.push(
    '- **Compare like with like**: changing the corpus, prompts, model, judge, or scoring version starts a new result series.',
  );
  L.push('');
  return L.join('\n');
}

function printConsole(summaries, agg, meta) {
  console.log('');
  console.log('════════ Consolidation discipline evaluation ════════');
  console.log(
    `commit ${meta.commit} · Node ${meta.node} · ${meta.platform}/${meta.arch} · model ${meta.model} · judge ${JUDGE_PROMPT_V1.version}`,
  );
  console.log(`语料：${meta.scenarioCount}/${meta.totalScenarios} 场景`);
  console.log('');
  console.log(`结构断言通过率  ${agg.structPass}/${agg.structTotal} = ${pct(agg.structRate)}`);
  console.log(
    `场景全部通过    ${agg.scenariosPassed}/${meta.scenarioCount}（执行失败 ${agg.errored}）`,
  );
  console.log(`平均 gistRecall     ${f2(agg.avgGistRecall)}`);
  console.log(`平均 overInferRate  ${f2(agg.avgOverInferRate)}`);
  console.log('── 按 discipline ──');
  for (const g of agg.groups) {
    console.log(
      `${g.discipline.padEnd(20)} n=${g.n}  结构 ${g.structPass}/${g.structTotal}  gistRecall=${f2(g.gistRecall)}  overInfer=${f2(g.overInferRate)}`,
    );
  }
  console.log('════════════════════════════════════════════');
  console.log('');
}

// ══════════════════════════════════════════════════════════════════════════
// Output paths and summaries
// ══════════════════════════════════════════════════════════════════════════

/**
 * Resolves report paths. `--out` overrides the default ignored path under
 * `bench/runs/`.
 */
function resolveOutputPaths(meta, outPrefix) {
  if (outPrefix) return { md: `${outPrefix}.md`, json: `${outPrefix}.json` };
  mkdirSync(RUNS_DIR, { recursive: true });
  const date = meta.generatedAt.slice(0, 10); // YYYY-MM-DD
  const parts = [];
  if (meta.subjectEnv)
    parts.push(`subject-${String(meta.model || meta.subjectEnv).replace(/[^A-Za-z0-9._-]/g, '_')}`);
  if (meta.filter?.discipline) parts.push(meta.filter.discipline);
  if (meta.filter?.limit) parts.push(`limit${meta.filter.limit}`);
  const tag = parts.join('-') || 'full';
  const base = resolve(RUNS_DIR, `${date}-${meta.commit}-consolidation-${tag}`);
  return { md: `${base}.md`, json: `${base}.json` };
}

/** Formats a compact summary for one run. */
function commitSummarySingle(agg, meta) {
  return `结构断言 ${pct(agg.structRate)}(${agg.structPass}/${agg.structTotal})；scenariosPassed ${agg.scenariosPassed}/${meta.scenarioCount}；errored ${agg.errored}；avgGistRecall ${f2(agg.avgGistRecall)}；avgOverInferRate ${f2(agg.avgOverInferRate)}`;
}

// ══════════════════════════════════════════════════════════════════════════
// Offline run comparison
// ══════════════════════════════════════════════════════════════════════════

const rateOf = (g) => (g && g.structTotal ? g.structPass / g.structTotal : null);
const subOrNull = (x, y) =>
  x === null || x === undefined || y === null || y === undefined ? null : x - y;

/**
 * Compares two parsed run files. The result includes metric deltas, prompt
 * version changes, and comparability warnings.
 */
function diffRuns(a, b) {
  const am = a.meta ?? {};
  const bm = b.meta ?? {};
  const aAgg = a.agg ?? {};
  const bAgg = b.agg ?? {};

  const warnings = [];
  if (am.scenarioCount !== bm.scenarioCount)
    warnings.push(`样本不同：${am.scenarioCount} → ${bm.scenarioCount} 场景，不可直接比。`);
  if (Boolean(am.partial) !== Boolean(bm.partial))
    warnings.push(
      `partial 不一致：before partial=${Boolean(am.partial)}, after partial=${Boolean(bm.partial)}，不可直接比。`,
    );
  if (am.model !== bm.model)
    warnings.push(`被测模型变了：${am.model} → ${bm.model}，分数不可直接归因到提示词。`);
  if (am.judgePromptVersion !== bm.judgePromptVersion)
    warnings.push(
      `judge 提示词变了：${am.judgePromptVersion} → ${bm.judgePromptVersion}，model-judged metrics（gistRecall/overInferRate）不可比。`,
    );
  // Missing scoring metadata denotes the version-1 method.
  const gsvA = am.gistScoringVersion ?? 'v1';
  const gsvB = bm.gistScoringVersion ?? 'v1';
  if (gsvA !== gsvB)
    warnings.push(
      `gist 评分口径变了：${gsvA} → ${gsvB}（v2 uses persisted conflict status rather than the model judge）——conflict gistRecall 与总体 avgGistRecall 不可跨版本比。`,
    );

  // Compare every prompt identifier present in either run.
  const pvA = am.promptVersions ?? {};
  const pvB = bm.promptVersions ?? {};
  const promptChanges = [];
  for (const id of [...new Set([...Object.keys(pvA), ...Object.keys(pvB)])].sort()) {
    if (pvA[id] !== pvB[id])
      promptChanges.push({ id, before: pvA[id] ?? '(缺)', after: pvB[id] ?? '(缺)' });
  }

  const structRateBefore = aAgg.structRate ?? null;
  const structRateAfter = bAgg.structRate ?? null;
  const overall = {
    structPass: { before: aAgg.structPass ?? null, after: bAgg.structPass ?? null },
    structTotal: { before: aAgg.structTotal ?? null, after: bAgg.structTotal ?? null },
    structRate: {
      before: structRateBefore,
      after: structRateAfter,
      deltaPP:
        subOrNull(structRateAfter, structRateBefore) === null
          ? null
          : subOrNull(structRateAfter, structRateBefore) * 100,
    },
    scenariosPassed: { before: aAgg.scenariosPassed ?? null, after: bAgg.scenariosPassed ?? null },
    errored: { before: aAgg.errored ?? null, after: bAgg.errored ?? null },
    avgGistRecall: {
      before: aAgg.avgGistRecall ?? null,
      after: bAgg.avgGistRecall ?? null,
      delta: subOrNull(bAgg.avgGistRecall, aAgg.avgGistRecall),
    },
    avgOverInferRate: {
      before: aAgg.avgOverInferRate ?? null,
      after: bAgg.avgOverInferRate ?? null,
      delta: subOrNull(bAgg.avgOverInferRate, aAgg.avgOverInferRate),
    },
  };

  const aMap = new Map((aAgg.groups ?? []).map((g) => [g.discipline, g]));
  const bMap = new Map((bAgg.groups ?? []).map((g) => [g.discipline, g]));
  const byDiscipline = [...new Set([...aMap.keys(), ...bMap.keys()])].sort().map((d) => {
    const ga = aMap.get(d);
    const gb = bMap.get(d);
    const ra = rateOf(ga);
    const rb = rateOf(gb);
    return {
      discipline: d,
      onlyIn: !ga ? 'after' : !gb ? 'before' : null,
      structPass: { before: ga?.structPass ?? null, after: gb?.structPass ?? null },
      structTotal: { before: ga?.structTotal ?? null, after: gb?.structTotal ?? null },
      structRate: {
        before: ra,
        after: rb,
        deltaPP: subOrNull(rb, ra) === null ? null : subOrNull(rb, ra) * 100,
      },
      gistRecall: {
        before: ga?.gistRecall ?? null,
        after: gb?.gistRecall ?? null,
        delta: subOrNull(gb?.gistRecall, ga?.gistRecall),
      },
      overInferRate: {
        before: ga?.overInferRate ?? null,
        after: gb?.overInferRate ?? null,
        delta: subOrNull(gb?.overInferRate, ga?.overInferRate),
      },
    };
  });

  return { warnings, promptChanges, overall, byDiscipline, meta: { before: am, after: bm } };
}

const SOFT_NOTE = '（model-judged; interpret as a point-in-time observation）';

/** Prints a human-readable run comparison. */
function printDiff(diff, beforePath, afterPath) {
  const mb = diff.meta.before;
  const ma = diff.meta.after;
  const tag = (m) =>
    `commit ${m.commit ?? '?'} · ${m.scenarioCount ?? '?'} 场景${m.partial ? '(PARTIAL)' : ''} · model ${m.model ?? '?'} · judge ${m.judgePromptVersion ?? '?'}`;
  console.log('');
  console.log('════════ Consolidation run comparison ════════');
  console.log(`before(a): ${beforePath}`);
  console.log(`           [${tag(mb)}]`);
  console.log(`after (b): ${afterPath}`);
  console.log(`           [${tag(ma)}]`);
  console.log('');

  if (diff.warnings.length) {
    console.log('⚠ 可比性警示：');
    for (const w of diff.warnings) console.log(`  - ${w}`);
  } else {
    console.log('（样本 / 模型 / judge 提示词一致，可比。）');
  }
  console.log('');

  console.log('提示词版本变更：');
  if (diff.promptChanges.length) {
    for (const c of diff.promptChanges) console.log(`  ▶ ${c.id}: ${c.before} → ${c.after}`);
  } else {
    console.log('  （提示词版本无变化。）');
  }
  console.log('');

  const o = diff.overall;
  const passArrow =
    o.structTotal.before === o.structTotal.after
      ? `${o.structPass.before}→${o.structPass.after}/${o.structTotal.after}`
      : `${o.structPass.before}/${o.structTotal.before}→${o.structPass.after}/${o.structTotal.after}`;
  console.log('── 总体（deterministic checks）──');
  console.log(
    `结构断言   ${pct(o.structRate.before)}→${pct(o.structRate.after)}  (${passArrow})  Δ${signed(o.structRate.deltaPP, 1)}pp`,
  );
  console.log(
    `通过场景   ${o.scenariosPassed.before} → ${o.scenariosPassed.after}  Δ${signed(subOrNull(o.scenariosPassed.after, o.scenariosPassed.before), 0)}`,
  );
  console.log(
    `errored    ${o.errored.before} → ${o.errored.after}  Δ${signed(subOrNull(o.errored.after, o.errored.before), 0)}`,
  );
  console.log('── 总体（model-judged metrics）──');
  console.log(
    `avgGistRecall     ${f2(o.avgGistRecall.before)} → ${f2(o.avgGistRecall.after)}  Δ${signed(o.avgGistRecall.delta, 2)}  ${SOFT_NOTE}`,
  );
  console.log(
    `avgOverInferRate  ${f2(o.avgOverInferRate.before)} → ${f2(o.avgOverInferRate.after)}  Δ${signed(o.avgOverInferRate.delta, 2)}  ${SOFT_NOTE}`,
  );
  console.log('');

  console.log('── 按 discipline ──');
  console.log(
    `overInferRate and non-conflict gistRecall are model-judged ${SOFT_NOTE}; conflict gistRecall uses persisted conflicted status.`,
  );
  for (const g of diff.byDiscipline) {
    if (g.onlyIn) {
      console.log(`${g.discipline.padEnd(20)} （仅存在于 ${g.onlyIn}，无法对比）`);
      continue;
    }
    const structCol = `${g.structPass.before}/${g.structTotal.before}→${g.structPass.after}/${g.structTotal.after} (Δ${signed(g.structRate.deltaPP, 1)}pp)`;
    const gistDet = g.discipline === 'conflict' ? '[确定性]' : '';
    const gistCol = `gistRecall ${f2(g.gistRecall.before)}→${f2(g.gistRecall.after)}(Δ${signed(g.gistRecall.delta, 2)})${gistDet}`;
    const overCol = `overInfer ${f2(g.overInferRate.before)}→${f2(g.overInferRate.after)}(Δ${signed(g.overInferRate.delta, 2)})`;
    console.log(`${g.discipline.padEnd(20)} 结构 ${structCol.padEnd(28)} ${gistCol}  ${overCol}`);
  }
  console.log('════════════════════════════════════════════');
}

/** Formats overall and per-discipline structural deltas. */
function commitSummaryFromDiff(diff) {
  const o = diff.overall;
  const parts = [];
  const passArrow =
    o.structTotal.before === o.structTotal.after
      ? `${o.structPass.before}→${o.structPass.after}/${o.structTotal.after}`
      : `${o.structPass.before}/${o.structTotal.before}→${o.structPass.after}/${o.structTotal.after}`;
  parts.push(`结构断言 ${pct(o.structRate.before)}→${pct(o.structRate.after)}(${passArrow})`);
  parts.push(`通过场景 ${o.scenariosPassed.before}→${o.scenariosPassed.after}`);
  for (const g of diff.byDiscipline) {
    if (g.onlyIn) continue;
    if (g.structPass.before === g.structPass.after && g.structTotal.before === g.structTotal.after)
      continue;
    parts.push(
      `${g.discipline} ${g.structPass.before}/${g.structTotal.before}→${g.structPass.after}/${g.structTotal.after}`,
    );
  }
  return parts.join('；');
}

/** Loads and compares two run files without model or environment access. */
function runCompare(beforePath, afterPath) {
  for (const [label, p] of [
    ['before(a)', beforePath],
    ['after(b)', afterPath],
  ]) {
    if (!p) {
      console.error(
        '[eval-consolidation] --compare 需要两个 JSON：--compare <before.json> <after.json>',
      );
      process.exit(1);
    }
    if (!existsSync(p)) {
      console.error(`[eval-consolidation] --compare ${label} 文件不存在: ${p}`);
      process.exit(1);
    }
  }
  const a = JSON.parse(readFileSync(beforePath, 'utf8'));
  const b = JSON.parse(readFileSync(afterPath, 'utf8'));
  const diff = diffRuns(a, b);
  printDiff(diff, beforePath, afterPath);
  console.log('');
  console.log('── Metric summary ──');
  console.log(commitSummaryFromDiff(diff));
  process.exit(0);
}

// ══════════════════════════════════════════════════════════════════════════
// Model-backed execution
// ══════════════════════════════════════════════════════════════════════════

async function mainReal({ limit, discipline, outPrefix, subjectEnv }) {
  if (!existsSync(CORPUS_PATH)) {
    console.error(`\n[eval-consolidation] Corpus file not found: ${CORPUS_PATH}`);
    process.exit(1);
  }
  const corpus = JSON.parse(readFileSync(CORPUS_PATH, 'utf8'));
  let scenarios = corpus.scenarios;
  if (discipline) {
    scenarios = scenarios.filter((s) => s.discipline === discipline);
    if (scenarios.length === 0) {
      const known = [...new Set(corpus.scenarios.map((s) => s.discipline))].sort().join(' · ');
      console.error(`[eval-consolidation] --discipline "${discipline}" matched no scenarios.`);
      console.error(`  语料里的纪律: ${known}`);
      process.exit(1);
    }
  }
  if (limit) scenarios = scenarios.slice(0, limit);

  // Model-backed runs require explicit credentials.
  let llmCfg;
  try {
    llmCfg = loadLLMConfig();
  } catch (e) {
    console.error('\n[eval-consolidation] 完整模型评测需要 LLM 配置，未开始运行。');
    console.error(`  原因: ${e instanceof Error ? e.message : String(e)}`);
    console.error('  Configure MEMOWEFT_LLM_BASE_URL / _API_KEY / _MODEL in the root .env.');
    console.error('  （离线自检请跑: node bench/eval-consolidation.mjs --selftest）');
    process.exit(2);
  }

  // Subject model: default chat model, or MEMOWEFT_<PREFIX>_* with --subject-env.
  // The judge remains the default model at temperature 0 so a model-arm comparison changes one variable.
  let subjectCfg;
  try {
    subjectCfg = subjectEnv ? loadLLMConfig(subjectEnv) : llmCfg;
  } catch (e) {
    console.error(
      `\n[eval-consolidation] --subject-env ${subjectEnv} 所需的被测模型未配置，未开始运行。`,
    );
    console.error(`  原因: ${e instanceof Error ? e.message : String(e)}`);
    console.error(
      `  Configure MEMOWEFT_${subjectEnv}_BASE_URL / _API_KEY / _MODEL in the root .env.`,
    );
    process.exit(2);
  }

  const judge = new OpenAICompatClient({ ...llmCfg, temperature: 0 });
  const meta = collectMeta(
    corpus,
    scenarios,
    { subjectCfg, judgeCfg: llmCfg, subjectEnv },
    { limit, discipline },
  );
  console.log(
    `[eval-consolidation] Starting ${meta.scenarioCount} scenarios · subject=${meta.model}${subjectEnv ? ` (--subject-env ${subjectEnv})` : ''} · judge=${meta.judgeModel} at temperature 0 · estimated judge calls=${meta.judgeCalls}`,
  );

  const summaries = [];
  for (const sc of scenarios) {
    const t0 = Date.now();
    console.log(`[eval-consolidation] Evaluating ${sc.id} (${sc.discipline}/${sc.lang})…`);
    const llm = new OpenAICompatClient(subjectCfg); // One subject-model client per scenario keeps usage accounting isolated.
    const run = await runScenario(sc, llm);
    const checks = checkStructural(sc, run);
    let gist = { formResults: [], notResults: [], gistRecall: null, overInferRate: null };
    if (run.error) {
      console.error(`  ✗ updateProfile failed: ${run.error}`);
    } else {
      console.log(
        `  结构断言 ${checks.filter((c) => c.pass).length}/${checks.length} · created=${run.consolidated.createdCount} corrected=${run.consolidated.corrected} conflicted=${run.consolidated.conflicted}`,
      );
      try {
        gist = await scoreGists(sc, run, judge);
      } catch (e) {
        console.error(`  judge 判分失败: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    console.log(`  用时 ${((Date.now() - t0) / 1000).toFixed(1)}s`);
    summaries.push(buildSummary(sc, run, checks, gist));
  }

  const agg = aggregate(summaries);
  printConsole(summaries, agg, meta);

  if (meta.subjectEnv) {
    console.log(
      `ℹ Alternate subject-model arm: subject=${meta.model} (--subject-env ${meta.subjectEnv}), judge=${meta.judgeModel}.`,
    );
    console.log(
      '  Compare only against a run with matching corpus, prompt, judge, and scoring metadata.',
    );
    console.log('');
  }
  if (meta.partial) {
    console.log(
      `⚠ PARTIAL RUN：只跑了 ${meta.scenarioCount}/${meta.totalScenarios} 场景（filter=${describeFilter(meta.filter)}）。`,
    );
    console.log('  Partial-run aggregates are not directly comparable with full-corpus runs.');
    console.log('');
  }

  const paths = resolveOutputPaths(meta, outPrefix);
  writeFileSync(paths.md, buildReport(summaries, agg, meta), 'utf8');
  writeFileSync(paths.json, JSON.stringify({ meta, agg, summaries }, null, 2), 'utf8');
  console.log(`[eval-consolidation] 报告已写入 ${paths.md}`);
  console.log(`[eval-consolidation] 机读 JSON 已写入 ${paths.json}`);
  console.log('');
  console.log('── Metric summary ──');
  console.log(commitSummarySingle(agg, meta));
}

// ══════════════════════════════════════════════════════════════════════════
// Offline self-test
// ══════════════════════════════════════════════════════════════════════════

/** Mock subject client with prompt-aware distill, consolidate, and attribute responses. */
class MockLLMClient {
  constructor(spec) {
    this.spec = spec;
    this._callCount = 0;
  }
  get callCount() {
    return this._callCount;
  }
  async chat(messages) {
    this._callCount++;
    const sys = messages[0]?.content ?? '';
    const user = messages.find((m) => m.role === 'user')?.content ?? '';
    if (/认知画像|cognitive profile/.test(sys)) {
      const { profilePart, materialPart } = splitProfileMaterial(user);
      const cogIds = uuidsIn(profilePart);
      const evIds = evidenceRefsIn(materialPart);
      return JSON.stringify(this.spec.consolidate ? this.spec.consolidate(cogIds, evIds) : {});
    }
    if (/可能的原因|possible causes/.test(sys)) {
      return JSON.stringify(this.spec.attribute ? this.spec.attribute() : { hypotheses: [] });
    }
    return this.spec.distill ?? '（模拟事件摘要）';
  }
}

/** Mock judge that returns a configured answer sequence. */
class MockJudge {
  constructor(answers) {
    this.answers = answers;
    this.i = 0;
    this._callCount = 0;
  }
  get callCount() {
    return this._callCount;
  }
  async chat(_messages) {
    this._callCount++;
    return this.answers[this.i++] ?? 'NO';
  }
}

async function selftest() {
  let failures = 0;
  const ok = (cond, msg) => {
    if (cond) console.log(`  ✓ ${msg}`);
    else {
      console.error(`  ✗ ${msg}`);
      failures++;
    }
  };

  // ── 1) 结构断言 · newCognitions + 三条不变量（preference + state 封顶） ──
  console.log('[selftest] 1) 结构断言 · newCognitions + 不变量');
  const s1 = {
    id: 'ST-1',
    discipline: 'emotion-cap',
    lang: 'zh',
    title: '新认知：咖啡偏好 + 疲惫 state',
    messages: [
      { sourceKind: 'spoken', rawContent: '我特别喜欢喝美式咖啡' },
      { sourceKind: 'spoken', rawContent: '今天好累' },
    ],
    expect: {
      newCognitions: { min: 1, max: 3, types: ['preference', 'state'] },
      shouldFormGists: ['用户喜欢咖啡'],
      shouldNotFormGists: ['用户是咖啡师'],
    },
  };
  const s1mock = new MockLLMClient({
    consolidate: (_cogIds, evIds) => ({
      new: [
        {
          content: '用户喜欢喝美式咖啡',
          content_type: 'preference',
          formed_by: 'stated',
          support_evidence_ids: [evIds[0]],
        },
        {
          content: '用户今天很累',
          content_type: 'state',
          formed_by: 'stated',
          support_evidence_ids: [evIds[1] ?? evIds[0]],
        },
      ],
    }),
  });
  const r1 = await runScenario(s1, s1mock);
  ok(!r1.error, `ST-1 updateProfile 无错误（${r1.error ?? 'ok'}）`);
  const c1 = checkStructural(s1, r1);
  ok(
    c1.every((c) => c.pass),
    `ST-1 结构断言全过 → ${checksInline(c1)}`,
  );
  ok(
    r1.consolidated?.createdCount === 2,
    `ST-1 created=2（实际 ${r1.consolidated?.createdCount}）`,
  );
  const st1 = r1.active.find((a) => a.contentType === 'state');
  ok(
    st1 && (st1.credStatus === 'low' || st1.credStatus === 'candidate') && st1.confidence <= 300,
    `ST-1 state 封顶（cred=${st1?.credStatus} conf=${st1?.confidence}）`,
  );
  // gist 判分：form 3 票 YES → 命中；not 3 票 NO → 未过度推断
  const g1 = await scoreGists(s1, r1, new MockJudge(['YES', 'YES', 'YES', 'NO', 'NO', 'NO']));
  ok(g1.gistRecall === 1, `ST-1 gistRecall=1（实际 ${g1.gistRecall}）`);
  ok(g1.overInferRate === 0, `ST-1 overInferRate=0（实际 ${g1.overInferRate}）`);

  // ── 2) 结构断言 · conflict ──
  console.log('[selftest] 2) 结构断言 · conflict');
  const s2 = {
    id: 'ST-2',
    discipline: 'conflict',
    lang: 'zh',
    title: '冲突：早睡 vs 凌晨打游戏',
    seed: [
      {
        content: '用户喜欢早睡',
        contentType: 'preference',
        formedBy: 'stated',
        confidence: 600,
        credStatus: 'limited',
      },
    ],
    messages: [{ sourceKind: 'observed', rawContent: '凌晨3点还在打游戏' }],
    expect: { conflict: true, newCognitions: { min: 0, max: 2, types: ['state'] } },
  };
  const s2mock = new MockLLMClient({
    consolidate: (cogIds, evIds) => ({
      conflict: [{ cognition_id: cogIds[0], support_evidence_ids: [evIds[0]] }],
    }),
  });
  const r2 = await runScenario(s2, s2mock);
  ok(!r2.error, `ST-2 updateProfile 无错误（${r2.error ?? 'ok'}）`);
  ok(r2.consolidated?.conflicted >= 1, `ST-2 conflicted≥1（实际 ${r2.consolidated?.conflicted}）`);
  const c2 = checkStructural(s2, r2);
  ok(
    c2.every((c) => c.pass),
    `ST-2 结构断言全过 → ${checksInline(c2)}`,
  );

  // ── 2b) Deterministic conflict-status scoring ──
  console.log('[selftest] 2b) conflict gistRecall uses persisted status');
  const s2b = {
    id: 'ST-2b',
    discipline: 'conflict',
    lang: 'zh',
    title: '冲突 gist：暴露即命中（确定性）',
    seed: [
      {
        content: '用户喜欢早睡',
        contentType: 'preference',
        formedBy: 'stated',
        confidence: 600,
        credStatus: 'limited',
      },
    ],
    messages: [{ sourceKind: 'observed', rawContent: '凌晨3点还在打游戏' }],
    expect: {
      conflict: true,
      newCognitions: { min: 0, max: 2, types: ['state'] },
      shouldFormGists: ['把矛盾行为作为观察记录，并作为与早睡偏好矛盾的反证暴露出来'],
      shouldNotFormGists: ['直接改写或删除旧的早睡偏好'],
    },
  };
  const s2bmock = new MockLLMClient({
    consolidate: (cogIds, evIds) => ({
      conflict: [{ cognition_id: cogIds[0], support_evidence_ids: [evIds[0]] }],
    }),
  });
  const r2b = await runScenario(s2b, s2bmock);
  ok(
    r2b.active.some((a) => a.credStatus === 'conflicted'),
    `ST-2b 落库存在在册 conflicted 认知（暴露不裁决）`,
  );
  const judge2b = new MockJudge(['NO', 'NO', 'NO']); // 只有 shouldNot 会消耗 judge：3 次
  const g2b = await scoreGists(s2b, r2b, judge2b);
  ok(
    g2b.gistRecall === 1,
    `ST-2b conflict shouldForm 确定性命中 → gistRecall=1（实际 ${g2b.gistRecall}）`,
  );
  ok(
    g2b.formResults[0]?.deterministic === true,
    `ST-2b conflict 的 form 判分标记为确定性（signal=${g2b.formResults[0]?.signal}）`,
  );
  ok(
    judge2b.callCount === JUDGE_RUNS,
    `ST-2b judge 只判 shouldNot、不判 conflict 的 shouldForm（callCount=${judge2b.callCount}，期望 ${JUDGE_RUNS}）`,
  );
  ok(
    g2b.overInferRate === 0,
    `ST-2b shouldNot 全 NO → overInferRate=0（实际 ${g2b.overInferRate}）`,
  );

  // ── 2c) Negative control for the deterministic conflict signal ──
  console.log('[selftest] 2c) conflict signal negative control');
  const s2cmock = new MockLLMClient({
    consolidate: () => ({ new: [], reinforce: [], correct: [], conflict: [] }),
  });
  const r2c = await runScenario(s2b, s2cmock); // 同场景但 mock 什么都不标
  ok(
    !r2c.active.some((a) => a.credStatus === 'conflicted'),
    `ST-2c 未暴露冲突 → 落库无 conflicted 认知`,
  );
  const g2c = await scoreGists(s2b, r2c, new MockJudge(['NO', 'NO', 'NO']));
  ok(g2c.gistRecall === 0, `ST-2c 无 conflicted 状态 → gistRecall=0（实际 ${g2c.gistRecall}）`);

  // ── 3) 结构断言 · chitchat-negative（不该形成认知） ──
  console.log('[selftest] 3) 结构断言 · chitchat-negative');
  const s3 = {
    id: 'ST-3',
    discipline: 'chitchat-negative',
    lang: 'zh',
    title: '闲聊：无认知',
    messages: [{ sourceKind: 'spoken', rawContent: '哈哈哈你说得对' }],
    expect: { newCognitions: { min: 0, max: 0 }, shouldNotFormGists: ['把附和当成一条认知'] },
  };
  const s3mock = new MockLLMClient({
    consolidate: () => ({ new: [], reinforce: [], correct: [], conflict: [] }),
  });
  const r3 = await runScenario(s3, s3mock);
  ok(
    r3.consolidated?.createdCount === 0,
    `ST-3 created=0（实际 ${r3.consolidated?.createdCount}）`,
  );
  const c3 = checkStructural(s3, r3);
  ok(
    c3.every((c) => c.pass),
    `ST-3 结构断言全过 → ${checksInline(c3)}`,
  );
  // 误判过度推断：judge 对 shouldNot 全票 YES → overInferRate=1
  const g3 = await scoreGists(s3, r3, new MockJudge(['YES', 'YES', 'YES']));
  ok(
    g3.overInferRate === 1,
    `ST-3 judge 检出 over-inference → overInferRate=1（实际 ${g3.overInferRate}）`,
  );

  // ── 4) Structural-check negative controls ──
  console.log('[selftest] 4) 结构断言负例');
  const cNeg = checkStructural({ ...s3, expect: { conflict: true } }, r3);
  ok(cNeg.find((c) => c.name === 'conflicted≥1')?.pass === false, '对未发生的冲突判 fail');
  const fakeRun = {
    error: null,
    consolidated: {
      created: [],
      createdCount: 0,
      reinforced: 0,
      corrected: 0,
      conflicted: 0,
      processedEvents: 0,
    },
    active: [
      {
        id: 'x',
        content: 'y',
        contentType: 'state',
        credStatus: 'stable',
        confidence: 900,
        formedBy: 'stated',
      },
    ],
    cogSources: [
      { id: 'x', contentType: 'state', sources: [{ evidenceId: 'ghost', relation: 'support' }] },
    ],
    evidenceIds: new Set(), // ghost 不在其中
  };
  const cFake = checkStructural({ discipline: 'emotion-cap', expect: {} }, fakeRun);
  ok(
    cFake.find((c) => c.name.includes('state封顶'))?.pass === false,
    '不变量·state封顶 对越界档判 fail',
  );
  ok(
    cFake.find((c) => c.name.includes('证据链'))?.pass === false,
    '不变量·证据链 对虚构 id 判 fail',
  );
  ok(
    cFake.find((c) => c.name.includes('confidence'))?.pass === true,
    '不变量·confidence 对合法值判 pass',
  );

  // ── 5) judge 多数投票逻辑 ──
  console.log('[selftest] 5) judge 多数投票');
  ok(
    (await judgeMajority(new MockJudge(['YES', 'YES', 'NO']), 'zh', 'q')).yes === true,
    'YES/YES/NO → 多数 YES',
  );
  ok(
    (await judgeMajority(new MockJudge(['NO', 'NO', 'YES']), 'zh', 'q')).yes === false,
    'NO/NO/YES → 多数 NO',
  );
  ok(
    (await judgeMajority(new MockJudge(['YES', 'NO', 'NO']), 'zh', 'q')).yes === false,
    'YES/NO/NO → 多数 NO',
  );
  ok(
    (await judgeMajority(new MockJudge(['YES', 'YES', 'YES']), 'zh', 'q')).yes === true,
    'YES×3 → 多数 YES',
  );
  ok(
    parseYesNo('YES') === true &&
      parseYesNo('  no.') === false &&
      parseYesNo('Yes, there is one.') === true &&
      parseYesNo('嗯') === false,
    'parseYesNo 容错（大小写/标点/含糊保守判NO）',
  );

  // ── 6) diffRuns 纯函数（离线前后对比：上升 / 下降 / 样本不同 / 提示词版本变更） ──
  console.log('[selftest] 6) diffRuns 纯函数（离线前后对比）');
  const mkRun = (o = {}) => ({
    meta: {
      commit: o.commit ?? 'abc1234',
      scenarioCount: o.scenarioCount ?? 42,
      totalScenarios: 42,
      partial: o.partial ?? false,
      model: o.model ?? 'subject-model',
      judgePromptVersion: o.judgePromptVersion ?? 'v1',
      gistScoringVersion: o.gistScoringVersion, // Missing metadata represents scoring version 1.
      promptVersions: o.promptVersions ?? { consolidate: 'v2', distill: 'v1' },
    },
    agg: {
      structPass: o.structPass,
      structTotal: o.structTotal,
      structRate: o.structTotal ? o.structPass / o.structTotal : null,
      scenariosPassed: o.scenariosPassed ?? 0,
      errored: o.errored ?? 0,
      avgGistRecall: o.avgGistRecall ?? null,
      avgOverInferRate: o.avgOverInferRate ?? null,
      groups: o.groups ?? [
        {
          discipline: 'chitchat-negative',
          n: 7,
          structPass: o.chitPass ?? 21,
          structTotal: 35,
          gistRecall: null,
          overInferRate: o.chitOver ?? 0.3,
        },
      ],
    },
    summaries: [],
  });

  // 6a) Higher structural score and scenario-pass count.
  const up = diffRuns(
    mkRun({ structPass: 198, structTotal: 223, scenariosPassed: 25, chitPass: 21 }),
    mkRun({ structPass: 210, structTotal: 223, scenariosPassed: 30, chitPass: 33 }),
  );
  ok(
    up.overall.structRate.deltaPP > 0,
    `diffRuns 上升 → structRate ΔPP>0（实际 ${signed(up.overall.structRate.deltaPP, 1)}pp）`,
  );
  ok(
    up.overall.scenariosPassed.after === 30 && up.overall.scenariosPassed.before === 25,
    'diffRuns 上升 → 通过场景 25→30',
  );
  ok(
    up.warnings.length === 0,
    `diffRuns 上升 → 样本/模型/judge 一致，无警示（实际 ${up.warnings.length}）`,
  );
  ok(
    commitSummaryFromDiff(up).includes('chitchat-negative 21/35→33/35'),
    `diffRuns 上升 → commit 摘要含 chitchat 变化（${commitSummaryFromDiff(up)}）`,
  );

  // 6b) 分数下降：210→198/223
  const down = diffRuns(
    mkRun({ structPass: 210, structTotal: 223 }),
    mkRun({ structPass: 198, structTotal: 223 }),
  );
  ok(
    down.overall.structRate.deltaPP < 0,
    `diffRuns 下降 → structRate ΔPP<0（实际 ${signed(down.overall.structRate.deltaPP, 1)}pp）`,
  );

  // 6c) Differing sample sets produce a comparability warning.
  const diffSample = diffRuns(
    mkRun({ structPass: 198, structTotal: 223, scenarioCount: 42 }),
    mkRun({ structPass: 40, structTotal: 45, scenarioCount: 7, partial: true }),
  );
  ok(
    diffSample.warnings.some((w) => /样本不同/.test(w)),
    'diffRuns 样本不同 → 警示「样本不同」',
  );
  ok(
    diffSample.warnings.some((w) => /partial 不一致/.test(w)),
    'diffRuns partial 不一致 → 警示「partial 不一致」',
  );

  // 6d) 提示词版本变更 → promptChanges 逐条列出 consolidate v2→v3
  const diffPrompt = diffRuns(
    mkRun({
      structPass: 198,
      structTotal: 223,
      promptVersions: { consolidate: 'v2', distill: 'v1' },
    }),
    mkRun({
      structPass: 210,
      structTotal: 223,
      promptVersions: { consolidate: 'v3', distill: 'v1' },
    }),
  );
  ok(
    diffPrompt.promptChanges.length === 1 &&
      diffPrompt.promptChanges[0].id === 'consolidate' &&
      diffPrompt.promptChanges[0].before === 'v2' &&
      diffPrompt.promptChanges[0].after === 'v3',
    `diffRuns 提示词变更 → promptChanges=[consolidate v2→v3]（实际 ${JSON.stringify(diffPrompt.promptChanges)}）`,
  );

  // 6e) Judge-prompt changes invalidate model-judged metric comparisons.
  const diffJudge = diffRuns(
    mkRun({ structPass: 200, structTotal: 223, judgePromptVersion: 'v1' }),
    mkRun({ structPass: 200, structTotal: 223, judgePromptVersion: 'v2' }),
  );
  ok(
    diffJudge.warnings.some((w) => /judge 提示词变了/.test(w)),
    'diffRuns judge 版本变更 → 警示「judge 提示词变了」',
  );

  // 6f) A scoring-version change must produce a comparability warning.
  const diffGsv = diffRuns(
    mkRun({ structPass: 200, structTotal: 223 }),
    mkRun({ structPass: 200, structTotal: 223, gistScoringVersion: 'v2' }),
  );
  ok(
    diffGsv.warnings.some((w) => /gist 评分口径变了/.test(w)),
    'diffRuns gist 口径变更（缺字段→v2）→ 警示「gist 评分口径变了」',
  );
  // 同口径（都 v2）→ 不误报
  const diffGsvSame = diffRuns(
    mkRun({ structPass: 200, structTotal: 223, gistScoringVersion: 'v2' }),
    mkRun({ structPass: 200, structTotal: 223, gistScoringVersion: 'v2' }),
  );
  ok(
    !diffGsvSame.warnings.some((w) => /gist 评分口径变了/.test(w)),
    'diffRuns 同 gist 口径（v2=v2）→ 不误报口径变更',
  );

  // ── 7) Subject-model injection and run-output routing ──
  console.log('[selftest] 7)  被测模型注入 meta/落盘路由');
  const corpusStub = { scenarios: [{ discipline: 'conflict', expect: {} }] };
  const metaSubj = collectMeta(
    corpusStub,
    corpusStub.scenarios,
    { subjectCfg: { model: 'subject-x' }, judgeCfg: { model: 'judge-x' }, subjectEnv: 'ALT' },
    {},
  );
  ok(
    metaSubj.model === 'subject-x' && metaSubj.judgeModel === 'judge-x',
    `collectMeta 分离 subject/judge model（subject=${metaSubj.model} judge=${metaSubj.judgeModel}）`,
  );
  ok(
    metaSubj.subjectEnv === 'ALT' && metaSubj.partial === false,
    `collectMeta records subjectEnv=${metaSubj.subjectEnv} for a full run`,
  );
  const pSubj = resolveOutputPaths(metaSubj, null);
  ok(
    /[\\/]runs[\\/]/.test(pSubj.json) && /subject-subject-x/.test(pSubj.json),
    `subject 臂落 runs/、文件名带被测模型（${pSubj.json.split(/[\\/]/).pop()}）`,
  );
  ok(/[\\/]runs[\\/]/.test(pSubj.json), 'subject 臂写入 runs 目录');
  // 默认完整运行也写入按提交标记的 runs 产物。
  const metaBase = collectMeta(
    corpusStub,
    corpusStub.scenarios,
    {
      subjectCfg: { model: 'subject-default' },
      judgeCfg: { model: 'judge-default' },
      subjectEnv: null,
    },
    {},
  );
  ok(
    /[\\/]runs[\\/]/.test(resolveOutputPaths(metaBase, null).json) &&
      /consolidation-full\.json$/.test(resolveOutputPaths(metaBase, null).json),
    '默认完整运行写入 commit-stamped runs 产物',
  );

  if (failures === 0) {
    console.log('\n[selftest] ✓ 全部通过（结构断言、judge 投票、gist 判分、run 对比与输出路由）');
    process.exit(0);
  }
  console.error(`\n[selftest] ✗ ${failures} 项失败`);
  process.exit(1);
}

// ══════════════════════════════════════════════════════════════════════════
// CLI
// ══════════════════════════════════════════════════════════════════════════

const args = process.argv.slice(2);
const isSelftest = args.includes('--selftest');

/** Reject malformed CLI input before an accidental, expensive full run starts. */
function die(msg) {
  console.error(`[eval-consolidation] ${msg}`);
  process.exit(1);
}

// Validate `--limit` before a model-backed run begins.
const limitIdx = args.indexOf('--limit');
let limit = null;
if (limitIdx >= 0) {
  const raw = args[limitIdx + 1];
  const n = raw === undefined ? NaN : Number(raw);
  if (!Number.isInteger(n) || n < 1) die(`--limit 需要一个 ≥1 的整数（收到: ${raw ?? '(空)'}）。`);
  limit = n;
}

const discIdx = args.indexOf('--discipline');
let discipline = null;
if (discIdx >= 0) {
  const raw = args[discIdx + 1];
  if (!raw || raw.startsWith('--')) die(`--discipline 需要一个纪律名（收到: ${raw ?? '(空)'}）。`);
  discipline = raw;
}

const outIdx = args.indexOf('--out');
let outPrefix = null;
if (outIdx >= 0) {
  const raw = args[outIdx + 1];
  if (!raw || raw.startsWith('--')) die(`--out 需要一个产物路径前缀（收到: ${raw ?? '(空)'}）。`);
  outPrefix = raw;
}

// `--subject-env` selects MEMOWEFT_<PREFIX>_* for the subject model. The judge
// remains on the default configuration.
const subjEnvIdx = args.indexOf('--subject-env');
let subjectEnv = null;
if (subjEnvIdx >= 0) {
  const raw = args[subjEnvIdx + 1];
  if (!raw || raw.startsWith('--'))
    die(
      `--subject-env 需要一个 env 前缀（如 GPT4O，读 MEMOWEFT_<前缀>_BASE_URL/_API_KEY/_MODEL；收到: ${raw ?? '(空)'}）。`,
    );
  subjectEnv = raw;
}

const cmpIdx = args.indexOf('--compare');
let compare = null;
if (cmpIdx >= 0) {
  const before = args[cmpIdx + 1];
  const after = args[cmpIdx + 2];
  if (!before || !after || before.startsWith('--') || after.startsWith('--')) {
    die('--compare 需要两个 run JSON 路径：--compare <before.json> <after.json>');
  }
  compare = { before, after };
}

async function main() {
  if (isSelftest) {
    await selftest();
    return;
  }
  if (compare) {
    runCompare(compare.before, compare.after);
    return;
  }
  await mainReal({ limit, discipline, outPrefix, subjectEnv });
}

// Imports expose helpers only; they never read model configuration, call a model, or write a report.
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch((err) => {
    console.error('[eval-consolidation] 失败：', err);
    process.exit(1);
  });
}
