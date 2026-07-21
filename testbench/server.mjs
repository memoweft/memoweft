/**
 * MemoWeft 测试台 · 本地服务端：接入真实证据层与会话。
 *
 * 一轮：感知用户消息 → 存为证据（SQLite）→ 空召回 → 带窗口回话 → 落盘内幕。
 * 后端已是真逻辑（非占位）；召回、画像、假设与冲突等功能由各自模块提供。
 *
 * 零外部依赖：node:http + node:fs + node:sqlite。
 * 启动：npm run testbench → http://localhost:7888
 */
import { createServer } from 'node:http';
import { readFile, readdir, stat, rename } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRunLogger } from '../src/obs/runLog.ts';
import { openStores } from '../src/store/openStores.ts';
import { createMemoryManagementAPI } from '../src/memory/managementApi.ts';
import { distill } from '../src/distillation/distill.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { updateProfile } from '../src/consolidation/updateProfile.ts';
import { perceive } from '../src/pipeline/perceive.ts';
import { ingestObservations } from '../src/perception/ingest.ts';
// 活动窗口映射已迁出 Core 到采集插件；testbench 手动 observe 调试表单从插件路径引。
import { activeWindowToObservation } from '../plugins/collector-active-window/src/activeWindow.ts';
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
import { loadLLMConfig } from '../src/llm/client.ts';
import { Conversation } from '../src/pipeline/conversation.ts';
import { config } from '../src/config.ts';
import { exportBundle, importBundle } from '../src/portable/index.ts';
import { resetTestbenchSubject } from './factoryReset.mjs';
import { portableDeps } from './portableDeps.mjs';
import {
  ClientInputError,
  clientInputRejection,
  encodeDotenvEntries,
  requestRejection,
  setOwnPath,
} from './serverSecurity.mjs';

// 开发者模式·热调：进程一启动就深拷一份 config 当"出厂默认"（必须在任何请求改动 config 之前拍这张快照）。
// 之后 /api/config/reset 靠它把 config 恢复原样。structuredClone 是 Node 内置，零依赖。
const configDefaults = structuredClone(config);

const PORT = 7888;
const __dirname = dirname(fileURLToPath(import.meta.url));
const LOG_DIR = join(__dirname, '..', 'logs');
const DB_PATH = join(__dirname, 'testbench-evidence.db'); // 独立库，不污染正式 ./dla.db

// 共享依赖：证据库、空召回和 LLM。缺少 .env 时仍可启动，模型调用错误通过 error 字段返回。
// 三个 store 共用【一条】连接 + 一个事务器——让 consolidate 的多步、多表写能原子化（跨连接事务是硬约束，见 store/openStores.ts）。
const stores = openStores(DB_PATH);
const store = stores.evidenceStore;
const eventStore = stores.eventStore;
const cogStore = stores.cognitionStore;
const transaction = stores.transaction; // 传给 updateProfile / consolidate 即让其写入原子化
// 受控记忆管理 API：删除、标失效、授权变更等关键管理行为。
// 一律走它——带 reason 落审计（management_log），Host 不再直接摸 Sqlite*Store 完成这些操作。
const memoryApi = createMemoryManagementAPI(stores);
// 模型池：写路径（distill/consolidate/attribute/trends）使用写模型，对话使用聊天模型。
const llmPool = loadLLMPool();
const llm = llmPool.for('write'); // 写路径统一用它（updateProfile / 手动 distill/consolidate/attribute/ask / trends）
const chatLLM = llmPool.for('chat'); // 对话使用聊天模型，写路径可独立选择更轻量的模型

// LLM 网络错误（socket closed / fetch failed 等）不应终止诊断台进程。
process.on('unhandledRejection', (e) =>
  console.error('[兜底] 未处理的 rejection（服务继续）：', e instanceof Error ? e.message : e),
);

// 召回：配了 MEMOWEFT_EMBED_*（或兼容 DLA_EMBED_*）用向量召回；否则降级为空召回（回话不注入画像，不报错）。
const embedConfig = loadEmbedConfig();
const retriever = embedConfig
  ? new VectorRetriever(DB_PATH, new OpenAICompatEmbedder(embedConfig))
  : new NullRetriever();
if (!embedConfig)
  console.log('  ⚠️ 未配 MEMOWEFT_EMBED_*（或兼容 DLA_EMBED_*），召回降级为空（回话不注入画像）');

// loadLLMPool 允许无 .env 启动；未配置写模型时回退到聊天模型，详见 src/llm/pool.ts。

