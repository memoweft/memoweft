/**
 * 固化质量评测器（Phase 2 · §15.2）。手动 / nightly 跑、不进 CI 护栏、不设门。
 *
 * 逐场景跑【真实模型】固化（updateProfile：distill→consolidate→attribute），两级比对：
 *   1) 结构性断言（程序判，先跑，不调 LLM）：从每个场景的 expect 逐项判 + 三条不变量（每场景都查）。
 *   2) 要点语义匹配（LLM-as-judge，后跑）：每个 shouldFormGist / shouldNotFormGist 用 judge 判，
 *      温度 0、跑 3 次取多数。产出 gistRecall / overInferRate。
 * 汇总落 bench/consolidation-baseline.md。**先入库基线，才谈优化。**
 *
 * 直接从 src 的 .ts import（Node ≥24 原生剥类型，无需 build）。只读依赖，绝不改 src/tests。
 *
 * 用法：
 *   node bench/eval-consolidation.mjs             # 真实全量跑（慢，约 场景数×30s + judge 调用；由 Integrator 执行）
 *   node bench/eval-consolidation.mjs --limit 1   # 只跑前 N 个场景（dev 起跑 / 冒烟）
 *   node bench/eval-consolidation.mjs --selftest  # 离线自检（mock LLM + 内联 stub），必须退出 0；CI/无 key 也能验逻辑
 *
 * 纪律：被测模型 = mimo（new OpenAICompatClient() 自动读根 .env）；judge 复用同端点但【温度 0】；
 *       置信度由系统按规则自算，语料从不给期望置信数值；judge 判分提示词内联为带版本号常量（见 JUDGE_PROMPT_V1）。
 *       真实模型非确定、慢——本报告数字是 nightly / 本地跑的一次快照，不做 CI 断言。不粉饰。
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';
import { updateProfile } from '../src/consolidation/updateProfile.ts';
import { OpenAICompatClient, loadLLMConfig } from '../src/llm/client.ts';
import { config } from '../src/config.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const CORPUS_PATH = resolve(HERE, '../tests/consolidation-corpus/corpus.json');
const REPORT_PATH = resolve(HERE, 'consolidation-baseline.md');
const GEN_CMD = 'node bench/eval-consolidation.mjs';
/** judge 每个要点跑几次取多数（温度 0，防真模型抖动）。 */
const JUDGE_RUNS = 3;

// ══════════════════════════════════════════════════════════════════════════
// judge 判分提示词（带版本号常量）。
// 【改它必须 bump 版本号并重跑全量基线】——judge 措辞变会动 gistRecall / overInferRate 分数，
// 旧报告与新报告不可直接比。报告头会记录本版本号，供追溯是哪套提示词产出的分数。
// ══════════════════════════════════════════════════════════════════════════
const JUDGE_PROMPT_V1 = {
  version: 'v1',
  system: {
    zh: '你是严格的语义匹配判官。只回答一个词：YES 或 NO。不要解释、不要任何多余文字。',
    en: 'You are a strict semantic-match judge. Answer with exactly one word: YES or NO. No explanation, no extra text.',
  },
  /** shouldFormGist：问「已形成认知里是否有一条语义上匹配这个要点」。期望 YES。 */
  form(contents, gist, lang) {
    const list = renderCognitionList(contents, lang);
    return lang === 'zh'
      ? `已形成的认知如下：\n${list}\n\n其中是否有一条在语义上匹配这个要点：『${gist}』？只答 YES 或 NO。`
      : `The formed cognitions are:\n${list}\n\nIs there one among them that semantically matches this point: "${gist}"? Answer only YES or NO.`;
  },
  /** shouldNotFormGist：问「是否有一条断言了这个（过度推断的）要点」。期望 NO。 */
  not(contents, gist, lang) {
    const list = renderCognitionList(contents, lang);
    return lang === 'zh'
      ? `已形成的认知如下：\n${list}\n\n其中是否有一条断言了『${gist}』（这属于过度推断）？只答 YES 或 NO。`
      : `The formed cognitions are:\n${list}\n\nDoes any one of them assert "${gist}" (which would be an over-inference)? Answer only YES or NO.`;
  },
};

