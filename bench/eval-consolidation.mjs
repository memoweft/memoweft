/**
 * 固化质量评测器（Phase 2 · §15.2）。手动 / nightly 跑、不进 CI 护栏、不设门。
 *
 * 逐场景跑【真实模型】固化（updateProfile：distill→consolidate→attribute），两级比对：
 *   1) 结构性断言（程序判，先跑，不调 LLM）：从每个场景的 expect 逐项判 + 三条不变量（每场景都查）。
 *   2) 要点语义匹配（后跑）：shouldNotFormGist（及非-conflict 场景的 shouldFormGist）用 LLM-as-judge 判，
 *      温度 0、跑 3 次取多数；conflict 场景的 shouldFormGist 改走确定性硬判（看是否落库 conflicted 状态，
 *      因「只暴露不裁决」不产可判文本，见 scoreGists）。产出 gistRecall / overInferRate。
 * 汇总落 bench/consolidation-baseline.{md,json}（全量）或 bench/runs/*（部分跑）。**先入库基线，才谈优化。**
 *
 * 直接从 src 的 .ts import（Node ≥24 原生剥类型，无需 build）。只读依赖，绝不改 src/tests。
 *
 * 用法：
 *   node bench/eval-consolidation.mjs                        # 真实全量跑（慢；实测 82–141s/场景 + judge 调用，全量 42 场景约 77 分钟；由 Integrator 执行）
 *   node bench/eval-consolidation.mjs --limit N              # 只跑前 N 个场景（dev 起跑 / 冒烟）——PARTIAL，写 bench/runs/，绝不碰基线
 *   node bench/eval-consolidation.mjs --discipline <name>    # 只跑某 discipline（如 chitchat-negative）——PARTIAL，写 bench/runs/，绝不碰基线
 *   node bench/eval-consolidation.mjs --out <prefix>         # 覆盖产物前缀：写 <prefix>.md 与 <prefix>.json
 *   node bench/eval-consolidation.mjs --subject-env GPT4O    # §15.5 多模型分差：换被测模型为 MEMOWEFT_GPT4O_*（judge 仍 mimo 固定）——写 runs/、不碰 mimo 基线
 *   node bench/eval-consolidation.mjs --compare a.json b.json# 纯离线比对两份 run JSON（a=before, b=after）：不调模型、不读 .env、不加载语料
 *   node bench/eval-consolidation.mjs --selftest             # 离线自检（mock LLM + 内联 stub），必须退出 0；CI/无 key 也能验逻辑
 *
 * 纪律：被测模型 = mimo（new OpenAICompatClient() 自动读根 .env）；judge 复用同端点但【温度 0】；
 *       置信度由系统按规则自算，语料从不给期望置信数值；judge 判分提示词内联为带版本号常量（见 JUDGE_PROMPT_V1）。
 *       真实模型非确定、慢——本报告数字是 nightly / 本地跑的一次快照，不做 CI 断言。不粉饰。
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
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
import { promptVersions } from '../src/prompts/registry.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const CORPUS_PATH = resolve(HERE, '../tests/consolidation-corpus/corpus.json');
const BASELINE_MD_PATH = resolve(HERE, 'consolidation-baseline.md');
const BASELINE_JSON_PATH = resolve(HERE, 'consolidation-baseline.json');
const RUNS_DIR = resolve(HERE, 'runs');
const GEN_CMD = 'node bench/eval-consolidation.mjs';
/** judge 每个要点跑几次取多数（温度 0，防真模型抖动）。 */
const JUDGE_RUNS = 3;
/**
 * gist 评分【口径】版本。改的是「shouldForm/shouldNot 怎么算命中」这套方法学，不是 judge 措辞（那是 JUDGE_PROMPT_V1.version）。
 * 与 judgePromptVersion 同理：口径变 → gistRecall/overInferRate 的语义变 → 跨版本 run 的软/硬判分数不可直接比。
 *   v1：全部 shouldForm 走 LLM 软判（含 conflict——但 conflict 天生产不出可判文本 → 恒 0，度量盲区）。
 *   v2：conflict 场景的 shouldForm 改走【确定性硬判】（看落库是否存在在册 conflicted 认知；见 scoreGists）；其它不变。
 * diffRuns 跨版本对比时会据此高声告警（同 judgePromptVersion 的可比性纪律）。
 */
const GIST_SCORING_VERSION = 'v2';

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

/**
 * 对一个场景的 shouldForm / shouldNot 要点逐条判分，算 gistRecall / overInferRate。
 *
 * shouldForm 的判分口径按 discipline 分两种：
 *   - **conflict 场景 → 确定性硬判**（不调 judge）：conflict 的处理是「只暴露不裁决」——给旧认知打
 *     credStatus='conflicted' 标 + 挂 contradict 证据，两条都留、不落一条断言矛盾的新认知（consolidate.ts:244-255）。
 *     这条路径【天生】产不出可被 judge 从 active 认知文本里匹配到的「已暴露矛盾」句子，旧口径（judge 只看
 *     active 认知文本）对 conflict 的 shouldForm 恒判 NO → gistRecall 恒 0，是【度量盲区非质量缺陷】
 *     （诊断见 ROADMAP · CURRENT「B 靶子」· 软判方差见 D-0009）。改看落库终态：存在一条【仍 active 且
 *     credStatus==='conflicted'】的认知 = 冲突已暴露且旧认知仍留档（暴露不裁决）即命中。比看
 *     consolidated.conflicted 计数更 faithful：若模型把旧认知误删/失效（违反留档），它不再 active-conflicted
 *     → 正确判 miss。
 *   - **其它 discipline → LLM 软判**（judge 温度 0、3 次多数），沿用原口径。
 * shouldNot（overInferRate）对所有 discipline【一律 LLM 软判】不变——conflict 的「不删/不覆盖/不裁决」正是能从
 * 认知文本判的过度推断靶心，保留软判。
 */
