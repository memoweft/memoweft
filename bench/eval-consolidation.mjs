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

/**
 * Wrap an LLM client so transient network failures retry with linear backoff.
 * Bench-only: a full-corpus run makes hundreds of subject/judge calls, and a single
 * flaky `fetch failed` / 429 / connection reset would otherwise drop a scenario
 * (errored) or a judge verdict from the baseline. Retries ONLY transient errors;
 * deterministic failures (malformed JSON, 4xx other than 429) still surface immediately.
 * callCount/tier/usage pass through so cost accounting still reads the real totals.
 */
function withChatRetry(inner, { tries = 3, baseMs = 1500 } = {}) {
  const isTransient = (e) => {
    const msg = String((e && e.message) || e);
    // Network-layer failures (Node fetch native — language-independent).
    if (
      /fetch failed|network|socket|ECONNRESET|ETIMEDOUT|EAI_AGAIN|timed out|timeout|超时/i.test(msg)
    )
      return true;
    // Classify HTTP failures by the status number, not the localized message text:
    // OpenAICompatClient formats them as `... failed <status>` / `... 请求失败 <status>`
    // (zh vs en). Retry 429 + 5xx; other 4xx are deterministic and surface immediately.
    const status = msg.match(/(?:failed|请求失败)\s+(\d{3})/i);
    if (status) {
      const code = Number(status[1]);
      return code === 429 || (code >= 500 && code < 600);
    }
    return /\b429\b|rate.?limit/i.test(msg);
  };
  return {
    get callCount() {
      return inner.callCount;
    },
    get tier() {
      return inner.tier;
    },
    get usage() {
      return inner.usage;
    },
    async chat(messages) {
      let lastErr;
      for (let attempt = 1; attempt <= tries; attempt++) {
        try {
          return await inner.chat(messages);
        } catch (e) {
          lastErr = e;
          if (attempt === tries || !isTransient(e)) throw e;
          await new Promise((r) => setTimeout(r, baseMs * attempt));
        }
      }
      throw lastErr;
    },
  };
}

const HERE = dirname(fileURLToPath(import.meta.url));
const CORPUS_PATH = resolve(HERE, '../tests/consolidation-corpus/corpus.json');
const RUNS_DIR = resolve(HERE, 'runs');
const GEN_CMD = 'node bench/eval-consolidation.mjs';
/** Number of temperature-zero judge votes per semantic check. */
const JUDGE_RUNS = 3;
/**
 * Version of the gist-scoring method. Version 2 checks conflict formation via
 * active `conflicted` status; version 3 also rejects invalid judge responses.
 * Scores from different method or judge-prompt versions are not directly
 * comparable.
 */
const GIST_SCORING_VERSION = 'v3';

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

/**
 * Legacy cross-language parser used by the shared parity fixture. The live
 * judge path applies the stricter protocol parser below before accepting a
 * vote.
 */
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