function renderCognitionList(contents, lang) {
  if (!contents.length) return lang === 'zh' ? '（无，没有形成任何认知）' : '(none, no cognition was formed)';
  return contents.map((c, i) => `${i + 1}. ${c}`).join('\n');
}

// ══════════════════════════════════════════════════════════════════════════
// 小工具
// ══════════════════════════════════════════════════════════════════════════

/** 解析 judge 的 YES/NO 回答（容错：大小写、带标点、夹在句子里；含糊 → 保守判 NO）。 */
function parseYesNo(ans) {
  const t = String(ans).trim().toUpperCase();
  const yi = t.search(/\bYES\b/);
  const ni = t.search(/\bNO\b/);
  const hasYes = yi >= 0;
  const hasNo = ni >= 0;
  if (hasYes && !hasNo) return true;
  if (hasNo && !hasYes) return false;
  if (hasYes && hasNo) return yi < ni; // 两个都出现 → 取先出现的
  return false; // 都没有 → 保守判 NO（shouldForm 记未命中 / shouldNot 记未过度推断）
}

const UUID_RE = /\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]/gi;
/** 抠出文本里所有 `[uuid]` 形式的 id（consolidate prompt 里 [认知id] / [证据id] 都是这形状）。 */
function uuidsIn(text) {
  return [...(text ?? '').matchAll(UUID_RE)].map((m) => m[1]);
}

/** 把 consolidate 的 user prompt 切成【现有画像】段 与【新材料】段（用于 mock 分别抠认知 id / 证据 id）。 */
function splitProfileMaterial(user) {
  for (const marker of ['【新材料】', '[New material]']) {
    const idx = user.indexOf(marker);
    if (idx >= 0) return { profilePart: user.slice(0, idx), materialPart: user.slice(idx) };
  }
  return { profilePart: '', materialPart: user };
}

// ══════════════════════════════════════════════════════════════════════════
// judge 多数投票
// ══════════════════════════════════════════════════════════════════════════

/** 用 judge 对一个问题投 JUDGE_RUNS 票（温度 0 由 judge 实例保证），取严格多数。返回 { votes, yes }。 */
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
  return { votes, yes: yesCount * 2 > JUDGE_RUNS }; // 严格多数：3 票里 ≥2 YES
}

/** 对一个场景的 shouldForm / shouldNot 要点逐条判分，算 gistRecall / overInferRate。 */
async function scoreGists(scenario, run, judge) {
  const contents = run.active.map((c) => c.content);
  const lang = scenario.lang === 'zh' ? 'zh' : 'en';
  const forms = scenario.expect?.shouldFormGists ?? [];
  const nots = scenario.expect?.shouldNotFormGists ?? [];

  const formResults = [];
  for (const gist of forms) {
    const { votes, yes } = await judgeMajority(judge, lang, JUDGE_PROMPT_V1.form(contents, gist, lang));
    formResults.push({ gist, votes, hit: yes }); // 期望 YES：命中 = 形成了该要点
  }
  const notResults = [];
  for (const gist of nots) {
    const { votes, yes } = await judgeMajority(judge, lang, JUDGE_PROMPT_V1.not(contents, gist, lang));
    notResults.push({ gist, votes, overInferred: yes }); // 期望 NO：yes = 误踩了过度推断
  }
  const gistRecall = forms.length ? formResults.filter((r) => r.hit).length / forms.length : null;
  const overInferRate = nots.length ? notResults.filter((r) => r.overInferred).length / nots.length : null;
  return { formResults, notResults, gistRecall, overInferRate };
}

// ══════════════════════════════════════════════════════════════════════════
// 跑一个场景（真实写路径）+ 结构性断言
// ══════════════════════════════════════════════════════════════════════════

