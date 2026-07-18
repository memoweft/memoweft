/**
 * @memoweft/host —— 本地单用户参考宿主。
 *
 * 通过公开入口 `import 'memoweft'` 调用 Core，提供聊天、记忆管理、导入导出、对话历史和记忆图谱。
 * 后台整理、聊天历史和多对话编排均由宿主实现；Core 只提供 `updateProfile()` 等明确调用的能力，
 * 不会自行启动计划任务。记忆管理只使用 `core.memory.*` 受控 API，不直接访问存储层。
 *
 * 多对话状态：会话册（列表/新建/切换/归档）是宿主的持久数据，由 sessions 目录的 JSONL 文件组成，
 *   不从 Core 掏——Core 的 conversations Map 只是活跃实例窗口缓存、故意不暴露枚举。Host 维护两样进程内状态：
 *   ① currentConvId：当前活跃对话（模块级，单用户单进程）；
 *   ② activatedConvs：本进程已在 Core 建过实例的对话集合（决定 chat 时要不要传 seedTurns 重建窗口）。
 *   续聊靠 seedTurns：切到一条【本进程还没在 Core 建实例】的旧对话，下次 chat 从其历史读最近几轮转 Turn[] 作 seedTurns，
 *   让 Core 首次建实例时重建上下文窗口（Core 语义：seedTurns 仅首次建实例生效，后续复用不重建）。
 *
 * 模块边界：只通过 `import 'memoweft'` 调用 Core；不得导入 `../../src/*` 内部实现。
 * 数据隔离：Host 用自己独立的库（默认 apps/memoweft-host/data/host.db，env MEMOWEFT_HOST_DB 覆盖），
 *   聊天历史落【库同目录下的 sessions/】（跟随库路径：隔离库时聊天历史也隔离），与 testbench 互不污染。
 * 仅绑定 127.0.0.1，以减少外部网络暴露；服务仍无生产级认证，不能原样对外发布。
 */
