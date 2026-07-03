/**
 * Host 聊天历史落盘（Host 自建，架构归位·批次5 步1→步4）。
 *
 * 这是【Host 职责】的持久化编排（蓝图 §3.3）：Host 自己记"每轮谁说了什么"，
 * 不依赖 Core 的 RunLogger 内幕格式（那是调试用、字段随内幕演进）。够用即从简：
 * 一条对话 = 一个 .jsonl 文件，每行一条 {role, content, ts}，追加写、顺序读回。
 *
 * 步4 扩多对话：一条对话一个 jsonl（文件名含 conversationId）。会话册（列表/归档）是
 *   【Host 自己的持久数据】，不从 Core 掏——Core 的 conversations Map 只是活跃实例窗口缓存、
 *   故意不暴露枚举（蓝图 §3.3）。多对话（列表/新建/切换/续聊/归档）全在这一层文件系统编排。
 *
 * 归档 = 软移除：给 jsonl 加 `.archived` 后缀，数据不删、可恢复（对齐 testbench 的调性——
 *   "记住你"的产品不毁历史）。列表默认不列已归档；archive 只改后缀、内容原样留盘。
 *
 * UTF-8 护栏（testbench readJson 的教训）：读写全显式 utf-8，中文别乱码。
 * 读时对损坏行容错：跳过解析不了的行，不让一行坏数据毁掉整段历史。
 * 文件名安全：conversationId 只允许安全字符，其余 sanitize 掉，防路径穿越/非法文件名。
 */
import { appendFileSync, readFileSync, existsSync, mkdirSync, readdirSync, statSync, renameSync } from 'node:fs';
import { join } from 'node:path';

/** 一轮里的一条消息（用户或助手）。 */
export interface HistoryTurn {
  role: 'user' | 'assistant';
  content: string;
  /** 记录时刻（ISO 字符串）。 */
  ts: string;
}

/** 一条对话在列表里的摘要（供侧栏渲染）。 */
export interface SessionMeta {
  /** 对话标识（= jsonl 文件名去掉扩展名，已是安全字符）。 */
  id: string;
  /** 首轮用户话的预览（截断），空对话为空串。 */
  preview: string;
  /** 最后活跃时刻（文件 mtime，毫秒）。 */
  lastActiveMs: number;
  /** 是否已归档（.archived 后缀）。 */
  archived: boolean;
}

export interface ChatHistory {
  /** 追加一条消息到指定对话历史（每轮 chat 存 user + assistant 两条）。 */
  append(conversationId: string, turn: HistoryTurn): void;
  /** 读回指定对话全部历史（按写入顺序；损坏行跳过）。空/不存在返回空列表。 */
  read(conversationId: string): HistoryTurn[];
  /** 列所有对话（默认只列未归档；archived:true 连归档一起列）。按最后活跃倒序。 */
  list(opts?: { includeArchived?: boolean }): SessionMeta[];
  /** 归档一条对话（jsonl 加 .archived 后缀，数据不删）。文件不在则静默略过。 */
  archive(conversationId: string): void;
  /** 生成一个新对话 id（时间戳 + 进程内递增序号，防同毫秒撞车；已是安全字符）。 */
  newId(): string;
}

/**
 * 把 conversationId 收敛成安全文件名片段：只留 [A-Za-z0-9._-]，其余替换成 '_'，避免
 *   路径穿越（'/'、'..'）与 Windows 非法文件名字符。空/全非法 → 'default'（兜底不产生空文件名）。
 */
function sanitizeId(id: string): string {
  const safe = String(id).replace(/[^A-Za-z0-9._-]/g, '_');
  return safe || 'default';
}

/**
 * @param dir 会话文件目录（如 <库目录>/sessions）。
 */