/**
 * 建 :memory: 三 store、预置 seed、按序喂 message、真跑 updateProfile，收集固化产出 + 落库快照。
 * 【证据授权】：evidence 一律显式 allowCloudRead=true——被测 mimo 是云 tier，若 observed 走默认（不上云）
 *   会被隐私关静默丢弃、根本喂不进模型，评的就不是固化质量了（照 e2e 体例对 observed 显式放行，扩到全部）。
 * @param llm 被测客户端（真实=OpenAICompatClient / 自检=MockLLMClient）。
 */
async function runScenario(scenario, llm) {
  config.language = scenario.lang === 'zh' ? 'zh' : 'en'; // 影响 distill/consolidate/attribute 提示词语言
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
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
    // occurredAt 递增（放在过去 1h 内），保证多条 message 的时序，且落在 attribute 时间窗内。
    const base = Date.now() - 3600_000;
    scenario.messages.forEach((m, i) => {
      ev.put({
        subjectId: 'owner',
        sourceKind: m.sourceKind,
        hostId: 'local',
        rawContent: m.rawContent,
        occurredAt: new Date(base + i * 1000).toISOString(),
        allowCloudRead: true, // 见函数注释：让被测云模型真读到全部语料证据
      });
    });

    const result = await updateProfile('owner', {
      evidenceStore: ev,
      eventStore: evt,
      cognitionStore: cog,
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
    const cogSources = cog.all('owner').map((c) => ({ id: c.id, contentType: c.contentType, sources: cog.sourcesOf(c.id) }));
    const evidenceIds = new Set(ev.all().map((e) => e.id));

    return {
      error: null,
      consolidated: {
        created: result.consolidated.created.map((c) => ({ content: c.content, contentType: c.contentType, credStatus: c.credStatus, confidence: c.confidence })),
        createdCount: result.consolidated.created.length,
        reinforced: result.consolidated.reinforced,
        corrected: result.consolidated.corrected,
        conflicted: result.consolidated.conflicted,
        processedEvents: result.consolidated.processedEvents,
      },
      active,
      cogSources,
      evidenceIds,
      timings: result.timings,
    };
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e), consolidated: null, active: [], cogSources: [], evidenceIds: new Set(), timings: null };
  } finally {
    ev.close();
    evt.close();
    cog.close();
  }
}

/**
 * 结构性断言（程序判，不调 LLM）：
 *   from expect：conflict→conflicted≥1；correct→corrected≥1；newCognitions→created∈[min,max] 且类型⊆types；
 *                discipline==='chitchat-negative'→created===0。
 *   不变量（每场景都查，与模型判定无关）：
 *     ① 每条 active 认知 confidence ∈ (0,1000]；
 *     ② 每条 state 认知 credStatus ∈ {candidate,low}（情绪封顶）；
 *     ③ 每条认知的证据链引用的 evidenceId 都真实存在于 evidence store（证据白名单/虚构丢弃）。
 */