import { createServer, type IncomingMessage } from 'node:http';
import { chmodSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { basename, dirname, join } from 'node:path';
import { createMemoWeftCore, config, type MemoryBundle, type Observation } from 'memoweft';
import { createProfileScheduler } from './scheduler.ts';
import { createChatHistory, type HistoryTurn } from './chatHistory.ts';
import { credBand } from './confBand.ts';
import {
  getExperience,
  listExperiences,
  listPlugins,
  ALL_PLUGINS,
  EXPERIENCE_IDS,
  DEFAULT_EXPERIENCE_ID,
} from './experiences/index.ts';
import { buildEnvResponse } from './genEnv.ts';

// .env 固定在本 Host 目录（apps/memoweft-host/.env），用脚本自身位置定位、不受启动时 cwd 影响。
//   （`npm start -w` 会把 cwd 切到本包目录，但从仓库根直接 `node …` 时 cwd 是根——写死路径避免"存一处读另一处"。）
//   配置向导的「保存配置并启用」(/api/save-env) 写的就是这同一个文件，读写一致。
const ENV_PATH = process.env.MEMOWEFT_HOST_ENV_PATH ?? join(import.meta.dirname, '..', '.env');
// 先读 .env（Node 不加 --env-file 不会自动读）：确保下面 DB_PATH / 纯库开关 / Core 构造都拿得到配置。
//   loadEnvFile 幂等；没有该文件抛错忽略。放在最顶部——否则 DB_PATH（下面就求值）读不到 MEMOWEFT_HOST_DB。
try {
  process.loadEnvFile(ENV_PATH);
} catch {
  /* 没有 .env 或已加载，忽略 */
}

// 纯库模式（MEMOWEFT_EXPERIENCE_UI=off）：Host 被当库 import 时不起网页——【在建任何库/目录之前】就退出，
//   不 createMemoWeftCore、不建 host.db、不建 data 目录（纯库模式不该在磁盘留 Host 残留）。
if (process.env.MEMOWEFT_EXPERIENCE_UI === 'off') {
  console.log(
    "\n  纯库模式：未启动网页；作为库使用请直接 import 'memoweft'（MEMOWEFT_EXPERIENCE_UI=off）。",
  );
  console.log('  想起网页请把该行改回 on 或删掉，再启动 Host。\n');
  // 提前退出：不 createMemoWeftCore、不建 host.db/data 目录（纯库模式不该在磁盘留 Host 残留）。
  //   真实终端下干净退 0；仅当 stdout 被管道捕获（如自动化冒烟 2>&1）时，Windows 偶报一句无害的
  //   libuv 退出竞态 assertion（stdout 异步 flush 未完就 exit），不影响"不起网页/不建库"这两件正事。
  process.exit(0);
}

// 端口：默认 7788（避开 testbench 的 7888，也避开 Clash/FlClash 常用的 7890/7891 代理端口）；env PORT 覆盖。
const PORT = Number(process.env.PORT) || 7788;

// 库路径：默认 data/host.db（相对本脚本位置，不受 cwd 影响）；env MEMOWEFT_HOST_DB 覆盖。
const DB_PATH = process.env.MEMOWEFT_HOST_DB ?? join(import.meta.dirname, '..', 'data', 'host.db');
mkdirSync(dirname(DB_PATH), { recursive: true }); // 目录不存在则建（首启即可写库）

// 聊天历史目录：Host 自己的对话日志，【跟随库路径】——落在库文件同目录下的 sessions/。
//   使用 MEMOWEFT_HOST_DB 指向独立数据库时，聊天历史也会随之隔离。
const SESSIONS_DIR = join(dirname(DB_PATH), 'sessions');

// 单条消息字符上限：挡异常客户端发超长串撑爆后续 updateProfile 的 prompt（正常长输入 2 万字符足够）。
const MAX_MESSAGE_CHARS = 20000;

// 采集摄入（/api/observe）：采集插件 → Host 审核 → Core（三层边界）。
//   COLLECTOR_ENABLED：采集总开关（用户设置），env MEMOWEFT_HOST_COLLECTOR=off 则 Host 拒收（403）。缺省 on。
//   MAX_OBSERVE_BATCH：单次 POST 最多几条 observation（挡异常客户端一次灌爆）。
const COLLECTOR_ENABLED = (process.env.MEMOWEFT_HOST_COLLECTOR ?? 'on').toLowerCase() !== 'off';
const MAX_OBSERVE_BATCH = 200;

// JSON 请求在完整缓冲、JSON.parse 前的总字节数上限。导入记忆包需要比聊天消息大得多的空间，
// 因此留 5 MiB；超过上限一律 413，避免一个本机异常客户端把进程内存吃掉。
const MAX_REQUEST_BODY_BYTES = 5 * 1024 * 1024;

// 每次启动重新生成。它只嵌入同源返回的 HTML，且所有会改变 Host 状态的请求都必须带回它。
// 这不是生产认证机制：Reference Host 仍只监听 loopback、只面向单用户本机使用。
const CSRF_TOKEN = randomBytes(32).toString('base64url');

// 前端单文件（同目录 web/index.html）。
const INDEX_HTML = join(import.meta.dirname, 'web', 'index.html');

// ── Active experience plugin ──
// The selected experience provides the system prompt (plain assistant or Xingyao; see experiences/).
// MemoWeft core stays neutral; tone and anthropomorphism belong to the host experience layer.
// activeExperienceId：模块级、单用户单进程。初值取 DEFAULT_EXPERIENCE_ID（env MEMOWEFT_EXPERIENCE，缺省 plain）。
//   切换见 POST /api/experience：切换后移除当前会话的激活标记，让下一句重建实例并应用新语气。
let activeExperienceId: string = DEFAULT_EXPERIENCE_ID;

// 语言默认（Host 壳策略）：用户没显式设 MEMOWEFT_LANG 时，中文系统环境就默认用中文写记忆——
//   中文用户开箱即中文、不必手配；显式 MEMOWEFT_LANG=en/zh 一律优先、绝不覆盖。
//   只改 Host 侧单例 config.language（Core 缺省仍 en·进英文市场），不碰 Core 写路径逻辑。
if (!process.env.MEMOWEFT_LANG) {
  const envLocale = (
    process.env.LANG ||
    process.env.LC_ALL ||
    process.env.LC_MESSAGES ||
    ''
  ).toLowerCase();
  let preferZh = envLocale.startsWith('zh');
  if (!preferZh) {
    try {
      preferZh = (Intl.DateTimeFormat().resolvedOptions().locale || '')
        .toLowerCase()
        .startsWith('zh');
    } catch {
      /* Intl 不可用则维持默认 en */
    }
  }
  if (preferZh) config.language = 'zh';
}

// 将已注册插件传给 Core：experience 类没有钩子；tool/collector 类的钩子在此生效。
const core = createMemoWeftCore({ dbPath: DB_PATH, plugins: ALL_PLUGINS });

// 聊天历史（Host 自建落盘）：目录级多对话管理器（一对话一 jsonl，见 chatHistory.ts）。
const history = createChatHistory(SESSIONS_DIR);

// ── 多对话状态（Host 自实现）──
// currentConvId：当前活跃对话（模块级，单用户单进程）。首启从磁盘拣一条【未归档且最近活跃】的续上；
//   没有历史对话 → 直接 newId 起一条新的。这样重启后能接着上次那条聊，不每次从空白开始。
let currentConvId: string = (() => {
  const existing = history.list().filter((s) => !s.archived);
  return existing[0]?.id ?? history.newId();
})();

// activatedConvs：本进程已在 Core 建过实例（handleConversationTurn 建过窗口）的对话集合。
//   不在集合里 = 本进程首次 chat 该对话 → 要传 seedTurns 让 Core 重建上下文窗口；建过后加入集合，之后复用不再 seed。
const activatedConvs = new Set<string>();

// switchedExperienceConvs：刚切过人设、下句 chat 要"只种用户话"的对话。换人设时若把整段历史（含旧人设的
//   assistant 回复）种回窗口，历史中的旧自我表述可能覆盖新 systemPrompt 的人设约束。
//   所以切人设后第一句只种【用户说过的话】、不认领旧人设的回复——用户的话是跨人设的事实、保留。
const switchedExperienceConvs = new Set<string>();

// 后台画像更新调度器（Host 自建）：注入 core.updateProfile，其余状态自持。
const scheduler = createProfileScheduler({ updateProfile: () => core.updateProfile() });

/**
 * 续聊种子：把一条对话历史的最近几轮转成 Core 的 Turn[]（{role, content}，剥掉 ts）。
 * 只取最近 config.workingMemory.maxTurns 条——回话窗口就这么大，多传也会被 Core 丢老的，省内存。
 * 历史里 user/assistant 已是分开的两条，直接映射即可（无需像 testbench 从一条 run 记录拆两条）。
 */
function seedFor(
  conversationId: string,
  opts: { onlyUser?: boolean } = {},
): Array<{ role: HistoryTurn['role']; content: string }> {
  let turns = history.read(conversationId);
  // onlyUser（切人设后第一句）：只留用户说过的话，滤掉上一个人设的 assistant 回复——否则历史里旧人设的
  //   自我表述（"我是星瑶"）可能干扰新人设。用户消息属于跨人设上下文，需要保留。
  if (opts.onlyUser) turns = turns.filter((t) => t.role === 'user');
  const recent = turns.slice(-config.workingMemory.maxTurns);
  return recent.map((t) => ({ role: t.role, content: t.content }));
}

// ── 小工具 ──

class RequestError extends Error {
  readonly statusCode: number;

  constructor(statusCode: number, message: string) {
    super(message);
    this.statusCode = statusCode;
  }
}

function trustedAuthority(value: string | undefined): boolean {
  if (!value) return false;
  const authority = value.toLowerCase();
  return authority === `127.0.0.1:${PORT}` || authority === `localhost:${PORT}`;
}

function hasJsonContentType(req: IncomingMessage): boolean {
  const contentType = req.headers['content-type'];
  return (
    typeof contentType === 'string' &&
    contentType.split(';', 1)[0]?.trim().toLowerCase() === 'application/json'
  );
}

function hasValidCsrfToken(req: IncomingMessage): boolean {
  const supplied = req.headers['x-memoweft-csrf-token'];
  if (typeof supplied !== 'string') return false;
  const actual = Buffer.from(supplied);
  const expected = Buffer.from(CSRF_TOKEN);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

/**
 * Reference Host 没有生产鉴权，但状态更改仍须防本机浏览器跨站请求与 DNS rebinding：
 * - Host 必须是本服务实际的 loopback authority；
 * - 浏览器给出 Origin 时必须同源；无 Origin 的本机脚本仍可用，但必须持有随机 token；
 * - 仅收 JSON，阻断 form/text/plain 这类 simple request。
 */
function assertTrustedStateChange(req: IncomingMessage): void {
  if (!trustedAuthority(req.headers.host)) {
    throw new RequestError(403, '拒绝非本机 Host 的状态更改请求');
  }

  const origin = req.headers.origin;
  if (origin) {
    let parsed: URL;
    try {
      parsed = new URL(origin);
    } catch {
      throw new RequestError(403, 'Origin 不可信');
    }
    if (parsed.protocol !== 'http:' || !trustedAuthority(parsed.host)) {
      throw new RequestError(403, 'Origin 不可信');
    }
  }

  if (!hasJsonContentType(req)) {
    throw new RequestError(415, '状态更改请求必须使用 application/json');
  }
  if (!hasValidCsrfToken(req)) {
    throw new RequestError(403, '缺少或无效的本机会话令牌');
  }
}

/** 读请求体为 JSON。UTF-8 护栏：非法 UTF-8 解码出 U+FFFD 时拒收，避免乱码入库。 */
function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const declaredLength = Number(req.headers['content-length']);
    if (Number.isFinite(declaredLength) && declaredLength > MAX_REQUEST_BODY_BYTES) {
      // 先恢复流再返回 413：不把余下 body 留在 socket 缓冲中，更不会完整拼到内存。
      req.resume();
      reject(new RequestError(413, `请求体过大（上限 ${MAX_REQUEST_BODY_BYTES} 字节）`));
      return;
    }

    const chunks: Buffer[] = [];
    let totalBytes = 0;
    let rejected = false;
    req.on('data', (c: Buffer) => {
      if (rejected) return;
      totalBytes += c.length;
      if (totalBytes > MAX_REQUEST_BODY_BYTES) {
        rejected = true;
        // 继续 drain 而非 Buffer.concat，确保此处之后不再完整缓冲请求体。
        req.resume();
        reject(new RequestError(413, `请求体过大（上限 ${MAX_REQUEST_BODY_BYTES} 字节）`));
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      if (rejected) return;
      try {
        const body = Buffer.concat(chunks).toString('utf8');
        if (body.includes('�')) {
          reject(
            new Error(
              '请求体不是合法 UTF-8（Windows cmd 的 curl 会按 GBK 发中文；请改用界面，或以 UTF-8 编码发送）',
            ),
          );
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

/** 同目录临时文件后原子替换，避免进程中断时留下半份含密钥的 .env。 */
function saveEnvAtomically(env: string): void {
  const tempPath = join(
    dirname(ENV_PATH),
    `.${basename(ENV_PATH)}.${randomBytes(12).toString('hex')}.tmp`,
  );
  let shouldCleanUp = true;
  try {
    // mode 在 Unix 上限制为当前用户可读写；Windows 的 chmod 语义有限，但不会降低其 ACL。
    writeFileSync(tempPath, env, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
    chmodSync(tempPath, 0o600);
    renameSync(tempPath, ENV_PATH); // 同目录 rename：Windows/Linux 均由文件系统完成替换，不暴露半写内容。
    shouldCleanUp = false;
  } finally {
    if (shouldCleanUp) {
      try {
        unlinkSync(tempPath);
      } catch {
        /* 临时文件可能尚未创建或已被系统清理 */
      }
    }
  }
}

function sendJson(res: import('node:http').ServerResponse, code: number, data: unknown): void {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

/**
 * 采集观察审核·清洗一条外来 observation（采集插件 POST 来的，不可信）。
 * 只保留 generic Observation 的安全字段；【强制剥掉所有授权位】——observed 数据默认不进入 MemoWeft 内建云写模型提示词，
 *   插件无权自行放行 allowCloudRead（rule 「插件不能直接改 allowCloudRead」）。剥空后 Core
 *   ingestObservations 会套 observedDefaults（本地写模型可用 / 不用于内建云写模型 / 可推画像）。这不是通用读取或访问控制。
 * kind / content 缺失 → 返回 null（丢弃这条）。occurredAt 非法/缺失 → 补成现在（防时间窗比较错位）。
 */
function sanitizeObservation(raw: unknown): Observation | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  const kind = String(o.kind ?? '').trim();
  const content = String(o.content ?? '').trim();
  if (!kind || !content) return null;
  const parsed = o.occurredAt ? new Date(String(o.occurredAt)) : new Date();
  const occurredAt = isNaN(parsed.getTime()) ? new Date().toISOString() : parsed.toISOString();
  const clean: Observation = { kind, occurredAt, content };
  if (o.originId != null) clean.originId = String(o.originId);
  if (o.meta && typeof o.meta === 'object') clean.meta = o.meta as Record<string, unknown>;
  // 不复制 allowCloudRead/allowLocalRead/allowInference；统一使用 Core observedDefaults。
  return clean;
}

// 配置向导·拼 .env 的纯字符串函数已抽到 ./genEnv.ts（便于单测：server.ts 顶层会 listen，不宜在测试里 import）。

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);

  try {
    // 所有 POST 都可能触发 Host 状态变化（或返回敏感的配置生成结果），统一在进入路由前做边界校验，
    // 免得新增端点时漏掉某一个 handler。
    if (req.method === 'POST') assertTrustedStateChange(req);

    // 前端：干净单文件 html（只含用户模式聊天）。
    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      const html = readFileSync(INDEX_HTML, 'utf-8').replace('__MEMOWEFT_CSRF_TOKEN__', CSRF_TOKEN);
      res.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        // HTML 内含本进程的会话 token，不能被浏览器/代理复用到下一次 Host 重启。
        'Cache-Control': 'no-store',
      });
      res.end(html);
      return;
    }

    // Originless local helpers (such as the active-window collector) obtain the same per-process token
    // that the HTML receives. Host and Origin validation prevents a cross-site page or rebound hostname
    // from reading it; the browser same-origin policy remains an additional boundary.
    if (req.method === 'GET' && url.pathname === '/api/csrf-token') {
      if (!trustedAuthority(req.headers.host)) {
        throw new RequestError(403, '拒绝非本机 Host 的会话令牌请求');
      }
      const origin = req.headers.origin;
      if (origin) {
        let parsed: URL;
        try {
          parsed = new URL(origin);
        } catch {
          throw new RequestError(403, 'Origin 不可信');
        }
        if (parsed.protocol !== 'http:' || !trustedAuthority(parsed.host)) {
          throw new RequestError(403, 'Origin 不可信');
        }
      }
      res.writeHead(200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      res.end(JSON.stringify({ token: CSRF_TOKEN }));
      return;
    }

    // 首次配置检查：前端据此决定提示配置还是直接进入聊天。没有 .env 也能正常响应状态。
    if (req.method === 'GET' && url.pathname === '/api/health') {
      sendJson(res, 200, core.health());
      return;
    }

    // 用量累计：宿主可以用 LLM/嵌入分项计数计算费用；Core 不内置价格策略。
    //   端点常不回 usage（本地模型多见）时对应桶为 0；宿主要按对话/画像切分，自己在调用前后取差值即可。只读、不碰库。
    if (req.method === 'GET' && url.pathname === '/api/usage') {
      sendJson(res, 200, core.usage());
      return;
    }

    // 一轮对话：存证据 → 召回 → 回话（Core），再把这轮 user + assistant 落 Host 历史、排后台整理。
    if (req.method === 'POST' && url.pathname === '/api/chat') {
      const body = await readJson(req);
      // 服务端兜底（前端 trim 可被直连 API 绕过，服务端是唯一可信边界）：只收字符串、trim 后非空、不超上限，
      //   否则 400 且【不落库、不排整理】——防空/脏（[object Object] 之类）证据污染画像、白耗一次 LLM 回话。
      if (typeof body.message !== 'string') {
        sendJson(res, 400, { error: '消息必须是文本' });
        return;
      }
      const message = body.message.trim();
      if (!message) {
        sendJson(res, 400, { error: '消息不能为空' });
        return;
      }
      if (message.length > MAX_MESSAGE_CHARS) {
        sendJson(res, 400, { error: `消息太长（上限 ${MAX_MESSAGE_CHARS} 字），分几次说吧` });
        return;
      }

      // 续聊种子重建：当前对话若本进程尚未在 Core 建立实例（刚打开的旧对话或重启后续聊），
      //   把它的历史最近几轮作 seedTurns 传给 Core，让首次建窗口时接上上下文。已激活过的对话不传（Core 复用不重建）。
      const convId = currentConvId;
      const firstThisProcess = !activatedConvs.has(convId);
      const seedOnlyUser = switchedExperienceConvs.has(convId); // 切换人设后的第一句仅注入用户消息，避免旧人设回复干扰
      const outcome = await core.handleConversationTurn({
        message,
        conversationId: convId,
        // 当前激活体验的人设：仅该对话首次建实例时生效（后续复用不重建）。切体验后靠 activatedConvs.delete
        //   + core.dropConversation 让下一句重建实例，届时这里取到的就是新体验的 systemPrompt。
        systemPrompt: getExperience(activeExperienceId).systemPrompt,
        seedTurns: firstThisProcess ? seedFor(convId, { onlyUser: seedOnlyUser }) : undefined,
      });
      activatedConvs.add(convId); // 本进程已为该对话建过实例，后续 chat 不再 seed
      switchedExperienceConvs.delete(convId); // "只种用户话"只作用于切人设后紧接的这一句

      // user 原话落 Host 历史（话已由 Core 存为证据，无论回话成败）。
      history.append(convId, { role: 'user', content: message, ts: new Date().toISOString() });

      if (outcome.error) {
        // 回复失败时，Core 将回退文本写入 outcome.reply，并将原始错误保存在 outcome.error。
        //   别把这句失败串当正常回复落 assistant 历史 / 渲染给用户（否则用户分不清系统故障与模型真答，
        //   还会永久留在历史里）——回一个可识别的失败信号，前端走"出错了/请重试"。
        sendJson(res, 200, { error: '回话没成功：' + outcome.error, recall: [] });
      } else {
        history.append(convId, {
          role: 'assistant',
          content: outcome.reply,
          ts: new Date().toISOString(),
        });
        // 回忆结果只精简回传，供宿主界面按需展示。
        const recall = outcome.recall.map((r) => ({ content: r.content, score: r.score }));
        sendJson(res, 200, { reply: outcome.reply, recall });
      }

      // 回合后排后台整理（fire-and-forget：不 await、不挡这次回话，防抖攒批见 scheduler）。
      //   user 的话已入库为证据，回话成败都该攒进下一批整理。
      scheduler.onTurn();
      return;
    }

    // 聊天历史：读回【当前对话】的轮列表，前端加载时渲染。空对话返回空列表、不报错。
    if (req.method === 'GET' && url.pathname === '/api/chat-history') {
      sendJson(res, 200, { turns: history.read(currentConvId), conversationId: currentConvId });
      return;
    }

    // 后台整理状态：前端顶栏轮询显示"正在整理记忆…/已整理"。
    if (req.method === 'GET' && url.pathname === '/api/bg-status') {
      sendJson(res, 200, scheduler.status());
      return;
    }

    // ── 体验插件 ──
    // 体验 = 回话的人设/语气（普通助手 / 星瑶），只换 systemPrompt，不碰记忆本体（记忆全在 Core，各体验共用同一份）。

    // 列出可选体验 + 当前是哪个：前端顶栏选择器据此渲染下拉、标出当前。只透 id/name，不外泄 systemPrompt 原文。
    if (req.method === 'GET' && url.pathname === '/api/experiences') {
      const experiences = listExperiences().map((e) => ({
        ...e,
        current: e.id === activeExperienceId,
      }));
      sendJson(res, 200, { experiences, current: activeExperienceId });
      return;
    }

    // 插件管理（v2）：列出全部已注册插件 + 类型 + 声明的权限（供插件管理面板只读展示）。
    //   experience 类的"启用"= 当前激活的那个人设（activeExperienceId）；tool/collector 类注册即启用（v2 不做运行时装卸）。
    if (req.method === 'GET' && url.pathname === '/api/plugins') {
      const plugins = listPlugins().map((p) => ({
        ...p,
        // experience 的"启用"跟随当前人设；非 experience 注册即启用。
        active: p.type === 'experience' ? p.id === activeExperienceId : true,
      }));
      sendJson(res, 200, { plugins, activeExperience: activeExperienceId });
      return;
    }

    // 切换体验：body {id} → 校验在白名单里 → 换 activeExperienceId。
    //   【切换后当前会话下一句就生效】：Core 语义是 systemPrompt 仅首次建实例生效，靠"重建实例"换人设。
    //   要真重建，得【两套缓存一起清】：① activatedConvs.delete → Host 下句传 seedTurns + 新 systemPrompt；
    //   ② core.dropConversation → 丢掉 Core 缓存的旧实例。只清 ① 不够：Core 命中旧实例就不重建、还用旧人设
    //   Host 的 activatedConvs 与 Core 的 conversations Map 是两套独立缓存，需要同时处理。
    if (req.method === 'POST' && url.pathname === '/api/experience') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) {
        sendJson(res, 400, { error: '缺少要切换的体验 id' });
        return;
      }
      // 白名单校验：只接受注册表中存在的体验 id，确保 activeExperienceId 始终有效。
      if (!EXPERIENCE_IDS.includes(id)) {
        sendJson(res, 404, { error: '没有这个体验' });
        return;
      }
      activeExperienceId = id;
      // 两套缓存一起清，当前会话下一句才真换人设：
      activatedConvs.delete(currentConvId); // Host 侧：下句 chat 传 seedTurns + 新 systemPrompt
      core.dropConversation(currentConvId); // Core 侧：丢旧实例 → 下句真重建（否则 Core 命中旧实例、忽略新人设）
      switchedExperienceConvs.add(currentConvId); // 下一句仅注入用户消息，避免旧人设回复干扰新人设
      sendJson(res, 200, { ok: true, current: activeExperienceId });
      return;
    }

    // 用户主动整理记忆：不等待攒批，直接请求调度器执行一次更新。
    //   用户版“立即整理”在 Host 中调用 core.updateProfile；详细诊断视图由可选诊断台提供。
    //   【不并发】：refreshNow 走调度器【同一把单飞锁】——后台正忙则 ran:false（不抢、不排队），
    //   前端提示"正在整理中，稍等"。成功则回本轮新增/强化等摘要，前端据此刷胶囊/抽屉、织记忆气泡。
    if (req.method === 'POST' && url.pathname === '/api/refresh') {
      const r = await scheduler.refreshNow();
      if (!r.ran) {
        // 后台正在整理 → 这次不重复跑；回 200 带 busy 标记（不是错误，是"已有一趟在跑"）。
        sendJson(res, 200, { ran: false, updating: true });
        return;
      }
      // 成功：回本轮摘要（含 newCognitions 供前端织记忆气泡；created 数供提示"新记住 N 件"）。
      sendJson(res, 200, { ran: true, summary: r.summary });
      return;
    }

    // ── 采集观察摄入（采集器插件 → Host 审核 → Core，遵循三层边界）──
    // 采集插件（如 @memoweft/collector-active-window）把窗口样本映射成 generic Observation 后 POST 这里。
    // Host 审核三件事：① 采集总开关（COLLECTOR_ENABLED，off 则 403 拒收）；
    //   ② 隐私不变量——sanitizeObservation 强制剥离授权位，observed 数据默认不用于内建云写模型（插件无权自行放行）；
    //   ③ 调 core.ingestObservation（插件绝不直穿 Core / Store）。前端无需入口——采集器直接 POST。
    if (req.method === 'POST' && url.pathname === '/api/observe') {
      if (!COLLECTOR_ENABLED) {
        sendJson(res, 403, { error: '采集已关闭（MEMOWEFT_HOST_COLLECTOR=off）' });
        return;
      }
      const body = await readJson(req);
      const rawList = Array.isArray(body.observations) ? body.observations : [];
      if (rawList.length === 0) {
        sendJson(res, 400, { error: '缺 observations（generic Observation 数组）' });
        return;
      }
      if (rawList.length > MAX_OBSERVE_BATCH) {
        sendJson(res, 400, { error: `一次最多 ${MAX_OBSERVE_BATCH} 条 observation` });
        return;
      }
      const observations = rawList
        .map(sanitizeObservation)
        .filter((o): o is Observation => o !== null);
      if (observations.length === 0) {
        sendJson(res, 400, { error: 'observation 都不合法（需至少 kind + content）' });
        return;
      }
      // 审核通过 → 交 Core 落 observed 证据（subjectId 缺省=库主人；不带授权位=走 observedDefaults，不进入内建云写模型）。
      const stored = await core.ingestObservation({ observations });
      // stored=真新落库条数；其余=幂等命中（同 originId 重复采集）跳过。
      sendJson(res, 200, { stored: stored.length, skipped: observations.length - stored.length });
      return;
    }

    // ── 多对话 ──
    // 会话册是 Host 的持久数据（扫描 sessions 目录的 JSONL 文件），不从 Core 读取。
    // 新建 = 换当前对话 id；列表 = history.list()；切换 = 改 currentConvId + 标未激活以触发 seed 重建；
    // 归档 = 文件加 .archived（数据不删）。

    // 新建对话：生成新 id → 设为当前对话 → 返回新 id（前端据此清空聊天区）。
    //   不预建文件（首条 chat 才落盘）；新对话本进程从没建过实例，自然要 seed（其实空历史、seed 为空，等价全新窗口）。
    if (req.method === 'POST' && url.pathname === '/api/reset') {
      currentConvId = history.newId();
      sendJson(res, 200, { ok: true, conversationId: currentConvId });
      return;
    }

    // 会话列表：列所有未归档对话（供侧栏渲染），标出当前是哪条。按最后活跃倒序。
    if (req.method === 'GET' && url.pathname === '/api/sessions') {
      const sessions = history.list().map((s) => ({
        id: s.id,
        preview: s.preview,
        lastActiveMs: s.lastActiveMs,
        current: s.id === currentConvId,
      }));
      sendJson(res, 200, { sessions, currentId: currentConvId });
      return;
    }

    // 打开一条对话：切当前对话为它 + 标记它【本进程未激活】（下次 chat 用 seedTurns 重建窗口）+ 返回其历史供前端渲染。
    //   即便这条本进程之前激活过（Core 里已有窗口），open 也重置为未激活——用户切走再切回，语义上按"从历史续聊"更直观。
    //   ⚠ Core 只复用不覆盖旧实例：光标记未激活不够，还得 core.dropConversation 丢掉 Core 缓存的旧实例，
    //   下一句 chat 才会用 seedTurns 重建窗口（否则命中旧实例，seedTurns 会被忽略）。
    if (req.method === 'POST' && url.pathname === '/api/session/open') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) {
        sendJson(res, 400, { error: '缺少要打开的对话 id' });
        return;
      }
      // 白名单：只接受确实存在的对话 id（含已归档）——挡住 sanitizeId 多对一碰撞/非规范 id，
      //   也保证 currentConvId 与 /api/sessions 列出的 id 口径一致（否则侧栏当前高亮会错位）。
      const known = history.list({ includeArchived: true }).some((s) => s.id === id);
      if (!known) {
        sendJson(res, 404, { error: '没有这条对话' });
        return;
      }
      currentConvId = id; // id 来自 list、已是规范安全形态
      activatedConvs.delete(id); // Host 侧：下句 chat 传 seedTurns
      core.dropConversation(id); // Core 侧：丢旧实例 → 下句真重建续聊窗口（两套缓存一起清）
      sendJson(res, 200, { ok: true, conversationId: id, turns: history.read(id) });
      return;
    }

    // 归档一条对话（软移除）：jsonl 加 .archived 后缀，数据不删、可恢复。
    //   归档的若是当前对话，自动切到另一条未归档的（没有就新建一条），避免 current 悬空。
    if (req.method === 'POST' && url.pathname === '/api/session/archive') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) {
        sendJson(res, 400, { error: '缺少要归档的对话 id' });
        return;
      }
      history.archive(id);
      activatedConvs.delete(id); // 归档就从活跃集移除（若在）
      let archivedCurrent = false;
      if (id === currentConvId) {
        const rest = history.list().filter((s) => !s.archived);
        currentConvId = rest[0]?.id ?? history.newId();
        archivedCurrent = true;
      }
      sendJson(res, 200, { ok: true, currentId: currentConvId, archivedCurrent });
      return;
    }

    // ── 配置向导：生成 .env 文本 ──
    // ⚠ 隐私保证：本 handler 仅组装文本并立即返回——
    //   绝不 writeFile 任何 .env、绝不把 apiKey（或任何请求体字段）写进模块级变量/缓存/全局/日志。
    //   apiKey 只允许在"读 body → 拼进返回串 → 响应"这一条瞬时栈路径上流过；函数返回即随栈回收、进程内不残留。
    //   收 9 个 env 值（对话三项必填 / 写路径三项可选 / 嵌入三项可选）+ 一个 withExperienceUI 布尔
    //   → 拼成 .env 文本 → 返回 { env: "<多行文本>" }。gen-env 是 Host 自实现，不碰 Core 或记忆数据。
    if (req.method === 'POST' && url.pathname === '/api/gen-env') {
      const body = await readJson(req); // 局部量，拼完即随栈回收；全程不 console.log(body)、不外泄
      sendJson(res, ...buildEnvResponse(body)); // 拼装是纯函数，key 只在其栈内流过
      return;
    }

    // ── 配置向导·保存并启用：把 .env 写到固定位置（重启后生效） ──
    // 与 gen-env 的区别：gen-env 仅返回文本且不落盘；save-env 由用户主动点击「保存配置并启用」触发，
    //   明示同意把配置（含 apiKey）写进本机 apps/memoweft-host/.env——该文件已被 git 忽略、不会上传，仅本地 demo 便利。
    //   仍复用 buildEnvResponse 拼文本（纯函数 + 必填校验）；落盘只发生在这一步、只写 ENV_PATH 这一个文件，不进日志/缓存。
    if (req.method === 'POST' && url.pathname === '/api/save-env') {
      const body = await readJson(req);
      const [code, payload] = buildEnvResponse(body);
      if (code !== 200 || !('env' in payload)) {
        sendJson(res, code, payload);
        return;
      }
      try {
        saveEnvAtomically(payload.env);
      } catch {
        // 不带底层异常/路径，避免把绝对路径或文件系统细节暴露给页面。
        sendJson(res, 500, { error: '写入 .env 失败，请确认 Host 目录可写。' });
        return;
      }
      // 成功响应不回显 API key、完整 env 或绝对路径；配置已安全落盘，重启后生效即可。
      sendJson(res, 200, { saved: true });
      return;
    }

    // ── 记忆管理页 ──
    // 全走 core.memory.* 受控 API，绝不直接访问 store。
    // 只做【列取 / 标失效 / 改授权 / 删除】，不做内容编辑；记忆文案编辑保留在 testbench。
    // 管理操作都带 reason，进审计表 management_log，留"我的记忆被怎么了"的痕迹。

    // 列"对你的理解"（认知）：每条含 sources 溯源 + 读时算的有效把握度。前端据此渲染理解列表。
    if (req.method === 'GET' && url.pathname === '/api/cognition') {
      // 每条附 confBand：按【有效把握度】(effectiveConfidence，衰减后)定的用户档，让前端如实反映"会变淡"。
      //   阈值取自 Core config（不硬编码、不漂移）；档位逻辑抽在 confBand.ts、有单测护栏。
      const thresholds = config.consolidation.credThresholds;
      const cognitions = core.memory
        .listCognitions()
        .map((c) => ({ ...c, confBand: credBand(c, thresholds) }));
      sendJson(res, 200, { cognitions });
      return;
    }

    // 聊天页顶栏的记忆数量 = 当前活跃理解条数（未失效且未归档）。
    //   单开一个轻量端点，让聊天页顶栏轮询它就够——不必在聊天页拉整份 /api/cognition 列表（那是记忆管理页/抽屉的活）。
    //   口径与记忆抽屉列表里"活跃"的过滤一致（!invalidAt && !archivedAt），胶囊数和抽屉里看到的对得上。
    if (req.method === 'GET' && url.pathname === '/api/cognition/count') {
      const active = core.memory.listCognitions().filter((c) => !c.invalidAt && !c.archivedAt);
      sendJson(res, 200, { count: active.length });
      return;
    }

    // 列"记忆线索"（证据）：原话/摘要 + 来源 + 授权位。前端据此渲染证据列表。
    if (req.method === 'GET' && url.pathname === '/api/evidence') {
      sendJson(res, 200, { evidences: core.memory.listEvidence() });
      return;
    }

    // 记忆图谱：产出 { nodes, edges, stats }，供界面渲染。
    //   全走 core.graph.buildMemoryGraph（门面收口，绝不直接摸 store）。
    //   后端默认不含失效/归档；前端勾"也显示"时带 includeInvalid=true/includeArchived=true 重新 fetch。
    if (req.method === 'GET' && url.pathname === '/api/memory-graph') {
      const sp = url.searchParams;
      sendJson(
        res,
        200,
        core.graph.buildMemoryGraph({
          includeEvidence: sp.get('includeEvidence') !== 'false',
          includeInvalid: sp.get('includeInvalid') === 'true',
          includeArchived: sp.get('includeArchived') === 'true',
        }),
      );
      return;
    }

    // 标失效一条理解（invalidAt=now，条目与溯源都保留、召回跳过；不是删除）。
    if (req.method === 'POST' && url.pathname === '/api/cognition/invalidate') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) {
        sendJson(res, 400, { error: '缺少要标失效的条目 id' });
        return;
      }
      const updated = core.memory.invalidateCognition({
        cognitionId: id,
        reason: 'host:用户在记忆管理页标失效',
      });
      // 不存在返回 null（受控 API 口径）：如实回 removed=false，别假报成功。
      sendJson(res, 200, { invalidated: !!updated, cognition: updated });
      return;
    }

    // 删一条理解（连溯源链一起删，挂着的原话证据本身不动）+ 审计。
    if (req.method === 'POST' && url.pathname === '/api/cognition/delete') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) {
        sendJson(res, 400, { error: '缺少要删除的条目 id' });
        return;
      }
      const r = core.memory.removeCognitionSafely({ cognitionId: id, reason: 'host:用户删除' });
      // removed=false = 目标早已不存在（别处/后台先删了）：如实回传，前端刷新同步。
      sendJson(res, 200, r);
      return;
    }

    // 改一条记忆线索的授权位（能否进入 MemoWeft 内建云写模型提示词 allowCloudRead / 能否据此推测 allowInference）+ 审计。
    if (req.method === 'POST' && url.pathname === '/api/evidence/authorization') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) {
        sendJson(res, 400, { error: '缺少要改授权的条目 id' });
        return;
      }
      // 授权位只收布尔；没传的位不动（受控 API 用 undefined 表示"不改这一位"）。
      const allowCloudRead =
        typeof body.allowCloudRead === 'boolean' ? body.allowCloudRead : undefined;
      const allowInference =
        typeof body.allowInference === 'boolean' ? body.allowInference : undefined;
      const updated = core.memory.updateEvidenceAuthorization({
        evidenceId: id,
        allowCloudRead,
        allowInference,
        reason: 'host:用户改授权',
      });
      sendJson(res, 200, { updated: !!updated, evidence: updated });
      return;
    }

    // 删一条记忆线索（证据）。默认【先不 force】：若被事件/认知引用 → 返回 removed=false + blockers 影响面，
    //   前端提示"这条被 N 处用到"后，用户确认再带 force=true 重试（此时断链一并删）。
    if (req.method === 'POST' && url.pathname === '/api/evidence/delete') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) {
        sendJson(res, 400, { error: '缺少要删除的条目 id' });
        return;
      }
      const force = body.force === true; // 只有显式 true 才强删；缺省/其它值都当作先探路（不 force）
      const r = core.memory.removeEvidenceSafely({
        evidenceId: id,
        force,
        reason: 'host:用户删除',
      });
      // r = { removed, blockers }：removed=false 且 blockers 非空 = 有引用被拦（原样回传给前端提示影响面）。
      sendJson(res, 200, r);
      return;
    }

    // ── 数据 / 备份 ──
    // 导出/导入/恢复出厂全走 core.portable.* / core.memory.resetSubject——不让 Host 自己遍历 store、不自己拼/解 bundle。

    // 导出记忆包：core.portable.exportBundle() 组三层数据 + 溯源为 MemoryBundle（缺 subjectId 用 Core 缺省 subject）。
    //   取舍：这里【返回 JSON（{ bundle }）由前端存盘】，不设 Content-Disposition 让后端直吐文件。
    //   理由——同源 fetch 拿到的响应体不会触发浏览器"下载"，得靠前端 Blob + a[download] 存盘；后端多设一个下载头
    //   反而在 fetch 场景没用、还得处理文件名编码。前端已有 Blob 存盘路径（见 web/index.html memExport），后端只管给数据最简。
    //   不需要 LLM / .env，不碰会话；向量索引不入包（派生物，导入后重建）。
    if (req.method === 'GET' && url.pathname === '/api/export-bundle') {
      const bundle = core.portable.exportBundle();
      sendJson(res, 200, { bundle });
      return;
    }

    // 导入记忆包：body = { bundle, mode:'dryRun'|'merge' }。
    //   先 validateBundle 拦非法包（非对象/格式错/引用悬空）→ 400 带 errors；合法才 importBundle。
    //   dryRun：只校验+试算将写入/重复条数、【不写库】；merge：真导（走 Core 内 transaction 原子化）。
    //   merge 成功建议前端提示"更新画像"重建召回（向量索引不入包，需重建才能语义想起导入的旧事）。
    if (req.method === 'POST' && url.pathname === '/api/import-bundle') {
      const body = await readJson(req);
      const mode = body.mode === 'merge' ? 'merge' : 'dryRun'; // 只认 merge，其余（含缺省/非法）一律当安全的 dryRun
      const bundle = body.bundle; // 可能是任意 JSON——先交给 validateBundle 严格把关，别信任

      // 先校验：非法包绝不进 importBundle、绝不写库；把 errors 摆给前端友好报错。
      const validation = core.portable.validateBundle(bundle);
      if (!validation.valid) {
        sendJson(res, 400, {
          error: '这个记忆包不能用',
          errors: validation.errors,
          warnings: validation.warnings,
        });
        return;
      }
      // 合法：dryRun 试算不写 / merge 真导，返回 ImportPlan（counts=将写入或已写入；duplicates=已存在跳过）。
      const plan = core.portable.importBundle(bundle as MemoryBundle, { mode });
      const out: { plan: typeof plan; needsReindex?: boolean } = { plan };
      if (mode === 'merge' && plan.valid) out.needsReindex = true; // 向量索引不入包 → 建议重建召回
      sendJson(res, 200, out);
      return;
    }

    // ── 恢复出厂：清空全部记忆（破坏性操作）──
    // ⚠ resetSubject 会清空全部记忆：证据、事件、认知、审计记录和向量索引，
    //   不可逆。冒烟/自测这条【绝对只对临时库/副本库跑】（env MEMOWEFT_HOST_DB 指临时路径），绝不碰默认 data/host.db 或真实库。
    // 全走 core.memory.resetSubject（清三层记忆、审计和索引；数据库操作在事务内）——
    //   不让 Host 自己遍历 store 逐条删（那样容易漏 indexAll([]) / managementLog.clear() 某一步）。
    // 对 Host 自己的会话历史（sessions/*.jsonl）的处理【本 handler 额外做】：
    //   resetSubject 只清 Core 的记忆库、【不碰 Host 的 sessions 文件】（那是 Host 职责、Core 够不着也不该碰）。
    //   "清空全部记忆·重新开始"对用户的语义是从头开始，若把满屏旧对话留着、只清了背后的记忆，体验割裂。
    //   因此重置完成后，Host 会归档所有未归档会话（archive 仅增加 .archived 后缀，不删除内容）；
    //     ② newSession() 开一条全新空对话作当前。这样用户回到聊天页是干净空白，旧对话文件仍在磁盘（归档态），不是永久抹除。
    if (req.method === 'POST' && url.pathname === '/api/factory-reset') {
      // 纵深防御（防 CSRF / 误触发直连）：要求 body 带确认词「清空」。
      //   恶意网页对本地服务发的 simple-request（无 body）到不了这一步 → 400；而带 JSON body 会触发
      //   CORS preflight，本地无鉴权服务不响应 preflight → 浏览器挡下跨源清库。CORS 只挡"读响应"、不挡
      //   "请求到达并执行"，所以裸端点直连就能清库——这里加一道服务端确认兜底。前端另有"输入清空二字"强确认。
      const body = await readJson(req);
      if (body.confirm !== '清空') {
        sendJson(res, 400, { error: '恢复出厂需要确认（body 缺 confirm）' });
        return;
      }
      // 破坏性收口：清 Core 记忆库（三层 + 审计 + 向量索引）。返回四个清除计数。
      const counts = core.memory.resetSubject({ reason: 'host:用户在记忆管理页恢复出厂' });

      // Host 会话历史：归档所有未归档对话（软移除、不硬删），再开一条空对话作当前 → 用户回到干净空白。
      //   history.list() 默认只列未归档，逐个 archive（加 .archived 后缀，数据留盘可挖回）。
      const active = history.list();
      for (const s of active) {
        history.archive(s.id);
        activatedConvs.delete(s.id); // 从本进程活跃实例集移除（若在）
      }
      const sessionsArchived = active.length;
      currentConvId = history.newId(); // 全新空对话作当前

      sendJson(res, 200, { ok: true, ...counts, sessionsArchived, conversationId: currentConvId });
      return;
    }

    res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ error: 'not found' }));
  } catch (e) {
    // 兜底：请求级错误保留其语义状态（413/403/415）；其余输入/handler 异常仍返回 400，不崩服务。
    const statusCode = e instanceof RequestError ? e.statusCode : 400;
    sendJson(res, statusCode, { error: e instanceof Error ? e.message : String(e) });
  }
});

