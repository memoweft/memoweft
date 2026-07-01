/**
 * DLA 测试台 · 本地服务端（独立于核心代码，删掉本目录不影响 src/）。
 * 用 Node 内置 http + node:sqlite，零外部依赖（D-021）。
 *
 * 启动：npm run testbench  →  浏览器打开 http://localhost:7801
 *
 * 设计：每个 TASK 阶段在这里加一组接口，网页上手动验证。
 * 当前覆盖 TASK-01（Event 存储层）：写入 / 读取 / 列出全部 / 跑验收自检。
 */

import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { EventStore } from '../src/dla/event/store.ts';
import { OpenAICompatClient } from '../src/dla/llm/client.ts';
import { runPipeline, createConversation } from '../src/dla/pipeline/runner.ts';
import { WorkingMemory } from '../src/dla/pipeline/workingMemory.ts';
import { computeWeight } from '../src/dla/event/weight.ts';

const PORT = 7801;
const __dirname = dirname(fileURLToPath(import.meta.url));

// 测试台用一个独立的磁盘库，和将来正式 ./dla.db 分开，互不污染。
const DB_PATH = join(__dirname, 'testbench.db');
const store = new EventStore(DB_PATH);

// 大模型客户端：懒加载，缺 .env 时不阻断启动（其余 TASK-01 功能仍可用）。
let llm = null;
function getLLM() {
  if (!llm) llm = new OpenAICompatClient();
  return llm;
}

// TASK-03 对话会话：进程内维护一个带短期窗口的会话；/api/reset 可新开。
let convo = null;
function getConvo() {
  if (!convo) convo = createConversation({ store, llm: getLLM() });
  return convo;
}

/** 读取请求体并解析 JSON。 */
function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (e) {
        reject(e);
      }
    });
    req.on('error', reject);
  });
}

function sendJson(res, code, data) {
  const payload = JSON.stringify(data);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(payload);
}

/**
 * 验收自检：覆盖各任务的【离线不变量】（不调模型、快、确定）。
 * 需模型的部分（解析/召回/窗口回话）由上方"对话面板"实跑验证。
 * 返回分组结果给网页展示。
 */
