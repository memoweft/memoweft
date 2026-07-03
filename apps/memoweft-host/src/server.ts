/**
 * @memoweft/host —— 用户产品运行壳（Host）。架构归位·批次5 步1/2/3。
 *
 * node:http 起服，经【公开入口】`import 'memoweft'` 调 Core。层层叠加：
 *   步1：GET /api/health、POST /api/chat、GET /api/chat-history、GET /api/bg-status、GET /（干净前端）。
 *   步2：POST /api/gen-env（配置向导拼 .env）、MEMOWEFT_EXPERIENCE_UI=off 纯库开关。
 *   步3：记忆管理页——GET /api/cognition、GET /api/evidence（列取）、
 *        POST /api/cognition/{invalidate,delete}、POST /api/evidence/{authorization,delete}（受控管理）。
 *   步4：多对话——POST /api/reset（新建）、GET /api/sessions（列表）、
 *        POST /api/session/open（切换+续聊种子重建）、POST /api/session/archive（软归档）。
 *   步5：数据/备份——GET /api/export-bundle（导出记忆包）、POST /api/import-bundle（dryRun 试算 / merge 导入）、
 *        POST /api/factory-reset（恢复出厂·破坏性·清空全部记忆）。全走 core.portable.* / core.memory.resetSubject。
 *   步6：S0/S1 用户正门——GET /api/cognition/count（记忆胶囊数）、POST /api/refresh（用户"立即整理记忆"，
 *        走 core.updateProfile；与后台调度共用单飞锁不并发）；S1 新理解信号经 bg-status 的 lastUpdate.newCognitions 透出。
 * 后台画像更新调度、聊天历史落盘、多对话编排 = Host 自实现（蓝图 §3.3）。
 * 记忆管理【全走 core.memory.*】（步0 已补齐的受控 API），绝不直接摸 store（Host 边界红线）。
 *
 * 多对话状态（蓝图 §3.3）：会话册（列表/新建/切换/归档）是【Host 的持久数据】，扫 sessions 目录的 jsonl 得来，
 *   不从 Core 掏——Core 的 conversations Map 只是活跃实例窗口缓存、故意不暴露枚举。Host 维护两样进程内状态：
 *   ① currentConvId：当前活跃对话（模块级，单用户单进程）；
 *   ② activatedConvs：本进程已在 Core 建过实例的对话集合（决定 chat 时要不要传 seedTurns 重建窗口）。
 *   续聊靠 seedTurns：切到一条【本进程还没在 Core 建实例】的旧对话，下次 chat 从其历史读最近几轮转 Turn[] 作 seedTurns，
 *   让 Core 首次建实例时重建上下文窗口（Core 语义：seedTurns 仅首次建实例生效，后续复用不重建）。
 *
 * 红线：只经 `import 'memoweft'` 调 Core，任何 `import '../../src/*'` 都算越界。
 * 数据隔离：Host 用自己独立的库（默认 apps/memoweft-host/data/host.db，env MEMOWEFT_HOST_DB 覆盖），
 *   聊天历史落【库同目录下的 sessions/】（跟随库路径：隔离库时聊天历史也隔离），与 testbench 互不污染。
 * 只绑 127.0.0.1：本服务无鉴权、直接读写个人画像，只开本机回环、杜绝外网面。
 */