async function scoreGists(scenario, run, judge) {
  const contents = run.active.map((c) => c.content);
  const lang = scenario.lang === 'zh' ? 'zh' : 'en';
  const forms = scenario.expect?.shouldFormGists ?? [];
  const nots = scenario.expect?.shouldNotFormGists ?? [];
  const isConflict = scenario.discipline === 'conflict';
  // conflict 的 shouldForm 确定性信号：落库里是否有「仍在册且被标 conflicted」的认知（冲突已暴露 + 旧认知留档）。
  // 【信号是场景级全局布尔】——依赖当前 conflict 语料的两个前提（新增/改 conflict 场景时须维持，否则信号失真）：
  //   ① 每个 conflict 场景恰 1 条 shouldFormGist（多条会全部坍缩成同一布尔，无法分辨哪条命中）；
  //   ② seed 不预置 credStatus==='conflicted' 的认知（否则模型什么都不做也恒命中 = 假阳）。
  // 现 7 条 conflict 语料（CC-001..007）均满足（各 1 条 shouldForm、seed 为 limited/stable）。要更精，可改成「校验本轮
  // 【新】被标 conflicted 的认知」（需 runScenario 透出 seed 初始档），当前语料不触发该需求，按铁律 4 暂不加防御码。
  const conflictSurfaced = run.active.some((c) => c.credStatus === 'conflicted');

  const formResults = [];
  for (const gist of forms) {
    if (isConflict) {
      // 确定性硬判：不调 judge（见函数 JSDoc）。命中 = 冲突已被暴露为在册的 conflicted 认知。
      formResults.push({ gist, hit: conflictSurfaced, deterministic: true, signal: 'conflicted-status' });
    } else {
      const { votes, yes } = await judgeMajority(judge, lang, JUDGE_PROMPT_V1.form(contents, gist, lang));
      formResults.push({ gist, votes, hit: yes }); // 期望 YES：命中 = 形成了该要点
    }
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
      // 注:no-over-inference 盘这条常因 fact-vs-state 定义灰区判红(一次性事件模型多标 fact、语料期望 state），
      // 这是 ContentType 缺「事件」型的已知局限、非过度推断（真靶心是 overInferRate，见 D-0019）；别据此下"质量退化"结论。
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

function collectMeta(corpus, scenarios, cfgs, filter) {
  const { subjectCfg, judgeCfg, subjectEnv } = cfgs; // subjectCfg=被测、judgeCfg=judge（§15.5 起可不同模型）
  // conflict 场景的 shouldForm 走确定性硬判、不调 judge（见 scoreGists），故不计入 judge 调用估算。
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
    model: subjectCfg.model, // 被测模型（§15.5：--subject-env 时 = gpt-4o 等，非 mimo）
    scenarioCount: scenarios.length,
    totalScenarios: corpus.scenarios.length,
    judgeCalls,
    // §15.3 归因：本轮用了哪版提示词 / judge / 模型，以及是否只跑了部分场景（决定产物落盘与可比性）。
    promptVersions: promptVersions(),
    judgePromptVersion: JUDGE_PROMPT_V1.version,
    gistScoringVersion: GIST_SCORING_VERSION, // gist 命中口径版本（conflict 硬判起 v2）；跨版本 gistRecall 不可直接比

    judgeModel: judgeCfg.model, // judge 模型（§15.5 起可与被测不同：判官固定 mimo、被测换 gpt-4o，跨臂可比）
    subjectEnv: subjectEnv ?? null, // 非 null = 换了被测模型（§15.5 分差臂）→ 落 runs/、不碰基线
    partial: Boolean(limit) || Boolean(discipline),
    filter: { limit, discipline },
  };
}

// ── 报告格式化 ──
const pct = (n) => (n === null ? 'n/a' : (n * 100).toFixed(1) + '%');
const f2 = (n) => (n === null ? 'n/a' : n.toFixed(2));
const checksInline = (checks) => checks.map((c) => `${c.pass ? '✓' : '✗'}${c.name}`).join(' · ');
/** `{consolidate:'v2',distill:'v1'}` → `consolidate@v2 · distill@v1`（按 id 字母序，确定性）。 */
const formatPromptVersions = (pv) => Object.keys(pv ?? {}).sort().map((k) => `${k}@${pv[k]}`).join(' · ');
/** 把 filter 打成人读串：`discipline=chitchat-negative, limit=5`；无过滤 → `无`。 */
function describeFilter(filter) {
  const p = [];
  if (filter?.discipline) p.push(`discipline=${filter.discipline}`);
  if (filter?.limit) p.push(`limit=${filter.limit}`);
  return p.length ? p.join(', ') : '无';
}
/** 带符号数字（正数补 +），供 Δ 展示。null → 'n/a'。 */
const signed = (n, digits) => (n === null || n === undefined ? 'n/a' : (n >= 0 ? '+' : '') + n.toFixed(digits));

function buildReport(summaries, agg, meta) {
  const L = [];
  L.push('# 固化质量评测基线报告 — Phase 2 §15.2');
  L.push('');
  if (meta.subjectEnv) {
    L.push(`> 🔬 **多模型分差臂（§15.5）：被测模型 = \`${meta.model}\`（--subject-env ${meta.subjectEnv}）、judge 固定 = \`${meta.judgeModel}\`。**`);
    L.push('> **这不是 mimo 基线**，写在 `bench/runs/` 下、不覆盖 `bench/consolidation-baseline.*`。与基线 `--compare` 时会高声提示「被测模型变了」——**结构硬指标 judge-无关、跨臂天然可比**（度量对被测模型的依赖度看这里）。');
    L.push('');
  }
  if (meta.partial) {
    L.push(`> ⚠ **PARTIAL RUN：只跑了 ${meta.scenarioCount}/${meta.totalScenarios} 场景（filter=${describeFilter(meta.filter)}）。**`);
    L.push('> **这不是基线，不可与全量基线直接比较。** 本产物写在 `bench/runs/` 下，未覆盖 `bench/consolidation-baseline.*`。');
    L.push('');
  }
  L.push('> 逐场景跑【真实模型】固化（updateProfile），两级比对：结构性断言（程序判，先跑）+ 要点语义匹配');
  L.push('> （LLM-as-judge，温度 0、3 次多数，后跑）。**先入库基线，才谈优化。** 真实模型非确定、慢，');
  L.push('> 本报告是 nightly / 本地跑的一次快照，不做 CI 断言，也不代表可复现的固定数字。');
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
  L.push(`| 被测 model（固化） | ${meta.model}（mimo，new OpenAICompatClient() 读根 .env） |`);
  L.push(`| judge model | ${meta.judgeModel}（复用同端点，温度 0 覆写） |`);
  L.push(`| judge 提示词版本 | ${meta.judgePromptVersion}（每要点 ${JUDGE_RUNS} 次取多数） |`);
  L.push(`| gist 评分口径版本 | ${meta.gistScoringVersion ?? 'v1'}（v2: conflict shouldForm 确定性硬判；跨版本 gistRecall 不可比） |`);
  L.push(`| 被测提示词版本 | ${formatPromptVersions(meta.promptVersions)} |`);
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
    for (const r of s.formResults) {
      const basis = r.deterministic ? `确定性·${r.signal}` : `票 ${r.votes.map((v) => (v ? 'Y' : 'N')).join('')}`;
      L.push(`- shouldForm ${r.hit ? '✓命中' : '✗漏形成'}（${basis}）：${r.gist}`);
    }
    for (const r of s.notResults) L.push(`- shouldNot ${r.overInferred ? '✗误踩过度推断' : '✓未过度推断'}（票 ${r.votes.map((v) => (v ? 'Y' : 'N')).join('')}）：${r.gist}`);
    L.push('');
  }
  L.push('## 备注');
  L.push('');
  L.push('- **真实模型非确定**：被测 mimo 与 judge 均为真实 LLM，重跑分数会抖；judge 已用温度 0 + 3 次多数压抖，但仍非逐位可复现。');
  L.push('- **慢 + 耗 token**：每场景实测 82–141s 固化（distill+consolidate+attribute 三次真调，见 bench/consolidation-baseline-run.log）；judge 另需 3×(要点数) 次短调用。全量 42 场景约 77 分钟，由 Integrator 在 nightly / 本地执行。');
  L.push('- **结构断言是硬判**（程序判、与模型无关），可信度高；**要点判分是软判**（LLM-as-judge），仅供趋势参考，改 judge 提示词版本后不可跨版本比。');
  L.push('- **conflict 场景的 gistRecall 是确定性硬判**（看落库是否存在 credStatus=`conflicted` 的在册认知 = 冲突已暴露且旧认知仍留档），非 LLM 软判——因为「只暴露不裁决」这条处理路径不产可被判官从认知文本匹配的句子，旧软判口径对 conflict 恒 0（度量盲区非缺陷）。其 shouldNotFormGists（不删/不覆盖/不裁决）仍为 LLM 软判。');
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
// 产物落盘路径 + commit 正文摘要
// ══════════════════════════════════════════════════════════════════════════

/**
 * 决定本轮 md/json 落哪。
 *   --out <prefix> → <prefix>.{md,json}（最高优先）。
 *   全量默认被测（!partial 且无 subjectEnv）→ bench/consolidation-baseline.{md,json}（唯一「基线」= mimo 全量，前一版由 git 提供）。
 *   部分（partial）或换了被测模型（subjectEnv，§15.5 分差臂）→ bench/runs/<date>-<sha>-consolidation-<tag>.{md,json}（绝不碰基线）。
 */
function resolveOutputPaths(meta, outPrefix) {
  if (outPrefix) return { md: `${outPrefix}.md`, json: `${outPrefix}.json` };
  // 「基线」严格指 mimo 全量：换了被测模型即便跑满 42 场景也【不是】基线，写 runs/。
  if (!meta.partial && !meta.subjectEnv) return { md: BASELINE_MD_PATH, json: BASELINE_JSON_PATH };
  mkdirSync(RUNS_DIR, { recursive: true });
  const date = meta.generatedAt.slice(0, 10); // YYYY-MM-DD
  const parts = [];
  if (meta.subjectEnv) parts.push(`subject-${String(meta.model || meta.subjectEnv).replace(/[^A-Za-z0-9._-]/g, '_')}`);
  if (meta.filter?.discipline) parts.push(meta.filter.discipline);
  if (meta.filter?.limit) parts.push(`limit${meta.filter.limit}`);
  const tag = parts.join('-') || 'partial';
  const base = resolve(RUNS_DIR, `${date}-${meta.commit}-consolidation-${tag}`);
  return { md: `${base}.md`, json: `${base}.json` };
}

/** 单跑（after 快照）的 commit 正文摘要：无 before 就不打箭头，只给当前数。 */
function commitSummarySingle(agg, meta) {
  return `结构断言 ${pct(agg.structRate)}(${agg.structPass}/${agg.structTotal})；全绿 ${agg.scenariosPassed}/${meta.scenarioCount}；errored ${agg.errored}；avgGistRecall ${f2(agg.avgGistRecall)}；avgOverInferRate ${f2(agg.avgOverInferRate)}`;
}

// ══════════════════════════════════════════════════════════════════════════
// --compare：纯离线前后对比（不调任何模型 / 不读 .env / 不加载语料）
// ══════════════════════════════════════════════════════════════════════════

const rateOf = (g) => (g && g.structTotal ? g.structPass / g.structTotal : null);
const subOrNull = (x, y) => (x === null || x === undefined || y === null || y === undefined ? null : x - y);

/**
 * 纯函数：比对两份 run JSON（a=before, b=after），返回结构化 diff。
 * 不做任何 IO / 不调模型——正因如此可离线自检（见 selftest 第 6 节）。
 * 归因核心：promptChanges 逐条列出 `consolidate: v2 → v3`；warnings 在样本/模型/judge 不一致时高声喊「不可直接比」。
 */
function diffRuns(a, b) {
  const am = a.meta ?? {};
  const bm = b.meta ?? {};
  const aAgg = a.agg ?? {};
  const bAgg = b.agg ?? {};

  const warnings = [];
  if (am.scenarioCount !== bm.scenarioCount) warnings.push(`样本不同：${am.scenarioCount} → ${bm.scenarioCount} 场景，不可直接比。`);
  if (Boolean(am.partial) !== Boolean(bm.partial)) warnings.push(`partial 不一致：before partial=${Boolean(am.partial)}, after partial=${Boolean(bm.partial)}，不可直接比。`);
  if (am.model !== bm.model) warnings.push(`被测模型变了：${am.model} → ${bm.model}，分数不可直接归因到提示词。`);
  if (am.judgePromptVersion !== bm.judgePromptVersion) warnings.push(`judge 提示词变了：${am.judgePromptVersion} → ${bm.judgePromptVersion}，软判分数（gistRecall/overInferRate）不可比。`);
  // gist 命中口径变更（缺字段 = 前 v2 的旧 run，按 v1 处理）：conflict 的 gistRecall 从软判恒 0 → 确定性 0/1，
  // 会让旧基线对比出现「无告警的 conflict gistRecall 0→1 跳变」，正是 §15.3 归因要防的度量-变化误判为质量-变化。
  const gsvA = am.gistScoringVersion ?? 'v1';
  const gsvB = bm.gistScoringVersion ?? 'v1';
  if (gsvA !== gsvB) warnings.push(`gist 评分口径变了：${gsvA} → ${gsvB}（v2 起 conflict 的 shouldForm 由 LLM 软判改确定性硬判）——conflict 的 gistRecall 与总体 avgGistRecall 不可跨版本比。`);

  // 提示词版本差异（归因核心）：并集里逐条比对。
  const pvA = am.promptVersions ?? {};
  const pvB = bm.promptVersions ?? {};
  const promptChanges = [];
  for (const id of [...new Set([...Object.keys(pvA), ...Object.keys(pvB)])].sort()) {
    if (pvA[id] !== pvB[id]) promptChanges.push({ id, before: pvA[id] ?? '(缺)', after: pvB[id] ?? '(缺)' });
  }

  const structRateBefore = aAgg.structRate ?? null;
  const structRateAfter = bAgg.structRate ?? null;
  const overall = {
    structPass: { before: aAgg.structPass ?? null, after: bAgg.structPass ?? null },
    structTotal: { before: aAgg.structTotal ?? null, after: bAgg.structTotal ?? null },
    structRate: {
      before: structRateBefore,
      after: structRateAfter,
      deltaPP: subOrNull(structRateAfter, structRateBefore) === null ? null : subOrNull(structRateAfter, structRateBefore) * 100,
    },
    scenariosPassed: { before: aAgg.scenariosPassed ?? null, after: bAgg.scenariosPassed ?? null },
    errored: { before: aAgg.errored ?? null, after: bAgg.errored ?? null },
    avgGistRecall: { before: aAgg.avgGistRecall ?? null, after: bAgg.avgGistRecall ?? null, delta: subOrNull(bAgg.avgGistRecall, aAgg.avgGistRecall) },
    avgOverInferRate: { before: aAgg.avgOverInferRate ?? null, after: bAgg.avgOverInferRate ?? null, delta: subOrNull(bAgg.avgOverInferRate, aAgg.avgOverInferRate) },
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
      structRate: { before: ra, after: rb, deltaPP: subOrNull(rb, ra) === null ? null : subOrNull(rb, ra) * 100 },
      gistRecall: { before: ga?.gistRecall ?? null, after: gb?.gistRecall ?? null, delta: subOrNull(gb?.gistRecall, ga?.gistRecall) },
      overInferRate: { before: ga?.overInferRate ?? null, after: gb?.overInferRate ?? null, delta: subOrNull(gb?.overInferRate, ga?.overInferRate) },
    };
  });

  return { warnings, promptChanges, overall, byDiscipline, meta: { before: am, after: bm } };
}

