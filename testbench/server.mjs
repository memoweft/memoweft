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
import { createHash } from 'node:crypto';
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
  createNextBridge,
  mergeRecallForRecord,
  selectCarryForwardEvidenceIds,
} from './next-bridge.mjs';
import { readTestbenchRuntimeConfig } from './runtime-config.mjs';
import { getWorkbenchIdentity, getWorkbenchIdentityRoute } from './workbench-identity.mjs';
import {
  clientInputRejection,
  encodeDotenvEntries,
  readJson,
  requestRejection,
  setOwnPath,
} from './serverSecurity.mjs';

// 开发者模式·热调：进程一启动就深拷一份 config 当"出厂默认"（必须在任何请求改动 config 之前拍这张快照）。
// 之后 /api/config/reset 靠它把 config 恢复原样。structuredClone 是 Node 内置，零依赖。
const configDefaults = structuredClone(config);
// Capture before any optional .env load: lifecycle identity is inherited only
// from the managed launcher and must not be changed by an application config.
const workbenchIdentity = getWorkbenchIdentity();

const __dirname = dirname(fileURLToPath(import.meta.url));
const runtimeConfig = readTestbenchRuntimeConfig({ dirname: __dirname });
const PORT = runtimeConfig.port;
const LOG_DIR = runtimeConfig.logDir;
const DB_PATH = runtimeConfig.dbPath; // 独立库，不污染正式 ./dla.db
const MEMORY_AUTHORITY = runtimeConfig.memoryAuthorityLabel;
const NEXT_AUTHORITY = runtimeConfig.memoryAuthority === 'next';

// In Next-authority mode the 1.x stores remain an Evidence compatibility
// ledger, never a second mutable long-term-memory authority.  Keep deletion
// routes outside this list: privacy erasure remains available to the Owner.
const LEGACY_COGNITION_MUTATION_PATHS = new Set([
  '/api/distill',
  '/api/consolidate',
  '/api/refresh',
  '/api/attribute',
  '/api/ask',
  '/api/cognition/update',
]);
const LEGACY_COGNITION_MUTATION_CODE = 'LEGACY_COGNITION_MUTATION_BLOCKED';

function legacyCognitionMutationBlocked(req, url) {
  if (!NEXT_AUTHORITY || req.method !== 'POST') return false;
  if (LEGACY_COGNITION_MUTATION_PATHS.has(url.pathname)) return true;
  return url.pathname === '/api/import-bundle' && url.searchParams.get('mode') === 'merge';
}

function sendLegacyCognitionMutationBlocked(res) {
  sendJson(res, 409, {
    error: '当前记忆权威是已接受的 2.0 世界，不能修改 1.x 画像。',
    code: LEGACY_COGNITION_MUTATION_CODE,
    memoryAuthority: MEMORY_AUTHORITY,
  });
}

function hasExactKeys(value, expected) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const keys = Object.keys(value).sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

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
const REPLY_CLAUSE_GAP = '[^，,。！？!?\uff1b;\\n]{0,24}';
const REPLY_SENTENCE_GAP = '[^。！？!?\uff1b;\\n]{0,48}';
const REPLY_PERSIST_VERB =
  '(?:记住|记下|记好|记牢|记着|记在|存好|存下|保存|记录|收好|留存|留档|归档|存档|备案|写入|写进|录入|录进|登记|收录|加入|放进|存进)';