import { createServer, type IncomingMessage } from 'node:http';
import { mkdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { createMemoWeftCore, config, type MemoryBundle, type Observation } from 'memoweft';
import { createProfileScheduler } from './scheduler.ts';
import { createChatHistory, type HistoryTurn } from './chatHistory.ts';
import { credBand } from './confBand.ts';
import { getExperience, listExperiences, EXPERIENCE_IDS, DEFAULT_EXPERIENCE_ID } from './experiences/index.ts';

// 先读 .env（Node 不加 --env-file 不会自动读）：确保下面 DB_PATH / 纯库开关 / Core 构造都拿得到 .env 配置。
//   loadEnvFile 幂等；没有 .env 抛错忽略。放在最顶部——否则 DB_PATH（下面就求值）读不到 .env 里的 MEMOWEFT_HOST_DB。
try { process.loadEnvFile(); } catch { /* 没有 .env 或已加载，忽略 */ }

// 纯库模式（MEMOWEFT_EXPERIENCE_UI=off）：Host 被当库 import 时不起网页——【在建任何库/目录之前】就退出，
//   不 createMemoWeftCore、不建 host.db、不建 data 目录（纯库模式不该在磁盘留 Host 残留）。
if (process.env.MEMOWEFT_EXPERIENCE_UI === 'off') {
  console.log('\n  纯库模式：未启动网页；作为库使用请直接 import \'memoweft\'（MEMOWEFT_EXPERIENCE_UI=off）。');
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
//   这样用 MEMOWEFT_HOST_DB 指到隔离库时，聊天历史也一并隔离、不落默认 data/sessions（步3 遗留 TODO 收口）。
const SESSIONS_DIR = join(dirname(DB_PATH), 'sessions');

// 单条消息字符上限：挡异常客户端发超长串撑爆后续 updateProfile 的 prompt（正常长输入 2 万字符足够）。
const MAX_MESSAGE_CHARS = 20000;

// 采集摄入（/api/observe）：采集插件 → Host 审核 → Core（架构归位路线 §3）。
//   COLLECTOR_ENABLED：采集总开关（用户设置），env MEMOWEFT_HOST_COLLECTOR=off 则 Host 拒收（403）。缺省 on。
//   MAX_OBSERVE_BATCH：单次 POST 最多几条 observation（挡异常客户端一次灌爆）。
const COLLECTOR_ENABLED = (process.env.MEMOWEFT_HOST_COLLECTOR ?? 'on').toLowerCase() !== 'off';
const MAX_OBSERVE_BATCH = 200;

// 前端单文件（同目录 web/index.html）。
const INDEX_HTML = join(import.meta.dirname, 'web', 'index.html');

// ── 当前激活的体验插件（批次5「做插件」v1）──
// 回话人设不再硬编码，改由【当前激活的体验插件】提供 systemPrompt（普通助手 / 星瑶，见 experiences/）。
//   MemoWeft 本体冷静克制、不拟人（naming.md §6）；"知道自己有长期记忆、会自然想起用户过往"的注入
//   归宿主这一层，且现在按体验分家——各体验的语气 / 拟人度写在各自插件的 systemPrompt 里。
// activeExperienceId：模块级、单用户单进程。初值取 DEFAULT_EXPERIENCE_ID（env MEMOWEFT_EXPERIENCE，缺省 plain）。
//   切换见 POST /api/experience：切完复用步4 的 activatedConvs.delete，让当前会话下一句重建实例、换上新人设。
let activeExperienceId: string = DEFAULT_EXPERIENCE_ID;

const core = createMemoWeftCore({ dbPath: DB_PATH });

// 聊天历史（Host 自建落盘）：目录级多对话管理器（一对话一 jsonl，见 chatHistory.ts）。
const history = createChatHistory(SESSIONS_DIR);

// ── 多对话状态（Host 自实现，蓝图 §3.3）──
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
//   assistant 回复）种回窗口，新人设会被历史里的旧自称带跑（LLM 更信历史里演过的角色，而非 systemPrompt）。
//   所以切人设后第一句只种【用户说过的话】、不认领旧人设的回复——用户的话是跨人设的事实、保留。
const switchedExperienceConvs = new Set<string>();

// 后台画像更新调度器（Host 自建）：注入 core.updateProfile，其余状态自持。
const scheduler = createProfileScheduler({ updateProfile: () => core.updateProfile() });

/**
 * 续聊种子：把一条对话历史的最近几轮转成 Core 的 Turn[]（{role, content}，剥掉 ts）。
 * 只取最近 config.workingMemory.maxTurns 条——回话窗口就这么大，多传也会被 Core 丢老的，省内存。
 * 历史里 user/assistant 已是分开的两条，直接映射即可（无需像 testbench 从一条 run 记录拆两条）。
 */
function seedFor(conversationId: string, opts: { onlyUser?: boolean } = {}): Array<{ role: HistoryTurn['role']; content: string }> {
  let turns = history.read(conversationId);
  // onlyUser（切人设后第一句）：只留用户说过的话，滤掉上一个人设的 assistant 回复——否则历史里旧人设的
  //   自我表述（"我是星瑶"）会把新人设带跑。用户的话是跨人设的事实、要保留。
  if (opts.onlyUser) turns = turns.filter((t) => t.role === 'user');
  const recent = turns.slice(-config.workingMemory.maxTurns);
  return recent.map((t) => ({ role: t.role, content: t.content }));
}

// ── 小工具 ──

/** 读请求体为 JSON。UTF-8 护栏（testbench readJson 教训）：非法 UTF-8 解码出 U+FFFD → 拒收，防乱码入库。 */
function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      try {
        const body = Buffer.concat(chunks).toString('utf8');
        if (body.includes('�')) {
          reject(new Error('请求体不是合法 UTF-8（Windows cmd 的 curl 会按 GBK 发中文；请改用界面，或以 UTF-8 编码发送）'));
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

function sendJson(res: import('node:http').ServerResponse, code: number, data: unknown): void {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

/**
 * 采集观察审核·清洗一条外来 observation（采集插件 POST 来的，不可信）。
 * 只保留 generic Observation 的安全字段；【强制剥掉所有授权位】——observed 数据默认不上云，
 *   插件无权自行放行 allowCloudRead（路线 §7「插件不能直接改 allowCloudRead」）。剥空后 Core
 *   ingestObservations 会套 observedDefaults（本地可读 / 不上云 / 可推画像），这是隐私红线。
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
  // 注意：不复制任何 allowCloudRead/allowLocalRead/allowInference —— 一律走 Core observedDefaults（不上云红线）。
  return clean;
}

/**
 * 配置向导·把请求体拼成 .env 文本（纯字符串函数，Host 自实现，不碰 Core/记忆）。
 *
 * ⚠ 隐私核心铁律：apiKey 只在【本函数栈内】流过——读进局部 const、拼进返回串、随 [code, body] 交回调用方。
 *   本函数不 writeFile、不 console.log、不往任何模块级变量/缓存写。函数返回后，局部量（含 apiKey）随栈回收，
 *   进程内不留 key。调用方 handler 也只把返回值一次性 sendJson 出去，同样不留存。
 *
 * 键名遵 Core 的 env 口径（src/llm/client.ts loadLLMConfig / src/retrieval/embedder.ts loadEmbedConfig）：
 *   对话模型 MEMOWEFT_LLM_* / 写路径小模型 MEMOWEFT_WRITE_LLM_* / 嵌入器 MEMOWEFT_EMBED_*。
 *   可选组整组为空则省略、只留一行注释说明回退/降级行为（不写半截配置）。
 *
 * @returns [httpCode, body]。缺对话模型必填三项 → [400, {error}]；否则 → [200, {env}]。
 */
function buildEnvResponse(body: Record<string, unknown>): [number, { env: string } | { error: string }] {
  const s = (v: unknown): string => String(v ?? '').trim(); // 统一去空白；不落库、不缓存
  // dotenv 值转义：含 '#'/空格/引号的值加双引号并转义内部引号——否则 process.loadEnvFile() 会把 '#'
  //   及其后当行内注释截断（apiKey/base_url 含 '#' 会被悄悄截短 → 加载回来鉴权失败、用户对着"看着完整"的 .env 难自查）。
  const q = (v: string): string => (/[#\s"]/.test(v) ? '"' + v.replace(/"/g, '\\"') + '"' : v);

  // 对话大模型（必配三项）
  const llmBase = s(body.llmBaseUrl), llmKey = s(body.llmApiKey), llmModel = s(body.llmModel);
  // 写路径小快模型（可选三项，整组空则整组省略）
  const wBase = s(body.writeBaseUrl), wKey = s(body.writeApiKey), wModel = s(body.writeModel);
  // 向量嵌入（可选三项，整组空则整组省略）
  const eBase = s(body.embedBaseUrl), eKey = s(body.embedApiKey), eModel = s(body.embedModel);
  // 部署选项：是否带体验界面（对齐 testbench gen-env 收的唯一布尔字段）
  const withUI = body.withExperienceUI === true;

  // 服务端兜底校验：对话三项缺任一 → 400（前端已拦，这里是唯一可信边界，绝不生成半截配置）。
  const missing: string[] = [];
  if (!llmBase) missing.push('MEMOWEFT_LLM_BASE_URL');
  if (!llmKey) missing.push('MEMOWEFT_LLM_API_KEY');
  if (!llmModel) missing.push('MEMOWEFT_LLM_MODEL');
  if (missing.length) {
    return [400, { error: `对话模型必填项缺失：${missing.join('、')}` }];
  }

  const lines: string[] = [];
  lines.push('# ── 对话大模型（chat · 必配）：回话质量优先 ──────────────');
  lines.push(`MEMOWEFT_LLM_BASE_URL=${q(llmBase)}`);
  lines.push(`MEMOWEFT_LLM_API_KEY=${q(llmKey)}`);
  lines.push(`MEMOWEFT_LLM_MODEL=${q(llmModel)}`);
  lines.push('');

  // 写路径小模型：整组任一非空才写；整组空 → 省略 + 注释说明回退（回退用对话大模型，行为同旧、不崩）。
  if (wBase || wKey || wModel) {
    lines.push('# ── 写路径小快模型（write · 可选）：整理记忆走它，不拖慢整理，也省钱 ──');
    lines.push(`MEMOWEFT_WRITE_LLM_BASE_URL=${q(wBase)}`);
    lines.push(`MEMOWEFT_WRITE_LLM_API_KEY=${q(wKey)}`);
    lines.push(`MEMOWEFT_WRITE_LLM_MODEL=${q(wModel)}`);
  } else {
    lines.push('# ── 写路径小快模型（write · 可选）：未配 → 自动复用上面的对话大模型（行为同旧，不崩）──');
  }
  lines.push('');

  // 向量嵌入：整组任一非空才写；整组空 → 省略 + 注释说明降级（语义联想降级为空，整理记忆照常）。
  if (eBase || eKey || eModel) {
    lines.push('# ── 嵌入模型（embed · 可选）：让它在对话里更容易想起相关的旧事 ──');
    lines.push(`MEMOWEFT_EMBED_BASE_URL=${q(eBase)}`);
    lines.push(`MEMOWEFT_EMBED_API_KEY=${q(eKey)}`);
    lines.push(`MEMOWEFT_EMBED_MODEL=${q(eModel)}`);
  } else {
    lines.push('# ── 嵌入模型（embed · 可选）：未配 → 暂不启用语义联想（聊天/整理记忆都不受影响）──');
  }
  lines.push('');

  // 部署选项：是否带体验界面 → MEMOWEFT_EXPERIENCE_UI=on/off（on=带网页 / off=纯库模式，见下方 EXPERIENCE_UI 开关）。
  lines.push('# ── 部署选项：是否带体验界面（on=带网页 / off=纯库，不起网页）──');
  lines.push(`MEMOWEFT_EXPERIENCE_UI=${withUI ? 'on' : 'off'}`);
  lines.push('');

  return [200, { env: lines.join('\n') }];
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);

  try {
    // 前端：干净单文件 html（只含用户模式聊天）。
    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      const html = readFileSync(INDEX_HTML, 'utf-8');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(html);
      return;
    }

    // 首启门（S3）：模型/嵌入器配没配 —— 前端据此决定先提示配置还是直接聊天。不需要 .env 也不崩。
    if (req.method === 'GET' && url.pathname === '/api/health') {
      sendJson(res, 200, core.health());
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

      // 续聊种子重建（步4）：当前对话若本进程还没在 Core 建过实例（刚 open 的旧对话、或刚重启续上的对话），
      //   把它的历史最近几轮作 seedTurns 传给 Core，让首次建窗口时接上上下文。已激活过的对话不传（Core 复用不重建）。
      const convId = currentConvId;
      const firstThisProcess = !activatedConvs.has(convId);
      const seedOnlyUser = switchedExperienceConvs.has(convId); // 切人设后第一句：只种用户话，别被旧人设回复带跑
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
        // 回话失败：Core 把错吞成兜底串塞进 outcome.reply、真错在 outcome.error。
        //   别把这句失败串当正常回复落 assistant 历史 / 渲染给用户（否则用户分不清系统故障与模型真答，
        //   还会永久留在历史里）——回一个可识别的失败信号，前端走"出错了/请重试"。
        sendJson(res, 200, { error: '回话没成功：' + outcome.error, recall: [] });
      } else {
        history.append(convId, { role: 'assistant', content: outcome.reply, ts: new Date().toISOString() });
        // recall 供未来"记忆气泡"（步6）：这里只精简回传，前端步1 可先不显示。
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

    // ── 体验插件（批次5「做插件」v1） ──
    // 体验 = 回话的人设/语气（普通助手 / 星瑶），只换 systemPrompt，不碰记忆本体（记忆全在 Core，各体验共用同一份）。

    // 列出可选体验 + 当前是哪个：前端顶栏选择器据此渲染下拉、标出当前。只透 id/name，不外泄 systemPrompt 原文。
    if (req.method === 'GET' && url.pathname === '/api/experiences') {
      const experiences = listExperiences().map((e) => ({ ...e, current: e.id === activeExperienceId }));
      sendJson(res, 200, { experiences, current: activeExperienceId });
      return;
    }

    // 切换体验：body {id} → 校验在白名单里 → 换 activeExperienceId。
    //   【切换后当前会话下一句就生效】：Core 语义是 systemPrompt 仅首次建实例生效，靠"重建实例"换人设。
    //   要真重建，得【两套缓存一起清】：① activatedConvs.delete → Host 下句传 seedTurns + 新 systemPrompt；
    //   ② core.dropConversation → 丢掉 Core 缓存的旧实例。只清 ① 不够：Core 命中旧实例就不重建、还用旧人设
    //   （这正是审查抓出的坑——Host 的 activatedConvs 与 Core 的 conversations Map 是两套独立缓存）。
    if (req.method === 'POST' && url.pathname === '/api/experience') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) { sendJson(res, 400, { error: '缺少要切换的体验 id' }); return; }
      // 白名单校验：只接受注册表里确有的体验 id（挡未知 id / 脏输入），别让 activeExperienceId 落到非法值。
      if (!EXPERIENCE_IDS.includes(id)) { sendJson(res, 404, { error: '没有这个体验' }); return; }
      activeExperienceId = id;
      // 两套缓存一起清，当前会话下一句才真换人设：
      activatedConvs.delete(currentConvId);   // Host 侧：下句 chat 传 seedTurns + 新 systemPrompt
      core.dropConversation(currentConvId);   // Core 侧：丢旧实例 → 下句真重建（否则 Core 命中旧实例、忽略新人设）
      switchedExperienceConvs.add(currentConvId); // 下句只种"用户说过的话"，别让旧人设的回复把新人设带跑
      sendJson(res, 200, { ok: true, current: activeExperienceId });
      return;
    }

    // 立即整理记忆（S1 · 用户主动，不等攒批）：用户点"立即整理记忆"按钮走这里。
    //   分歧点1 拍板：用户版"立即整理"进 Host，走 core.updateProfile（开发者版 genProfile 留 testbench）。
    //   【不并发】：refreshNow 走调度器【同一把单飞锁】——后台正忙则 ran:false（不抢、不排队），
    //   前端提示"正在整理中，稍等"。成功则回本轮新增/强化等摘要，前端据此刷胶囊/抽屉、织 S1 气泡。
    if (req.method === 'POST' && url.pathname === '/api/refresh') {
      const r = await scheduler.refreshNow();
      if (!r.ran) {
        // 后台正在整理 → 这次不重复跑；回 200 带 busy 标记（不是错误，是"已有一趟在跑"）。
        sendJson(res, 200, { ran: false, updating: true });
        return;
      }
      // 成功：回本轮摘要（含 newCognitions 供前端织 S1 气泡；created 数供提示"新记住 N 件"）。
      sendJson(res, 200, { ran: true, summary: r.summary });
      return;
    }

    // ── 采集观察摄入（采集器插件 → Host 审核 → Core，架构归位路线 §3）──
    // 采集插件（如 @memoweft/collector-active-window）把窗口样本映射成 generic Observation 后 POST 这里。
    // Host 审核三件事：① 采集总开关（COLLECTOR_ENABLED，off 则 403 拒收）；
    //   ② 隐私红线——sanitizeObservation 强制剥掉授权位，observed 数据默认不上云（插件无权自行放行上云）；
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
      const observations = rawList.map(sanitizeObservation).filter((o): o is Observation => o !== null);
      if (observations.length === 0) {
        sendJson(res, 400, { error: 'observation 都不合法（需至少 kind + content）' });
        return;
      }
      // 审核通过 → 交 Core 落 observed 证据（subjectId 缺省=库主人；不带授权位=走 observedDefaults 不上云）。
      const stored = await core.ingestObservation({ observations });
      // stored=真新落库条数；其余=幂等命中（同 originId 重复采集）跳过。
      sendJson(res, 200, { stored: stored.length, skipped: observations.length - stored.length });
      return;
    }

    // ── 多对话（批次5 步4） ──
    // 会话册是 Host 的持久数据（扫 sessions 目录 jsonl），不从 Core 掏（蓝图 §3.3）。
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
    //   下句 chat 才会用 seedTurns 真重建窗口（否则命中旧实例、seedTurns 被忽略。审查抓出的两套缓存坑，一并根治）。
    if (req.method === 'POST' && url.pathname === '/api/session/open') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) { sendJson(res, 400, { error: '缺少要打开的对话 id' }); return; }
      // 白名单：只接受确实存在的对话 id（含已归档）——挡住 sanitizeId 多对一碰撞/非规范 id，
      //   也保证 currentConvId 与 /api/sessions 列出的 id 口径一致（否则侧栏当前高亮会错位）。
      const known = history.list({ includeArchived: true }).some((s) => s.id === id);
      if (!known) { sendJson(res, 404, { error: '没有这条对话' }); return; }
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
      if (!id) { sendJson(res, 400, { error: '缺少要归档的对话 id' }); return; }
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

    // ── 配置向导·生成 .env 文本（批次5 步2） ──
    // ⚠ 隐私核心铁律（决策3）：本 handler 只【拼文本、当场返回】——
    //   绝不 writeFile 任何 .env、绝不把 apiKey（或任何请求体字段）写进模块级变量/缓存/全局/日志。
    //   apiKey 只允许在"读 body → 拼进返回串 → 响应"这一条瞬时栈路径上流过；函数返回即随栈回收、进程内不残留。
    //   收 9 个 env 值（对话三项必填 / 写路径三项可选 / 嵌入三项可选）+ 一个 withExperienceUI 布尔
    //   → 拼成 .env 文本 → 返回 { env: "<多行文本>" }。gen-env 是 Host 自实现（蓝图 §3.3），不碰 Core、不碰记忆。
    if (req.method === 'POST' && url.pathname === '/api/gen-env') {
      const body = await readJson(req); // 局部量，拼完即随栈回收；全程不 console.log(body)、不外泄
      sendJson(res, ...buildEnvResponse(body)); // 拼装是纯函数，key 只在其栈内流过
      return;
    }

    // ── 记忆管理页（批次5 步3） ──
    // 全走 core.memory.*（步0 已补齐的受控 API），绝不直接摸 store（Host 边界红线）。
    // 只做【列取 / 标失效 / 改授权 / 删除】，不做内容编辑（用户拍板：编辑记忆文案留 testbench）。
    // 管理操作都带 reason，进审计表 management_log，留"我的记忆被怎么了"的痕迹。

    // 列"对你的理解"（认知）：每条含 sources 溯源 + 读时算的有效把握度。前端据此渲染理解列表。
    if (req.method === 'GET' && url.pathname === '/api/cognition') {
      // 每条附 confBand：按【有效把握度】(effectiveConfidence，衰减后)定的用户档，让前端如实反映"会变淡"。
      //   阈值取自 Core config（不硬编码、不漂移）；档位逻辑抽在 confBand.ts、有单测护栏。
      const thresholds = config.consolidation.credThresholds;
      const cognitions = core.memory.listCognitions().map((c) => ({ ...c, confBand: credBand(c, thresholds) }));
      sendJson(res, 200, { cognitions });
      return;
    }

    // 记忆胶囊数（S0）：聊天页顶栏「它记住我 N 件事」的 N = 当前【活跃理解】条数（未失效且未归档）。
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

    // 标失效一条理解（invalidAt=now，条目与溯源都保留、召回跳过；不是删除）。
    if (req.method === 'POST' && url.pathname === '/api/cognition/invalidate') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) { sendJson(res, 400, { error: '缺少要标失效的条目 id' }); return; }
      const updated = core.memory.invalidateCognition({ cognitionId: id, reason: 'host:用户在记忆管理页标失效' });
      // 不存在返回 null（受控 API 口径）：如实回 removed=false，别假报成功。
      sendJson(res, 200, { invalidated: !!updated, cognition: updated });
      return;
    }

    // 删一条理解（连溯源链一起删，挂着的原话证据本身不动）+ 审计。
    if (req.method === 'POST' && url.pathname === '/api/cognition/delete') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) { sendJson(res, 400, { error: '缺少要删除的条目 id' }); return; }
      const r = core.memory.removeCognitionSafely({ cognitionId: id, reason: 'host:用户删除' });
      // removed=false = 目标早已不存在（别处/后台先删了）：如实回传，前端刷新同步。
      sendJson(res, 200, r);
      return;
    }

    // 改一条记忆线索的授权位（能否用于云端 allowCloudRead / 能否据此推测 allowInference）+ 审计。
    if (req.method === 'POST' && url.pathname === '/api/evidence/authorization') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) { sendJson(res, 400, { error: '缺少要改授权的条目 id' }); return; }
      // 授权位只收布尔；没传的位不动（受控 API 用 undefined 表示"不改这一位"）。
      const allowCloudRead = typeof body.allowCloudRead === 'boolean' ? body.allowCloudRead : undefined;
      const allowInference = typeof body.allowInference === 'boolean' ? body.allowInference : undefined;
      const updated = core.memory.updateEvidenceAuthorization({
        evidenceId: id, allowCloudRead, allowInference, reason: 'host:用户改授权',
      });
      sendJson(res, 200, { updated: !!updated, evidence: updated });
      return;
    }

    // 删一条记忆线索（证据）。默认【先不 force】：若被事件/认知引用 → 返回 removed=false + blockers 影响面，
    //   前端提示"这条被 N 处用到"后，用户确认再带 force=true 重试（此时断链一并删）。
    if (req.method === 'POST' && url.pathname === '/api/evidence/delete') {
      const body = await readJson(req);
      const id = typeof body.id === 'string' ? body.id.trim() : '';
      if (!id) { sendJson(res, 400, { error: '缺少要删除的条目 id' }); return; }
      const force = body.force === true; // 只有显式 true 才强删；缺省/其它值都当作先探路（不 force）
      const r = core.memory.removeEvidenceSafely({ evidenceId: id, force, reason: 'host:用户删除' });
      // r = { removed, blockers }：removed=false 且 blockers 非空 = 有引用被拦（原样回传给前端提示影响面）。
      sendJson(res, 200, r);
      return;
    }

    // ── 数据 / 备份（批次5 步5） ──
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
        sendJson(res, 400, { error: '这个记忆包不能用', errors: validation.errors, warnings: validation.warnings });
        return;
      }
      // 合法：dryRun 试算不写 / merge 真导，返回 ImportPlan（counts=将写入或已写入；duplicates=已存在跳过）。
      const plan = core.portable.importBundle(bundle as MemoryBundle, { mode });
      const out: { plan: typeof plan; needsReindex?: boolean } = { plan };
      if (mode === 'merge' && plan.valid) out.needsReindex = true; // 向量索引不入包 → 建议重建召回
      sendJson(res, 200, out);
      return;
    }

    // ── 恢复出厂 · 清空全部记忆（批次5 步5 · ⚠⚠⚠ 破坏性极强 · 红线）──
    // ⚠ 红线（MEMORY 有误删事故教训）：resetSubject 会清空【全部记忆】——三层记忆(证据/经历/理解) + 审计表 + 向量索引，
    //   不可逆。冒烟/自测这条【绝对只对临时库/副本库跑】（env MEMOWEFT_HOST_DB 指临时路径），绝不碰默认 data/host.db 或真实库。
    // 全走 core.memory.resetSubject（步0 已收口：清三层 + 清审计 + 清索引，缺省 subject；库内四张表包在一个事务里）——
    //   不让 Host 自己遍历 store 逐条删（那样容易漏 indexAll([]) / managementLog.clear() 某一步）。
    // 对 Host 自己的会话历史（sessions/*.jsonl）的处理【本 handler 额外做】：
    //   resetSubject 只清 Core 的记忆库、【不碰 Host 的 sessions 文件】（那是 Host 职责、Core 够不着也不该碰）。
    //   "清空全部记忆·重新开始"对用户的语义是从头开始，若把满屏旧对话留着、只清了背后的记忆，体验割裂。
    //   所以出厂后 Host 顺手：① 归档当前所有未归档会话（archive=加 .archived 后缀，不硬删——留一线可挖回，合 MemoWeft 不毁历史的调性）；
    //     ② newSession() 开一条全新空对话作当前。这样用户回到聊天页是干净空白，旧对话文件仍在磁盘（归档态），不是永久抹除。
    if (req.method === 'POST' && url.pathname === '/api/factory-reset') {
      // 纵深防御（防 CSRF / 误触发直连）：要求 body 带确认词「清空」。
      //   恶意网页对本地服务发的 simple-request（无 body）到不了这一步 → 400；而带 JSON body 会触发
      //   CORS preflight，本地无鉴权服务不响应 preflight → 浏览器挡下跨源清库。CORS 只挡"读响应"、不挡
      //   "请求到达并执行"，所以裸端点直连就能清库——这里加一道服务端确认兜底。前端另有"输入清空二字"强确认。
      const body = await readJson(req);
      if (body.confirm !== '清空') { sendJson(res, 400, { error: '恢复出厂需要确认（body 缺 confirm）' }); return; }
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
    // 兜底：任何 handler 抛错（如非法 UTF-8 请求体）都返回 400，不崩服务。
    sendJson(res, 400, { error: e instanceof Error ? e.message : String(e) });
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
  console.log(`\n  MemoWeft Host（批次5 步6·S0/S1 用户正门）→ http://127.0.0.1:${PORT}`);
  console.log(`  记忆库 → ${DB_PATH}`);
  console.log(`  聊天历史 → ${SESSIONS_DIR}（跟随库路径）`);
  console.log(`  当前对话 → ${currentConvId}`);
  console.log(`  当前体验 → ${getExperience(activeExperienceId).name}（${activeExperienceId}）`);
  console.log('  端点 → GET / · GET /api/health · POST /api/chat · POST /api/gen-env · GET /api/chat-history · GET /api/bg-status');
  console.log('  记忆管理 → GET /api/cognition · GET /api/evidence · POST /api/cognition/{invalidate,delete} · POST /api/evidence/{authorization,delete}');
  console.log('  多对话 → POST /api/reset · GET /api/sessions · POST /api/session/{open,archive}');
  console.log('  体验 → GET /api/experiences · POST /api/experience（切人设：普通助手/星瑶）');
  console.log('  数据/备份 → GET /api/export-bundle · POST /api/import-bundle · POST /api/factory-reset');
  console.log('  用户正门 → GET /api/cognition/count · POST /api/refresh（立即整理记忆）\n');
});
