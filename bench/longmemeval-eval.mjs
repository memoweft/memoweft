/**
 * LongMemEval_S 评测器（Phase 6 · §19.1 第二套公开基准）。手动 / nightly 跑,不进 CI 护栏、不设门。
 *
 * LongMemEval(Wu 等, 2024, github.com/xiaowu0162/LongMemEval)测长期记忆:海量历史会话(haystack)+ 提问,
 *   LLM-as-judge 判答案对错。question_type:single-session-user / -assistant / -preference /
 *   temporal-reasoning / knowledge-update / multi-session;后缀 `_abs` = 该弃权(答案不在历史里)。
 *
 * ⚠ 本机现状(如实说,不粉饰):
 *   1) **无数据集** —— 仓库只带 LoCoMo;需把 LongMemEval_S 的 JSON 放本地,经 LONGMEMEVAL_PATH 指向(数据许可自查,绝不入库)。
 *   2) **无标准 judge** —— 官方用 gpt-4o;本机 .env 只有 mimo。可用 mimo 当 judge(MEMOWEFT_JUDGE_* 覆盖),
 *      但**mimo-as-judge ≠ gpt-4o,分数不可与官方/他人直接对比**,只作内部趋势。
 *   3) **认知纪律的原则性限制** —— MemoWeft 铁律 3a「助手输出永不成为证据」:本评测**只摄入 user 回合**为证据,
 *      `single-session-assistant` 类问题(答案在 assistant 说的话里)**按设计答不出**。这是定位使然、如实报告,不为刷分破纪律。
 *
 * 无数据也能验管线:`--selftest`(内联合成 2 条 + fake LLM,全离线,退出 0)。
 * 直接从 src 的 .ts import（Node ≥24 原生剥类型）。只读依赖,绝不改 src/tests。
 *
 * 用法:
 *   node bench/longmemeval-eval.mjs --selftest                 # 离线自检(无数据/无 key)——必须退出 0
 *   LONGMEMEVAL_PATH=/path/to/longmemeval_s.json node bench/longmemeval-eval.mjs --dry --limit 2   # 验 loader/检索结构(无答题)
 *   LONGMEMEVAL_PATH=... node bench/longmemeval-eval.mjs --limit 5    # 接 mimo 答题 + judge(judge 默认=答题模型 mimo,非标准)
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { OpenAICompatClient } from '../src/llm/client.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUNS_DIR = resolve(HERE, 'runs');
const LONGMEMEVAL_PATH = process.env.LONGMEMEVAL_PATH || '';
const TOP_K = 15;

const argv = process.argv.slice(2);
const DRY = argv.includes('--dry');
const SELFTEST = argv.includes('--selftest');
const getNum = (f, d) => { const i = argv.indexOf(f); return i >= 0 && argv[i + 1] ? Number(argv[i + 1]) : d; };
const LIMIT = getNum('--limit', Infinity);
const OFFSET = getNum('--offset', 0); // 分批跑:跳过前 N 题(配 --limit)。node:sqlite 累积 :memory: 连接在大单进程会 native 崩,分批规避(同 §19.2)。
const MERGE = argv.includes('--merge'); // 合并同 commit 的分片 JSON → 完整 LongMemEval 报告
const LAYER = (() => { const i = argv.indexOf('--layer'); return i >= 0 && argv[i + 1] ? argv[i + 1] : 'evidence'; })(); // evidence | cognition(updateProfile→core.recall,用消化后的画像答题;需 LLM)
const TYPE = (() => { const i = argv.indexOf('--type'); return i >= 0 && argv[i + 1] ? argv[i + 1] : ''; })(); // 只跑某 question_type(如 single-session-preference),做定向实验
const CONSOLIDATE_EVERY = getNum('--consolidate-every', 0); // cognition 层:每 N 条 user 证据 updateProfile 一次(增量消化,避一次性 distill 撞 120s)。0=一次性(长 haystack 会 timeout)。

// ── loader:LongMemEval_S 实例 → 归一 ─────────────────────────────────────────
// 实例 schema:{question_id, question_type, question, answer, question_date,
//   haystack_session_ids[], haystack_dates[], haystack_sessions[[{role,content,has_answer?}]], answer_session_ids[]}
function loadItem(it) {
  const sessions = it.haystack_sessions || [];
  const dates = it.haystack_dates || [];
  const ids = it.haystack_session_ids || [];
  const turns = [];
  sessions.forEach((sess, si) => {
    (sess || []).forEach((t, ti) => {
      turns.push({
        role: t.role, content: t.content, hasAnswer: !!t.has_answer,
        sessionId: ids[si] ?? `s${si}`, date: dates[si] || '', originId: `${ids[si] ?? si}:${ti}`,
      });
    });
  });
  return {
    id: it.question_id, type: it.question_type || 'unknown',
    isAbstention: /_abs$/.test(it.question_type || '') || /_abs$/.test(it.question_id || ''),
    question: it.question, answer: String(it.answer ?? ''),
    date: it.question_date || '', answerSessionIds: it.answer_session_ids || [], turns,
  };
}

// ── 关键词 top-k(evidence 层,与 locomo 同口径的极简检索)─────────────────────
const STOP = new Set(['a', 'an', 'the', 'of', 'to', 'in', 'on', 'at', 'is', 'was', 'were', 'and', 'or', 'for', 'did', 'do', 'what', 'when', 'where', 'who', 'how']);
const tok = (s) => String(s).toLowerCase().replace(/[^\w\s]/g, ' ').split(/\s+/).filter((w) => w && !STOP.has(w));
function retrieveTopK(evs, q, k) {
  const qt = new Set(tok(q));
  return evs.map((e) => { const et = tok(e.rawContent); let h = 0; for (const w of et) if (qt.has(w)) h++; return { e, s: h }; })
    .sort((x, y) => y.s - x.s).slice(0, k).map((x) => x.e);
}

// ── 答题 + judge(LLM-as-judge,版本化提示词)──────────────────────────────────
async function answer(llm, excerpts, question) {
  const ctx = excerpts.map((e) => e.rawContent).join('\n');
  return String(await llm.chat([
    { role: 'system', content: 'Answer the question using ONLY the conversation excerpts. If the excerpts do not contain the answer, reply exactly: No information available. Keep it short.' },
    { role: 'user', content: `Excerpts:\n${ctx}\n\nQuestion: ${question}\nAnswer:` },
  ])).trim();
}
const JUDGE_PROMPT_V1 = 'You are grading whether a model answer is correct. Reply with exactly YES or NO. YES if the model answer conveys the gold answer (or, for an abstention question, correctly declines because the info is absent). NO otherwise.';
async function judge(llm, question, gold, pred, isAbstention) {
  const g = isAbstention ? '(abstention expected — correct = declining / "no information")' : gold;
  const out = String(await llm.chat([
    { role: 'system', content: JUDGE_PROMPT_V1 },
    { role: 'user', content: `Question: ${question}\nGold: ${g}\nModel answer: ${pred}\nCorrect?` },
  ])).trim().toUpperCase();
  return out.startsWith('YES');
}

// ── runner:一个 item ─────────────────────────────────────────────────────────
async function runItem(item, llm, judgeLLM) {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  const subjectId = item.id;
  // 铁律 3a:只摄入 user 回合为 spoken 证据;assistant 回合是助手输出,不摄入。
  // cognition 层 + CONSOLIDATE_EVERY>0:边摄入边周期消化(增量),每次 distill 只处理新证据、不撞 120s LLM 超时。
  let ingested = 0;
  for (const t of item.turns) {
    if (t.role !== 'user') continue;
    const content = t.date ? `[${t.date}] ${t.content}` : t.content;
    await core.ingestUserMessage({ subjectId, content, originId: t.originId });
    if (LAYER === 'cognition' && CONSOLIDATE_EVERY > 0 && ++ingested % CONSOLIDATE_EVERY === 0) {
      try { await core.updateProfile({ subjectId }); } catch (e) { process.stderr.write(`    (incr updateProfile 跳过: ${String(e?.message || e).slice(0, 50)})\n`); }
    }
  }
  let top, evCount;
  if (LAYER === 'cognition') {
    // 收尾再消化一次剩余未消化证据(增量模式下这批很小;一次性模式=0 则这里全量、长 haystack 会 timeout)。
    try { await core.updateProfile({ subjectId }); } catch (e) { process.stderr.write(`    (final updateProfile 跳过: ${String(e?.message || e).slice(0, 50)})\n`); }
    const recalled = await core.recall({ subjectId, query: item.question });
    top = recalled.map((r) => ({ rawContent: r.content }));
    evCount = core.memory.listCognitions({ subjectId }).length;
  } else {
    const evs = core.memory.listEvidence({ subjectId });
    top = retrieveTopK(evs, item.question, TOP_K);
    evCount = evs.length;
  }
  let pred = '(dry)', correct = null;
  if (!DRY) {
    pred = await answer(llm, top, item.question);
    correct = await judge(judgeLLM, item.question, item.answer, pred, item.isAbstention);
  }
  core.close();
  const userTurns = item.turns.filter((t) => t.role === 'user').length;
  return { id: item.id, type: item.type, isAbstention: item.isAbstention, evidenceCount: evCount, userTurns, pred, correct };
}

function summarize(rows) {
  const byType = {};
  for (const r of rows) {
    byType[r.type] ??= { n: 0, correctN: 0, yes: 0 };
    byType[r.type].n++;
    if (r.correct != null) { byType[r.type].correctN++; if (r.correct) byType[r.type].yes++; }
  }
  return { total: rows.length, byType };
}

// ── --selftest:全离线,合成 2 条 + fake LLM,验 loader/摄入/检索/judge 管线 ────
async function selftest() {
  const fixture = [
    { question_id: 'demo_1', question_type: 'single-session-user', question: 'What pet does the user have?', answer: 'a cat named Mochi', question_date: '2026-01-02',
      haystack_session_ids: ['sA', 'sB'], haystack_dates: ['2026-01-01', '2026-01-01'],
      answer_session_ids: ['sA'],
      haystack_sessions: [
        [{ role: 'user', content: 'I just adopted a cat named Mochi.', has_answer: true }, { role: 'assistant', content: 'Congrats on Mochi!' }],
        [{ role: 'user', content: 'The weather is nice today.' }],
      ] },
    { question_id: 'demo_2_abs', question_type: 'single-session-user_abs', question: 'What car does the user drive?', answer: 'N/A', question_date: '2026-01-03',
      haystack_session_ids: ['sC'], haystack_dates: ['2026-01-01'], answer_session_ids: [],
      haystack_sessions: [[{ role: 'user', content: 'I love hiking on weekends.' }]] },
  ];
  const items = fixture.map(loadItem);
  // fake LLM:答题时如命中 Mochi 就答对,弃权题答 "No information available";judge 简单包含匹配。
  const fakeAnswer = { async chat(msgs) { const u = msgs[1].content; return /Mochi/i.test(u) ? 'a cat named Mochi' : 'No information available'; } };
  const fakeJudge = { async chat(msgs) { const u = msgs[1].content; const gold = /Gold: (.*)/.exec(u)?.[1] || ''; const pred = /Model answer: (.*)/.exec(u)?.[1] || ''; if (/abstention/.test(gold)) return /no information/i.test(pred) ? 'YES' : 'NO'; return pred && gold.split(' ').some((w) => w.length > 2 && pred.includes(w)) ? 'YES' : 'NO'; } };
  const rows = [];
  for (const it of items) rows.push(await runItemWith(it, fakeAnswer, fakeJudge));
  const ok1 = rows[0].correct === true; // Mochi 命中(user 回合)
  const ok2 = rows[1].correct === true; // 弃权题正确弃权
  // demo_1 共 3 回合(2 user + 1 assistant);铁律 3a:只摄入 2 条 user,assistant「Congrats」不进 → 证据=2。
  const ok3 = rows[0].evidenceCount === 2 && rows[0].userTurns === 2;
  console.log('selftest rows:', JSON.stringify(rows.map((r) => ({ id: r.id, ev: r.evidenceCount, pred: r.pred, correct: r.correct })), null, 0));
  if (!(ok1 && ok2 && ok3)) { console.error(`selftest FAIL: ok1=${ok1} ok2=${ok2} ok3=${ok3}`); process.exit(1); }
  console.log('selftest OK(loader + 只摄入user + 检索 + 答题 + judge + 弃权 全绿)');
}
// selftest 用:不走真 DRY 分支,强制答题+judge。与 runItem 同逻辑,注入 fake LLM。
async function runItemWith(item, ansLLM, judgeLLM) {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  const subjectId = item.id;
  for (const t of item.turns) { if (t.role !== 'user') continue; await core.ingestUserMessage({ subjectId, content: t.content, originId: t.originId }); }
  const evs = core.memory.listEvidence({ subjectId });
  const top = retrieveTopK(evs, item.question, TOP_K);
  const pred = await answer(ansLLM, top, item.question);
  const correct = await judge(judgeLLM, item.question, item.answer, pred, item.isAbstention);
  core.close();
  return { id: item.id, evidenceCount: evs.length, userTurns: item.turns.filter((t) => t.role === 'user').length, pred, correct };
}

// ── main ──────────────────────────────────────────────────────────────────────
async function main() {
  if (SELFTEST) { await selftest(); return; }
  if (MERGE) {
    const commit = (() => { try { return execSync('git rev-parse --short HEAD').toString().trim(); } catch { return 'nogit'; } })();
    const files = readdirSync(RUNS_DIR).filter((f) => new RegExp(`-${commit}-longmemeval-s\\d+n\\d+\\.json$`).test(f)).sort();
    if (!files.length) { console.error(`无匹配分片(先跑 per-batch --offset/--limit;commit ${commit})`); process.exit(1); }
    const byType = {}; let ansTok = 0; const ids = new Set(); let judgeModel = '?', answerModel = '?';
    for (const f of files) {
      const j = JSON.parse(readFileSync(resolve(RUNS_DIR, f), 'utf8'));
      judgeModel = j.judge; answerModel = j.answerModel; ansTok += j.answerTokens || 0;
      for (const it of j.items) ids.add(it.id);
      for (const [t, b] of Object.entries(j.byType)) { byType[t] ??= { n: 0, correctN: 0, yes: 0 }; byType[t].n += b.n; byType[t].correctN += b.correctN; byType[t].yes += b.yes; }
    }
    const totN = Object.values(byType).reduce((a, b) => a + b.n, 0);
    const totYes = Object.values(byType).reduce((a, b) => a + b.yes, 0);
    const totJudged = Object.values(byType).reduce((a, b) => a + b.correctN, 0);
    const L = [`# LongMemEval_S · 完整 (accuracy · LLM-judge, 合并 ${files.length} 批)`, ''];
    L.push(`- commit: \`${commit}\` · items: ${totN} (unique ${ids.size}) · answer: ${answerModel} · judge: ${judgeModel} · answer tokens: ${ansTok}`);
    L.push('- 摄入纪律:只 user 回合(铁律 3a);single-session-assistant 类按设计答不出。会话日期已注入。judge=gpt-4o 快照即标准口径。');
    L.push('', '| question_type | n | 正确率 |', '|---|---|---|');
    for (const t of Object.keys(byType).sort()) { const b = byType[t]; L.push(`| ${t} | ${b.n} | ${b.correctN ? (b.yes / b.correctN * 100).toFixed(1) + '%' : 'n/a'} |`); }
    L.push(`| **overall** | ${totN} | **${totJudged ? (totYes / totJudged * 100).toFixed(1) + '%' : 'n/a'}** |`);
    const report = L.join('\n') + '\n';
    console.log('\n' + report);
    if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
    const date = new Date().toISOString().slice(0, 10);
    writeFileSync(resolve(RUNS_DIR, `${date}-${commit}-longmemeval-merged.md`), report);
    console.log('written merged.');
    return;
  }
  if (!LONGMEMEVAL_PATH || !existsSync(LONGMEMEVAL_PATH)) {
    console.error(`LongMemEval 数据缺失。设 LONGMEMEVAL_PATH 指向本地 longmemeval_s.json(数据自查许可,不入库;从 github.com/xiaowu0162/LongMemEval 获取)。\n无数据也可验管线:node bench/longmemeval-eval.mjs --selftest`);
    process.exit(1);
  }
  if (LAYER === 'cognition' && DRY) { console.error('--layer cognition 需 LLM(updateProfile),不能 --dry'); process.exit(1); }
  const data = JSON.parse(readFileSync(LONGMEMEVAL_PATH, 'utf8'));
  let raw = Array.isArray(data) ? data : data.questions || [];
  if (TYPE) raw = raw.filter((q) => (q.question_type || '') === TYPE); // 定向某 question_type
  const items = raw.slice(OFFSET, OFFSET + LIMIT).map(loadItem);
  let llm = null, judgeLLM = null;
  if (!DRY) {
    llm = new OpenAICompatClient();
    // judge:配了 MEMOWEFT_JUDGE_*(BASE_URL/API_KEY/MODEL)就用独立 judge(如 gpt-4o,温度 0);否则回退答题模型 mimo(非标准)。
    //   key 只从环境变量读、绝不落代码/盘(铁律 9)。
    const jb = process.env.MEMOWEFT_JUDGE_BASE_URL, jk = process.env.MEMOWEFT_JUDGE_API_KEY, jm = process.env.MEMOWEFT_JUDGE_MODEL;
    if (jb && jk && jm) {
      judgeLLM = new OpenAICompatClient({ baseUrl: jb, apiKey: jk, model: jm, temperature: 0 });
      console.log('answer:', process.env.MEMOWEFT_LLM_MODEL || process.env.DLA_LLM_MODEL || '?', '· judge:', jm, '(独立端点·温度0)');
    } else {
      judgeLLM = llm;
      console.log('answer:', process.env.MEMOWEFT_LLM_MODEL || process.env.DLA_LLM_MODEL || '?', '· judge: mimo(=answer,非标准 gpt-4o)');
    }
  }
  const rows = [];
  for (const it of items) {
    process.stderr.write(`  item ${it.id} [${it.type}]: ${it.turns.length} turns…\n`);
    try { rows.push(await runItem(it, llm, judgeLLM)); }
    catch (e) { process.stderr.write(`  item ${it.id} FAILED(跳过): ${e?.message || e}\n`); rows.push({ id: it.id, type: it.type, isAbstention: it.isAbstention, evidenceCount: 0, userTurns: 0, pred: '(error)', correct: null, failed: true }); }
  }
  const sum = summarize(rows);
  const commit = (() => { try { return execSync('git rev-parse --short HEAD').toString().trim(); } catch { return 'nogit'; } })();

  const L = [`# LongMemEval_S ${DRY ? '(DRY 结构验证)' : '(accuracy · LLM-judge)'}`, ''];
  L.push(`- commit: \`${commit}\` · items: ${sum.total}`);
  if (!DRY) L.push(`- answer: ${process.env.MEMOWEFT_LLM_MODEL || process.env.DLA_LLM_MODEL || '?'} · judge: ${process.env.MEMOWEFT_JUDGE_MODEL || 'mimo(=answer,非标准 gpt-4o)'} · tokens(answer): ${llm?.usage?.totalTokens ?? 'n/a'}`);
  L.push(`- 检索层:${LAYER === 'cognition' ? `cognition(updateProfile→core.recall·env bge-m3${CONSOLIDATE_EVERY > 0 ? `·每${CONSOLIDATE_EVERY}条增量消化` : '·一次性'})` : 'evidence(keyword top-15)'}${TYPE ? ` · 仅 ${TYPE}` : ''} · 只 user 回合(铁律 3a)· 会话日期已注入。`);
  L.push('', `| question_type | n | ${DRY ? 'user证据均值' : '正确率'} |`, '|---|---|---|');
  for (const t of Object.keys(sum.byType).sort()) {
    const b = sum.byType[t];
    const val = DRY ? (rows.filter((r) => r.type === t).reduce((a, r) => a + r.userTurns, 0) / b.n).toFixed(1) : (b.correctN ? (b.yes / b.correctN * 100).toFixed(1) + '%' : 'n/a');
    L.push(`| ${t} | ${b.n} | ${val} |`);
  }
  const report = L.join('\n') + '\n';
  console.log('\n' + report);
  if (!DRY) {
    if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
    const date = new Date().toISOString().slice(0, 10);
    const expTag = `${LAYER === 'cognition' ? '-cognition' : ''}${TYPE ? `-${TYPE}` : ''}`;
    const batched = OFFSET > 0 || Number.isFinite(LIMIT);
    const base = `${date}-${commit}-longmemeval${expTag}${batched ? `-s${OFFSET}n${items.length}` : ''}`;
    writeFileSync(resolve(RUNS_DIR, `${base}.md`), report);
    // JSON 侧车(供 --merge 合并 + 复现):byType 原始计数 + 每题对错。
    writeFileSync(resolve(RUNS_DIR, `${base}.json`), JSON.stringify({
      commit, date, offset: OFFSET, judge: process.env.MEMOWEFT_JUDGE_MODEL || 'mimo',
      answerModel: process.env.MEMOWEFT_LLM_MODEL || process.env.DLA_LLM_MODEL || '?',
      byType: sum.byType, answerTokens: llm?.usage?.totalTokens ?? 0,
      items: rows.map((r) => ({ id: r.id, type: r.type, correct: r.correct, failed: !!r.failed })),
    }, null, 2));
    console.log('written:', `${base}.md (+ .json)`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
