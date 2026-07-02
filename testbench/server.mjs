/**
 * MemoWeft 测试台 · 本地服务端（阶段 0：接入真实证据层 + 回话）。地图 cell 14/15。
 *
 * 一轮：感知用户消息 → 存为证据（SQLite）→ 空召回 → 带窗口回话 → 落盘内幕。
 * 后端已是真逻辑（非占位）；召回 / 画像 / 假设 / 冲突 等后续阶段填。
 *
 * 零外部依赖：node:http + node:fs + node:sqlite。
 * 启动：npm run testbench → http://localhost:7888
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRunLogger } from '../src/obs/runLog.ts';
import { openStores } from '../src/store/openStores.ts';
import { distill } from '../src/distillation/distill.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { updateProfile } from '../src/consolidation/updateProfile.ts';
import { perceive } from '../src/pipeline/perceive.ts';
import { ingestObservations } from '../src/perception/ingest.ts';
import { activeWindowToObservation } from '../src/perception/collectors/activeWindow.ts';
import { attribute } from '../src/attribution/attribute.ts';
import { proposeAsk } from '../src/asking/proposeAsk.ts';
import { revisitConflicts } from '../src/asking/revisitConflicts.ts';
import { expire } from '../src/background/expire.ts';
import { aggregateTrends } from '../src/background/trends.ts';
import { effectiveConfidence } from '../src/background/decay.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { OpenAICompatEmbedder, loadEmbedConfig } from '../src/retrieval/embedder.ts';
import { loadLLMPool } from '../src/llm/pool.ts';
import { Conversation } from '../src/pipeline/conversation.ts';
import { config } from '../src/config.ts';
import { exportBundle, importBundle } from '../src/portable/index.ts';

// 开发者模式·热调：进程一启动就深拷一份 config 当"出厂默认"（必须在任何请求改动 config 之前拍这张快照）。
// 之后 /api/config/reset 靠它把 config 恢复原样。structuredClone 是 Node 内置，零依赖。
const configDefaults = structuredClone(config);

const PORT = 7888;
const __dirname = dirname(fileURLToPath(import.meta.url));
const LOG_DIR = join(__dirname, '..', 'logs');
const DB_PATH = join(__dirname, 'testbench-evidence.db'); // 独立库，不污染正式 ./dla.db

// 共享：证据库、空召回、LLM（懒、健壮：缺 .env 也不崩，回话时报错并落进 error）。
// 三个 store 共用【一条】连接 + 一个事务器——让 consolidate 的多步、多表写能原子化（跨连接事务是硬约束，见 store/openStores.ts）。
const stores = openStores(DB_PATH);
const store = stores.evidenceStore;
const eventStore = stores.eventStore;
const cogStore = stores.cognitionStore;
const transaction = stores.transaction; // 传给 updateProfile / consolidate 即让其写入原子化
// 治慢③：模型池（可切换模型第一块）——写路径(distill/consolidate/attribute/trends)用小快模型，对话用大模型。
const llmPool = loadLLMPool();
const llm = llmPool.for('write'); // 写路径统一用它（updateProfile / 手动 distill/consolidate/attribute/ask / trends）
const chatLLM = llmPool.for('chat'); // 对话保持大模型（质量优先，别被小模型拖低对话体验）

// 兜底：一次 LLM 网络抖动（socket closed / fetch failed 等）不该让整个测试台进程崩掉（dogfood 暴露）。
process.on('unhandledRejection', (e) => console.error('[兜底] 未处理的 rejection（服务继续）：', e instanceof Error ? e.message : e));

// 召回：配了 MEMOWEFT_EMBED_*（或兼容 DLA_EMBED_*）用向量召回；否则降级为空召回（回话不注入画像，不报错）。
const embedConfig = loadEmbedConfig();
const retriever = embedConfig
  ? new VectorRetriever(DB_PATH, new OpenAICompatEmbedder(embedConfig))
  : new NullRetriever();
if (!embedConfig) console.log('  ⚠️ 未配 MEMOWEFT_EMBED_*（或兼容 DLA_EMBED_*），召回降级为空（回话不注入画像）');

// （旧 makeLLM 已并入 loadLLMPool：缺 .env 不崩、缺写路径小模型回退对话模型——见 src/llm/pool.ts。）

// 每会话一个 Conversation（窗口）+ logger；/api/reset 新开。
let sessionId = `s-${Date.now()}`;
let convo = new Conversation({ store, retriever, cognitionStore: cogStore, llm: chatLLM });
let logger = createRunLogger({ dir: LOG_DIR, sessionId });

function newSession() {
  sessionId = `s-${Date.now()}`;
  convo = new Conversation({ store, retriever, cognitionStore: cogStore, llm: chatLLM });
  logger = createRunLogger({ dir: LOG_DIR, sessionId });
}

// ── 后台自动更新画像（空闲防抖触发）──
// 写路径（提炼画像）是重活、要调几次大模型，故意不挡聊天：聊天即记证据，停下来后台慢慢消化。
// 共用一把锁：同一用户的画像更新【不能并发】（否则重复消化同一批事件、markConsolidated 竞争）。
// 核心①攒批（2026-07-01）：旧"停手7秒就更新"太勤又费，改为攒够 config.profileUpdate.batchSize 条 / 空闲 idleMinutes 才更新。
let profileUpdating = false;
let bgTimer = null;
let bgLast = null; // 上次更新结果摘要，供前端轮询显示
let seedProgress = { running: false, step: 0, total: 6, label: '空闲' }; // dogfood 灌数据进度（脚本上报、前端轮询画进度条）

async function runProfileUpdate(trigger = 'background') {
  if (profileUpdating) return null; // 正忙 → 调用方决定重排/提示
  profileUpdating = true;
  try {
    const r = await updateProfile(config.identity.subjectId, {
      evidenceStore: store, eventStore, cognitionStore: cogStore, retriever, llm, transaction,
    });
    // 周期后台：跨会话趋势聚合（规则筛够频才调模型）+ 自然过期（临时类老了标失效）。
    const trd = await aggregateTrends(config.identity.subjectId, { evidenceStore: store, cognitionStore: cogStore, llm });
    const exp = expire(config.identity.subjectId, { cognitionStore: cogStore });
    const c = r.consolidated;
    bgLast = {
      at: new Date().toISOString(),
      created: c.created.length, reinforced: c.reinforced, corrected: c.corrected,
      conflicted: c.conflicted, hypotheses: r.attributed.hypotheses.length,
      trends: trd.trends.length, expired: exp.expired, indexError: r.indexError,
    };
    // ②治慢落盘：各步耗时 + 摘要（AGENTS.md"内幕必落盘"；最慢的写路径以前没诊断日志）。
    logger.appendProfileUpdate({
      trigger,
      timings: r.timings,
      summary: {
        pendingCount: r.distilled.pendingCount,
        created: c.created.length, reinforced: c.reinforced, corrected: c.corrected,
        conflicted: c.conflicted, hypotheses: r.attributed.hypotheses.length,
        trends: trd.trends.length, expired: exp.expired,
      },
      llmCalls: r.distilled.llmCalls + c.llmCalls + r.attributed.llmCalls,
      indexError: r.indexError,
    });
    return r;
  } catch (e) {
    logger.appendProfileUpdate({ trigger, error: e instanceof Error ? e.message : String(e) });
    throw e;
  } finally {
    profileUpdating = false;
  }
}

// 核心①攒批触发（治"勤"，落实原则一/二）：每次聊完调这里累加计数——
// 攒够 batchSize 条新对话【立即】排更新；否则重置空闲计时、歇够 idleMinutes 没动静再更新一次。先到先触发。
let pendingSinceUpdate = 0;
function scheduleBackgroundUpdate() {
  pendingSinceUpdate++;
  const { batchSize, idleMinutes } = config.profileUpdate;
  if (pendingSinceUpdate >= batchSize) {
    if (bgTimer) { clearTimeout(bgTimer); bgTimer = null; } // 攒够一批 → 立刻排，清掉空闲计时
    void triggerProfileUpdate();
  } else {
    if (bgTimer) clearTimeout(bgTimer); // 又聊了 → 重置空闲计时
    bgTimer = setTimeout(() => { bgTimer = null; void triggerProfileUpdate(); }, idleMinutes * 60000);
  }
}
async function triggerProfileUpdate() {
  try {
    const r = await runProfileUpdate('background');
    if (r === null) { // 手动更新正占着锁 → 过 10s 再排（保留计数，别丢这批）
      if (bgTimer) clearTimeout(bgTimer);
      bgTimer = setTimeout(() => { bgTimer = null; void triggerProfileUpdate(); }, 10000);
      return;
    }
    pendingSinceUpdate = 0; // 更新成功 → 计数清零，重新攒下一批
  } catch (e) {
    // 一次 LLM 网络抖动不该崩服务（错误已在 runProfileUpdate 里落盘）。
    console.error('后台更新画像失败（已兜底，不崩服务）：', e instanceof Error ? e.message : e);
  }
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try {
        const body = Buffer.concat(chunks).toString('utf8');
        // 字符集护栏：非法 UTF-8 字节解码后必出现 U+FFFD(�) → 拒收，防乱码入库。
        // （事故：Windows cmd 的 curl 发中文默认 GBK，被按 UTF-8 解 → 乱码写进证据库。
        //   浏览器 fetch 恒为 UTF-8 不受影响；命令行注入请用 UTF-8 编码的请求体。）
        if (body.includes('�')) {
          reject(new Error('请求体不是合法 UTF-8（Windows cmd 的 curl 会按 GBK 发中文；请改用测试台界面，或以 UTF-8 编码发送）'));
          return;
        }
        resolve(body ? JSON.parse(body) : {});
      } catch (e) {
        reject(e);
      }
    });
    req.on('error', reject);
  });
}

function sendJson(res, code, data) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

// 开发者模式·热调：按 'a.b.c' 路径深入 config，走到倒数第二层父对象再赋最后一段的值。
// 返回 null 表示成功；返回错误字符串表示失败（中途某段不存在就报错、绝不自动建键）。
// 注意：往【已有的 config 对象】里改字段，src 各模块运行时现读 config 才能即时生效——绝不整体替换引用。
function setByPath(obj, path, value) {
  const segs = String(path).split('.');
  if (segs.length === 0 || segs.some((s) => s === '')) return `非法路径：${path}`;
  let cur = obj;
  // 走到倒数第二层：中间每一段都必须是已存在的对象，否则报错不建键。
  for (let i = 0; i < segs.length - 1; i++) {
    const k = segs[i];
    if (cur == null || typeof cur !== 'object' || !(k in cur)) return `路径不存在：${segs.slice(0, i + 1).join('.')}`;
    cur = cur[k];
  }
  const last = segs[segs.length - 1];
  if (cur == null || typeof cur !== 'object' || !(last in cur)) return `路径不存在：${path}`;
  cur[last] = value; // value 原样写入（类型由前端负责），后端保持薄。
  return null;
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  try {
    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      const html = await readFile(join(__dirname, 'index.html'), 'utf8');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(html);
      return;
    }

    // 静态 ES 模块：index.html 里 `import { CONFIG_META } from './config-meta.js'` 要能取到，
    // 否则开发者模式「参数旋钮/设置面板」拿不到元数据、渲不出来。只白名单这一个文件、只读、不接受路径穿越。
    if (req.method === 'GET' && url.pathname === '/config-meta.js') {
      const js = await readFile(join(__dirname, 'config-meta.js'), 'utf8');
      res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' });
      res.end(js);
      return;
    }

    // 一轮对话：真实存证据 + 回话 → 落盘内幕 → 返回完整记录给透视区
    if (req.method === 'POST' && url.pathname === '/api/chat') {
      const { text, originId } = await readJson(req);
      const outcome = await convo.handle(String(text ?? ''), { originId: originId ?? null });
      const record = logger.appendTurn({
        userInput: String(text ?? ''),
        reply: outcome.reply,
        evidence: [{ id: outcome.storedEvidence.id, summary: outcome.storedEvidence.summary }],
        recall: outcome.recall.map((r) => ({ summary: r.content, score: Math.round(r.score * 1000) })),
        llmCalls: outcome.llmCalls,
        error: outcome.error,
      });
      sendJson(res, 200, { record, sessionId, logFile: logger.file });
      scheduleBackgroundUpdate(); // 聊完一轮排上后台消化（防抖：停手 7 秒才真跑，不挡这次回话）
      return;
    }

    // 后台消化状态（前端轮询：转圈 / 待消化 / 刚更新了什么）
    if (req.method === 'GET' && url.pathname === '/api/bg-status') {
      sendJson(res, 200, { updating: profileUpdating, pending: !!bgTimer, last: bgLast });
      return;
    }

    // 读回本会话最近内幕（执行 Agent 也读同一个 jsonl 文件）
    if (req.method === 'GET' && url.pathname === '/api/logs') {
      sendJson(res, 200, { sessionId, logFile: logger.file, records: logger.readRecent(100) });
      return;
    }

    // 看证据库（dogfood：看证据在不在涨）
    if (req.method === 'GET' && url.pathname === '/api/evidence') {
      sendJson(res, 200, { evidence: store.all() });
      return;
    }

    // 用户主动改一条证据的 summary / raw_content（cell 8 规则 10）
    if (req.method === 'POST' && url.pathname === '/api/evidence/update') {
      const { id, rawContent, summary } = await readJson(req);
      const updated = store.update(String(id ?? ''), { rawContent, summary });
      sendJson(res, 200, { updated });
      return;
    }

    // 用户主动删一条证据（真删，非系统自动删；cell 6 条件性真删）
    if (req.method === 'POST' && url.pathname === '/api/evidence/delete') {
      const { id } = await readJson(req);
      const removed = store.remove(String(id ?? ''));
      sendJson(res, 200, { removed });
      return;
    }

    // 整理事件（事件化）：未整理的近期对话 → 总结成一个带情境的事件
    if (req.method === 'POST' && url.pathname === '/api/distill') {
      const r = await distill(config.identity.subjectId, { evidenceStore: store, eventStore, llm });
      sendJson(res, 200, { event: r.event, pendingCount: r.pendingCount, llmCalls: r.llmCalls });
      return;
    }

    // 看事件（事件 + 覆盖的原话证据 id）
    if (req.method === 'GET' && url.pathname === '/api/event') {
      const list = eventStore.all().map((e) => ({ ...e, evidenceIds: eventStore.evidenceOf(e.id) }));
      sendJson(res, 200, { event: list });
      return;
    }

    // 增量消化（阶段 2）：未消化事件 + 现有画像 → 新增/强化/纠正/冲突
    if (req.method === 'POST' && url.pathname === '/api/consolidate') {
      const r = await consolidate(config.identity.subjectId, { eventStore, evidenceStore: store, cognitionStore: cogStore, llm, transaction });
      sendJson(res, 200, {
        created: r.created, reinforced: r.reinforced, corrected: r.corrected,
        conflicted: r.conflicted, processedEvents: r.processedEvents, llmCalls: r.llmCalls,
      });
      return;
    }

    // ①治等（2026-07-01）：手动"更新画像"不再阻塞——触发后台跑、立即返回，前端靠 /api/bg-status 状态条看进度、跑完自动刷新画像。
    if (req.method === 'POST' && url.pathname === '/api/refresh') {
      if (profileUpdating) { sendJson(res, 200, { busy: true }); return; } // 后台正忙 → 稍候
      // fire-and-forget：不 await 完成，让用户不干等（写路径要几十秒）。落盘 + bgLast 由 runProfileUpdate 内部管。
      runProfileUpdate('manual').catch((e) => console.error('手动更新画像失败：', e));
      sendJson(res, 200, { started: true });
      return;
    }

    // 注入观察证据（observed，如"游戏开到 3:30"）：直接落库，不走回话（MemoWeft 不对观察开口）。
    if (req.method === 'POST' && url.pathname === '/api/observe') {
      const { rawContent, occurredAt } = await readJson(req);
      const raw = String(rawContent ?? '');
      const ev = store.put(
        perceive(raw, {
          sourceKind: 'observed',
          occurredAt: occurredAt || undefined,
          // 幂等：同内容+同时间只落一条（防 dogfood 重复注入出两条一样的观察）。
          originId: `observed:${raw}:${occurredAt || ''}`,
        }),
      );
      sendJson(res, 200, { evidence: ev });
      return;
    }

    // 注入活动窗口观察（4-A 档1）：结构化字段 → observed 证据。默认不上云；勾"允许上云"才 cloud=true（路线 A 验证）。
    if (req.method === 'POST' && url.pathname === '/api/observe-window') {
      const { app, title, durationSec, occurredAt, allowCloud } = await readJson(req);
      // 规范化时间：把 datetime-local 之类（缺秒/时区 Z）补成完整 ISO，避免时间窗字符串比较错位（dogfood 诊断修）。
      const parsed = occurredAt ? new Date(occurredAt) : new Date();
      const occ = isNaN(parsed.getTime()) ? new Date().toISOString() : parsed.toISOString();
      const observation = activeWindowToObservation({
        app: String(app ?? ''),
        title: String(title ?? ''),
        durationSec: Number(durationSec) || 0,
        occurredAt: occ,
      });
      if (allowCloud) observation.allowCloudRead = true; // 显式授权上云（仅测试数据，路线 A）
      const r = ingestObservations(config.identity.subjectId, [observation], { evidenceStore: store });
      sendJson(res, 200, { stored: r.stored, skipped: r.skipped });
      return;
    }

    // dogfood 灌数据进度（脚本 POST 上报 + 前端 GET 轮询画进度条）。
    if (req.method === 'POST' && url.pathname === '/api/seed-progress') {
      const p = await readJson(req);
      seedProgress = { running: !!p.running, step: Number(p.step) || 0, total: Number(p.total) || 0, label: String(p.label ?? '') };
      sendJson(res, 200, { ok: true });
      return;
    }
    if (req.method === 'GET' && url.pathname === '/api/seed-progress') {
      sendJson(res, 200, seedProgress);
      return;
    }
    // 最近对话（前端聊天区轮询：脚本灌的对话也实时显示，不只手动发的）。
    if (req.method === 'GET' && url.pathname === '/api/chat-history') {
      const turns = logger.readRecent(50).filter((t) => t.kind !== 'profile_update' && t.userInput != null);
      sendJson(res, 200, { turns: turns.map((t) => ({ turn: t.turn, userInput: t.userInput, reply: t.reply })) });
      return;
    }

    // M4 归因：现象（state 认知）+ 时间窗证据 → 可解释假设（低置信、挂证据、可推翻）。
    if (req.method === 'POST' && url.pathname === '/api/attribute') {
      const r = await attribute(config.identity.subjectId, {
        evidenceStore: store,
        cognitionStore: cogStore,
        llm,
      });
      sendJson(res, 200, {
        hypotheses: r.hypotheses,
        consideredPhenomena: r.consideredPhenomena,
        llmCalls: r.llmCalls,
      });
      return;
    }

    // M5 带证据主动询问：挑低置信假设 + 复看冲突认知 → 提问建议（含证据、把握度透明）。测试台替宿主朴素发问。
    if (req.method === 'POST' && url.pathname === '/api/ask') {
      const a = await proposeAsk(config.identity.subjectId, { cognitionStore: cogStore, evidenceStore: store, llm });
      const c = await revisitConflicts(config.identity.subjectId, { cognitionStore: cogStore, evidenceStore: store, llm });
      sendJson(res, 200, { proposals: [...a.proposals, ...c.proposals], llmCalls: a.llmCalls + c.llmCalls });
      return;
    }

    // 看画像（认知 + 各自溯源链）
    if (req.method === 'GET' && url.pathname === '/api/cognition') {
      const list = cogStore.all().map((c) => ({
        ...c,
        sources: cogStore.sourcesOf(c.id),
        effectiveConfidence: effectiveConfidence(c), // 衰减后的有效置信（读时算，给透视看"情绪在淡"）
      }));
      sendJson(res, 200, { cognition: list });
      return;
    }

    // 用户主动改一条认知（cell 8 规则 10）
    if (req.method === 'POST' && url.pathname === '/api/cognition/update') {
      const { id, content, confidence, credStatus, scope } = await readJson(req);
      const updated = cogStore.update(String(id ?? ''), { content, confidence, credStatus, scope });
      sendJson(res, 200, { updated });
      return;
    }

    // 用户主动删一条认知
    if (req.method === 'POST' && url.pathname === '/api/cognition/delete') {
      const { id } = await readJson(req);
      const removed = cogStore.remove(String(id ?? ''));
      sendJson(res, 200, { removed });
      return;
    }

    // ── 开发者模式·config 实时热调（cell 14/15）──
    // 读当前全量 config，前端据此渲染旋钮当前值。
    if (req.method === 'GET' && url.pathname === '/api/config') {
      sendJson(res, 200, { config });
      return;
    }

    // 改一个字段：收 { path, value } → 往同一个 config 引用深处赋值 → 即时生效（src 各模块运行时现读 config）。
    if (req.method === 'POST' && url.pathname === '/api/config') {
      const { path, value } = await readJson(req);
      const err = setByPath(config, String(path), value);
      if (err) { sendJson(res, 200, { error: err }); return; }
      sendJson(res, 200, { ok: true, path, value });
      return;
    }

    // 恢复默认：清空 config 现有键、再把出厂快照 Object.assign 回【同一个引用】（不换引用，热调才不失效）。
    if (req.method === 'POST' && url.pathname === '/api/config/reset') {
      for (const k of Object.keys(config)) delete config[k];
      Object.assign(config, structuredClone(configDefaults));
      sendJson(res, 200, { ok: true });
      return;
    }

    // ── 配置向导·生成 .env 文本（体验层阶段二）──
    // 铁律（决策3）：后端只【拼文本、当场返回】——绝不 writeFile、绝不把 apiKey 存进任何变量/缓存/日志。
    // 收 9 个 env 值 + withExperienceUI 布尔 → 拼成 .env 文本字符串 → 返回 { envText }。文本一走出这个函数就没了。
    if (req.method === 'POST' && url.pathname === '/api/gen-env') {
      const b = await readJson(req);
      const s = (v) => String(v ?? '').trim(); // 统一去空白；不落库、不缓存
      // 对话大模型（必填三项）
      const llmBase = s(b.llmBaseUrl), llmKey = s(b.llmApiKey), llmModel = s(b.llmModel);
      // 写路径小模型（可选三项，整组空则整组省略）
      const wBase = s(b.writeBaseUrl), wKey = s(b.writeApiKey), wModel = s(b.writeModel);
      // 向量嵌入（可选三项，整组空则整组省略）
      const eBase = s(b.embedBaseUrl), eKey = s(b.embedApiKey), eModel = s(b.embedModel);
      const withUI = b.withExperienceUI === true;

      // 必填校验：对话三项缺任一 → 报错（前端已拦，这里兜底，绝不生成半截配置）
      const missing = [];
      if (!llmBase) missing.push('MEMOWEFT_LLM_BASE_URL');
      if (!llmKey) missing.push('MEMOWEFT_LLM_API_KEY');
      if (!llmModel) missing.push('MEMOWEFT_LLM_MODEL');
      if (missing.length) {
        sendJson(res, 200, { error: `对话模型必填项缺失：${missing.join('、')}` });
        return;
      }

      const lines = [];
      lines.push('# ── 对话大模型（chat · 必配）：质量优先 ──────────────');
      lines.push(`MEMOWEFT_LLM_BASE_URL=${llmBase}`);
      lines.push(`MEMOWEFT_LLM_API_KEY=${llmKey}`);
      lines.push(`MEMOWEFT_LLM_MODEL=${llmModel}`);
      lines.push('');

      // 写路径小模型：整组任一非空才写；整组空 → 省略 + 注释说明回退（回退对话大模型，行为同旧）
      if (wBase || wKey || wModel) {
        lines.push('# ── 写路径小快模型（write · 可选）：整理事件/画像/归因走它，不拖慢更新画像 ──');
        lines.push(`MEMOWEFT_WRITE_LLM_BASE_URL=${wBase}`);
        lines.push(`MEMOWEFT_WRITE_LLM_API_KEY=${wKey}`);
        lines.push(`MEMOWEFT_WRITE_LLM_MODEL=${wModel}`);
      } else {
        lines.push('# ── 写路径小快模型（write · 可选）：未配 → 写路径自动回退对话大模型（行为同旧，不崩）──');
      }
      lines.push('');

      // 向量嵌入：整组任一非空才写；整组空 → 省略 + 注释说明降级（召回降级为空，画像照写）
      if (eBase || eKey || eModel) {
        lines.push('# ── 嵌入器（embed · 可选）：语义召回用 ──');
        lines.push(`MEMOWEFT_EMBED_BASE_URL=${eBase}`);
        lines.push(`MEMOWEFT_EMBED_API_KEY=${eKey}`);
        lines.push(`MEMOWEFT_EMBED_MODEL=${eModel}`);
      } else {
        lines.push('# ── 嵌入器（embed · 可选）：未配 → 语义召回降级为空（画像照写，只是回话不注入偏好）──');
      }
      lines.push('');

      // 部署选项（决策5）：是否带体验界面 → 生成 MEMOWEFT_EXPERIENCE_UI=on/off（给未来带界面的宿主读）
      lines.push('# ── 部署选项：是否带体验界面（on=带界面 / off=纯库）──');
      lines.push(`MEMOWEFT_EXPERIENCE_UI=${withUI ? 'on' : 'off'}`);
      lines.push('');

      sendJson(res, 200, { envText: lines.join('\n') });
      return;
    }

    // 新开会话（清空窗口；已落库证据不动）
    if (req.method === 'POST' && url.pathname === '/api/reset') {
      newSession();
      sendJson(res, 200, { ok: true, sessionId });
      return;
    }

    // ── 便携记忆包 · 导出（Phase 5-B · 备份/迁移）──
    // 只读三层数据组包（portable/exportBundle 已做好，这里纯接线）；向量索引不入包（派生物，导入后重建）。
    // 不需要 LLM / .env。前端拿 { bundle } 后用 Blob 下载成文件。
    if (req.method === 'GET' && url.pathname === '/api/export-bundle') {
      const subjectId = url.searchParams.get('subjectId') || config.identity.subjectId;
      const bundle = exportBundle(subjectId, { evidenceStore: store, eventStore, cognitionStore: cogStore });
      sendJson(res, 200, { bundle });
      return;
    }

    // ── 便携记忆包 · 导入（Phase 5-B）──
    // mode=dryRun（安全默认）：只校验、算将写入/重复条数，不落库；mode=merge：实际写入（走 transaction 原子化）。
    // 非法包（valid=false）由 importBundle 内部拦下、绝不写库。merge 成功时提示 needsReindex：向量索引不入包，需点「更新画像」重建召回。
    if (req.method === 'POST' && url.pathname === '/api/import-bundle') {
      const mode = url.searchParams.get('mode') === 'merge' ? 'merge' : 'dryRun';
      const bundle = await readJson(req);
      const plan = importBundle(bundle, { evidenceStore: store, eventStore, cognitionStore: cogStore, transaction }, { mode });
      const body = { plan };
      if (mode === 'merge' && plan.valid) body.needsReindex = true; // 向量索引不入包 → 建议重建召回
      sendJson(res, 200, body);
      return;
    }

    // ── 恢复出厂 · 清空全部数据（体验层阶段二 · 不可逆）──
    // 铁律：绝不改 src/——只用现有 store 的公开方法清三层数据 + 检索索引。
    //   证据 evidence：EvidenceStore 无 removeBySubject，用 all() 逐条 remove(id)（测试台单 subject，全清）。
    //   事件 event   ：eventStore.removeBySubject(subjectId) → 连带清 event_evidence 关联表。
    //   画像 cognition：cogStore.removeBySubject(subjectId) → 连带清 cognition_evidence 溯源链。
    //   检索索引     ：retriever.indexAll([]) → VectorRetriever 会 DELETE FROM vectors（空数组即清空）；
    //                  NullRetriever 为 no-op（本就无索引）。两种都安全。
    // 清完顺手 newSession()：把当前会话窗口也清掉，避免旧上下文残留（跟"＋新会话"同一动作）。
    if (req.method === 'POST' && url.pathname === '/api/factory-reset') {
      const subjectId = config.identity.subjectId;
      let evidenceRemoved = 0;
      for (const e of store.all()) {
        if (store.remove(e.id)) evidenceRemoved++;
      }
      const eventRemoved = eventStore.removeBySubject(subjectId);
      const cognitionRemoved = cogStore.removeBySubject(subjectId);
      await retriever.indexAll([]); // 清空向量索引（空召回时无副作用）
      newSession(); // 顺手清当前会话窗口（不留旧上下文）
      sendJson(res, 200, { ok: true, evidenceRemoved, eventRemoved, cognitionRemoved });
      return;
    }

    sendJson(res, 404, { error: 'not found' });
  } catch (e) {
    sendJson(res, 400, { error: String(e) });
  }
});

// 部署选项（决策5·做法B）：.env 里 MEMOWEFT_EXPERIENCE_UI=off → 不起网页（只把 MemoWeft 当库用）。
// 只改展示层：显式等于 'off' 才拦；其它值（含未设）照常 listen。env 早已由上面 loadLLMPool() 触发的
// process.loadEnvFile() 读入；此处再兜底读一次（loadEnvFile 幂等，无 .env 时抛错被忽略），确保 listen 前可读。
try { process.loadEnvFile(); } catch { /* 没有 .env 或已加载，忽略 */ }
if (process.env.MEMOWEFT_EXPERIENCE_UI === 'off') {
  console.log('\n  体验界面已在 .env 关闭（MEMOWEFT_EXPERIENCE_UI=off），未启动网页。');
  console.log('  （把它当库 import 即可；想起网页请改回 on 或删掉该行，再跑 npm run experience）\n');
} else {
// 只绑 127.0.0.1（本机回环）：本服务无鉴权、直接读写个人画像/对话等隐私数据，
// 且配置向导会经 HTTP 明文传 API key。若不指定 host，Node 默认绑 ::/0.0.0.0，
// 同网段任何人都能读全部画像、发起对话、截明文 key。只开本机，杜绝这条外网面。
server.listen(PORT, '127.0.0.1', () => {
  console.log(`\n  MemoWeft 测试台（阶段 1：画像 + 召回）→ http://localhost:${PORT}`);
  console.log(`  证据库 → ${DB_PATH}`);
  console.log(`  运行日志 → ${LOG_DIR}\\run-${sessionId}.jsonl`);
  console.log('  (Ctrl+C 停止)\n');
});
}