const SOFT_NOTE = '（软判·单跑高方差，仅供趋势；以结构硬指标为准 — D-0009）';

/** 把 diffRuns 的结构化 diff 打成人读表（含可比性警示 / 提示词版本差异 / 总体 / 逐 discipline）。 */
function printDiff(diff, beforePath, afterPath) {
  const mb = diff.meta.before;
  const ma = diff.meta.after;
  const tag = (m) => `commit ${m.commit ?? '?'} · ${m.scenarioCount ?? '?'} 场景${m.partial ? '(PARTIAL)' : ''} · model ${m.model ?? '?'} · judge ${m.judgePromptVersion ?? '?'}`;
  console.log('');
  console.log('════════ 固化质量评测 · 前后对比（§15.3）════════');
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

  console.log('提示词版本变更（归因核心）：');
  if (diff.promptChanges.length) {
    for (const c of diff.promptChanges) console.log(`  ▶ ${c.id}: ${c.before} → ${c.after}`);
  } else {
    console.log('  （提示词版本无变化。）');
  }
  console.log('');

  const o = diff.overall;
  const passArrow = o.structTotal.before === o.structTotal.after
    ? `${o.structPass.before}→${o.structPass.after}/${o.structTotal.after}`
    : `${o.structPass.before}/${o.structTotal.before}→${o.structPass.after}/${o.structTotal.after}`;
  console.log('── 总体（硬指标）──');
  console.log(`结构断言   ${pct(o.structRate.before)}→${pct(o.structRate.after)}  (${passArrow})  Δ${signed(o.structRate.deltaPP, 1)}pp`);
  console.log(`全绿场景   ${o.scenariosPassed.before} → ${o.scenariosPassed.after}  Δ${signed(subOrNull(o.scenariosPassed.after, o.scenariosPassed.before), 0)}`);
  console.log(`errored    ${o.errored.before} → ${o.errored.after}  Δ${signed(subOrNull(o.errored.after, o.errored.before), 0)}`);
  console.log('── 总体（软判）──');
  console.log(`avgGistRecall     ${f2(o.avgGistRecall.before)} → ${f2(o.avgGistRecall.after)}  Δ${signed(o.avgGistRecall.delta, 2)}  ${SOFT_NOTE}`);
  console.log(`avgOverInferRate  ${f2(o.avgOverInferRate.before)} → ${f2(o.avgOverInferRate.after)}  Δ${signed(o.avgOverInferRate.delta, 2)}  ${SOFT_NOTE}`);
  console.log('');

  console.log('── 按 discipline ──');
  console.log(`overInferRate 全列 + gistRecall（conflict 外）软判 ${SOFT_NOTE}；conflict 的 gistRecall 为确定性硬判（看落库 conflicted 状态）`);
  for (const g of diff.byDiscipline) {
    if (g.onlyIn) {
      console.log(`${g.discipline.padEnd(20)} （仅存在于 ${g.onlyIn}，无法对比）`);
      continue;
    }
    const structCol = `${g.structPass.before}/${g.structTotal.before}→${g.structPass.after}/${g.structTotal.after} (Δ${signed(g.structRate.deltaPP, 1)}pp)`;
    // conflict 的 gistRecall 是确定性硬判（scoreGists），别套「软判高方差」标——标出来避免与报告正文口径打架。
    const gistDet = g.discipline === 'conflict' ? '[确定性]' : '';
    const gistCol = `gistRecall ${f2(g.gistRecall.before)}→${f2(g.gistRecall.after)}(Δ${signed(g.gistRecall.delta, 2)})${gistDet}`;
    const overCol = `overInfer ${f2(g.overInferRate.before)}→${f2(g.overInferRate.after)}(Δ${signed(g.overInferRate.delta, 2)})`;
    console.log(`${g.discipline.padEnd(20)} 结构 ${structCol.padEnd(28)} ${gistCol}  ${overCol}`);
  }
  console.log('════════════════════════════════════════════');
}