/** Returns a vote only when the judge emitted exactly one YES/NO token. */
function parseJudgeVote(ans) {
  const t = String(ans).trim().toUpperCase();
  const hasYes = /\bYES\b/.test(t);
  const hasNo = /\bNO\b/.test(t);
  if (hasYes === hasNo) return null;
  return hasYes;
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
    const vote = parseJudgeVote(ans);
    if (vote === null) {
      throw new Error('Invalid judge response: expected exactly one YES or NO token.');
    }
    votes.push(vote);
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

function buildSummary(sc, run, checks, gist, evaluationError = run.error) {
  return {
    id: sc.id,
    discipline: sc.discipline,
    lang: sc.lang,
    title: sc.title,
    error: evaluationError,
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

/** 95% 用的 z（双侧 0.975 分位）。 */
const Z_95 = 1.959963984540054;

/**
 * 二项比例的 Wilson score 区间。
 *
 * 用 Wilson 而不是教科书 Wald：样本小、或比例贴近 0/1 时 Wald 会给出越界区间
 * （overInfer 恰恰贴着 0，structRate 贴着 1），Wilson 不会。
 * 只对**计数比率**用（分子分母都是个数）——均值类指标（每场景一个 0~1 的值再平均）
 * 不是二项，必须走 bootstrapMeanCI，套这个就是错量具。
 */
function wilsonInterval(successes, total, z = Z_95) {
  if (!Number.isFinite(total) || total <= 0) return null;
  const p = successes / total;
  const z2 = z * z;
  const denom = 1 + z2 / total;
  const center = (p + z2 / (2 * total)) / denom;
  const half = (z / denom) * Math.sqrt((p * (1 - p)) / total + z2 / (4 * total * total));
  return { lo: Math.max(0, center - half), hi: Math.min(1, center + half) };
}

/**
 * xorshift32 确定性伪随机。
 *
 * bootstrap 必须可复现——同一批分数任何时候重算都要给出同一个区间，
 * 否则「区间变了」分不清是数据变了还是抽样变了。故不用 Math.random。
 */
function makeRng(seed) {
  let x = seed >>> 0 || 1;
  return () => {
    x ^= x << 13;
    x >>>= 0;
    x ^= x >>> 17;
    x ^= x << 5;
    x >>>= 0;
    return x / 4294967296;
  };
}

/**
 * 均值的 bootstrap 百分位区间（默认 10000 次重抽、固定种子）。
 *
 * 给 avgGistRecall / avgOverInferRate 这类「每场景一个 0~1 的值，再取平均」的指标用：
 * 它们不是二项比例，Wilson 不适用。
 */
/**
 * 聚类（cluster）bootstrap：以**场景**为重抽单位算一个比率的区间。
 *
 * 为什么结构断言通过率不能用 Wilson：Wilson 假设每次试验独立，而同一场景的 4~8 条断言
 * 是对**同一次模型输出**的相关观测（一次执行错误还会让整个场景一起塌），60 个场景根本
 * 提供不了 318 份独立信息。对合并计数套 Wilson 会算出**虚假变窄**的区间——正是评测纪律
 * 里禁止的那种「看着精确其实没有」。
 *
 * 故按场景重抽整条 (pass,total) 向量再求合并比率，场景内相关性被原样保留。
 * 场景**之间**才是独立的，所以「场景全过率」那类一场景一观测的指标仍走 Wilson。
 */
function clusterBootstrapRateCI(clusters, { iterations = 10000, seed = 20260728 } = {}) {
  const n = clusters.length;
  if (!n) return null;
  const totalAll = clusters.reduce((a, c) => a + c.total, 0);
  if (totalAll <= 0) return null;
  const rng = makeRng(seed);
  const rates = [];
  for (let i = 0; i < iterations; i += 1) {
    let pass = 0;
    let total = 0;
    for (let j = 0; j < n; j += 1) {
      const c = clusters[(rng() * n) | 0];
      pass += c.pass;
      total += c.total;
    }
    if (total > 0) rates.push(pass / total);
  }
  if (!rates.length) return null;
  rates.sort((a, b) => a - b);
  const at = (q) =>
    rates[Math.min(rates.length - 1, Math.max(0, Math.round(q * (rates.length - 1))))];
  const lo = at(0.025);
  const hi = at(0.975);

  // bootstrap 的经典缺陷：重抽只能产生样本里出现过的值。所有场景都满分时区间会退化成
  // [1,1]——读起来像「确定就是 100%」，其实只是样本太小、还没见过失败（7 个场景全过
  // 完全可能出自一个真实通过率 85% 的系统）。这种同质情形退回**场景级** Wilson
  // （n = 场景数，一场景一观测），把不确定性如实留住。
  if (lo === hi) {
    const usable = clusters.filter((c) => c.total > 0);
    const allPass = usable.filter((c) => c.pass === c.total).length;
    const w = wilsonInterval(allPass, usable.length);
    if (w) return w;
  }
  return { lo, hi };
}

function bootstrapMeanCI(values, { iterations = 10000, seed = 20260728 } = {}) {
  const n = values.length;
  if (!n) return null;
  if (n === 1) return { lo: values[0], hi: values[0] };
  const rng = makeRng(seed);
  const means = new Float64Array(iterations);
  for (let i = 0; i < iterations; i += 1) {
    let sum = 0;
    for (let j = 0; j < n; j += 1) sum += values[(rng() * n) | 0];
    means[i] = sum / n;
  }
  means.sort();
  const at = (q) => means[Math.min(iterations - 1, Math.max(0, Math.round(q * (iterations - 1))))];
  return { lo: at(0.025), hi: at(0.975) };
}

export function aggregate(summaries) {
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
    // 按场景聚类重抽（同 aggregate 顶层的理由）：组内断言同样是相关观测。
    structRateCI: clusterBootstrapRateCI(
      arr.map((s) => ({ pass: s.structPass, total: s.structTotal })),
    ),
    gistRecall: mean(arr.map((s) => s.gistRecall).filter((v) => v !== null)),
    overInferRate: mean(arr.map((s) => s.overInferRate).filter((v) => v !== null)),
  }));

  const scenariosPassed = summaries.filter(
    (s) => !s.error && s.structPass === s.structTotal,
  ).length;

  return {
    structPass,
    structTotal,
    structRate: structTotal ? structPass / structTotal : null,
    // 结构断言率 → 按场景聚类 bootstrap（断言不独立，Wilson 会虚假变窄）；
    // 一场景一观测的比率（场景全过率）→ Wilson；均值类 → 普通 bootstrap。三者不可互换。
    structRateCI: clusterBootstrapRateCI(
      summaries.map((s) => ({ pass: s.structPass, total: s.structTotal })),
    ),
    avgGistRecall: mean(recalls),
    avgGistRecallCI: bootstrapMeanCI(recalls),
    avgOverInferRate: mean(overs),
    avgOverInferRateCI: bootstrapMeanCI(overs),
    scenariosPassed,
    scenarioPassRate: summaries.length ? scenariosPassed / summaries.length : null,
    scenarioPassRateCI: wilsonInterval(scenariosPassed, summaries.length),
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
/** 区间渲染：比率类按百分比，均值类按两位小数；没有区间（样本为 0）显式写 n/a，不留空。 */
const ciPct = (ci) => (ci ? `[${pct(ci.lo)}, ${pct(ci.hi)}]` : 'n/a');
const ciF2 = (ci) => (ci ? `[${f2(ci.lo)}, ${f2(ci.hi)}]` : 'n/a');
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

export function buildReport(summaries, agg, meta) {
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
  L.push(
    `| judge model | ${meta.judgeModel}（温度 0 覆写；缺省用默认 LLM，--judge-env 时为独立端点） |`,
  );
  L.push(`| judge 提示词版本 | ${meta.judgePromptVersion}（每要点 ${JUDGE_RUNS} 次取多数） |`);
  L.push(
    `| gist 评分口径版本 | ${meta.gistScoringVersion ?? 'v1'}（v2: conflict shouldForm uses persisted status; v3: invalid judge responses fail closed; cross-version gistRecall is not comparable） |`,
  );
  L.push(`| 被测提示词版本 | ${formatPromptVersions(meta.promptVersions)} |`);
  L.push(
    `| 语料 | tests/consolidation-corpus/corpus.json（跑 ${meta.scenarioCount}/${meta.totalScenarios} 场景） |`,
  );
  L.push('');
  L.push('## 总分');
  L.push('');
  L.push('| 指标 | 值 | 95% CI | 区间口径 |');
  L.push('| --- | --- | --- | --- |');
  L.push(
    `| 结构断言通过率 | ${agg.structPass}/${agg.structTotal} = ${pct(agg.structRate)} | ${ciPct(agg.structRateCI)} | cluster bootstrap（按场景） |`,
  );
  L.push(
    `| 场景全部通过（结构断言通过且无执行错误） | ${agg.scenariosPassed}/${meta.scenarioCount} = ${pct(agg.scenarioPassRate)} | ${ciPct(agg.scenarioPassRateCI)} | Wilson |`,
  );
  L.push(
    `| 平均 gistRecall（越高越好） | ${f2(agg.avgGistRecall)} | ${ciF2(agg.avgGistRecallCI)} | bootstrap |`,
  );
  L.push(
    `| 平均 overInferRate（越低越好） | ${f2(agg.avgOverInferRate)} | ${ciF2(agg.avgOverInferRateCI)} | bootstrap |`,
  );
  L.push(`| 执行失败场景（LLM/网络错误） | ${agg.errored} | — | — |`);
  L.push('');
  L.push(
    '> 三种口径按数据结构选，不可互换：**结构断言通过率**走**按场景聚类的 bootstrap**——同一场景的多条断言是对同一次模型输出的相关观测（执行错误还会让整场景一起塌），把它们当独立试验套 Wilson 会算出**虚假变窄**的区间；**场景全过率**是一场景一观测、场景间独立，用 Wilson score 区间；**均值类**（各场景 gistRecall / overInferRate 再平均）用普通 bootstrap 百分位。全部 10000 次重抽 + 固定种子，故同一批分数恒得同一区间。**单轮点估计不足以下结论**——比较两次跑分先看区间是否重叠。',
  );
  L.push('');
  L.push('## 按 discipline 分组');
  L.push('');
  L.push(
    '| discipline | 场景数 | 结构通过率 | 结构 95% CI | 平均 gistRecall | 平均 overInferRate |',
  );
  L.push('| --- | --- | --- | --- | --- | --- |');
  for (const g of agg.groups) {
    L.push(
      `| ${g.discipline} | ${g.n} | ${g.structPass}/${g.structTotal} = ${pct(g.structTotal ? g.structPass / g.structTotal : null)} | ${ciPct(g.structRateCI)} | ${f2(g.gistRecall)} | ${f2(g.overInferRate)} |`,
    );
  }
  L.push('');
  L.push(
    '> 分组样本很小（每组约 7 个场景），分组区间会宽到几乎不可用——那正是它要告诉你的：**单个 discipline 的分数差异多半是噪声，别据此下结论**。',
  );
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
  console.log(
    `结构断言通过率  ${agg.structPass}/${agg.structTotal} = ${pct(agg.structRate)}  95%CI ${ciPct(agg.structRateCI)}（按场景聚类）`,
  );
  console.log(
    `场景全部通过    ${agg.scenariosPassed}/${meta.scenarioCount} = ${pct(agg.scenarioPassRate)}  95%CI ${ciPct(agg.scenarioPassRateCI)}（执行失败 ${agg.errored}）`,
  );
  console.log(
    `平均 gistRecall     ${f2(agg.avgGistRecall)}  95%CI ${ciF2(agg.avgGistRecallCI)}（bootstrap）`,
  );
  console.log(
    `平均 overInferRate  ${f2(agg.avgOverInferRate)}  95%CI ${ciF2(agg.avgOverInferRateCI)}（bootstrap）`,
  );
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
 * Resolves report paths and creates their parent directory. `--out` overrides
 * the default ignored path under `bench/runs/`.
 */
export function resolveOutputPaths(meta, outPrefix) {
  let base = outPrefix;
  if (!base) {
    const date = meta.generatedAt.slice(0, 10); // YYYY-MM-DD
    const parts = [];
    if (meta.subjectEnv)
      parts.push(
        `subject-${String(meta.model || meta.subjectEnv).replace(/[^A-Za-z0-9._-]/g, '_')}`,
      );
    if (meta.filter?.discipline) parts.push(meta.filter.discipline);
    if (meta.filter?.limit) parts.push(`limit${meta.filter.limit}`);
    const tag = parts.join('-') || 'full';
    base = resolve(RUNS_DIR, `${date}-${meta.commit}-consolidation-${tag}`);
  }
  mkdirSync(dirname(resolve(base)), { recursive: true });
  return { md: `${base}.md`, json: `${base}.json` };
}

/** Formats a compact summary for one run. */
export function commitSummarySingle(agg, meta) {
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
  if (am.judgeModel !== bm.judgeModel)
    warnings.push(
      `判官模型变了：${am.judgeModel} → ${bm.judgeModel}，模型判定的指标（gist / chitchat 等）不可直接比。`,
    );
  if (am.judgePromptVersion !== bm.judgePromptVersion)
    warnings.push(
      `judge 提示词变了：${am.judgePromptVersion} → ${bm.judgePromptVersion}，model-judged metrics（gistRecall/overInferRate）不可比。`,
    );
  // Missing scoring metadata denotes the version-1 method.
  const gsvA = am.gistScoringVersion ?? 'v1';
  const gsvB = bm.gistScoringVersion ?? 'v1';
  if (gsvA !== gsvB)
    warnings.push(
      `gist 评分口径变了：${gsvA} → ${gsvB}（评分信号或 judge 响应协议可能不同）——gistRecall 与总体 avgGistRecall 不可跨版本比。`,
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

async function mainReal({ limit, discipline, outPrefix, subjectEnv, judgeEnv }) {
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

  // Judge model: MEMOWEFT_<PREFIX>_* with --judge-env, else the default MEMOWEFT_LLM_*.
  let judgeCfg;
  try {
    judgeCfg = judgeEnv ? loadLLMConfig(judgeEnv) : loadLLMConfig();
  } catch (e) {
    console.error('\n[eval-consolidation] judge 模型配置缺失，未开始运行。');
    console.error(`  原因: ${e instanceof Error ? e.message : String(e)}`);
    console.error(
      judgeEnv
        ? `  Configure MEMOWEFT_${judgeEnv}_BASE_URL / _API_KEY / _MODEL in the root .env.`
        : '  Configure MEMOWEFT_LLM_BASE_URL / _API_KEY / _MODEL in .env，或传 --judge-env <PREFIX>。',
    );
    console.error('  （离线自检请跑: node bench/eval-consolidation.mjs --selftest）');
    process.exit(2);
  }

  // Subject model: MEMOWEFT_<PREFIX>_* with --subject-env, else the default MEMOWEFT_LLM_*.
  //   Must NOT fall back to judgeCfg: with --judge-env ALT and no --subject-env, reusing judgeCfg
  //   would silently run the ALT judge model against itself instead of the documented default subject.
  let subjectCfg;
  try {
    subjectCfg = subjectEnv ? loadLLMConfig(subjectEnv) : loadLLMConfig();
  } catch (e) {
    console.error(
      subjectEnv
        ? `\n[eval-consolidation] --subject-env ${subjectEnv} 所需的被测模型未配置，未开始运行。`
        : '\n[eval-consolidation] 默认被测模型（MEMOWEFT_LLM_*）未配置，未开始运行。',
    );
    console.error(`  原因: ${e instanceof Error ? e.message : String(e)}`);
    console.error(
      subjectEnv
        ? `  Configure MEMOWEFT_${subjectEnv}_BASE_URL / _API_KEY / _MODEL in the root .env.`
        : '  Configure MEMOWEFT_LLM_BASE_URL / _API_KEY / _MODEL in .env（或用 --subject-env <PREFIX> 指定被测模型）。',
    );
    process.exit(2);
  }

  const judge = withChatRetry(new OpenAICompatClient({ ...judgeCfg, temperature: 0 }));
  const meta = collectMeta(
    corpus,
    scenarios,
    { subjectCfg, judgeCfg, subjectEnv },
    { limit, discipline },
  );
  console.log(
    `[eval-consolidation] Starting ${meta.scenarioCount} scenarios · subject=${meta.model}${subjectEnv ? ` (--subject-env ${subjectEnv})` : ''} · judge=${meta.judgeModel} at temperature 0 · estimated judge calls=${meta.judgeCalls}`,
  );

  const summaries = [];
  for (const sc of scenarios) {
    const t0 = Date.now();
    console.log(`[eval-consolidation] Evaluating ${sc.id} (${sc.discipline}/${sc.lang})…`);
    const llm = withChatRetry(new OpenAICompatClient(subjectCfg)); // One subject-model client per scenario keeps usage accounting isolated; retry wraps transient network errors.
    const run = await runScenario(sc, llm);
    const checks = checkStructural(sc, run);
    let gist = { formResults: [], notResults: [], gistRecall: null, overInferRate: null };
    let evaluationError = run.error;
    if (run.error) {
      console.error(`  ✗ updateProfile failed: ${run.error}`);
    } else {
      console.log(
        `  结构断言 ${checks.filter((c) => c.pass).length}/${checks.length} · created=${run.consolidated.createdCount} corrected=${run.consolidated.corrected} conflicted=${run.consolidated.conflicted}`,
      );
      try {
        gist = await scoreGists(sc, run, judge);
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        evaluationError = `judge failed: ${message}`;
        console.error(`  judge 判分失败: ${message}`);
      }
    }
    console.log(`  用时 ${((Date.now() - t0) / 1000).toFixed(1)}s`);
    summaries.push(buildSummary(sc, run, checks, gist, evaluationError));
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
  const judgeFailureSummary = buildSummary(s1, r1, c1, g1, 'judge failed: timeout');
  ok(
    aggregate([judgeFailureSummary]).errored === 1,
    'ST-1 judge 失败进入 errored 崩溃门（不得以空评分假报成功）',
  );

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
  for (const invalid of ['嗯', 'YES and NO']) {
    let invalidJudgeRejected = false;
    try {
      await judgeMajority(new MockJudge([invalid]), 'zh', 'q');
    } catch (error) {
      invalidJudgeRejected =
        error instanceof Error &&
        error.message === 'Invalid judge response: expected exactly one YES or NO token.';
    }
    ok(invalidJudgeRejected, `judge 无效判词失败关闭（${JSON.stringify(invalid)}）`);
  }

  // ── 6) diffRuns 纯函数（离线前后对比：上升 / 下降 / 样本不同 / 提示词版本变更） ──
  console.log('[selftest] 6) diffRuns 纯函数（离线前后对比）');
  const mkRun = (o = {}) => ({
    meta: {
      commit: o.commit ?? 'abc1234',
      scenarioCount: o.scenarioCount ?? 42,
      totalScenarios: 42,
      partial: o.partial ?? false,
      model: o.model ?? 'subject-model',
      judgeModel: o.judgeModel ?? 'judge-model',
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

  // 6g) A judge-model change must produce a comparability warning (judge-driven metrics not comparable).
  const diffJudgeModel = diffRuns(
    mkRun({ structPass: 200, structTotal: 223 }),
    mkRun({ structPass: 200, structTotal: 223, judgeModel: 'other-judge' }),
  );
  ok(
    diffJudgeModel.warnings.some((w) => /判官模型变了/.test(w)),
    'diffRuns judge 模型变更 → 警示「判官模型变了」',
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

  // 8) withChatRetry：transient 网络错重试后成功、deterministic 错误立即抛、callCount 透传（含重试）
  console.log('[selftest] 8) withChatRetry 网络重试');
  {
    let flakyCalls = 0;
    const flaky = {
      get callCount() {
        return flakyCalls;
      },
      async chat() {
        flakyCalls++;
        if (flakyCalls < 3) throw new Error('fetch failed');
        return '{"ok":true}';
      },
    };
    const wrappedFlaky = withChatRetry(flaky, { tries: 3, baseMs: 0 });
    const flakyOut = await wrappedFlaky.chat([]);
    ok(
      flakyOut === '{"ok":true}',
      `withChatRetry 前两次 fetch failed→第三次成功（实际 ${flakyOut}）`,
    );
    ok(
      wrappedFlaky.callCount === 3,
      `withChatRetry callCount 透传含重试（实际 ${wrappedFlaky.callCount}）`,
    );

    let hardCalls = 0;
    const deterministic = {
      get callCount() {
        return hardCalls;
      },
      async chat() {
        hardCalls++;
        throw new Error('Unexpected LLM response format');
      },
    };
    let threw = false;
    try {
      await withChatRetry(deterministic, { tries: 3, baseMs: 0 }).chat([]);
    } catch {
      threw = true;
    }
    ok(
      threw && hardCalls === 1,
      `withChatRetry 非 transient 错误立即抛、不重试（调用 ${hardCalls} 次）`,
    );

    // 语言无关分类 + 5xx（Codex P2）：mock 首次抛 message、二次成功 → 若判为 transient 则重试拿到结果。
    const retryCase = async (label, message, expectRetry) => {
      let n = 0;
      const c = {
        get callCount() {
          return n;
        },
        async chat() {
          n++;
          if (n < 2) throw new Error(message);
          return '{"ok":true}';
        },
      };
      let succeeded = false;
      try {
        await withChatRetry(c, { tries: 2, baseMs: 0 }).chat([]);
        succeeded = true;
      } catch {
        succeeded = false;
      }
      ok(
        succeeded === expectRetry && n === (expectRetry ? 2 : 1),
        `withChatRetry ${label}（期望重试=${expectRetry}，实际调用 ${n} 次）`,
      );
    };
    await retryCase('zh 超时', 'LLM 请求超时（超过 120000ms）', true);
    await retryCase('en 5xx (503)', 'LLM request failed 503: upstream', true);
    await retryCase('zh 5xx (502)', 'LLM 请求失败 502: 网关错误', true);
    await retryCase('4xx (400) 不重试', 'LLM request failed 400: bad request', false);
  }

  // ── 9) 置信区间量具（Wilson / bootstrap）──
  // 期望值是**独立手算**的教科书值，不是拿本实现的输出回填——否则等于用实现验证自己。
  console.log('[selftest] 9) 置信区间量具（Wilson / bootstrap）');
  {
    const near = (a, b, tol = 5e-4) => Math.abs(a - b) <= tol;

    // Wilson, 5/10, z=1.96 → 中心 0.5，半宽 ≈0.26341 → [0.23659, 0.76341]
    const w5 = wilsonInterval(5, 10);
    ok(
      near(w5.lo, 0.23659) && near(w5.hi, 0.76341),
      `Wilson 5/10 = [${w5.lo.toFixed(5)}, ${w5.hi.toFixed(5)}]（期望 [0.23659, 0.76341]）`,
    );

    // Wilson 的关键性质：比例贴边时**不越界**（Wald 在这里会给出负下界）。
    const w0 = wilsonInterval(0, 10);
    ok(
      w0.lo === 0 && near(w0.hi, 0.27753),
      `Wilson 0/10 = [${w0.lo}, ${w0.hi.toFixed(5)}]（下界须为 0、不得为负）`,
    );
    // p=1 时上界数学上恰为 1（center+half 化简得 (1+z²/n)/denom = 1），但浮点会差几个 ULP，
    // 所以用容差 + 不越界断言，别拿逐位相等去卡浮点（本仓库为此红过 CI）。
    const wAll = wilsonInterval(10, 10);
    ok(
      near(wAll.hi, 1) && wAll.hi <= 1 && near(wAll.lo, 0.72247),
      `Wilson 10/10 = [${wAll.lo.toFixed(5)}, ${wAll.hi.toFixed(5)}]（期望 ≈[0.72247, 1]、上界不得越 1）`,
    );

    ok(wilsonInterval(0, 0) === null, 'Wilson 分母为 0 → null（不造假区间）');

    // 区间随样本量收窄：同比例、10 倍样本 → 明显更窄。
    const wSmall = wilsonInterval(50, 100);
    const wBig = wilsonInterval(500, 1000);
    ok(wBig.hi - wBig.lo < (wSmall.hi - wSmall.lo) / 2, '同比例下样本 ×10 → 区间宽度显著收窄');

    // 聚类 bootstrap：断言不独立时，区间必须比「把断言当独立试验」的 Wilson 宽。
    // 这正是本量具存在的理由——对合并计数套 Wilson 会谎报精度。
    // 构造 20 个场景 ×5 断言、场景内全对或全错（最强的场景内相关）。
    const clustered = Array.from({ length: 20 }, (_, i) => ({ pass: i < 16 ? 5 : 0, total: 5 }));
    const cbCI = clusterBootstrapRateCI(clustered);
    const naiveCI = wilsonInterval(80, 100); // 同是 80/100，但假装 100 次独立试验
    ok(
      cbCI.hi - cbCI.lo > (naiveCI.hi - naiveCI.lo) * 1.5,
      `聚类区间须显著宽于朴素 Wilson（聚类 ${(cbCI.hi - cbCI.lo).toFixed(3)} vs Wilson ${(naiveCI.hi - naiveCI.lo).toFixed(3)}）`,
    );
    ok(
      cbCI.lo < 0.8 && cbCI.hi > 0.8,
      `聚类区间须含真实比率 0.8（得 [${cbCI.lo.toFixed(3)}, ${cbCI.hi.toFixed(3)}]）`,
    );

    const cb2 = clusterBootstrapRateCI(clustered);
    ok(cbCI.lo === cb2.lo && cbCI.hi === cb2.hi, '聚类 bootstrap 同输入同种子 → 逐位一致');
    ok(clusterBootstrapRateCI([]) === null, '聚类 bootstrap 空输入 → null');
    ok(
      clusterBootstrapRateCI([{ pass: 0, total: 0 }]) === null,
      '聚类 bootstrap 全零分母 → null（不造假区间）',
    );
    // 全通过时 bootstrap 会退化成 [1,1]（重抽产生不出没见过的失败），那是「样本太小」
    // 被误读成「确定 100%」。此时须退回场景级 Wilson，保留不确定性。
    const cbPerfect = clusterBootstrapRateCI([
      { pass: 5, total: 5 },
      { pass: 4, total: 4 },
      { pass: 6, total: 6 },
      { pass: 5, total: 5 },
      { pass: 5, total: 5 },
      { pass: 4, total: 4 },
      { pass: 5, total: 5 },
    ]);
    const w7 = wilsonInterval(7, 7);
    ok(
      cbPerfect.hi === 1 && cbPerfect.lo < 1 && near(cbPerfect.lo, w7.lo),
      `7 场景全通过 → 退回场景级 Wilson [${cbPerfect.lo.toFixed(3)}, ${cbPerfect.hi.toFixed(3)}]，不得谎报 [1,1]`,
    );

    // bootstrap：确定性（同输入同种子恒同输出），否则区间变化分不清是数据还是抽样。
    const vals = [0.2, 0.4, 0.6, 0.8, 1.0, 0.5, 0.3, 0.9];
    const b1 = bootstrapMeanCI(vals);
    const b2 = bootstrapMeanCI(vals);
    ok(b1.lo === b2.lo && b1.hi === b2.hi, 'bootstrap 同输入同种子 → 逐位一致（可复现）');
    ok(
      b1.lo < mean(vals) && mean(vals) < b1.hi,
      `bootstrap 区间须含样本均值（均值 ${mean(vals).toFixed(3)}，区间 [${b1.lo.toFixed(3)}, ${b1.hi.toFixed(3)}]）`,
    );

    // 常数样本无离散度 → 区间退化为该常数。
    const bConst = bootstrapMeanCI([0.7, 0.7, 0.7, 0.7]);
    ok(bConst.lo === 0.7 && bConst.hi === 0.7, '常数样本 → bootstrap 区间退化为该常数');

    ok(bootstrapMeanCI([]) === null, 'bootstrap 空样本 → null');
    const bOne = bootstrapMeanCI([0.42]);
    ok(bOne.lo === 0.42 && bOne.hi === 0.42, '单样本 → 区间即该点（不谎称有不确定性范围）');

    // aggregate 出口接线：区间真的挂上去了，且类型选对（比率走 Wilson、均值走 bootstrap）。
    const mkS = (id, pass, total, gist, over) => ({
      id,
      discipline: 'd',
      lang: 'zh',
      structPass: pass,
      structTotal: total,
      gistRecall: gist,
      overInferRate: over,
      error: null,
    });
    const aggCI = aggregate([mkS('a', 5, 5, 1.0, 0.0), mkS('b', 3, 5, 0.5, 0.2)]);
    const expectCluster = clusterBootstrapRateCI([
      { pass: 5, total: 5 },
      { pass: 3, total: 5 },
    ]);
    ok(
      aggCI.structRateCI &&
        aggCI.structRateCI.lo === expectCluster.lo &&
        aggCI.structRateCI.hi === expectCluster.hi,
      'aggregate.structRateCI 走按场景聚类 bootstrap',
    );
    // 并且确实**不是**朴素 Wilson——防止哪天改回合并计数而无人察觉。
    const naive8of10 = wilsonInterval(8, 10);
    ok(
      !(near(aggCI.structRateCI.lo, naive8of10.lo) && near(aggCI.structRateCI.hi, naive8of10.hi)),
      'aggregate.structRateCI 不得退回对合并计数套 Wilson',
    );
    ok(
      aggCI.scenarioPassRate === 0.5 && aggCI.scenarioPassRateCI !== null,
      'aggregate 暴露 scenarioPassRate 及其 Wilson 区间',
    );
    ok(
      aggCI.avgGistRecallCI !== null && aggCI.avgOverInferRateCI !== null,
      'aggregate 给均值类指标挂 bootstrap 区间',
    );
    ok(
      aggCI.groups[0].structRateCI !== null,
      '分组也带结构通过率区间（小样本下宽区间本身就是结论）',
    );
  }

  if (failures === 0) {
    console.log(
      '\n[selftest] ✓ 全部通过（结构断言、judge 投票、gist 判分、run 对比、输出路由、置信区间量具）',
    );
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

// `--judge-env` selects MEMOWEFT_<PREFIX>_* for the judge model; without it the
// judge falls back to the default MEMOWEFT_LLM_*. Lets subject and judge come from
// distinct named prefixes when no default LLM entry exists.
const judgeEnvIdx = args.indexOf('--judge-env');
let judgeEnv = null;
if (judgeEnvIdx >= 0) {
  const raw = args[judgeEnvIdx + 1];
  if (!raw || raw.startsWith('--'))
    die(
      `--judge-env 需要一个 env 前缀（如 LUNA，读 MEMOWEFT_<前缀>_BASE_URL/_API_KEY/_MODEL；收到: ${raw ?? '(空)'}）。`,
    );
  judgeEnv = raw;
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
  await mainReal({ limit, discipline, outPrefix, subjectEnv, judgeEnv });
}

// Imports expose helpers only; they never read model configuration, call a model, or write a report.
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch((err) => {
    console.error('[eval-consolidation] 失败：', err);
    process.exit(1);
  });
}