// 优雅收尾：进程被 kill/中断时清调度器计时、关 Core 库连接（冒烟脚本会 kill 本进程）。
//   纯库模式（EXPERIENCE_UI=off）已在文件顶部提前 exit、根本走不到这里。
for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => {
    scheduler.dispose();
    core.close();
    server.close(() => process.exit(0));
  });
}

server.listen(PORT, '127.0.0.1', () => {
  console.log(`\n  MemoWeft Reference Host → http://127.0.0.1:${PORT}`);
  console.log(`  记忆库 → ${DB_PATH}`);
  console.log(`  聊天历史 → ${SESSIONS_DIR}（跟随库路径）`);
  console.log(`  当前对话 → ${currentConvId}`);
  console.log(`  当前体验 → ${getExperience(activeExperienceId).name}（${activeExperienceId}）`);
  console.log(
    '  端点 → GET / · GET /api/health · GET /api/usage · POST /api/chat · POST /api/gen-env · GET /api/chat-history · GET /api/bg-status',
  );
  console.log(
    '  记忆管理 → GET /api/cognition · GET /api/evidence · POST /api/cognition/{invalidate,delete} · POST /api/evidence/{authorization,delete}',
  );
  console.log('  多对话 → POST /api/reset · GET /api/sessions · POST /api/session/{open,archive}');
  console.log('  体验 → GET /api/experiences · POST /api/experience（切人设：普通助手/星瑶）');
  console.log(
    '  数据/备份 → GET /api/export-bundle · POST /api/import-bundle · POST /api/factory-reset',
  );
  console.log('  用户正门 → GET /api/cognition/count · POST /api/refresh（立即整理记忆）');
  console.log('  记忆图谱 → GET /api/memory-graph\n');
});