// These are speech-act patterns, not user-topic keywords. They cover a reply
// claiming that the current turn is already durable, promising that it will be
// durable later, or promising future recognition/retention. A plain past
// recall such as “我记得你之前说过…” intentionally does not match: it may be
// grounded in the accepted recall block supplied before reply generation.
const UNVERIFIED_MEMORY_CLAIM_PATTERNS = [
  new RegExp(
    `(?:已|已经|刚刚|刚|现在|这就|这下|我${REPLY_CLAUSE_GAP}(?:已|已经))${REPLY_CLAUSE_GAP}${REPLY_PERSIST_VERB}`,
    'iu',
  ),
  new RegExp(
    `${REPLY_PERSIST_VERB}${REPLY_CLAUSE_GAP}(?:了|啦|咯|喏|好了|完了|完成|成功|妥了|到位)`,
    'iu',
  ),
  new RegExp(
    `(?:我|我们|这边|系统)${REPLY_CLAUSE_GAP}(?:会|将|要|能|一定|肯定|保证|永远|一直)${REPLY_CLAUSE_GAP}(?:记住|记得|记着|记在|保存|记录|留存|保留|归档|存档|写入|录入|认得|认出来|知道|对得上|对上号|接得上|接得住|续得上|接着聊|放在心上|有印象|不忘)`,
    'iu',
  ),
  new RegExp(
    `(?:以后|今后|往后|之后|下次|下回|将来|日后|回头|从现在起|从今以后|再聊${REPLY_CLAUSE_GAP}时)${REPLY_SENTENCE_GAP}(?:记住|记得|记着|认得|认出来|知道|对得上|对上号|接得上|接得住|续得上|续上|接着聊|有印象|联系得上|不会忘|不忘|保留|留着|(?:不必|不用)(?:再|重新)?(?:提醒|解释|告诉|重复))`,
    'iu',
  ),
  new RegExp(`(?:不会|绝不会|永远不会)${REPLY_CLAUSE_GAP}(?:忘|忘掉|漏掉|丢掉|丢失)`, 'iu'),
  new RegExp(
    `(?:放|写|存|收|录|加)(?:进|入|到)${REPLY_CLAUSE_GAP}(?:长期|永久|跨对话)?${REPLY_CLAUSE_GAP}(?:记忆|档案|资料库|数据库|系统)`,
    'iu',
  ),
  new RegExp(
    `(?:长期记忆|永久记忆|跨对话记忆|档案|资料库|数据库|记忆库)${REPLY_CLAUSE_GAP}(?:已|已经|会|将|能|一直)?${REPLY_CLAUSE_GAP}(?:保存|存储|记录|写入|收录|保留|留存|有效)`,
    'iu',
  ),
  new RegExp(
    `(?:记在|刻在|印在)${REPLY_CLAUSE_GAP}(?:脑子|心里|心上|记忆里)${REPLY_CLAUSE_GAP}(?:了|啦|不会忘|一直)`,
    'iu',
  ),
  /\b(?:i(?:'ve| have)|we(?:'ve| have)|it(?:'s| is)|this is|that is)\b[^,.!?;\n]{0,28}\b(?:remembered|saved|stored|recorded|filed|archived|logged|written|added|committed|locked in|tucked (?:it |that |this )?away)\b/iu,
  /\b(?:i(?:'ll| will)|we(?:'ll| will)|i(?:'m| am) going to)\b[^,.!?;\n]{0,28}\b(?:remember|save|store|record|keep|retain|recognize|know|file|archive)\b/iu,
  /\b(?:i|we)\b[^,.!?;\n]{0,20}\b(?:won't|will not|never)\b[^,.!?;\n]{0,16}\b(?:forget|lose|drop)\b/iu,
  /\b(?:next time|from now on|in the future|in later conversations?|when (?:you|we) (?:mention|discuss) it again)\b[^,.!?;\n]{0,36}\b(?:remember|recognize|know|recall|pick (?:it )?up|connect)\b/iu,
  /\b(?:saved|stored|recorded|filed|archived|logged|written|added|committed)\b[^,.!?;\n]{0,24}\b(?:for (?:later|next time)|in (?:long[- ]term )?memory|in (?:the )?(?:database|profile|records?))\b/iu,
  /\b(?:this|that|it)\s+(?:is|'s)\s+(?:now\s+)?(?:in|part of)\s+(?:my\s+)?(?:long[- ]term\s+)?memory\b/iu,
  /\bi(?:'ve| have)\b[^,.!?;\n]{0,20}\b(?:got|put)\b[^,.!?;\n]{0,16}\b(?:on file|in memory|in the records?)\b/iu,
  /\b(?:consider|mark) (?:it|that|this) (?:remembered|saved|stored|recorded|done)\b/iu,
  /\b(?:this|that|it)\b[^,.!?;\n]{0,20}\b(?:stays?|is staying|will stay)\b[^,.!?;\n]{0,16}\b(?:with me|in mind|on file)\b/iu,
  /\b(?:this|that|it)(?:'ll| will)\b[^,.!?;\n]{0,20}\bstay\b[^,.!?;\n]{0,16}\b(?:with me|in mind|on file)\b/iu,
  /\byou\b[^,.!?;\n]{0,16}\b(?:won't|will not)\b[^,.!?;\n]{0,18}\b(?:repeat|remind|explain|tell me)\b[^,.!?;\n]{0,18}\b(?:again|next time|in the future)\b/iu,
];

const HONEST_NON_ACCEPTANCE_PATTERNS = [
  /(?:已|已经)?(?:保存|记录|存储)(?:为|成|到)?\s*(?:Evidence|证据)/giu,
  new RegExp(
    `(?:尚未|还没有|还没|并未|未)(?:被|正式|真正|实际)?${REPLY_CLAUSE_GAP}(?:记住|记下|保存|记录|写入|写进|录入|加入|进入|存入|归档|存档|接受)`,
    'giu',
  ),
  new RegExp(
    `(?:不|并不|不能|无法|没法)(?:代表|表示|说明|保证|承诺|确认)${REPLY_CLAUSE_GAP}(?:已|已经|会|将)?${REPLY_CLAUSE_GAP}(?:记住|记下|记得|保存|记录|写入|录入|保留|不会忘)`,
    'giu',
  ),
  /\b(?:has|have|is|are|was|were)?\s*not\s+(?:yet\s+)?(?:been\s+)?(?:remembered|saved|stored|recorded|added|written|committed|accepted|filed|archived)\b[^,.!?;\n]{0,30}/giu,
  /\b(?:cannot|can't|do not|don't|does not|doesn't)\s+(?:mean|promise|guarantee|confirm)\b[^,.!?;\n]{0,55}/giu,
];

function withoutHonestNonAcceptanceClauses(value) {
  let remaining = value;
  for (const pattern of HONEST_NON_ACCEPTANCE_PATTERNS) {
    remaining = remaining.replace(pattern, ' ');
  }
  return remaining;
}

export function replyMakesUnverifiedMemoryClaim(value) {
  if (typeof value !== 'string' || !value.trim()) return false;
  const remaining = withoutHonestNonAcceptanceClauses(
    value.normalize('NFKC').replace(/[‘’]/gu, "'"),
  );
  return UNVERIFIED_MEMORY_CLAIM_PATTERNS.some((pattern) => pattern.test(remaining));
}

/**
 * A chat reply is produced before the Next candidate exists, and Owner
 * acceptance can only happen in a later decision request. Therefore any
 * completion/promise claim about this turn is unknowable at this boundary.
 * Fail closed to a natural acknowledgement; do not let a false claim enter
 * either the HTTP response or Conversation's subsequent working-memory window.
 */
export function enforceReplyMemoryHonesty(value) {
  const reply = typeof value === 'string' ? value.trim() : '';
  if (!replyMakesUnverifiedMemoryClaim(reply)) return reply;
  return /[\p{Script=Han}]/u.test(reply) ? '我明白你的意思了。' : 'I understand what you mean.';
}

export function createReplyHonestyGuard(inner) {
  if (!inner || typeof inner.chat !== 'function') {
    throw new TypeError('reply honesty guard requires an LLM client');
  }
  return {
    async chat(messages) {
      return enforceReplyMemoryHonesty(await inner.chat(messages));
    },
    get callCount() {
      return inner.callCount;
    },
    get tier() {
      return inner.tier;
    },
    get usage() {
      return inner.usage;
    },
  };
}

// 模型池：写路径（distill/consolidate/attribute/trends）使用写模型，对话使用聊天模型。
const llmPool = loadLLMPool();
const llm = llmPool.for('write'); // 写路径统一用它（updateProfile / 手动 distill/consolidate/attribute/ask / trends）
const chatLLM = createReplyHonestyGuard(llmPool.for('chat')); // 回复在进入会话窗口前强制经过诚实性边界

// Next 只是一条候选记忆实验支路，绝不能因本地 Lab 缺席而阻断 1.x 聊天、Evidence 或画像。
// URL 校验失败同样降级；配置错误只在本机控制台留一条不含 URL/请求体的诊断。
let nextBridge = null;
try {
  nextBridge = createNextBridge();
} catch {
  console.warn('  ⚠️ MemoWeft Next bridge 配置不可用；1.x 测试台照常运行。');
}
// Legacy cognition migration is intentionally one-at-a-time.  A live world
// preflight plus this local guard prevents two browser requests from both
// observing an empty review queue and staging two new model operations.
let legacyCognitionMigrationInFlight = false;

// LLM 网络错误（socket closed / fetch failed 等）不应终止诊断台进程。
process.on('unhandledRejection', (e) =>
  console.error('[兜底] 未处理的 rejection（服务继续）：', e instanceof Error ? e.message : e),
);

// 召回：配了 MEMOWEFT_EMBED_*（或兼容 DLA_EMBED_*）用向量召回；否则降级为空召回（回话不注入画像，不报错）。
const embedConfig = loadEmbedConfig();
const retriever = embedConfig
  ? new VectorRetriever(DB_PATH, new OpenAICompatEmbedder(embedConfig))
  : new NullRetriever();
if (!embedConfig) {
  console.log(
    NEXT_AUTHORITY
      ? '  ℹ️ 未配 MEMOWEFT_EMBED_*：1.0 向量召回关闭；2.0 已接受世界仍可进行本地正式召回'
      : '  ⚠️ 未配 MEMOWEFT_EMBED_*（或兼容 DLA_EMBED_*），召回降级为空（回话不注入画像）',
  );
}

// loadLLMPool 允许无 .env 启动；未配置写模型时回退到聊天模型，详见 src/llm/pool.ts。

// 诊断台作为宿主注入 MemoWeft-aware 设定；语气归宿主，Core 默认保持中性。
// 这里只教模型如何表达；上面的程序边界才是最终保证。
export const REPLY_PERSONA =
  '你是一个长期陪伴用户的助手。' +
  '只有明确标注为“已接受记忆”的内容，才是已经持久化的长期记忆。' +
  '当前用户消息在你生成回复时，尚未得知是否形成候选，更没有经过用户接受；' +
  '你可以用“我明白了”“原来如此”自然表达理解，但不能宣称本轮内容已经或一定会被记住、保存、写入、以后认得，也不能承诺不会忘。' +
  '下面若给出「你已了解关于这个用户的情况」，那些内容来自已接受记忆，可以自然使用，也可以说“我记得你之前说过…”。' +
  '如果用户直接问本轮是否已记住，就说当前还未确认进入长期记忆；除非用户追问，不主动解释后台流程。' +
  '语气自然、简洁、真诚，别生硬地复述记忆内容。';

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

// 清空聊天记录只处理会话日志与内存中的 Conversation 窗口。日志改名保留，
// 1.0 Evidence/画像库和 Next 已接受世界都不在这个函数的触及范围内。
async function clearChatSessions() {
  let files = [];
  try {
    files = (await readdir(LOG_DIR)).filter((file) => /^run-s-.*\.jsonl$/.test(file));
  } catch {
    files = [];
  }
  const batchId = `${Date.now()}-${seq++}`;
  let archivedCount = 0;
  for (const file of files) {
    try {
      await rename(join(LOG_DIR, file), join(LOG_DIR, `${file}.cleared-${batchId}`));
      archivedCount += 1;
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }
  const liveSessionCount = sessions.size;
  sessions.clear();
  newSession();
  return { archivedCount, liveSessionCount, batchId };
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
// 默认批量更新：累计达到 config.profileUpdate.batchSize 条，或空闲达到 idleMinutes 后更新画像。
// 隔离测试台可用启动期开关显式改为每轮安排一次，不能由 HTTP 请求修改。
let profileUpdating = false;
let bgTimer = null;
let bgLast = null; // 上次更新结果摘要，供前端轮询显示
let seedProgress = { running: false, step: 0, total: 6, label: '空闲' }; // integration testing 灌数据进度（脚本上报、前端轮询画进度条）

async function runProfileUpdate(trigger = 'background', session = current) {
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
    session.logger.appendProfileUpdate({
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
    session.logger.appendProfileUpdate({
      trigger,
      error: e instanceof Error ? e.message : String(e),
    });
    throw e;
  } finally {
    profileUpdating = false;
  }
}

// 触发器：默认每次对话完成后累加计数，达到 batchSize 时立即安排更新；否则重置空闲计时，
// 在 idleMinutes 内无新对话时更新。测试台的显式 per-turn 开关会立即安排更新；写路径仍不阻塞回复。
let pendingSinceUpdate = 0;
function scheduleBackgroundUpdate(session) {
  if (NEXT_AUTHORITY) return;
  pendingSinceUpdate++;
  const { batchSize, idleMinutes } = config.profileUpdate;
  if (runtimeConfig.profileEveryTurn || pendingSinceUpdate >= batchSize) {
    if (bgTimer) {
      clearTimeout(bgTimer);
      bgTimer = null;
    } // 攒够一批 → 立刻排，清掉空闲计时
    void triggerProfileUpdate(session);
  } else {
    if (bgTimer) clearTimeout(bgTimer); // 又聊了 → 重置空闲计时
    bgTimer = setTimeout(() => {
      bgTimer = null;
      void triggerProfileUpdate(session);
    }, idleMinutes * 60000);
  }
}
async function triggerProfileUpdate(session = current) {
  try {
    const r = await runProfileUpdate('background', session);
    if (r === null) {
      // 手动更新占用锁时，保留待处理计数并在 10 秒后重试。
      if (bgTimer) clearTimeout(bgTimer);
      bgTimer = setTimeout(() => {
        bgTimer = null;
        void triggerProfileUpdate(session);
      }, 10000);
      return;
    }
    pendingSinceUpdate = 0; // 更新成功 → 计数清零，重新攒下一批
  } catch (e) {
    // LLM 网络错误不会终止服务；runProfileUpdate 已记录错误。
    console.error('后台更新画像失败（已兜底，不崩服务）：', e instanceof Error ? e.message : e);
  }
}

export const MAX_CHAT_HISTORY_RESPONSE_BYTES = 512 * 1024;

const EXPECTED_CLIENT_DISCONNECT_CODES = new Set([
  'ECONNRESET',
  'EPIPE',
  'ERR_STREAM_DESTROYED',
  'ERR_HTTP_REQUEST_ABORTED',
]);

function responseCanWrite(res) {
  return res && res.destroyed !== true && res.writableEnded !== true;
}

function isExpectedClientDisconnect(error) {
  return EXPECTED_CLIENT_DISCONNECT_CODES.has(error?.code);
}

/**
 * A browser may navigate, reload, or abort a poll while JSON is being written.
 * ServerResponse emits those socket failures asynchronously; without a local
 * listener Node treats the `error` event as uncaught and can terminate the
 * entire diagnostics process even though the request itself is disposable.
 */
function guardRequestSocket(req, res) {
  const observe = (stream, label) => {
    if (!stream || typeof stream.on !== 'function') return;
    stream.on('error', (error) => {
      if (!isExpectedClientDisconnect(error)) {
        console.error(
          `测试台${label}流异常（服务继续）：`,
          error instanceof Error ? error.message : error,
        );
      }
    });
  };
  observe(req, '请求');
  observe(res, '响应');
}

function sendJson(res, code, data) {
  if (!responseCanWrite(res)) return false;
  const raw = JSON.stringify(data);
  try {
    if (!responseCanWrite(res)) return false;
    if (!res.headersSent) {
      res.writeHead(code, {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': String(Buffer.byteLength(raw)),
      });
    }
    if (!responseCanWrite(res)) return false;
    res.end(raw);
    return true;
  } catch (error) {
    if (!responseCanWrite(res) || isExpectedClientDisconnect(error)) return false;
    throw error;
  }
}

function compactHistoryText(value, maxChars) {
  const text = typeof value === 'string' ? value : value == null ? '' : String(value);
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars)}…[历史显示已截断]`;
}

function objectRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

function compactCandidateItem(value) {
  if (!objectRecord(value)) return compactHistoryText(value, 1000);
  const item = {};
  if (typeof value.id === 'string') item.id = compactHistoryText(value.id, 240);
  for (const key of ['content', 'summary', 'canonical_name', 'relation_type']) {
    if (typeof value[key] === 'string' && value[key]) {
      item[key] = compactHistoryText(value[key], 1000);
      break;
    }
  }
  if (Object.keys(item).length === 0 && typeof value.kind === 'string') {
    item.kind = compactHistoryText(value.kind, 120);
  }
  return item;
}

function compactCandidateMemory(value) {
  const memory = objectRecord(value) ?? {};
  const result = {};
  for (const key of ['entities', 'relationships', 'events', 'cognitions']) {
    result[key] = Array.isArray(memory[key])
      ? memory[key].slice(0, 5).map(compactCandidateItem)
      : [];
  }
  return result;
}

function compactEvidence(value) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 5).map((entry) => {
    if (!objectRecord(entry)) return compactHistoryText(entry, 1200);
    const result = {};
    if (typeof entry.evidenceId === 'string') {
      result.evidenceId = compactHistoryText(entry.evidenceId, 240);
    }
    const text = entry.text ?? entry.content;
    if (text !== undefined) result.text = compactHistoryText(text, 1200);
    return result;
  });
}

function compactPipeline(value) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 10).map((entry) => {
    if (!objectRecord(entry)) return { detail: compactHistoryText(entry, 500) };
    return {
      ...(typeof entry.name === 'string' ? { name: compactHistoryText(entry.name, 120) } : {}),
      ...(typeof entry.state === 'string' ? { state: compactHistoryText(entry.state, 120) } : {}),
      ...(entry.detail !== undefined ? { detail: compactHistoryText(entry.detail, 500) } : {}),
    };
  });
}

function compactFailure(value) {
  if (!objectRecord(value)) return value == null ? null : compactHistoryText(value, 500);
  return {
    ...(typeof value.kind === 'string' ? { kind: compactHistoryText(value.kind, 160) } : {}),
    ...(Array.isArray(value.codes)
      ? { codes: value.codes.slice(0, 10).map((code) => compactHistoryText(code, 300)) }
      : {}),
    ...(Number.isInteger(value.attempts) ? { attempts: value.attempts } : {}),
  };
}

function compactCorrection(value) {
  if (!objectRecord(value)) return undefined;
  return {
    ...(value.replacementContent !== undefined
      ? { replacementContent: compactHistoryText(value.replacementContent, 1200) }
      : {}),
    ...(Array.isArray(value.supersededCognitions)
      ? { supersededCognitions: value.supersededCognitions.slice(0, 5).map(compactCandidateItem) }
      : {}),
  };
}

/**
 * History is a UI recovery projection, not a second copy of the 2.0 ledger.
 * Keep the Owner decision keys and a human-readable candidate/Evidence
 * summary, while intentionally excluding the full world, run transcript,
 * assistant context, and any other duplicated diagnostic payload.
 */
export function compactNextMemoryForHistory(value) {
  const wrapped = objectRecord(value);
  if (!wrapped) return undefined;
  const run = objectRecord(wrapped.run);
  const explicitProposal =
    objectRecord(wrapped.memoryProposal) ?? objectRecord(run?.memoryProposal);
  const proposal = explicitProposal ?? run ?? wrapped;
  const state = proposal.state ?? run?.state ?? wrapped.state;
  const pipeline = wrapped.pipeline ?? run?.pipeline ?? proposal.pipeline;
  const failure = wrapped.memoryFailure ?? run?.memoryFailure ?? run?.failure ?? proposal.failure;
  const candidateCorrection = compactCorrection(proposal.candidateCorrection);
  const correction = compactCorrection(proposal.correction);
  const result = {
    ...(typeof wrapped.status === 'string'
      ? { status: compactHistoryText(wrapped.status, 80) }
      : {}),
    ...(typeof wrapped.code === 'string' ? { code: compactHistoryText(wrapped.code, 160) } : {}),
  };

  if (typeof state === 'string') {
    const projectedProposal = {
      ...(typeof proposal.id === 'string' ? { id: compactHistoryText(proposal.id, 240) } : {}),
      ...(typeof proposal.runId === 'string'
        ? { runId: compactHistoryText(proposal.runId, 240) }
        : {}),
      state: compactHistoryText(state, 80),
      ...(typeof proposal.reviewId === 'string'
        ? { reviewId: compactHistoryText(proposal.reviewId, 240) }
        : {}),
      ...(typeof proposal.resultHash === 'string'
        ? { resultHash: compactHistoryText(proposal.resultHash, 240) }
        : {}),
      ...(Number.isInteger(proposal.baseRevision) ? { baseRevision: proposal.baseRevision } : {}),
      ...(proposal.candidateMemory !== undefined
        ? { candidateMemory: compactCandidateMemory(proposal.candidateMemory) }
        : {}),
      ...(proposal.evidence !== undefined ? { evidence: compactEvidence(proposal.evidence) } : {}),
      ...(candidateCorrection ? { candidateCorrection } : {}),
      ...(correction ? { correction } : {}),
    };
    result.memoryProposal = projectedProposal;
  }
  if (failure !== undefined && failure !== null) result.memoryFailure = compactFailure(failure);
  if (pipeline !== undefined) result.pipeline = compactPipeline(pipeline);
  return result;
}

function compactHistoryTurn(turn) {
  return {
    turn: turn.turn,
    userInput: compactHistoryText(turn.userInput, 4000),
    reply: compactHistoryText(turn.reply, 12_000),
    ...(turn.nextMemory !== undefined
      ? { nextMemory: compactNextMemoryForHistory(turn.nextMemory) }
      : {}),
  };
}

export function buildBoundedHistoryPayload({ sessionId, turns, extra = {} }) {
  const projected = turns.map(compactHistoryTurn);
  const originalCount = projected.length;
  let payload;
  do {
    const omittedTurnCount = originalCount - projected.length;
    payload = {
      ...extra,
      sessionId,
      turns: projected,
      ...(omittedTurnCount > 0 ? { historyTruncated: true, omittedTurnCount } : {}),
    };
    if (
      projected.length <= 1 ||
      Buffer.byteLength(JSON.stringify(payload)) <= MAX_CHAT_HISTORY_RESPONSE_BYTES
    ) {
      break;
    }
    projected.shift();
  } while (true);
  if (Buffer.byteLength(JSON.stringify(payload)) > MAX_CHAT_HISTORY_RESPONSE_BYTES) {
    // An adversarial single record should fail closed instead of allocating or
    // writing an unbounded response. Normal records cannot reach this branch
    // after the field and array caps above.
    return {
      ...extra,
      sessionId,
      turns: [],
      historyTruncated: true,
      omittedTurnCount: originalCount,
      error: '该会话的单轮历史超过安全显示上限。',
      code: 'CHAT_HISTORY_RESPONSE_TOO_LARGE',
    };
  }
  return payload;
}

/**
 * Construct a migration request only from original, eligible spoken Evidence.
 * A legacy cognition's derived content intentionally never enters this path.
 */
function buildLegacyCognitionMigration(cognitionId) {
  const turns = cogStore
    .sourcesOf(cognitionId)
    .filter((source) => source.relation === 'support')
    .map((source) => store.get(source.evidenceId))
    .filter(
      (evidence) =>
        evidence &&
        evidence.sourceKind === 'spoken' &&
        evidence.allowLocalRead === true &&
        evidence.allowInference === true &&
        typeof evidence.rawContent === 'string' &&
        evidence.rawContent.length > 0,
    )
    .sort((a, b) => a.occurredAt.localeCompare(b.occurredAt) || a.id.localeCompare(b.id))
    .map((evidence) => ({
      turnId: evidence.id,
      role: 'user',
      content: evidence.rawContent,
      occurredAt: evidence.occurredAt,
    }));

  if (turns.length === 0) return null;
  // The downstream bridge permits at most twenty exact user turns and 20k
  // source characters.  Reject rather than selecting a partial support set,
  // because a partial migration would alter the source meaning silently.
  if (turns.length > 20 || turns.reduce((sum, turn) => sum + turn.content.length, 0) > 20_000) {
    return { error: 'LEGACY_COGNITION_MIGRATION_TOO_LARGE' };
  }
  const fingerprint = createHash('sha256').update(JSON.stringify(turns)).digest('hex');
  return { operationId: `legacy-cognition:sha256:${fingerprint}`, turns };
}

function safeNextMemory(result) {
  if (!result || result.status !== 'ok') {
    return {
      status: result?.status === 'failed' ? 'failed' : 'unavailable',
      code: typeof result?.code === 'string' ? result.code : 'NEXT_UNAVAILABLE',
    };
  }
  const value = result.value && typeof result.value === 'object' ? result.value : {};
  const run = value.run ?? (typeof value.id === 'string' ? value : undefined);
  return {
    status: 'ok',
    ...(value.memoryProposal !== undefined ? { memoryProposal: value.memoryProposal } : {}),
    ...(value.memoryFailure !== undefined
      ? { memoryFailure: value.memoryFailure }
      : value.failure !== undefined
        ? { memoryFailure: value.failure }
        : {}),
    ...(value.pipeline !== undefined
      ? { pipeline: value.pipeline }
      : run?.pipeline !== undefined
        ? { pipeline: run.pipeline }
        : {}),
    ...(value.world !== undefined ? { world: value.world } : {}),
    ...(run !== undefined ? { run } : {}),
  };
}

async function stageNextMemoryTurn(input) {
  if (!nextBridge) return { status: 'unavailable', code: 'NEXT_BRIDGE_DISABLED' };
  return safeNextMemory(await nextBridge.stageMemoryTurn(input));
}

/**
 * The Next Lab may provide already accepted 2.0 cognitions for this reply,
 * but it never owns the 1.x chat turn.  Its failure is intentionally silent
 * here: 1.x still stores the user Evidence and makes its one normal reply.
 */
async function recallNextReplyMemory(query) {
  if (!nextBridge) return [];
  const result = await nextBridge.recallMemory(query);
  return result.status === 'ok' ? result.value.memories : [];
}

async function forwardNext(res, operation) {
  if (!nextBridge) {
    sendJson(res, 503, { next: { status: 'unavailable', code: 'NEXT_BRIDGE_DISABLED' } });
    return;
  }
  const result = await operation(nextBridge);
  if (result.status !== 'ok') {
    sendJson(res, result.status === 'unavailable' ? 503 : 400, {
      next: { status: result.status, code: result.code },
    });
    return;
  }
  sendJson(res, 200, result.value);
}

function sendNextBridgeFailure(res, result) {
  sendJson(res, result?.status === 'unavailable' ? 503 : 400, {
    next: {
      status: result?.status === 'unavailable' ? 'unavailable' : 'failed',
      code: typeof result?.code === 'string' ? result.code : 'NEXT_UNAVAILABLE',
    },
  });
}

export const server = createServer(async (req, res) => {
  guardRequestSocket(req, res);
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

    if (legacyCognitionMutationBlocked(req, url)) {
      sendLegacyCognitionMutationBlocked(res);
      return;
    }

    const workbenchIdentityRoute = getWorkbenchIdentityRoute({
      method: req.method,
      pathname: url.pathname,
      identity: workbenchIdentity,
    });
    if (workbenchIdentityRoute) {
      sendJson(res, workbenchIdentityRoute.statusCode, workbenchIdentityRoute.body);
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

    // 一轮对话：1.x 先安全落用户 Evidence 并完成自身 recall；紧接着在唯一一次原有聊天调用
    // 前，只读找 Next 2.0 已接受记忆。然后才将本轮 Evidence + 既有上下文送到 Next 提候选记忆。
    // Next 不生成第二个聊天回复，且任一 Next 失败只能降级，不能阻断 Evidence 或聊天。
    if (req.method === 'POST' && url.pathname === '/api/chat') {
      const { text, originId, sessionId: reqSid } = await readJson(req);
      // The active UI session can change while the model/Next bridge awaits.
      // Resolve it once after parsing and keep this request on that exact
      // conversation/logger through every later await.  Requests from older
      // clients without sessionId retain the historical "current at parse"
      // behavior.
      const session = reqSid && sessions.has(reqSid) ? sessions.get(reqSid) : current;
      if (reqSid && sessions.has(reqSid)) current = session; // preserve existing explicit-open behavior
      const userText = String(text ?? '');
      let trustedReplyMemory = [];
      const outcome = await session.convo.handle(userText, {
        originId: originId ?? null,
        // Conversation invokes this only after durable 1.x Evidence (and
        // skips native cognition recall under the Next authority).  The
        // closure retains exactly
        // what was injected so the diagnostic log never claims a phantom 2.0
        // recall item.
        skipNativeCognitionRecall: NEXT_AUTHORITY,
        loadTrustedReplyMemory: async () => {
          trustedReplyMemory = await recallNextReplyMemory(userText);
          return trustedReplyMemory;
        },
      });
      const turnDraft = {
        // 用户 Evidence 的 recorded/occurred 事实来自 1.x store；以它为 bridge 时间，重试可保持同一请求哈希。
        ts: outcome.storedEvidence.occurredAt,
        // 尚未 append，因此这里只是给 bridge 排除当前轮的占位值；实际持久轮号仍完全由 RunLogger 分配。
        turn: -1,
        userInput: String(text ?? ''),
        reply: outcome.reply,
        evidence: [{ id: outcome.storedEvidence.id, summary: outcome.storedEvidence.summary }],
      };
      const previousRecords = session.logger.readRecent(200);
      const carryForwardEvidenceIds = selectCarryForwardEvidenceIds({
        sessionId: session.id,
        record: turnDraft,
        previousRecords,
      });
      const nextMemory = await stageNextMemoryTurn({
        sessionId: session.id,
        record: turnDraft,
        previousRecords,
        carryForwardEvidenceIds,
      });
      const record = session.logger.appendTurn({
        ...turnDraft,
        reply: outcome.reply,
        recall: mergeRecallForRecord(outcome.recall, trustedReplyMemory),
        llmCalls: outcome.llmCalls,
        error: outcome.error,
        nextMemory,
      });
      sendJson(res, 200, { record, sessionId: session.id, logFile: session.logger.file });
      scheduleBackgroundUpdate(session); // keep deferred diagnostics with the request's session
      return;
    }

    // Next 的查询/世界/决定接口只做同源、字段白名单代理；浏览器不可借此访问 Lab 的任意路径。
    if (req.method === 'GET' && url.pathname === '/api/next/status') {
      await forwardNext(res, (bridge) => bridge.getStatus());
      return;
    }
    if (req.method === 'GET' && url.pathname === '/api/next/memory-world') {
      await forwardNext(res, (bridge) => bridge.getMemoryWorld());
      return;
    }
    if (req.method === 'GET' && url.pathname === '/api/next/memory-runs') {
      await forwardNext(res, (bridge) => bridge.getMemoryRuns());
      return;
    }
    if (req.method === 'POST' && url.pathname === '/api/next/memory-decisions') {
      const body = await readJson(req);
      await forwardNext(res, (bridge) => bridge.decideMemory(body));
      return;
    }
    if (req.method === 'POST' && url.pathname === '/api/next/memory-queries') {
      const body = await readJson(req);
      await forwardNext(res, (bridge) => bridge.queryMemory(body));
      return;
    }
    if (req.method === 'POST' && url.pathname === '/api/next/memory-corrections') {
      const body = await readJson(req);
      await forwardNext(res, (bridge) => bridge.correctMemory(body));
      return;
    }

    // Owner-triggered, evidence-only migration from one existing 1.x
    // cognition into the 2.0 candidate path.  The browser can name exactly one
    // cognition id; the server independently reads only eligible *supporting
    // spoken Evidence*, never the cognition's derived profile text.
    if (req.method === 'POST' && url.pathname === '/api/next/legacy-cognition-migrations') {
      const body = await readJson(req);
      if (
        !hasExactKeys(body, ['cognitionId']) ||
        typeof body.cognitionId !== 'string' ||
        !body.cognitionId ||
        body.cognitionId.length > 200
      ) {
        sendJson(res, 400, {
          error: '迁移请求必须且只能包含有效的 cognitionId。',
          code: 'LEGACY_COGNITION_ID_INVALID',
        });
        return;
      }
      if (!cogStore.get(body.cognitionId)) {
        sendJson(res, 404, {
          error: '未找到要迁移的 1.x 画像。',
          code: 'LEGACY_COGNITION_NOT_FOUND',
        });
        return;
      }
      if (legacyCognitionMigrationInFlight) {
        sendJson(res, 409, {
          error: '已有一条 legacy 画像正在迁移，请等待其完成后再迁移下一条。',
          code: 'LEGACY_MIGRATION_IN_FLIGHT',
        });
        return;
      }
      if (!nextBridge) {
        sendNextBridgeFailure(res, { status: 'unavailable', code: 'NEXT_BRIDGE_DISABLED' });
        return;
      }
      legacyCognitionMigrationInFlight = true;
      try {
        // Read the live accepted world before reading legacy Evidence or
        // invoking the long local-model migration.  Unknown/malformed world
        // state is fail-closed, never treated as an empty review queue.
        const worldResult = await nextBridge.getMemoryWorld();
        if (worldResult.status !== 'ok') {
          sendNextBridgeFailure(res, worldResult);
          return;
        }
        const pendingReviews = worldResult.value?.pendingReviews;
        if (!Array.isArray(pendingReviews)) {
          sendNextBridgeFailure(res, { status: 'failed', code: 'NEXT_INVALID_MEMORY_WORLD' });
          return;
        }
        if (pendingReviews.length > 0) {
          sendJson(res, 409, {
            code: 'LEGACY_MIGRATION_PENDING_REVIEW_EXISTS',
            error: '请先处理当前待决定候选，再迁移下一条',
            message: '请先处理当前待决定候选，再迁移下一条',
          });
          return;
        }
        const migration = buildLegacyCognitionMigration(body.cognitionId);
        if (migration === null) {
          sendJson(res, 422, {
            error: '该画像没有可用于迁移的原始用户 Evidence。',
            code: 'LEGACY_COGNITION_NO_ELIGIBLE_EVIDENCE',
          });
          return;
        }
        if ('error' in migration) {
          sendJson(res, 422, {
            error: '该画像的原始用户 Evidence 超过安全迁移上限。',
            code: migration.error,
          });
          return;
        }
        await forwardNext(res, (bridge) => bridge.stageLegacyMemory(migration));
      } finally {
        legacyCognitionMigrationInFlight = false;
      }
      return;
    }

    // 后台消化状态（前端轮询：转圈 / 待消化 / 刚更新了什么）
    if (req.method === 'GET' && url.pathname === '/api/bg-status') {
      sendJson(res, 200, {
        authority: MEMORY_AUTHORITY,
        memoryAuthority: MEMORY_AUTHORITY,
        updating: profileUpdating,
        pending: !!bgTimer,
        last: bgLast,
      });
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
      sendJson(res, 200, { llmReady, embedReady, memoryAuthority: MEMORY_AUTHORITY });
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
      sendJson(res, 200, buildBoundedHistoryPayload({ sessionId: sid || current.id, turns }));
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

    // 批量清空聊天记录：全部会话日志软归档并建立一个空白当前会话。
    // 该入口不删除 1.0 兼容数据，也不调用 Next 正式记忆写路径。
    if (req.method === 'POST' && url.pathname === '/api/sessions/clear') {
      const result = await clearChatSessions();
      sendJson(res, 200, {
        ok: true,
        scope: 'chat-only',
        currentId: current.id,
        archivedCount: result.archivedCount,
        liveSessionCount: result.liveSessionCount,
        recoveryBatch: result.batchId,
        legacyMemoryPreserved: true,
        acceptedWorldPreserved: true,
      });
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
        .filter((t) => t.kind !== 'profile_update' && t.userInput != null);
      sendJson(
        res,
        200,
        buildBoundedHistoryPayload({ sessionId: s.id, turns, extra: { ok: true } }),
      );
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
        scope: 'legacy-only',
        memoryAuthority: MEMORY_AUTHORITY,
        message: '恢复出厂仅清除 1.x legacy 数据；不会清除已接受的 2.0 世界。',
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
    console.log(
      NEXT_AUTHORITY
        ? `\n  MemoWeft 测试台：原 1.0 控制台 + 2.0 正式记忆 → http://localhost:${PORT}`
        : `\n  MemoWeft 测试台：画像 + 召回 → http://localhost:${PORT}`,
    );
    console.log(`  证据库 → ${DB_PATH}`);
    console.log(`  运行日志 → ${LOG_DIR}\\run-${current.id}.jsonl`);
    console.log('  (Ctrl+C 停止)\n');
  });
}