export function createChatHistory(dir: string): ChatHistory {
  mkdirSync(dir, { recursive: true }); // 目录不存在则建，首启即可写
  let seq = 0; // 进程内递增序号，防同毫秒 newId 撞车

  const fileFor = (conversationId: string): string => join(dir, `${sanitizeId(conversationId)}.jsonl`);

  /** 读一个 jsonl 文件为轮列表（损坏行跳过）。文件不存在返回空。 */
  function readFile(file: string): HistoryTurn[] {
    if (!existsSync(file)) return [];
    const text = readFileSync(file, { encoding: 'utf-8' });
    const turns: HistoryTurn[] = [];
    for (const line of text.split('\n')) {
      const s = line.trim();
      if (!s) continue;
      try {
        const obj = JSON.parse(s) as HistoryTurn;
        // 轻校验：结构不对的行跳过（防坏数据毁整段）。
        if (obj && (obj.role === 'user' || obj.role === 'assistant') && typeof obj.content === 'string') {
          turns.push({ role: obj.role, content: obj.content, ts: typeof obj.ts === 'string' ? obj.ts : '' });
        }
      } catch {
        /* 损坏行跳过，不中断整段历史读回 */
      }
    }
    return turns;
  }

  return {
    append(conversationId, turn) {
      const active = fileFor(conversationId);
      // 不变量：同一 id 不能【活跃 + 归档】并存。此前归档过（只剩 .archived）、现在又要写入 →
      //   先把归档【恢复成活跃】（取消归档），续写在同一份历史上。这一招根治步4 审查两处：
      //   ① 别新建空活跃文件把归档历史遮蔽/分叉（open 已归档对话再聊，历史不隐身）；
      //   ② 之后再归档时 .archived 已被恢复走、renameSync 不会覆盖旧归档丢历史（must-fix）。
      const archived = active + '.archived';
      if (!existsSync(active) && existsSync(archived)) renameSync(archived, active);
      // 一行一条 JSON，末尾换行。显式 utf-8。
      appendFileSync(active, JSON.stringify(turn) + '\n', { encoding: 'utf-8' });
    },

    read(conversationId) {
      const active = fileFor(conversationId);
      // 活跃文件优先；不在（已归档）则回退读 .archived——让"归档=软移除、数据可恢复"名副其实：
      //   查看/恢复一条已归档对话时，read 仍能拿到它的历史，而非空。
      if (existsSync(active)) return readFile(active);
      return readFile(active + '.archived');
    },

    list(opts) {
      const includeArchived = opts?.includeArchived === true;
      let names: string[];
      try {
        names = readdirSync(dir);
      } catch {
        return []; // 目录还没建/读不到 → 空列表
      }
      const metas: SessionMeta[] = [];
      for (const name of names) {
        // 活跃文件 <id>.jsonl；归档文件 <id>.jsonl.archived。
        const archived = name.endsWith('.jsonl.archived');
        const active = name.endsWith('.jsonl');
        if (!archived && !active) continue;
        if (archived && !includeArchived) continue;
        const id = archived ? name.slice(0, -'.jsonl.archived'.length) : name.slice(0, -'.jsonl'.length);
        const full = join(dir, name);
        let lastActiveMs = 0;
        try {
          lastActiveMs = statSync(full).mtimeMs;
        } catch {
          continue; // 读不到状态的坏文件跳过
        }
        const turns = readFile(full);
        // 首轮用户话作预览（截断 40 字）；纯空对话预览为空串（但仍列出，供"刚新建还没聊"的对话可见）。
        const firstUser = turns.find((t) => t.role === 'user');
        const preview = firstUser ? firstUser.content.slice(0, 40) : '';
        metas.push({ id, preview, lastActiveMs, archived });
      }
      // 最后活跃倒序（新的在上）。
      metas.sort((a, b) => b.lastActiveMs - a.lastActiveMs);
      return metas;
    },

    archive(conversationId) {
      const file = fileFor(conversationId);
      try {
        renameSync(file, file + '.archived');
      } catch {
        /* 文件不在（从没聊过就归档）就算了，不报错 */
      }
    },

    newId() {
      // s-<毫秒>-<进程内序号>：时间戳保证跨进程大致有序、序号保证同毫秒不撞。已是安全字符。
      return `s-${Date.now()}-${seq++}`;
    },
  };
}