function checkStructural(scenario, run) {
  if (run.error) return [{ name: 'run', pass: false, detail: `updateProfile 抛错: ${run.error}` }];
  const c = run.consolidated;
  const ex = scenario.expect ?? {};
  const checks = [];

  if (ex.conflict) checks.push({ name: 'conflicted≥1', pass: c.conflicted >= 1, detail: `conflicted=${c.conflicted}` });
  if (ex.correct) checks.push({ name: 'corrected≥1', pass: c.corrected >= 1, detail: `corrected=${c.corrected}` });
  if (ex.newCognitions) {
    const { min, max, types } = ex.newCognitions;
    checks.push({ name: `created∈[${min},${max}]`, pass: c.createdCount >= min && c.createdCount <= max, detail: `created=${c.createdCount}` });
    if (types) {
      const set = new Set(types);
      const bad = [...new Set(c.created.filter((x) => !set.has(x.contentType)).map((x) => x.contentType))];
      checks.push({ name: `created类型⊆{${types.join(',')}}`, pass: bad.length === 0, detail: bad.length ? `越界类型: ${bad.join(',')}` : 'ok' });
    }
  }
  if (scenario.discipline === 'chitchat-negative') {
    checks.push({ name: 'chitchat→created===0', pass: c.createdCount === 0, detail: `created=${c.createdCount}` });
  }

  // 不变量①：confidence ∈ (0,1000]
  const confBad = run.active.filter((a) => !(a.confidence > 0 && a.confidence <= 1000));
  checks.push({ name: '不变量·confidence∈(0,1000]', pass: confBad.length === 0, detail: confBad.length ? `越界: ${confBad.map((a) => a.confidence).join(',')}` : `${run.active.length}条active合规` });
  // 不变量②：state 封顶 → credStatus ∈ {candidate,low}
  const stateBad = run.active.filter((a) => a.contentType === 'state' && !(a.credStatus === 'candidate' || a.credStatus === 'low'));
  checks.push({ name: '不变量·state封顶∈{candidate,low}', pass: stateBad.length === 0, detail: stateBad.length ? `越界档: ${stateBad.map((a) => a.credStatus).join(',')}` : 'ok' });
  // 不变量③：证据链引用真实存在
  const chainBad = [];
  for (const cs of run.cogSources) for (const s of cs.sources) if (!run.evidenceIds.has(s.evidenceId)) chainBad.push(s.evidenceId);
  checks.push({ name: '不变量·证据链引用真实存在', pass: chainBad.length === 0, detail: chainBad.length ? `虚构evidenceId ${chainBad.length}个` : 'ok' });

  return checks;
}

// ══════════════════════════════════════════════════════════════════════════
// 汇总
// ══════════════════════════════════════════════════════════════════════════

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

function collectMeta(corpus, scenarios, llmCfg) {
  const judgeCalls = scenarios.reduce(
    (a, s) => a + JUDGE_RUNS * ((s.expect?.shouldFormGists ?? []).length + (s.expect?.shouldNotFormGists ?? []).length),
    0,
  );
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
    model: llmCfg.model,
    scenarioCount: scenarios.length,
    totalScenarios: corpus.scenarios.length,
    judgeCalls,
  };
}

// ── 报告格式化 ──
const pct = (n) => (n === null ? 'n/a' : (n * 100).toFixed(1) + '%');
const f2 = (n) => (n === null ? 'n/a' : n.toFixed(2));
const checksInline = (checks) => checks.map((c) => `${c.pass ? '✓' : '✗'}${c.name}`).join(' · ');