// 诊断台作为宿主注入 MemoWeft-aware 设定；语气归宿主，Core 默认保持中性。
// 修一个 integration testing 暴露的坑：素提示下大模型会露出出厂反射「我不保留记忆、聊完就忘」，正好否定 MemoWeft 的价值。
const REPLY_PERSONA =
  '你是一个长期陪着这个用户、会持续记住 ta 的助手。' +
  '下面若给出「你已了解关于这个用户的情况」，就自然地把它用上，像一个真的记得 ta 的人那样回应。' +
  '绝不要说“我不会保留记忆”“每次对话都是全新开始”“对话结束就忘”这类话——' +
  '你背后有一个跨对话持续记住 ta 的记忆层，ta 说的会被记下来、以后还认得。' +
  '语气自然、简洁、真诚，别生硬地复述你了解到的东西。';

// ── 多会话：内存里的活跃会话 + 磁盘上的历史日志两处；current 指向"当前会话"。──
// 一个会话 = 一个 Conversation（回话窗口）+ 一个 logger（run-<id>.jsonl）。/api/reset 新建并【保留】旧的
// 旧会话保留在列表中，可回访并继续对话。
const sessions = new Map(); // id → { id, convo, logger, createdAt }
let seq = 0;
function makeSession(seedTurns = []) {
  const id = `s-${Date.now()}-${seq++}`; // 带序号防同毫秒撞车
  const s = {
    id,
    createdAt: new Date().toISOString(),
    convo: new Conversation({
      store,
      retriever,
      cognitionStore: cogStore,
      llm: chatLLM,
      systemPrompt: REPLY_PERSONA,
      seedTurns,
    }),
    logger: createRunLogger({ dir: LOG_DIR, sessionId: id }),
  };
  sessions.set(id, s);
  return s;
}
let current = makeSession();

// 新开一段会话并设为当前（旧的不销毁）。
function newSession() {
  current = makeSession();
  return current;
}

// 打开一条会话：活跃的直接切；只在盘上的 → 读回最近几轮做种子重建（logger 续写同文件、轮号接着走）。
function openSession(id) {
  let s = sessions.get(id);
  if (!s) {
    const lg = createRunLogger({ dir: LOG_DIR, sessionId: id });
    const past = lg
      .readRecent(200)
      .filter((t) => t.kind !== 'profile_update' && t.userInput != null);
    const seed = past.slice(-config.workingMemory.maxTurns).flatMap((t) => [
      { role: 'user', content: t.userInput },
      { role: 'assistant', content: t.reply },
    ]);
    s = {
      id,
      createdAt: new Date().toISOString(),
      convo: new Conversation({
        store,
        retriever,
        cognitionStore: cogStore,
        llm: chatLLM,
        systemPrompt: REPLY_PERSONA,
        seedTurns: seed,
      }),
      logger: lg,
    };
    sessions.set(id, s);
  }
  current = s;
  return s;
}

// ── 后台自动更新画像（空闲防抖触发）──
// 写路径需要多次模型调用，因此不阻塞聊天：对话立即记录证据，画像在后台更新。
// 共用一把锁：同一用户的画像更新【不能并发】（否则重复消化同一批事件、markConsolidated 竞争）。
// 批量更新：累计达到 config.profileUpdate.batchSize 条，或空闲达到 idleMinutes 后更新画像。
let profileUpdating = false;
let bgTimer = null;
let bgLast = null; // 上次更新结果摘要，供前端轮询显示
let seedProgress = { running: false, step: 0, total: 6, label: '空闲' }; // integration testing 灌数据进度（脚本上报、前端轮询画进度条）

async function runProfileUpdate(trigger = 'background') {
  if (profileUpdating) return null; // 正忙 → 调用方决定重排/提示
  profileUpdating = true;
  try {
    const r = await updateProfile(config.identity.subjectId, {
      evidenceStore: store,
      eventStore,
      cognitionStore: cogStore,
      retriever,
      llm,
      transaction,
    });
    // 周期后台：跨会话趋势聚合（规则筛够频才调模型）+ 自然过期（临时类老了标失效）。
    const trd = await aggregateTrends(config.identity.subjectId, {
      evidenceStore: store,
      cognitionStore: cogStore,
      llm,
    });
    const exp = expire(config.identity.subjectId, { cognitionStore: cogStore });
    const c = r.consolidated;
    bgLast = {
      at: new Date().toISOString(),
      created: c.created.length,
      reinforced: c.reinforced,
      corrected: c.corrected,
      conflicted: c.conflicted,
      hypotheses: r.attributed.hypotheses.length,
      trends: trd.trends.length,
      expired: exp.expired,
      indexError: r.indexError,
      // 记忆气泡：带上这批新生成认知的精简内容（只 id/content/credStatus），供前端织进聊天流。
      newCognitions: c.created.map((x) => ({
        id: x.id,
        content: x.content,
        credStatus: x.credStatus,
      })),
    };
    // Persist per-stage timing and a content-free summary for diagnostics.
    current.logger.appendProfileUpdate({
      trigger,
      timings: r.timings,
      summary: {
        pendingCount: r.distilled.pendingCount,
        created: c.created.length,
        reinforced: c.reinforced,
        corrected: c.corrected,
        conflicted: c.conflicted,
        hypotheses: r.attributed.hypotheses.length,
        trends: trd.trends.length,
        expired: exp.expired,
        // 写路径仪表（只观测）：记录画像和 prompt 大小，便于诊断写入开销。
        profileSize: r.metrics.profileSize,
        promptChars: r.metrics.promptChars,
      },
      llmCalls: r.distilled.llmCalls + c.llmCalls + r.attributed.llmCalls,
      indexError: r.indexError,
    });
    return r;
  } catch (e) {
    current.logger.appendProfileUpdate({
      trigger,
      error: e instanceof Error ? e.message : String(e),
    });
    throw e;
  } finally {
    profileUpdating = false;
  }
}