function runAcceptanceChecks() {
  const checks = [];
  const push = (group, name, pass, detail) => checks.push({ group, name, pass, detail: String(detail) });
  const sample = (over = {}) => ({
    raw_content: '自检样例：决定早睡', event_form: 'explicit', is_directional_change: true,
    topic: '作息', tags: ['睡眠', '健康'], summary: '决定调整作息', sentiment: 'positive',
    source_type: 'user', temporal_orientation: 'long_term', related_event_ids: ['a', 'b'],
    correction_target_id: null, ...over,
  });
  const guard = (group, name, fn) => {
    try { const [pass, detail] = fn(); push(group, name, pass, detail); }
    catch (e) { push(group, name, false, e); }
  };

  // ── TASK-01 存储层 ──
  guard('TASK-01 存储层', '写入→读出字段一致', () => {
    const s = new EventStore(':memory:'); const inp = sample();
    const got = s.read(s.write(inp)); s.close();
    const ok = got && got.raw_content === inp.raw_content &&
      JSON.stringify(got.tags) === JSON.stringify(inp.tags) && got.is_directional_change === true;
    return [!!ok, ok ? '字段全一致' : '字段不一致'];
  });
  guard('TASK-01 存储层', '防回潮（表无 5 个禁止字段）', () => {
    const tmp = join(__dirname, `.selfcheck_${process.pid}.db`);
    const s = new EventStore(tmp); s.write(sample()); s.close();
    const insp = new DatabaseSync(tmp);
    const cols = insp.prepare('PRAGMA table_info(event)').all().map((c) => c.name); insp.close();
    import('node:fs').then((fs) => { for (const p of [tmp, `${tmp}-wal`, `${tmp}-shm`, `${tmp}-journal`]) { try { fs.rmSync(p); } catch {} } });
    const hit = ['event_type', 'pattern', 'weight', 'repetition_count', 'is_correction'].filter((f) => cols.includes(f));
    return [hit.length === 0, hit.length ? `出现：${hit}` : `列数 ${cols.length}`];
  });
  guard('TASK-01 存储层', '无物理删除接口（D-006）', () => {
    const s = new EventStore(':memory:');
    const exposed = ['delete', 'remove', 'drop', 'clear', 'truncate', 'purge'].filter((b) => typeof s[b] !== 'undefined');
    s.close();
    return [exposed.length === 0, exposed.length ? `暴露：${exposed}` : '无删除方法'];
  });

  // ── TASK-03 短期对话窗口 ──
  guard('TASK-03 对话窗口', '按 token 长度滑动（非轮数）', () => {
    const wm = new WorkingMemory(20);
    wm.push({ role: 'user', content: '甲' }); wm.push({ role: 'assistant', content: '乙' }); wm.push({ role: 'user', content: '丙' });
    const sizeBefore = wm.size;
    const evicted = wm.push({ role: 'assistant', content: '撑爆窗口的较长话语内容' });
    return [sizeBefore === 3 && evicted.length > 0, `滑出前3轮、超长后挤出${evicted.length}轮`];
  });
  guard('TASK-03 对话窗口', '短内容同轮数不滑出（证明按长度）', () => {
    const wm = new WorkingMemory(20);
    wm.push({ role: 'user', content: 'a' }); wm.push({ role: 'assistant', content: 'b' }); wm.push({ role: 'user', content: 'c' });
    return [wm.size === 3, `3 短轮未滑出（size=${wm.size}）`];
  });

  // ── TASK-04 召回（找相关·SQL，离线部分）──
  guard('TASK-04 召回', 'distinctTopics 去重 / findByTopics 命中', () => {
    const s = new EventStore(':memory:');
    s.write(sample({ topic: '志向' })); s.write(sample({ topic: '作息' })); s.write(sample({ topic: '志向' }));
    const topics = s.distinctTopics().sort();
    const hit = s.findByTopics(['志向']).length;
    const empty = s.findByTopics([]).length;
    s.close();
    const ok = topics.length === 2 && hit === 2 && empty === 0;
    return [ok, `distinct=${topics.join(',')} 志向命中${hit} 空查${empty}`];
  });

  // ── TASK-05 权重 ──
  guard('TASK-05 权重', '恒 >0 且整数（含最低档）', () => {
    const s = new EventStore(':memory:');
    const low = s.read(s.write(sample({ topic: 'L', source_type: 'observed', temporal_orientation: 'present', event_form: 'explicit' })));
    const w = computeWeight(low, s); s.close();
    return [Number.isInteger(w) && w > 0, `最低档权重=${w}`];
  });
  guard('TASK-05 权重', 'user 权重明显高于 observed', () => {
    const s = new EventStore(':memory:');
    const u = computeWeight(s.read(s.write(sample({ topic: 'U', source_type: 'user' }))), s);
    const o = computeWeight(s.read(s.write(sample({ topic: 'O', source_type: 'observed' }))), s);
    s.close();
    return [u - o >= 200, `user=${u} observed=${o} 差=${u - o}`];
  });
  guard('TASK-05 权重', '重复/关联放大（>同类）', () => {
    const s = new EventStore(':memory:');
    const solo = computeWeight(s.read(s.write(sample({ topic: 'Y', related_event_ids: [] }))), s);
    s.write(sample({ topic: 'X' })); s.write(sample({ topic: 'X' }));
    const rep = computeWeight(s.read(s.write(sample({ topic: 'X' }))), s);
    s.close();
    return [rep > solo, `重复x3=${rep} > 无重复=${solo}`];
  });
  guard('TASK-05 权重', '可复现（同输入同输出）', () => {
    const s = new EventStore(':memory:');
    const e = s.read(s.write(sample({ topic: 'R' })));
    const a = computeWeight(e, s), b = computeWeight(e, s); s.close();
    return [a === b, `两次=${a}/${b}`];
  });

  return checks;
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  try {
    // 静态首页
    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      const html = await readFile(join(__dirname, 'index.html'), 'utf8');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(html);
      return;
    }

    // 写入一条 Event
    if (req.method === 'POST' && url.pathname === '/api/write') {
      const input = await readJson(req);
      const id = store.write(input);
      sendJson(res, 200, { id });
      return;
    }

    // 读取一条
    if (req.method === 'GET' && url.pathname === '/api/read') {
      const id = url.searchParams.get('id') ?? '';
      sendJson(res, 200, { event: store.read(id) });
      return;
    }

    // 列出全部
    if (req.method === 'GET' && url.pathname === '/api/all') {
      sendJson(res, 200, { events: store.readAll() });
      return;
    }

    // TASK-03 窗口层对话：默认 1 次调用，按需召回才 2 次；滑出才沉淀
    if (req.method === 'POST' && url.pathname === '/api/chat') {
      const { text } = await readJson(req);
      const r = await getConvo().handle(String(text ?? ''));
      sendJson(res, 200, { ...r, window: getConvo().windowSnapshot() });
      return;
    }

    // 新开一个对话会话（清空短期窗口；已落库的 Event 不动，D-006）
    if (req.method === 'POST' && url.pathname === '/api/reset') {
      convo = createConversation({ store, llm: getLLM() });
      sendJson(res, 200, { ok: true });
      return;
    }

    // TASK-02 单发闭环（保留：感知→事件化→行动，每句必落库 + 两次调用）
    if (req.method === 'POST' && url.pathname === '/api/once') {
      const { text } = await readJson(req);
      const r = await runPipeline(String(text ?? ''), { store, llm: getLLM() });
      sendJson(res, 200, r);
      return;
    }

    // 跑验收自检
    if (req.method === 'GET' && url.pathname === '/api/selfcheck') {
      sendJson(res, 200, { checks: runAcceptanceChecks() });
      return;
    }

    sendJson(res, 404, { error: 'not found' });
  } catch (e) {
    sendJson(res, 400, { error: String(e) });
  }
});

server.listen(PORT, () => {
  console.log(`\n  DLA 测试台已启动 →  http://localhost:${PORT}\n  数据库：${DB_PATH}\n  (Ctrl+C 停止)\n`);
});