function buildReport(summaries, agg, meta) {
  const L = [];
  L.push('# 固化质量评测基线报告 — Phase 2 §15.2');
  L.push('');
  L.push('> 逐场景跑【真实模型】固化（updateProfile），两级比对：结构性断言（程序判，先跑）+ 要点语义匹配');
  L.push('> （LLM-as-judge，温度 0、3 次多数，后跑）。**先入库基线，才谈优化。** 真实模型非确定、慢，');
  L.push('> 本报告是 nightly / 本地跑的一次快照，不做 CI 断言，也不代表可复现的固定数字。');
  L.push('');
  L.push('## 生成环境');
  L.push('');
  L.push('| 项 | 值 |');
  L.push('| --- | --- |');
  L.push(`| 生成命令 | \`${GEN_CMD}${meta.scenarioCount < meta.totalScenarios ? ` --limit ${meta.scenarioCount}` : ''}\` |`);
  L.push(`| commit | \`${meta.commit}\` |`);
  L.push(`| Node | ${meta.node} |`);
  L.push(`| 平台 | ${meta.platform}/${meta.arch} |`);
  L.push(`| 生成时间 | ${meta.generatedAt} |`);
  L.push(`| 被测 model（固化） | ${meta.model}（mimo，new OpenAICompatClient() 读根 .env） |`);
  L.push(`| judge model | ${meta.model}（复用同端点，温度 0 覆写） |`);
  L.push(`| judge 提示词版本 | ${JUDGE_PROMPT_V1.version}（每要点 ${JUDGE_RUNS} 次取多数） |`);
  L.push(`| 语料 | tests/consolidation-corpus/corpus.json（跑 ${meta.scenarioCount}/${meta.totalScenarios} 场景） |`);
  L.push('');
  L.push('## 总分');
  L.push('');
  L.push('| 指标 | 值 |');
  L.push('| --- | --- |');
  L.push(`| 结构断言通过率 | ${agg.structPass}/${agg.structTotal} = ${pct(agg.structRate)} |`);
  L.push(`| 场景全绿数（结构断言全过且无错） | ${agg.scenariosPassed}/${meta.scenarioCount} |`);
  L.push(`| 平均 gistRecall（越高越好） | ${f2(agg.avgGistRecall)} |`);
  L.push(`| 平均 overInferRate（越低越好） | ${f2(agg.avgOverInferRate)} |`);
  L.push(`| 跑挂的场景（LLM/网络错误） | ${agg.errored} |`);
  L.push('');
  L.push('## 按 discipline 分组');
  L.push('');
  L.push('| discipline | 场景数 | 结构通过率 | 平均 gistRecall | 平均 overInferRate |');
  L.push('| --- | --- | --- | --- | --- |');
  for (const g of agg.groups) {
    L.push(`| ${g.discipline} | ${g.n} | ${g.structPass}/${g.structTotal} = ${pct(g.structTotal ? g.structPass / g.structTotal : null)} | ${f2(g.gistRecall)} | ${f2(g.overInferRate)} |`);
  }
  L.push('');
  L.push('## 逐场景明细');
  L.push('');
  L.push('| id | discipline | lang | 结构 | gistRecall | overInferRate | 备注 |');
  L.push('| --- | --- | --- | --- | --- | --- | --- |');
  for (const s of summaries) {
    const note = s.error ? `错误: ${s.error.slice(0, 60)}` : s.title;
    L.push(`| ${s.id} | ${s.discipline} | ${s.lang} | ${s.structPass}/${s.structTotal} | ${f2(s.gistRecall)} | ${f2(s.overInferRate)} | ${note} |`);
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
    for (const r of s.formResults) L.push(`- shouldForm ${r.hit ? '✓命中' : '✗漏形成'}（票 ${r.votes.map((v) => (v ? 'Y' : 'N')).join('')}）：${r.gist}`);
    for (const r of s.notResults) L.push(`- shouldNot ${r.overInferred ? '✗误踩过度推断' : '✓未过度推断'}（票 ${r.votes.map((v) => (v ? 'Y' : 'N')).join('')}）：${r.gist}`);
    L.push('');
  }
  L.push('## 备注');
  L.push('');
  L.push('- **真实模型非确定**：被测 mimo 与 judge 均为真实 LLM，重跑分数会抖；judge 已用温度 0 + 3 次多数压抖，但仍非逐位可复现。');
  L.push('- **慢 + 耗 token**：每场景约 30s 固化（distill+consolidate+attribute 三次真调）；judge 另需 3×(要点数) 次短调用。全量跑由 Integrator 在 nightly / 本地执行。');
  L.push('- **结构断言是硬判**（程序判、与模型无关），可信度高；**要点判分是软判**（LLM-as-judge），仅供趋势参考，改 judge 提示词版本后不可跨版本比。');
  L.push('- **置信度由系统按规则自算**，语料从不给期望置信数值；不变量②/③ 正是守"记≠信 / 证据白名单"这两条纪律。');
  L.push('- **先入库基线，才谈优化**：本报告是 §15.2 优化前的对照基准；任何提示词 / 参数改动后，重跑本命令产出 after 报告对比。');
  L.push('');
  return L.join('\n');
}