// 批量更新触发器：每次对话完成后累加计数。
// 达到 batchSize 时立即安排更新；否则重置空闲计时，在 idleMinutes 内无新对话时更新。先满足的条件触发。
let pendingSinceUpdate = 0;
function scheduleBackgroundUpdate() {
  pendingSinceUpdate++;
  const { batchSize, idleMinutes } = config.profileUpdate;
  if (pendingSinceUpdate >= batchSize) {
    if (bgTimer) {
      clearTimeout(bgTimer);
      bgTimer = null;
    } // 攒够一批 → 立刻排，清掉空闲计时
    void triggerProfileUpdate();
  } else {
    if (bgTimer) clearTimeout(bgTimer); // 又聊了 → 重置空闲计时
    bgTimer = setTimeout(() => {
      bgTimer = null;
      void triggerProfileUpdate();
    }, idleMinutes * 60000);
  }
}
async function triggerProfileUpdate() {
  try {
    const r = await runProfileUpdate('background');
    if (r === null) {
      // 手动更新占用锁时，保留待处理计数并在 10 秒后重试。
      if (bgTimer) clearTimeout(bgTimer);
      bgTimer = setTimeout(() => {
        bgTimer = null;
        void triggerProfileUpdate();
      }, 10000);
      return;
    }
    pendingSinceUpdate = 0; // 更新成功 → 计数清零，重新攒下一批
  } catch (e) {
    // LLM 网络错误不会终止服务；runProfileUpdate 已记录错误。
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
        // 请求体显式按 UTF-8 解码，避免不同命令行环境造成乱码写入证据库。
        //   浏览器 fetch 恒为 UTF-8 不受影响；命令行注入请用 UTF-8 编码的请求体。）
        if (body.includes('�')) {
          reject(new ClientInputError('请求体不是合法 UTF-8。'));
          return;
        }
        resolve(body ? JSON.parse(body) : {});
      } catch {
        reject(new ClientInputError('请求 JSON 无效。'));
      }
    });
    req.on('error', reject);
  });
}