/** 前后对比的 commit 正文摘要：总体 + 结构有变化的 discipline（形如 chitchat 21/35→33/35）。 */
function commitSummaryFromDiff(diff) {
  const o = diff.overall;
  const parts = [];
  const passArrow = o.structTotal.before === o.structTotal.after
    ? `${o.structPass.before}→${o.structPass.after}/${o.structTotal.after}`
    : `${o.structPass.before}/${o.structTotal.before}→${o.structPass.after}/${o.structTotal.after}`;
  parts.push(`结构断言 ${pct(o.structRate.before)}→${pct(o.structRate.after)}(${passArrow})`);
  parts.push(`全绿 ${o.scenariosPassed.before}→${o.scenariosPassed.after}`);
  for (const g of diff.byDiscipline) {
    if (g.onlyIn) continue;
    if (g.structPass.before === g.structPass.after && g.structTotal.before === g.structTotal.after) continue;
    parts.push(`${g.discipline} ${g.structPass.before}/${g.structTotal.before}→${g.structPass.after}/${g.structTotal.after}`);
  }
  return parts.join('；');
}

/** --compare 入口：读两份 JSON、算 diff、打表、给 commit 摘要，然后 exit 0。全程无模型/无 .env/无语料。 */
function runCompare(beforePath, afterPath) {
  for (const [label, p] of [['before(a)', beforePath], ['after(b)', afterPath]]) {
    if (!p) {
      console.error('[eval-consolidation] --compare 需要两个 JSON：--compare <before.json> <after.json>');
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
  console.log('── commit 正文摘要（可直接粘贴）──');
  console.log(commitSummaryFromDiff(diff));
  process.exit(0);
}

// ══════════════════════════════════════════════════════════════════════════
// 真实模式
// ══════════════════════════════════════════════════════════════════════════

async function mainReal({ limit, discipline, outPrefix, subjectEnv }) {
  if (!existsSync(CORPUS_PATH)) {
    console.error(`\n[eval-consolidation] 语料未就绪（test-author 并行产出中），无法起跑真实评测。\n  期望路径: ${CORPUS_PATH}`);
    process.exit(1);
  }
  const corpus = JSON.parse(readFileSync(CORPUS_PATH, 'utf8'));
  let scenarios = corpus.scenarios;
  if (discipline) {
    scenarios = scenarios.filter((s) => s.discipline === discipline);
    if (scenarios.length === 0) {
      const known = [...new Set(corpus.scenarios.map((s) => s.discipline))].sort().join(' · ');
      console.error(`[eval-consolidation] --discipline "${discipline}" 匹配到 0 个场景，无从评测。`);
      console.error(`  语料里的纪律: ${known}`);
      process.exit(1); // 不产出空报告：0 场景的"基线"比没有基线更危险
    }
  }
  if (limit) scenarios = scenarios.slice(0, limit); // limit 已在入口校验为 ≥1 的整数

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

  // 被测(subject)模型：默认 = 对话模型（loadLLMConfig()，mimo）；--subject-env <PREFIX> → 换成 MEMOWEFT_<PREFIX>_* 那组。
  //   judge 恒为默认（llmCfg，mimo，温度 0）不随被测变——§15.5 只动一个自变量（被测模型），judge 固定 → 跨臂可比。
  let subjectCfg;
  try {
    subjectCfg = subjectEnv ? loadLLMConfig(subjectEnv) : llmCfg;
  } catch (e) {
    console.error(`\n[eval-consolidation] BLOCKED：--subject-env ${subjectEnv} 的被测模型未配置。`);
    console.error(`  原因: ${e instanceof Error ? e.message : String(e)}`);
    console.error(`  解法: 在根 .env 配 MEMOWEFT_${subjectEnv}_BASE_URL / _API_KEY / _MODEL。`);
    process.exit(0);
  }

  const judge = new OpenAICompatClient({ ...llmCfg, temperature: 0 }); // judge 恒 = 对话模型 mimo，温度 0（跨被测臂固定）
  const meta = collectMeta(corpus, scenarios, { subjectCfg, judgeCfg: llmCfg, subjectEnv }, { limit, discipline });
  console.log(`[eval-consolidation] 真实评测起跑：${meta.scenarioCount} 场景 · 被测 model=${meta.model}${subjectEnv ? `（--subject-env ${subjectEnv}）` : ''} · judge=${meta.judgeModel} 温度 0（${meta.judgeCalls} 次 judge 调用）`);

  const summaries = [];
  for (const sc of scenarios) {
    const t0 = Date.now();
    console.log(`[eval-consolidation] 场景 ${sc.id} (${sc.discipline}/${sc.lang}) 固化中…`);
    const llm = new OpenAICompatClient(subjectCfg); // 被测：subjectCfg（默认 mimo / --subject-env 指定；每场景一新实例，计数独立）
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

  if (meta.subjectEnv) {
    console.log(`ℹ 非基线模型臂：被测=${meta.model}（--subject-env ${meta.subjectEnv}）、judge=${meta.judgeModel}（固定）。产物写在 bench/runs/，不覆盖 mimo 基线。`);
    console.log(`  与基线比：node bench/eval-consolidation.mjs --compare bench/consolidation-baseline.json <本次.json>（会高声提示「被测模型变了」；结构硬指标 judge-无关、天然可比）。`);
    console.log('');
  }
  if (meta.partial) {
    console.log(`⚠ PARTIAL RUN：只跑了 ${meta.scenarioCount}/${meta.totalScenarios} 场景（filter=${describeFilter(meta.filter)}）。`);
    console.log('  这不是基线，不可与全量基线直接比较。产物写在 bench/runs/ 下，未污染 bench/consolidation-baseline.*。');
    console.log('');
  }

  const paths = resolveOutputPaths(meta, outPrefix);
  writeFileSync(paths.md, buildReport(summaries, agg, meta), 'utf8');
  writeFileSync(paths.json, JSON.stringify({ meta, agg, summaries }, null, 2), 'utf8');
  console.log(`[eval-consolidation] 报告已写入 ${paths.md}`);
  console.log(`[eval-consolidation] 机读 JSON 已写入 ${paths.json}`);
  console.log('');
  console.log('── commit 正文摘要（可直接粘贴）──');
  console.log(commitSummarySingle(agg, meta));
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

  // ── 2b) conflict 的 shouldForm gist = 确定性硬判（看落库 conflicted 状态，不调 judge） ──
  console.log('[selftest] 2b) conflict 的 gistRecall 确定性硬判（不调 judge）');
  const s2b = {
    id: 'ST-2b',
    discipline: 'conflict',
    lang: 'zh',
    title: '冲突 gist：暴露即命中（确定性）',
    seed: [{ content: '用户喜欢早睡', contentType: 'preference', formedBy: 'stated', confidence: 600, credStatus: 'limited' }],
    messages: [{ sourceKind: 'observed', rawContent: '凌晨3点还在打游戏' }],
    expect: {
      conflict: true,
      newCognitions: { min: 0, max: 2, types: ['state'] },
      shouldFormGists: ['把矛盾行为作为观察记录，并作为与早睡偏好矛盾的反证暴露出来'],
      shouldNotFormGists: ['直接改写或删除旧的早睡偏好'],
    },
  };
  const s2bmock = new MockLLMClient({ consolidate: (cogIds, evIds) => ({ conflict: [{ cognition_id: cogIds[0], support_evidence_ids: [evIds[0]] }] }) });
  const r2b = await runScenario(s2b, s2bmock);
  ok(r2b.active.some((a) => a.credStatus === 'conflicted'), `ST-2b 落库存在在册 conflicted 认知（暴露不裁决）`);
  const judge2b = new MockJudge(['NO', 'NO', 'NO']); // 只有 shouldNot 会消耗 judge：3 次
  const g2b = await scoreGists(s2b, r2b, judge2b);
  ok(g2b.gistRecall === 1, `ST-2b conflict shouldForm 确定性命中 → gistRecall=1（实际 ${g2b.gistRecall}）`);
  ok(g2b.formResults[0]?.deterministic === true, `ST-2b conflict 的 form 判分标记为确定性（signal=${g2b.formResults[0]?.signal}）`);
  ok(judge2b.callCount === JUDGE_RUNS, `ST-2b judge 只判 shouldNot、不判 conflict 的 shouldForm（callCount=${judge2b.callCount}，期望 ${JUDGE_RUNS}）`);
  ok(g2b.overInferRate === 0, `ST-2b shouldNot 全 NO → overInferRate=0（实际 ${g2b.overInferRate}）`);

  // ── 2c) conflict 未暴露 → 确定性判 miss（证明非永真，不是恒 1） ──
  console.log('[selftest] 2c) conflict 未暴露时确定性判 miss（非永真）');
  const s2cmock = new MockLLMClient({ consolidate: () => ({ new: [], reinforce: [], correct: [], conflict: [] }) });
  const r2c = await runScenario(s2b, s2cmock); // 同场景但 mock 什么都不标
  ok(!r2c.active.some((a) => a.credStatus === 'conflicted'), `ST-2c 未暴露冲突 → 落库无 conflicted 认知`);
  const g2c = await scoreGists(s2b, r2c, new MockJudge(['NO', 'NO', 'NO']));
  ok(g2c.gistRecall === 0, `ST-2c 冲突未暴露 → conflict gist 确定性判 miss、gistRecall=0（非永真，实际 ${g2c.gistRecall}）`);

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

  // ── 6) diffRuns 纯函数（离线前后对比：上升 / 下降 / 样本不同 / 提示词版本变更） ──
  console.log('[selftest] 6) diffRuns 纯函数（离线前后对比）');
  const mkRun = (o = {}) => ({
    meta: {
      commit: o.commit ?? 'abc1234',
      scenarioCount: o.scenarioCount ?? 42,
      totalScenarios: 42,
      partial: o.partial ?? false,
      model: o.model ?? 'mimo',
      judgePromptVersion: o.judgePromptVersion ?? 'v1',
      gistScoringVersion: o.gistScoringVersion, // 不传 = 缺字段（模拟 v2 前的旧 run），diffRuns 按 'v1' 处理
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
      groups: o.groups ?? [{ discipline: 'chitchat-negative', n: 7, structPass: o.chitPass ?? 21, structTotal: 35, gistRecall: null, overInferRate: o.chitOver ?? 0.3 }],
    },
    summaries: [],
  });

  // 6a) 分数上升：198→210/223，全绿 25→30
  const up = diffRuns(
    mkRun({ structPass: 198, structTotal: 223, scenariosPassed: 25, chitPass: 21 }),
    mkRun({ structPass: 210, structTotal: 223, scenariosPassed: 30, chitPass: 33 }),
  );
  ok(up.overall.structRate.deltaPP > 0, `diffRuns 上升 → structRate ΔPP>0（实际 ${signed(up.overall.structRate.deltaPP, 1)}pp）`);
  ok(up.overall.scenariosPassed.after === 30 && up.overall.scenariosPassed.before === 25, 'diffRuns 上升 → 全绿 25→30');
  ok(up.warnings.length === 0, `diffRuns 上升 → 样本/模型/judge 一致，无警示（实际 ${up.warnings.length}）`);
  ok(commitSummaryFromDiff(up).includes('chitchat-negative 21/35→33/35'), `diffRuns 上升 → commit 摘要含 chitchat 变化（${commitSummaryFromDiff(up)}）`);

  // 6b) 分数下降：210→198/223
  const down = diffRuns(
    mkRun({ structPass: 210, structTotal: 223 }),
    mkRun({ structPass: 198, structTotal: 223 }),
  );
  ok(down.overall.structRate.deltaPP < 0, `diffRuns 下降 → structRate ΔPP<0（实际 ${signed(down.overall.structRate.deltaPP, 1)}pp）`);

  // 6c) 样本不同 → 高声警示，不可直接比
  const diffSample = diffRuns(
    mkRun({ structPass: 198, structTotal: 223, scenarioCount: 42 }),
    mkRun({ structPass: 40, structTotal: 45, scenarioCount: 7, partial: true }),
  );
  ok(diffSample.warnings.some((w) => /样本不同/.test(w)), 'diffRuns 样本不同 → 警示「样本不同」');
  ok(diffSample.warnings.some((w) => /partial 不一致/.test(w)), 'diffRuns partial 不一致 → 警示「partial 不一致」');

  // 6d) 提示词版本变更 → promptChanges 逐条列出 consolidate v2→v3
  const diffPrompt = diffRuns(
    mkRun({ structPass: 198, structTotal: 223, promptVersions: { consolidate: 'v2', distill: 'v1' } }),
    mkRun({ structPass: 210, structTotal: 223, promptVersions: { consolidate: 'v3', distill: 'v1' } }),
  );
  ok(
    diffPrompt.promptChanges.length === 1 && diffPrompt.promptChanges[0].id === 'consolidate' && diffPrompt.promptChanges[0].before === 'v2' && diffPrompt.promptChanges[0].after === 'v3',
    `diffRuns 提示词变更 → promptChanges=[consolidate v2→v3]（实际 ${JSON.stringify(diffPrompt.promptChanges)}）`,
  );

  // 6e) judge 提示词变更 → 软判不可比警示
  const diffJudge = diffRuns(mkRun({ structPass: 200, structTotal: 223, judgePromptVersion: 'v1' }), mkRun({ structPass: 200, structTotal: 223, judgePromptVersion: 'v2' }));
  ok(diffJudge.warnings.some((w) => /judge 提示词变了/.test(w)), 'diffRuns judge 版本变更 → 警示「judge 提示词变了」');

  // 6f) gist 评分口径变更（旧基线缺字段=v1 vs 新 run v2）→ 高声警示「gist 评分口径变了」、不可跨版本比
  const diffGsv = diffRuns(mkRun({ structPass: 200, structTotal: 223 }), mkRun({ structPass: 200, structTotal: 223, gistScoringVersion: 'v2' }));
  ok(diffGsv.warnings.some((w) => /gist 评分口径变了/.test(w)), 'diffRuns gist 口径变更（缺字段→v2）→ 警示「gist 评分口径变了」');
  // 同口径（都 v2）→ 不误报
  const diffGsvSame = diffRuns(mkRun({ structPass: 200, structTotal: 223, gistScoringVersion: 'v2' }), mkRun({ structPass: 200, structTotal: 223, gistScoringVersion: 'v2' }));
  ok(!diffGsvSame.warnings.some((w) => /gist 评分口径变了/.test(w)), 'diffRuns 同 gist 口径（v2=v2）→ 不误报口径变更');

  // ── 7) §15.5 被测模型注入：collectMeta 分离 subject/judge model、subjectEnv 落 runs/ 不碰基线 ──
  console.log('[selftest] 7) §15.5 被测模型注入 meta/落盘路由');
  const corpusStub = { scenarios: [{ discipline: 'conflict', expect: {} }] };
  const metaSubj = collectMeta(corpusStub, corpusStub.scenarios, { subjectCfg: { model: 'gpt-4o-x' }, judgeCfg: { model: 'mimo-x' }, subjectEnv: 'GPT4O' }, {});
  ok(metaSubj.model === 'gpt-4o-x' && metaSubj.judgeModel === 'mimo-x', `collectMeta 分离 subject/judge model（subject=${metaSubj.model} judge=${metaSubj.judgeModel}）`);
  ok(metaSubj.subjectEnv === 'GPT4O' && metaSubj.partial === false, `collectMeta 记 subjectEnv=${metaSubj.subjectEnv} 且非 partial（全量非基线）`);
  const pSubj = resolveOutputPaths(metaSubj, null);
  ok(/[\\/]runs[\\/]/.test(pSubj.json) && /subject-gpt-4o-x/.test(pSubj.json), `subject 臂落 runs/、文件名带被测模型（${pSubj.json.split(/[\\/]/).pop()}）`);
  ok(pSubj.json !== BASELINE_JSON_PATH, 'subject 臂绝不覆盖 mimo 基线');
  // 默认被测（无 subjectEnv、非 partial）仍落基线
  const metaBase = collectMeta(corpusStub, corpusStub.scenarios, { subjectCfg: { model: 'mimo' }, judgeCfg: { model: 'mimo' }, subjectEnv: null }, {});
  ok(resolveOutputPaths(metaBase, null).json === BASELINE_JSON_PATH, '默认被测全量仍落 baseline（唯一基线=mimo 全量）');

  if (failures === 0) {
    console.log('\n[selftest] ✓ 全部通过（结构断言判定、不变量、检测器负例、judge 多数投票、gist 判分、diffRuns 前后对比均离线验证）');
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

/** 退化输入直接报错早退，绝不"看起来跑了、其实跑了全量还盖了基线"。 */
function die(msg) {
  console.error(`[eval-consolidation] ${msg}`);
  process.exit(1);
}

// --limit：必须是 ≥1 的整数。
//   曾经的坑：`--limit 0` / `--limit abc` → limit 落成 0 / NaN，两者都是【假值】，于是
//   partial 判成 false、切片也不生效 → 跑满 42 场景【并覆盖 baseline】。手滑一个字符就毁基线。
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

// --subject-env <PREFIX>：换被测(subject)模型为 MEMOWEFT_<PREFIX>_* 那组（§15.5 多模型分差矩阵）。
//   judge 固定不变（仍 mimo）→ 换被测臂的结构硬指标与软判都可与 mimo 基线跨臂比。非默认被测一律写 runs/、绝不碰基线。
const subjEnvIdx = args.indexOf('--subject-env');
let subjectEnv = null;
if (subjEnvIdx >= 0) {
  const raw = args[subjEnvIdx + 1];
  if (!raw || raw.startsWith('--')) die(`--subject-env 需要一个 env 前缀（如 GPT4O，读 MEMOWEFT_<前缀>_BASE_URL/_API_KEY/_MODEL；收到: ${raw ?? '(空)'}）。`);
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
    runCompare(compare.before, compare.after); // 纯离线，内部 exit 0
    return;
  }
  await mainReal({ limit, discipline, outPrefix, subjectEnv });
}

main().catch((err) => {
  console.error('[eval-consolidation] 失败：', err);
  process.exit(1);
});