function printConsole(summaries, agg, meta) {
  console.log('');
  console.log('════════ 固化质量评测（Phase 2 §15.2）════════');
  console.log(`commit ${meta.commit} · Node ${meta.node} · ${meta.platform}/${meta.arch} · model ${meta.model} · judge ${JUDGE_PROMPT_V1.version}`);
  console.log(`语料：${meta.scenarioCount}/${meta.totalScenarios} 场景`);
  console.log('');
  console.log(`结构断言通过率  ${agg.structPass}/${agg.structTotal} = ${pct(agg.structRate)}`);
  console.log(`场景全绿        ${agg.scenariosPassed}/${meta.scenarioCount}（跑挂 ${agg.errored}）`);
  console.log(`平均 gistRecall     ${f2(agg.avgGistRecall)}`);
  console.log(`平均 overInferRate  ${f2(agg.avgOverInferRate)}`);
  console.log('── 按 discipline ──');
  for (const g of agg.groups) {
    console.log(`${g.discipline.padEnd(20)} n=${g.n}  结构 ${g.structPass}/${g.structTotal}  gistRecall=${f2(g.gistRecall)}  overInfer=${f2(g.overInferRate)}`);
  }
  console.log('════════════════════════════════════════════');
  console.log('');
}

// ══════════════════════════════════════════════════════════════════════════
// 真实模式
// ══════════════════════════════════════════════════════════════════════════

async function mainReal({ limit, discipline }) {
  if (!existsSync(CORPUS_PATH)) {
    console.error(`\n[eval-consolidation] 语料未就绪（test-author 并行产出中），无法起跑真实评测。\n  期望路径: ${CORPUS_PATH}`);
    process.exit(1);
  }
  const corpus = JSON.parse(readFileSync(CORPUS_PATH, 'utf8'));
  let scenarios = corpus.scenarios;
  if (discipline) scenarios = scenarios.filter((s) => s.discipline === discipline);
  if (limit && limit > 0) scenarios = scenarios.slice(0, limit);

  // LLM 配置探测：无 key → 说明卡在哪，不算失败（退出 0，供 CI/无 key 环境）。
  let llmCfg;
  try {
    llmCfg = loadLLMConfig();
  } catch (e) {
    console.error('\n[eval-consolidation] BLOCKED：LLM 未配置，无法起跑真实固化。');
    console.error(`  原因: ${e instanceof Error ? e.message : String(e)}`);
    console.error('  解法: 在根 .env 配 MEMOWEFT_LLM_BASE_URL / _API_KEY / _MODEL（mimo）。');
    console.error('  （离线自检请跑: node bench/eval-consolidation.mjs --selftest）');
    process.exit(0);
  }

  const judge = new OpenAICompatClient({ ...llmCfg, temperature: 0 }); // 复用 mimo 端点，但温度 0
  const meta = collectMeta(corpus, scenarios, llmCfg);
  console.log(`[eval-consolidation] 真实评测起跑：${meta.scenarioCount} 场景 · 被测 model=${meta.model} · judge 温度 0（${meta.judgeCalls} 次 judge 调用）`);

  const summaries = [];
  for (const sc of scenarios) {
    const t0 = Date.now();
    console.log(`[eval-consolidation] 场景 ${sc.id} (${sc.discipline}/${sc.lang}) 固化中…`);
    const llm = new OpenAICompatClient(); // 被测：mimo（每场景一新实例，计数独立）
    const run = await runScenario(sc, llm);
    const checks = checkStructural(sc, run);
    let gist = { formResults: [], notResults: [], gistRecall: null, overInferRate: null };
    if (run.error) {
      console.error(`  ✗ 固化抛错: ${run.error}`);
    } else {
      console.log(`  结构断言 ${checks.filter((c) => c.pass).length}/${checks.length} · created=${run.consolidated.createdCount} corrected=${run.consolidated.corrected} conflicted=${run.consolidated.conflicted}`);
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
  writeFileSync(REPORT_PATH, buildReport(summaries, agg, meta), 'utf8');
  console.log(`[eval-consolidation] 报告已写入 ${REPORT_PATH}`);
}

// ══════════════════════════════════════════════════════════════════════════
// --selftest：离线自检（mock LLM + 内联 stub，不调真实 LLM）
// ══════════════════════════════════════════════════════════════════════════

/** mock 被测客户端：按 system 提示词判断当前是 distill / consolidate / attribute 哪一步，返回预设产出。
 *  consolidate 步会从 prompt 里抠出真实 认知id / 证据id 再引用它们（等同真模型读 id 后引用），
 *  使 consolidate 的证据白名单校验（无有效引用则跳过）真实生效。 */
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
      const evIds = uuidsIn(materialPart);
      return JSON.stringify(this.spec.consolidate ? this.spec.consolidate(cogIds, evIds) : {});
    }
    if (/可能的原因|possible causes/.test(sys)) {
      return JSON.stringify(this.spec.attribute ? this.spec.attribute() : { hypotheses: [] });
    }
    return this.spec.distill ?? '（模拟事件摘要）'; // distill：返回一段事件摘要文本
  }
}