function sendJson(res, code, data) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  try {
    // 每一个请求都先过 loopback Host 边界，避免 DNS rebinding 下的伪 Host 同源读取静态页或
    // 记忆数据；POST 还必须通过浏览器同源 Origin 校验，任何写库、改配置或重置都不会在跨站
    // 请求下执行。没有 Origin 的本机脚本仍可用于诊断自动化。
    const rejection = requestRejection(req.headers, req.method, PORT);
    if (rejection) {
      sendJson(res, rejection.statusCode, { error: rejection.message });
      return;
    }

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

    // 一轮对话：记录证据并生成回复，写入诊断记录后返回完整结果。
    if (req.method === 'POST' && url.pathname === '/api/chat') {
      const { text, originId, sessionId: reqSid } = await readJson(req);
      if (reqSid && sessions.has(reqSid)) current = sessions.get(reqSid); // 指定了活跃会话 → 切过去
      const outcome = await current.convo.handle(String(text ?? ''), {
        originId: originId ?? null,
      });
      const record = current.logger.appendTurn({
        userInput: String(text ?? ''),
        reply: outcome.reply,
        evidence: [{ id: outcome.storedEvidence.id, summary: outcome.storedEvidence.summary }],
        recall: outcome.recall.map((r) => ({
          summary: r.content,
          score: Math.round(r.score * 1000),
        })),
        llmCalls: outcome.llmCalls,
        error: outcome.error,
      });
      sendJson(res, 200, { record, sessionId: current.id, logFile: current.logger.file });
      scheduleBackgroundUpdate(); // 对话完成后安排后台更新，不阻塞本轮回复
      return;
    }

    // 后台消化状态（前端轮询：转圈 / 待消化 / 刚更新了什么）
    if (req.method === 'GET' && url.pathname === '/api/bg-status') {
      sendJson(res, 200, { updating: profileUpdating, pending: !!bgTimer, last: bgLast });
      return;
    }

    // 首次启动：前端根据模型和嵌入器配置决定显示配置向导或聊天界面；无 .env 时仍可返回状态。
    if (req.method === 'GET' && url.pathname === '/api/health') {
      let llmReady = false,
        embedReady = false;
      try {
        llmReady = !!loadLLMConfig();
      } catch {
        /* 未配 → false */
      }
      try {
        embedReady = !!loadEmbedConfig();
      } catch {
        /* 未配 → false */
      }
      sendJson(res, 200, { llmReady, embedReady });
      return;
    }

    // 读取本会话最近的诊断记录；其他诊断工具使用同一个 jsonl 文件。
    if (req.method === 'GET' && url.pathname === '/api/logs') {
      sendJson(res, 200, {
        sessionId: current.id,
        logFile: current.logger.file,
        records: current.logger.readRecent(100),
      });
      return;
    }

    // 看证据库（integration testing：看证据在不在涨）
    if (req.method === 'GET' && url.pathname === '/api/evidence') {
      sendJson(res, 200, { evidence: store.all() });
      return;
    }

    // 用户主动改一条证据的 summary / raw_content / 授权位（记忆管理页）。
    // 授权位仅接受布尔值，避免非布尔值落库；未传时保持原值。
    // 授权位是隐私敏感的【关键管理行为】→ 走受控 API
    //   （带 reason 落审计；零变更时受控 API 原样返回、不落审计，前端行为不受影响）；
    //   rawContent/summary 是开发调试的内容编辑、非关键管理行为 → 保留 store 直调。
    if (req.method === 'POST' && url.pathname === '/api/evidence/update') {
      const { id, rawContent, summary, allowCloudRead, allowInference, reason } =
        await readJson(req);
      const evidenceId = String(id ?? '');
      const hasContentEdit = rawContent !== undefined || summary !== undefined;
      const hasAuthChange =
        typeof allowCloudRead === 'boolean' || typeof allowInference === 'boolean';
      if (hasContentEdit) store.update(evidenceId, { rawContent, summary }); // 内容编辑=调试，直调保留
      if (hasAuthChange) {
        memoryApi.updateEvidenceAuthorization({
          evidenceId,
          allowCloudRead: typeof allowCloudRead === 'boolean' ? allowCloudRead : undefined,
          allowInference: typeof allowInference === 'boolean' ? allowInference : undefined,
          reason:
            typeof reason === 'string' && reason ? reason : 'testbench:用户在记忆管理页修改授权', // UI 不传就用缺省
        });
      }
      // 响应统一取最新全量：内容与授权若一次同发（未来调用方，如 apps/memoweft-host），
      //   两次写库都已生效，这里 get 一遍才能反映两者（避免只回后一次的快照）。
      //   什么都没传：行为同旧（空 patch → 存在原样返回、不存在返回 null），响应形状 { updated } 不变。
      const updated =
        hasContentEdit || hasAuthChange ? store.get(evidenceId) : store.update(evidenceId, {});
      sendJson(res, 200, { updated });
      return;
    }

    // 用户主动删除一条证据；这是显式管理操作，不是系统自动清理。
    // 走受控 API。UI 已做二次确认 → 语义=用户执意删，故 force:true
    // （有事件/认知引用也删、连关联链一起清，blockers 快照进审计 detail）。响应仍是 { removed }，前端不感知。
    if (req.method === 'POST' && url.pathname === '/api/evidence/delete') {
      const { id } = await readJson(req);
      const r = memoryApi.removeEvidenceSafely({
        evidenceId: String(id ?? ''),
        force: true,
        reason: 'testbench:用户在记忆管理页删除',
      });
      sendJson(res, 200, { removed: r.removed });
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
      const list = eventStore
        .all()
        .map((e) => ({ ...e, evidenceIds: eventStore.evidenceOf(e.id) }));
      sendJson(res, 200, { event: list });
      return;
    }

    // 增量消化：未消化事件 + 现有画像 → 新增/强化/纠正/冲突
    if (req.method === 'POST' && url.pathname === '/api/consolidate') {
      const r = await consolidate(config.identity.subjectId, {
        eventStore,
        evidenceStore: store,
        cognitionStore: cogStore,
        llm,
        transaction,
      });
      sendJson(res, 200, {
        created: r.created,
        reinforced: r.reinforced,
        corrected: r.corrected,
        conflicted: r.conflicted,
        processedEvents: r.processedEvents,
        llmCalls: r.llmCalls,
      });
      return;
    }

    // 手动更新在后台执行并立即返回；前端通过 /api/bg-status 展示进度，完成后刷新画像。
    if (req.method === 'POST' && url.pathname === '/api/refresh') {
      if (profileUpdating) {
        sendJson(res, 200, { busy: true });
        return;
      } // 后台正忙 → 稍候
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
          // 幂等：同内容+同时间只落一条（防 integration testing 重复注入出两条一样的观察）。
          originId: `observed:${raw}:${occurredAt || ''}`,
        }),
      );
      sendJson(res, 200, { evidence: ev });
      return;
    }

    // 注入活动窗口观察（4-A observation mode）：结构化字段 → observed 证据。默认不上云；勾"允许上云"才 cloud=true（explicit authorization path 验证）。
    if (req.method === 'POST' && url.pathname === '/api/observe-window') {
      const { app, title, durationSec, occurredAt, allowCloud } = await readJson(req);
      // 规范化时间：把 datetime-local 之类（缺秒/时区 Z）补成完整 ISO，避免时间窗字符串比较错位（integration testing 诊断修）。
      const parsed = occurredAt ? new Date(occurredAt) : new Date();
      const occ = isNaN(parsed.getTime()) ? new Date().toISOString() : parsed.toISOString();
      const observation = activeWindowToObservation({
        app: String(app ?? ''),
        title: String(title ?? ''),
        durationSec: Number(durationSec) || 0,
        occurredAt: occ,
      });
      if (allowCloud) observation.allowCloudRead = true; // 显式授权上云（仅测试数据，explicit authorization path）
      const r = ingestObservations(config.identity.subjectId, [observation], {
        evidenceStore: store,
      });
      sendJson(res, 200, { stored: r.stored, skipped: r.skipped });
      return;
    }

    // integration testing 灌数据进度（脚本 POST 上报 + 前端 GET 轮询画进度条）。
    if (req.method === 'POST' && url.pathname === '/api/seed-progress') {
      const p = await readJson(req);
      seedProgress = {
        running: !!p.running,
        step: Number(p.step) || 0,
        total: Number(p.total) || 0,
        label: String(p.label ?? ''),
      };
      sendJson(res, 200, { ok: true });
      return;
    }
    if (req.method === 'GET' && url.pathname === '/api/seed-progress') {
      sendJson(res, 200, seedProgress);
      return;
    }
    // 最近对话（前端聊天区轮询：脚本灌的对话也实时显示，不只手动发的）。
    if (req.method === 'GET' && url.pathname === '/api/chat-history') {
      const sid = url.searchParams.get('sessionId');
      const lg = sid
        ? (sessions.get(sid)?.logger ?? createRunLogger({ dir: LOG_DIR, sessionId: sid }))
        : current.logger;
      const turns = lg
        .readRecent(50)
        .filter((t) => t.kind !== 'profile_update' && t.userInput != null);
      sendJson(res, 200, {
        sessionId: sid || current.id,
        turns: turns.map((t) => ({ turn: t.turn, userInput: t.userInput, reply: t.reply })),
      });
      return;
    }

    // 归因：现象（state 认知）+ 时间窗证据 → 可解释假设（低置信、挂证据、可推翻）。
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

    // 带证据主动询问：挑低置信假设 + 复看冲突认知 → 提问建议（含证据、把握度透明）。诊断台替宿主生成中性问题。
    if (req.method === 'POST' && url.pathname === '/api/ask') {
      const a = await proposeAsk(config.identity.subjectId, {
        cognitionStore: cogStore,
        evidenceStore: store,
        llm,
      });
      const c = await revisitConflicts(config.identity.subjectId, {
        cognitionStore: cogStore,
        evidenceStore: store,
        llm,
      });
      sendJson(res, 200, {
        proposals: [...a.proposals, ...c.proposals],
        llmCalls: a.llmCalls + c.llmCalls,
      });
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

    // 用户通过受控管理 API 主动修改一条认知。
    // 请求只带 invalidAt 且非 null =「标失效」这一关键管理行为 →
    //   走受控 API（reason 落审计；invalidAt 由 API 统一取"现在"，与前端本就传 now 等价）。
    //   其余字段（content/confidence/credStatus/scope 的开发调试编辑、invalidAt:null 恢复有效）保留 store 直调。
    if (req.method === 'POST' && url.pathname === '/api/cognition/update') {
      const { id, content, confidence, credStatus, scope, invalidAt } = await readJson(req);
      const onlyInvalidate =
        invalidAt != null &&
        content === undefined &&
        confidence === undefined &&
        credStatus === undefined &&
        scope === undefined;
      const updated = onlyInvalidate
        ? memoryApi.invalidateCognition({
            cognitionId: String(id ?? ''),
            reason: 'testbench:用户标失效',
          })
        : cogStore.update(String(id ?? ''), { content, confidence, credStatus, scope, invalidAt });
      sendJson(res, 200, { updated });
      return;
    }

    // 用户主动删一条认知：走受控 API——连溯源链删 + reason 落审计
    // （审计 detail 只存元数据不存内容原文）。响应仍是 { removed }，前端不感知。
    if (req.method === 'POST' && url.pathname === '/api/cognition/delete') {
      const { id } = await readJson(req);
      const r = memoryApi.removeCognitionSafely({
        cognitionId: String(id ?? ''),
        reason: 'testbench:用户删除',
      });
      sendJson(res, 200, { removed: r.removed });
      return;
    }

    // ── 诊断模式：运行时调整配置 ──
    // 读当前全量 config，前端据此渲染旋钮当前值。
    if (req.method === 'GET' && url.pathname === '/api/config') {
      sendJson(res, 200, { config });
      return;
    }

    // 改一个字段：收 { path, value } → 往同一个 config 引用深处赋值 → 即时生效（src 各模块运行时现读 config）。
    if (req.method === 'POST' && url.pathname === '/api/config') {
      const { path, value } = await readJson(req);
      const err = setOwnPath(config, String(path), value);
      if (err) {
        sendJson(res, 200, { error: err });
        return;
      }
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

    // ── 配置向导·生成 .env 文本 ──
    // 隐私保证：后端仅组装文本并立即返回；不调用 writeFile，也不将 apiKey 写入持久变量、缓存或日志。
    // 收 9 个 env 值 + withExperienceUI 布尔 → 拼成 .env 文本字符串 → 返回 { envText }。文本一走出这个函数就没了。
    if (req.method === 'POST' && url.pathname === '/api/gen-env') {
      const b = await readJson(req);
      const s = (v) => String(v ?? '').trim(); // 统一去空白；不落库、不缓存
      // 对话大模型（必填三项）
      const llmBase = s(b.llmBaseUrl),
        llmKey = s(b.llmApiKey),
        llmModel = s(b.llmModel);
      // 写路径小模型（可选三项，整组空则整组省略）
      const wBase = s(b.writeBaseUrl),
        wKey = s(b.writeApiKey),
        wModel = s(b.writeModel);
      // 向量嵌入（可选三项，整组空则整组省略）
      const eBase = s(b.embedBaseUrl),
        eKey = s(b.embedApiKey),
        eModel = s(b.embedModel);
      const withUI = b.withExperienceUI === true;

      // 服务端校验必填项；任一对话配置缺失时返回错误，不生成不完整配置。
      const missing = [];
      if (!llmBase) missing.push('MEMOWEFT_LLM_BASE_URL');
      if (!llmKey) missing.push('MEMOWEFT_LLM_API_KEY');
      if (!llmModel) missing.push('MEMOWEFT_LLM_MODEL');
      if (missing.length) {
        sendJson(res, 400, { error: `对话模型必填项缺失：${missing.join('、')}` });
        return;
      }

      const values = {
        MEMOWEFT_LLM_BASE_URL: llmBase,
        MEMOWEFT_LLM_API_KEY: llmKey,
        MEMOWEFT_LLM_MODEL: llmModel,
        MEMOWEFT_WRITE_LLM_BASE_URL: wBase,
        MEMOWEFT_WRITE_LLM_API_KEY: wKey,
        MEMOWEFT_WRITE_LLM_MODEL: wModel,
        MEMOWEFT_EMBED_BASE_URL: eBase,
        MEMOWEFT_EMBED_API_KEY: eKey,
        MEMOWEFT_EMBED_MODEL: eModel,
      };
      const { encoded, unrepresentable } = encodeDotenvEntries(values);
      if (unrepresentable.length) {
        sendJson(res, 400, {
          error: `以下配置包含当前 .env 格式无法无损保存的字符组合：${unrepresentable.join('、')}`,
        });
        return;
      }

      const lines = [];
      lines.push('# ── 对话大模型（chat · 必配）：质量优先 ──────────────');
      lines.push(`MEMOWEFT_LLM_BASE_URL=${encoded.MEMOWEFT_LLM_BASE_URL}`);
      lines.push(`MEMOWEFT_LLM_API_KEY=${encoded.MEMOWEFT_LLM_API_KEY}`);
      lines.push(`MEMOWEFT_LLM_MODEL=${encoded.MEMOWEFT_LLM_MODEL}`);
      lines.push('');

      // 写路径小模型：整组任一非空才写；整组空 → 省略 + 注释说明回退（回退对话大模型，行为同旧）
      if (wBase || wKey || wModel) {
        lines.push(
          '# ── 写路径小快模型（write · 可选）：整理事件/画像/归因走它，不拖慢更新画像 ──',
        );
        lines.push(`MEMOWEFT_WRITE_LLM_BASE_URL=${encoded.MEMOWEFT_WRITE_LLM_BASE_URL}`);
        lines.push(`MEMOWEFT_WRITE_LLM_API_KEY=${encoded.MEMOWEFT_WRITE_LLM_API_KEY}`);
        lines.push(`MEMOWEFT_WRITE_LLM_MODEL=${encoded.MEMOWEFT_WRITE_LLM_MODEL}`);
      } else {
        lines.push(
          '# ── 写路径小快模型（write · 可选）：未配 → 写路径自动回退对话大模型（行为同旧，不崩）──',
        );
      }
      lines.push('');

      // 向量嵌入：整组任一非空才写；整组空 → 省略 + 注释说明降级（召回降级为空，画像照写）
      if (eBase || eKey || eModel) {
        lines.push('# ── 嵌入器（embed · 可选）：语义召回用 ──');
        lines.push(`MEMOWEFT_EMBED_BASE_URL=${encoded.MEMOWEFT_EMBED_BASE_URL}`);
        lines.push(`MEMOWEFT_EMBED_API_KEY=${encoded.MEMOWEFT_EMBED_API_KEY}`);
        lines.push(`MEMOWEFT_EMBED_MODEL=${encoded.MEMOWEFT_EMBED_MODEL}`);
      } else {
        lines.push(
          '# ── 嵌入器（embed · 可选）：未配 → 语义召回降级为空（画像照写，只是回话不注入偏好）──',
        );
      }
      lines.push('');

      // 部署选项（部署契约）：是否带体验界面 → 生成 MEMOWEFT_EXPERIENCE_UI=on/off（给未来带界面的宿主读）
      lines.push('# ── 部署选项：是否带体验界面（on=带界面 / off=纯库）──');
      lines.push(`MEMOWEFT_EXPERIENCE_UI=${withUI ? 'on' : 'off'}`);
      lines.push('');

      sendJson(res, 200, { envText: lines.join('\n') });
      return;
    }

    // 新开会话：新建并设为当前，旧会话保留、进列表可回访；已落库证据不动。
    if (req.method === 'POST' && url.pathname === '/api/reset') {
      newSession();
      sendJson(res, 200, { ok: true, sessionId: current.id });
      return;
    }

    // 会话列表：磁盘上所有 run-s-*.jsonl → { sessionId, mtime, turnCount, preview, live, current }。
    // 空会话（只有 profile_update、没真对话）不列。开发者/当前会话由前端按 id 自己标注。
    if (req.method === 'GET' && url.pathname === '/api/sessions') {
      let files = [];
      try {
        files = (await readdir(LOG_DIR)).filter((f) => /^run-s-.*\.jsonl$/.test(f));
      } catch {
        files = [];
      }
      const list = [];
      for (const f of files) {
        const id = f.replace(/^run-/, '').replace(/\.jsonl$/, '');
        try {
          const st = await stat(join(LOG_DIR, f));
          const lg = sessions.get(id)?.logger ?? createRunLogger({ dir: LOG_DIR, sessionId: id });
          const turns = lg
            .readRecent(500)
            .filter((t) => t.kind !== 'profile_update' && t.userInput != null);
          if (turns.length === 0) continue;
          list.push({
            sessionId: id,
            mtime: st.mtimeMs,
            turnCount: turns.length,
            preview: String(turns[0].userInput || '').slice(0, 30),
            live: sessions.has(id),
            current: id === current.id,
          });
        } catch {
          /* 坏文件跳过 */
        }
      }
      list.sort((a, b) => b.mtime - a.mtime);
      sendJson(res, 200, { sessions: list, currentId: current.id });
      return;
    }

    // 打开一条会话：切当前 + 续聊种子（盘上的会重建带上下文）+ 返回历史轮供前端渲染。
    if (req.method === 'POST' && url.pathname === '/api/session/open') {
      const { id } = await readJson(req);
      if (!id || typeof id !== 'string') {
        sendJson(res, 400, { error: '缺 id' });
        return;
      }
      const s = openSession(String(id));
      const turns = s.logger
        .readRecent(200)
        .filter((t) => t.kind !== 'profile_update' && t.userInput != null)
        .map((t) => ({ turn: t.turn, userInput: t.userInput, reply: t.reply }));
      sendJson(res, 200, { ok: true, sessionId: s.id, turns });
      return;
    }

    // 归档一条会话：日志文件加 .archived 后缀 → 从列表消失，但【数据不删、可恢复】。
    // 如果归档当前会话，立即创建新会话，避免 current 指向已改名的文件。
    if (req.method === 'POST' && url.pathname === '/api/session/archive') {
      const { id } = await readJson(req);
      if (!id || typeof id !== 'string') {
        sendJson(res, 400, { error: '缺 id' });
        return;
      }
      sessions.delete(id); // 从活跃集移除（若在）
      try {
        await rename(join(LOG_DIR, `run-${id}.jsonl`), join(LOG_DIR, `run-${id}.jsonl.archived`));
      } catch {
        /* 文件不在就算了 */
      }
      let archivedCurrent = false;
      if (id === current.id) {
        newSession();
        archivedCurrent = true;
      }
      sendJson(res, 200, { ok: true, currentId: current.id, archivedCurrent });
      return;
    }

    // ── 导出便携记忆包（备份/迁移）──
    // 只读完整可移植记忆层组包（portableDeps 集中保活 v0.6 interaction stores）；向量索引不入包（派生物，导入后重建）。
    // 不需要 LLM / .env。前端拿 { bundle } 后用 Blob 下载成文件。
    if (req.method === 'GET' && url.pathname === '/api/export-bundle') {
      const subjectId = url.searchParams.get('subjectId') || config.identity.subjectId;
      const bundle = exportBundle(subjectId, portableDeps(stores));
      sendJson(res, 200, { bundle });
      return;
    }

    // ── 便携记忆包 · 导入 ──
    // mode=dryRun（安全默认）：只校验、算将写入/重复条数，不落库；mode=merge：实际写入（走 transaction 原子化）。
    // 非法包（valid=false）由 importBundle 内部拦下、绝不写库。merge 成功时提示 needsReindex：向量索引不入包，需点「更新画像」重建召回。
    if (req.method === 'POST' && url.pathname === '/api/import-bundle') {
      const mode = url.searchParams.get('mode') === 'merge' ? 'merge' : 'dryRun';
      const bundle = await readJson(req);
      const plan = importBundle(bundle, portableDeps(stores), { mode });
      const body = { plan };
      if (mode === 'merge' && plan.valid) body.needsReindex = true; // 向量索引不入包 → 建议重建召回
      sendJson(res, 200, body);
      return;
    }

    // ── 恢复出厂 · 清空全部数据（不可逆）──
    // 通过受控管理 API 在一个事务中清证据、事件、画像、审计和 v0.6 的交互/语义层。
    // 索引是派生数据，在事务成功后再清空；这样「恢复出厂」不会遗漏含用户原话的副本。
    // 清理完成后调用 newSession()，同步清空当前会话窗口，避免旧上下文残留。
    if (req.method === 'POST' && url.pathname === '/api/factory-reset') {
      const subjectId = config.identity.subjectId;
      const { evidenceRemoved, eventRemoved, cognitionRemoved, auditRemoved } =
        await resetTestbenchSubject({ memoryApi, retriever, subjectId });
      newSession(); // 同步清空当前会话窗口，不保留旧上下文
      sendJson(res, 200, {
        ok: true,
        evidenceRemoved,
        eventRemoved,
        cognitionRemoved,
        auditRemoved,
      });
      return;
    }

    sendJson(res, 404, { error: 'not found' });
  } catch (e) {
    const clientRejection = clientInputRejection(e);
    if (clientRejection) {
      sendJson(res, clientRejection.statusCode, { error: clientRejection.message });
      return;
    }
    // 服务器错误只写本地控制台；不要把路径、依赖版本或调用栈带回浏览器。
    console.error('测试台请求处理失败：', e);
    sendJson(res, 500, { error: '请求处理失败，请查看本地服务日志。' });
  }
});

