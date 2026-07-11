/**
 * LoCoMo-10 冒烟评测器（Phase 6 · §19.1 公开基准接入,第一步）。手动 / nightly 跑,不进 CI 护栏、不设门。
 *
 * LoCoMo（Maharana 等, ACL 2024, arXiv:2402.17753）测 AI Agent 的超长期【人-人多会话】对话记忆。
 * 本冒烟验证「喂对话 → 召回 → 答题」链路能跑通,并暴露 MemoWeft 的召回粒度取舍:
 *   - 第一步从 **evidence 层**检索（listEvidence + 关键词 overlap top-k）,不 updateProfile、不依赖 embedder。
 *     因为 LoCoMo 多是 episodic 事实召回（某会话谁说了啥）,而 recall 默认吐画像级 cognition,单跳题会漏。
 *   - 答案由一个外挂 LLM（mimo,读根 .env）根据检索到的证据合成;核心库本身不答题。
 *   - 评分 = 归一化 partial-match F1（词重叠,LoCoMo 标准,MVP 不接 LLM judge）,按 category 分桶。
 *
 * ⚠ 数据许可:LoCoMo 是 **CC BY-NC 4.0（仅研究、非商用）**。数据文件【绝不入库、绝不打进包】——
 *   经 LOCOMO_PATH env 指向本地副本;本仓库只发【聚合分数】。可商用背书靠 MIT 的 LongMemEval（后续）。
 *
 * 直接从 src 的 .ts import（Node ≥24 原生剥类型,无需 build）。只读依赖,绝不改 src/tests。
 *
 * 用法（LOCOMO_PATH=/path/to/locomo10.json node bench/locomo-eval.mjs …）:
 *   --dry                                # 无 key:验 loader/检索/F1/结构 + evidence 层检索召回率
 *   --limit 1 --qa 5                     # 接 mimo 跑 1 sample 前 5 QA 冒烟（evidence 层·关键词）
 *   --retriever semantic --limit 1       # evidence 层·bge-m3 语义检索臂
 *   --layer cognition --limit 1 --qa 5   # cognition 层:updateProfile 消化→core.recall 召回→答题（需 LLM,不能 --dry）
 *   --no-dates …                         # 关掉会话日期注入,做 temporal A/B 对比
 *   （无 flag）                           # 全量（慢,需 mimo key）
 *
 * 纪律:排除 category 5（adversarial,Mem0 惯例）;会话日期默认注入 evidence content + occurredAt（修 temporal 偏差,可 --no-dates 关）。
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { OpenAICompatClient } from '../src/llm/client.ts';
import { loadEmbedConfig, OpenAICompatEmbedder } from '../src/retrieval/embedder.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUNS_DIR = resolve(HERE, 'runs');
const LOCOMO_PATH = process.env.LOCOMO_PATH || resolve(HERE, 'data/locomo10.json');
const TOP_K = 15; // 每题喂给答案 LLM 的证据条数

// category 编号 → 名称（LoCoMo）。5=adversarial（不可答）,评测时排除。
const CAT_NAME = { 1: 'multi-hop', 2: 'temporal', 3: 'open-domain', 4: 'single-hop', 5: 'adversarial' };

// ── CLI ─────────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
const DRY = argv.includes('--dry');
const getNum = (flag, def) => { const i = argv.indexOf(flag); return i >= 0 && argv[i + 1] ? Number(argv[i + 1]) : def; };
const LIMIT = getNum('--limit', Infinity); // 跑前 N 个 sample
const QA_LIMIT = getNum('--qa', Infinity); // 每 sample 前 M 道 QA
const RETRIEVER = (() => { const i = argv.indexOf('--retriever'); return i >= 0 && argv[i + 1] ? argv[i + 1] : 'keyword'; })(); // keyword | semantic（bge-m3 语义检索,读 env embedder）
const MAXTURNS = getNum('--max-turns', Infinity); // 冒烟限回放轮数（本地 embed 慢,减 evidence 量;keyword 与 semantic 用同值才可比）
const LAYER = (() => { const i = argv.indexOf('--layer'); return i >= 0 && argv[i + 1] ? argv[i + 1] : 'evidence'; })(); // evidence | cognition（cognition = updateProfile→core.recall,用消化后的画像答题;需 LLM,不能 --dry）
const DATES = !argv.includes('--no-dates'); // 默认注入会话日期（修 temporal 偏差,§19 剩余项）;--no-dates 关掉做 A/B 对比

// ── F1（归一化 partial-match 词重叠,LoCoMo 标准）──────────────────────────────
const STOP = new Set(['a', 'an', 'the', 'of', 'to', 'in', 'on', 'at', 'is', 'was', 'were', 'and', 'or', 'for']);
function tokenize(s) {
  return String(s).toLowerCase().replace(/[^\w\s]/g, ' ').split(/\s+/).filter((w) => w && !STOP.has(w));
}
function f1(pred, gold) {
  const p = tokenize(pred), g = tokenize(gold);
  if (!p.length || !g.length) return p.length === g.length ? 1 : 0;
  const gc = new Map(); g.forEach((w) => gc.set(w, (gc.get(w) || 0) + 1));
  let common = 0;
  for (const w of p) if (gc.get(w) > 0) { common++; gc.set(w, gc.get(w) - 1); }
  if (!common) return 0;
  const prec = common / p.length, rec = common / g.length;
  return (2 * prec * rec) / (prec + rec);
}

// ── 会话日期解析（修 temporal 偏差）───────────────────────────────────────────
// LoCoMo 的 session_N_date_time 形如 "1:56 pm on 8 May, 2023"。抽出 "8 May, 2023" 转 ISO 作 occurredAt;
//   解析失败返回 null（占位不崩，退回系统时间）。日期串本身也会注入 evidence content 供答题 LLM 直接读。
function parseLocomoDate(raw) {
  if (!raw) return null;
  const m = String(raw).match(/(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})/);
  if (!m) return null;
  const d = new Date(`${m[1]} ${m[2]} ${m[3]} UTC`);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

// ── loader:sample → { id, turns[], qa[] } ───────────────────────────────────
function loadSample(s) {
  const conv = s.conversation || {};
  const turns = [];
  const sessKeys = Object.keys(conv).filter((k) => /^session_\d+$/.test(k)).sort((a, b) => Number(a.split('_')[1]) - Number(b.split('_')[1]));
  for (const k of sessKeys) {
    const date = conv[`${k}_date_time`] || '';
    for (const t of conv[k]) turns.push({ speaker: t.speaker, diaId: t.dia_id, text: t.text, date });
  }
  const qa = (s.qa || [])
    .filter((q) => q.category !== 5) // 排除 adversarial
    .map((q) => ({ question: q.question, answer: String(q.answer ?? ''), evidence: q.evidence || [], category: q.category }));
  return { id: s.sample_id, turns, qa };
}

// ── evidence 层检索:关键词 overlap top-k ────────────────────────────────────
function retrieveTopK(evidences, question, k) {
  const qt = new Set(tokenize(question));
  const scored = evidences.map((e) => {
    const et = tokenize(e.rawContent);
    let hit = 0; for (const w of et) if (qt.has(w)) hit++;
    return { e, score: hit };
  });
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, k).map((x) => x.e);
}

// 语义检索:逐条 embed（实测本地 ollama bge-m3 的 batch embed 异常慢——5 条就 >90s、单条仅 ~1s;
//   逐条串行绕过这个 batch 退化）+ 手算 cosine。本地 CPU 下仍 ~1s/条,完整跑需 GPU 或云 embedder。
async function embedAll(embedder, texts, batch = 1) {
  const out = [];
  for (let i = 0; i < texts.length; i += batch) out.push(...(await embedder.embed(texts.slice(i, i + batch))));
  return out;
}
function cosine(a, b) {
  let d = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { d += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return d / (Math.sqrt(na) * Math.sqrt(nb) + 1e-9);
}

// ── 答案 LLM（外挂,读 .env）─────────────────────────────────────────────────
async function answer(llm, excerpts, question) {
  const ctx = excerpts.map((e) => e.rawContent).join('\n');
  const messages = [
    { role: 'system', content: 'You answer a question using ONLY the conversation excerpts. Reply with a short phrase or a few words — no full sentences, no explanation. If the excerpts do not contain the answer, reply exactly: No information available.' },
    { role: 'user', content: `Conversation excerpts:\n${ctx}\n\nQuestion: ${question}\nAnswer:` },
  ];
  const out = await llm.chat(messages);
  return String(out).trim();
}

// ── runner:一个 sample ──────────────────────────────────────────────────────
async function runSample(sample, llm) {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  const subjectId = sample.id;
  for (const t of sample.turns.slice(0, MAXTURNS)) {
    // 两个说话人的话都进 evidence（content 带上 speaker 名,保留是谁说的）。
    // DATES 时把会话日期注入 content（供答题 LLM 直接读，修 temporal 偏差）+ 设 occurredAt（语义更真）。
    const content = DATES && t.date ? `[${t.date}] ${t.speaker}: ${t.text}` : `${t.speaker}: ${t.text}`;
    const occurredAt = DATES ? parseLocomoDate(t.date) : null;
    await core.ingestUserMessage({ subjectId, content, originId: t.diaId, occurredAt: occurredAt ?? undefined });
  }
  const allEv = core.memory.listEvidence({ subjectId });

  // 检索臂配置（三选一,互斥）:
  //  - cognition:updateProfile 消化证据→认知,答题走 core.recall（真实系统召回路径,用 env bge-m3;topK=系统默认 5）。
  //  - evidence+semantic:OpenAICompatEmbedder（env bge-m3）对 evidence 逐条 embed + 手算 cosine。
  //  - evidence+keyword（默认）:关键词 overlap top-k。
  let sem = null, cog = null;
  if (LAYER === 'cognition') {
    if (DRY) { console.error('--layer cognition 需 LLM(updateProfile 消化证据),不能 --dry'); process.exit(1); }
    await core.updateProfile({ subjectId });
    // 命中追溯链:recall 回的认知 → 其溯源证据 evidenceId → 该证据 originId(=LoCoMo dia_id) → 对 gold。
    const evById = new Map(allEv.map((e) => [e.id, e.originId]));
    const cogSources = new Map(core.memory.listCognitions({ subjectId }).map((c) => [c.id, c.sources || []]));
    cog = { evById, cogSources };
  } else if (RETRIEVER === 'semantic') {
    const cfg = loadEmbedConfig();
    if (!cfg) { console.error('--retriever semantic 需配 embedder（MEMOWEFT_EMBED_* / DLA_EMBED_*）'); process.exit(1); }
    const embedder = new OpenAICompatEmbedder(cfg);
    const evVecs = await embedAll(embedder, allEv.map((e) => e.rawContent));
    sem = { embedder, evVecs };
  }

  const rows = [];
  const qaList = sample.qa.slice(0, QA_LIMIT);
  for (const q of qaList) {
    // 命中率:top-k 里是否含 gold evidence 的 dia_id（召回粒度诊断;cognition 层走溯源链还原 dia_id）。
    let top, evHit;
    if (cog) {
      const recalled = await core.recall({ subjectId, query: q.question });
      top = recalled.map((r) => ({ rawContent: r.content })); // 答题喂消化后的认知（非原始证据）
      const hitDia = new Set();
      for (const r of recalled) for (const s of (cog.cogSources.get(r.id) || [])) { const oid = cog.evById.get(s.evidenceId); if (oid) hitDia.add(oid); }
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
    if (DRY) { pred = '(dry)'; score = null; }
    else { pred = await answer(llm, top, q.question); score = f1(pred, q.answer); }
    rows.push({ category: q.category, question: q.question, gold: q.answer, pred, f1: score, evHit });
  }
  // core 侧 token（cognition 臂:updateProfile 的 distill/consolidate + recall embed 都走 core 自带池;
  //   §19.0 要求记实际 token——外挂答题 LLM 只是其一,不记 core 侧会漏算 cognition 臂的大头）。
  const coreUsage = core.usage();
  core.close();
  return { id: sample.id, evidenceCount: allEv.length, rows, coreUsage };
}

// ── 汇总 ────────────────────────────────────────────────────────────────────
function summarize(sampleResults) {
  const all = sampleResults.flatMap((s) => s.rows);
  const byCat = {};
  for (const r of all) {
    const c = r.category;
    byCat[c] ??= { n: 0, f1: 0, evHitN: 0, evHitYes: 0 };
    byCat[c].n++;
    if (r.f1 != null) byCat[c].f1 += r.f1;
    if (r.evHit != null) { byCat[c].evHitN++; if (r.evHit) byCat[c].evHitYes++; }
  }
  return { total: all.length, byCat };
}

// ── main ────────────────────────────────────────────────────────────────────
async function main() {
  if (!existsSync(LOCOMO_PATH)) {
    console.error(`LoCoMo 数据缺失:${LOCOMO_PATH}\n设 LOCOMO_PATH 指向本地 locomo10.json（数据 CC BY-NC,不入库;从 github.com/snap-research/locomo 获取）。`);
    process.exit(1);
  }
  const data = JSON.parse(readFileSync(LOCOMO_PATH, 'utf8'));
  const samples = data.slice(0, LIMIT).map(loadSample);

  let llm = null;
  if (!DRY) {
    llm = new OpenAICompatClient();
    console.log('answer model:', process.env.MEMOWEFT_LLM_MODEL || process.env.DLA_LLM_MODEL || '(env 未设)');
  }

  const results = [];
  for (const s of samples) {
    process.stderr.write(`  sample ${s.id}: ${s.turns.length} turns, ${Math.min(s.qa.length, QA_LIMIT)} QA…\n`);
    results.push(await runSample(s, llm));
  }

  const sum = summarize(results);
  const commit = (() => { try { return execSync('git rev-parse --short HEAD').toString().trim(); } catch { return 'nogit'; } })();
  const usage = llm?.usage;
  // core 侧 token 跨 sample 聚合（cognition 臂的 updateProfile/recall/embed;evidence 臂 core 未被调用则为 0）。
  const coreTot = results.reduce((a, r) => {
    const u = r.coreUsage; if (!u) return a;
    return { llm: a.llm + (u.llm?.totalTokens || 0), embed: a.embed + (u.embed?.totalTokens || 0) };
  }, { llm: 0, embed: 0 });

  // 报告
  const lines = [];
  lines.push(`# LoCoMo-10 冒烟基线 (${DRY ? 'DRY 结构验证' : 'F1'})`);
  lines.push('');
  lines.push(`- commit: \`${commit}\` · samples: ${samples.length} · QA: ${sum.total} (已排除 category 5 adversarial)`);
  if (!DRY) lines.push(`- answer model: ${process.env.MEMOWEFT_LLM_MODEL || process.env.DLA_LLM_MODEL || '?'} · 答题 tokens: ${usage ? usage.totalTokens : 'n/a'} (calls with usage: ${usage ? usage.callsWithUsage : 'n/a'})`);
  if (!DRY && (coreTot.llm || coreTot.embed)) lines.push(`- core 侧（updateProfile+recall）: llm ${coreTot.llm} + embed ${coreTot.embed} tokens · 全程合计: ${(usage?.totalTokens || 0) + coreTot.llm + coreTot.embed}`);
  const recallDesc = LAYER === 'cognition'
    ? `cognition 层 core.recall top-5（updateProfile 消化后·env bge-m3）`
    : `evidence 层 ${RETRIEVER === 'semantic' ? 'bge-m3 语义检索' : '关键词'} top-${TOP_K}（未 updateProfile）`;
  const hitColName = LAYER === 'cognition' ? 'cognition 溯源命中率' : 'evidence 层命中率';
  lines.push(`- 召回:${recallDesc} · 会话日期${DATES ? '已注入' : '未注入(--no-dates)'}`);
  lines.push('');
  lines.push(`| category | n | ${hitColName} | ` + (DRY ? '' : '平均 F1 |'));
  lines.push('|---|---|---|' + (DRY ? '' : '---|'));
  for (const c of Object.keys(sum.byCat).sort()) {
    const b = sum.byCat[c];
    const evHit = b.evHitN ? (b.evHitYes / b.evHitN * 100).toFixed(1) + '%' : 'n/a';
    const meanF1 = b.f1 != null && b.n ? (b.f1 / b.n).toFixed(3) : 'n/a';
    lines.push(`| ${c} ${CAT_NAME[c] || ''} | ${b.n} | ${evHit} | ` + (DRY ? '' : `${meanF1} |`));
  }
  const report = lines.join('\n') + '\n';

  console.log('\n' + report);

  if (!DRY) {
    if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
    const date = new Date().toISOString().slice(0, 10);
    const armTag = (LAYER === 'cognition' ? 'cognition' : `evidence-${RETRIEVER}`) + (DATES ? '' : '-nodates');
    const out = resolve(RUNS_DIR, `${date}-${commit}-locomo-${armTag}.md`);
    writeFileSync(out, report);
    console.log('written:', out);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