/** mock judge：按预设答案序列逐次返回（验证多数投票逻辑）。 */
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
    expect: { newCognitions: { min: 1, max: 3, types: ['preference', 'state'] }, shouldFormGists: ['用户喜欢咖啡'], shouldNotFormGists: ['用户是咖啡师'] },
  };
  const s1mock = new MockLLMClient({
    consolidate: (_cogIds, evIds) => ({
      new: [
        { content: '用户喜欢喝美式咖啡', content_type: 'preference', formed_by: 'stated', support_evidence_ids: [evIds[0]] },
        { content: '用户今天很累', content_type: 'state', formed_by: 'stated', support_evidence_ids: [evIds[1] ?? evIds[0]] },
      ],
    }),
  });
  const r1 = await runScenario(s1, s1mock);
  ok(!r1.error, `ST-1 固化无错误（${r1.error ?? 'ok'}）`);
  const c1 = checkStructural(s1, r1);
  ok(c1.every((c) => c.pass), `ST-1 结构断言全过 → ${checksInline(c1)}`);
  ok(r1.consolidated?.createdCount === 2, `ST-1 created=2（实际 ${r1.consolidated?.createdCount}）`);
  const st1 = r1.active.find((a) => a.contentType === 'state');
  ok(st1 && (st1.credStatus === 'low' || st1.credStatus === 'candidate') && st1.confidence <= 300, `ST-1 state 封顶（cred=${st1?.credStatus} conf=${st1?.confidence}）`);
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
    seed: [{ content: '用户喜欢早睡', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' }],
    messages: [{ sourceKind: 'observed', rawContent: '凌晨3点还在打游戏' }],
    expect: { conflict: true, newCognitions: { min: 0, max: 2, types: ['state'] } },
  };
  const s2mock = new MockLLMClient({ consolidate: (cogIds, evIds) => ({ conflict: [{ cognition_id: cogIds[0], support_evidence_ids: [evIds[0]] }] }) });
  const r2 = await runScenario(s2, s2mock);
  ok(!r2.error, `ST-2 固化无错误（${r2.error ?? 'ok'}）`);
  ok(r2.consolidated?.conflicted >= 1, `ST-2 conflicted≥1（实际 ${r2.consolidated?.conflicted}）`);
  const c2 = checkStructural(s2, r2);
  ok(c2.every((c) => c.pass), `ST-2 结构断言全过 → ${checksInline(c2)}`);

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
  const s3mock = new MockLLMClient({ consolidate: () => ({ new: [], reinforce: [], correct: [], conflict: [] }) });
  const r3 = await runScenario(s3, s3mock);
  ok(r3.consolidated?.createdCount === 0, `ST-3 created=0（实际 ${r3.consolidated?.createdCount}）`);
  const c3 = checkStructural(s3, r3);
  ok(c3.every((c) => c.pass), `ST-3 结构断言全过 → ${checksInline(c3)}`);
  // 误判过度推断：judge 对 shouldNot 全票 YES → overInferRate=1
  const g3 = await scoreGists(s3, r3, new MockJudge(['YES', 'YES', 'YES']));
  ok(g3.overInferRate === 1, `ST-3 误踩过度推断时 overInferRate=1（实际 ${g3.overInferRate}）`);

  // ── 4) 检测器是【真会判失败】的（非永真）——负例 ──
  console.log('[selftest] 4) 检测器负例（证明不是永真）');
  const cNeg = checkStructural({ ...s3, expect: { conflict: true } }, r3);
  ok(cNeg.find((c) => c.name === 'conflicted≥1')?.pass === false, '对未发生的冲突判 fail');
  const fakeRun = {
    error: null,
    consolidated: { created: [], createdCount: 0, reinforced: 0, corrected: 0, conflicted: 0, processedEvents: 0 },
    active: [{ id: 'x', content: 'y', contentType: 'state', credStatus: 'stable', confidence: 900, formedBy: 'stated' }],
    cogSources: [{ id: 'x', contentType: 'state', sources: [{ evidenceId: 'ghost', relation: 'support' }] }],
    evidenceIds: new Set(), // ghost 不在其中
  };
  const cFake = checkStructural({ discipline: 'emotion-cap', expect: {} }, fakeRun);
  ok(cFake.find((c) => c.name.includes('state封顶'))?.pass === false, '不变量·state封顶 对越界档判 fail');
  ok(cFake.find((c) => c.name.includes('证据链'))?.pass === false, '不变量·证据链 对虚构 id 判 fail');
  ok(cFake.find((c) => c.name.includes('confidence'))?.pass === true, '不变量·confidence 对合法值判 pass');

  // ── 5) judge 多数投票逻辑 ──
  console.log('[selftest] 5) judge 多数投票');
  ok((await judgeMajority(new MockJudge(['YES', 'YES', 'NO']), 'zh', 'q')).yes === true, 'YES/YES/NO → 多数 YES');
  ok((await judgeMajority(new MockJudge(['NO', 'NO', 'YES']), 'zh', 'q')).yes === false, 'NO/NO/YES → 多数 NO');
  ok((await judgeMajority(new MockJudge(['YES', 'NO', 'NO']), 'zh', 'q')).yes === false, 'YES/NO/NO → 多数 NO');
  ok((await judgeMajority(new MockJudge(['YES', 'YES', 'YES']), 'zh', 'q')).yes === true, 'YES×3 → 多数 YES');
  ok(parseYesNo('YES') === true && parseYesNo('  no.') === false && parseYesNo('Yes, there is one.') === true && parseYesNo('嗯') === false, 'parseYesNo 容错（大小写/标点/含糊保守判NO）');

  if (failures === 0) {
    console.log('\n[selftest] ✓ 全部通过（结构断言判定、不变量、检测器负例、judge 多数投票、gist 判分均离线验证）');
    process.exit(0);
  }
  console.error(`\n[selftest] ✗ ${failures} 项失败`);
  process.exit(1);
}

// ══════════════════════════════════════════════════════════════════════════
// 入口
// ══════════════════════════════════════════════════════════════════════════

const args = process.argv.slice(2);
const isSelftest = args.includes('--selftest');
const limitIdx = args.indexOf('--limit');
const limit = limitIdx >= 0 && args[limitIdx + 1] ? parseInt(args[limitIdx + 1], 10) : null;
const discIdx = args.indexOf('--discipline');
const discipline = discIdx >= 0 && args[discIdx + 1] ? args[discIdx + 1] : null;

async function main() {
  if (isSelftest) {
    await selftest();
    return;
  }
  await mainReal({ limit, discipline });
}

main().catch((err) => {
  console.error('[eval-consolidation] 失败：', err);
  process.exit(1);
});