// 部署选项（部署契约·做法B）：.env 里 MEMOWEFT_EXPERIENCE_UI=off → 不起网页（只把 MemoWeft 当库用）。
// 只改展示层：显式等于 'off' 才拦；其它值（含未设）照常 listen。env 早已由上面 loadLLMPool() 触发的
// 再次调用幂等的 process.loadEnvFile()，确保 listen 前环境变量可用；无 .env 时忽略错误。
try {
  process.loadEnvFile();
} catch {
  /* 没有 .env 或已加载，忽略 */
}
if (process.env.MEMOWEFT_EXPERIENCE_UI === 'off') {
  console.log('\n  体验界面已在 .env 关闭（MEMOWEFT_EXPERIENCE_UI=off），未启动网页。');
  console.log(
    '  （把它当库 import 即可；想起网页请改回 on 或删掉该行，再跑 npm run experience）\n',
  );
} else {
  // 只绑 127.0.0.1（本机回环）：本服务无鉴权、直接读写个人画像/对话等隐私数据，
  // 且配置向导会经 HTTP 明文传 API key。若不指定 host，Node 默认绑 ::/0.0.0.0，
  // 同网段任何人都能读全部画像、发起对话、截明文 key。只开本机，杜绝这条外网面。
  server.listen(PORT, '127.0.0.1', () => {
    console.log(`\n  MemoWeft 测试台：画像 + 召回 → http://localhost:${PORT}`);
    console.log(`  证据库 → ${DB_PATH}`);
    console.log(`  运行日志 → ${LOG_DIR}\\run-${current.id}.jsonl`);
    console.log('  (Ctrl+C 停止)\n');
  });
}
