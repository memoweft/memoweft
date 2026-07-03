/**
 * 运行日志（落盘给执行 Agent 直读）。地图 cell 14/15。
 *
 * 每轮对话把"全部内幕"追加一行 JSON 到 logs/run-<sessionId>.jsonl。
 * 字段 = 测试台透视区要展示的东西；新增透视字段务必同步加进 TurnRecord。
 * 治慢（2026-07-01）：同一文件里另加一种记录 ProfileUpdateRecord（kind='profile_update'）——
 *   记"更新画像"各步耗时 + 摘要，符合 AGENTS.md"内幕必落盘"（最慢的步以前没有诊断日志）。
 *   两类记录靠 `kind` 区分；对话轮不写 kind（保持旧输出不变），更新画像记 kind='profile_update'。
 *
 * 只用 Node 内置 node:fs，零外部依赖（地图 cell 11）。
 */
import { appendFileSync, readFileSync, existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

/** 本轮召回到的一条认知 / 证据。 */
export interface RecallItem {
  summary: string;
  score?: number;
}

/** 可解释假设：低置信、挂证据、可推翻（地图 cell 8 第 6 条）。 */
export interface Hypothesis {
  text: string;
  /** 置信度，整数千分制 0~1000。 */
  confidence: number;
  /** 可信状态：候选 / 低置信 / 有限可信 / 稳定 / 冲突中。 */
  credStatus: string;
}

export interface ConflictItem {
  detail: string;
}

export interface ProfileChange {
  detail: string;
}

/** 一轮对话的完整内幕。落盘一行 = 一个 TurnRecord。 */
export interface TurnRecord {
  /** 记录类型（对话轮默认不写此字段）；更新画像见 ProfileUpdateRecord.kind='profile_update'。 */
  kind?: 'turn';
  ts: string;
  sessionId: string;
  turn: number;
  userInput: string;
  reply: string;
  /** 本轮落库的证据（id + summary）；助手回话不在内（禁止系统自证）。 */
  evidence: { id: string; summary: string }[];
  /** 本轮召回了什么（时间窗 + 向量语义，地图 cell 7）。 */
  recall: RecallItem[];
  /** 生成的假设 + 置信度 + 可信状态。 */
  hypotheses: Hypothesis[];
  /** 检测到的冲突（先暴露不急消解，地图 cell 8 第 5 条）。 */
  conflicts: ConflictItem[];
  /** 是否带证据主动询问用户（地图 cell 8 第 6 条）；无则 null。 */
  proactiveQuestion: string | null;
  /** 本轮模型调用次数。 */
  llmCalls: number;
  /** 本轮对用户画像的改动。 */
  profileChanges: ProfileChange[];
  /** 出错信息；无则 null。 */
  error: string | null;
}

/** "更新画像"各步耗时(ms)。 */
export interface ProfileUpdateTimings {
  distillMs: number;
  consolidateMs: number;
  attributeMs: number;
  indexMs: number;
  totalMs: number;
}

/** 一次"更新画像"的内幕（治慢落盘，2026-07-01）——区别于对话轮，靠 kind 分辨。 */
export interface ProfileUpdateRecord {
  kind: 'profile_update';
  ts: string;
  sessionId: string;
  /** 触发方式：手动按钮 / 后台防抖。 */
  trigger: 'manual' | 'background';
  /** 各步耗时(ms)——诊断"慢在哪步"（治慢核心）。 */
  timings: ProfileUpdateTimings;
  /** 结果摘要。 */
  summary: {
    pendingCount: number;
    created: number;
    reinforced: number;
    corrected: number;
    conflicted: number;
    hypotheses: number;
    trends: number;
    expired: number;
    /** 写路径仪表（D4 只观测）：本轮注入 prompt 的 active 认知条数。可选——旧日志无此字段，读取要兼容。 */
    profileSize?: number;
    /** 写路径仪表（D4 只观测）：本轮 consolidate prompt 全部 content 字符数之和。可选——旧日志无此字段。 */
    promptChars?: number;
  };
  /** 本次三步累计模型调用次数。 */
  llmCalls: number;
  indexError: string | null;
  error: string | null;
}

export interface RunLoggerOptions {
  /** 日志目录，通常是仓根 logs/。 */
  dir: string;
  /** 会话 id（一个会话一个 jsonl 文件）。 */
  sessionId: string;
}

const EMPTY_TIMINGS: ProfileUpdateTimings = { distillMs: 0, consolidateMs: 0, attributeMs: 0, indexMs: 0, totalMs: 0 };
const EMPTY_PU_SUMMARY = { pendingCount: 0, created: 0, reinforced: 0, corrected: 0, conflicted: 0, hypotheses: 0, trends: 0, expired: 0 };

export class RunLogger {
  readonly file: string;
  private turn = 0;
  private readonly opts: RunLoggerOptions;

  constructor(opts: RunLoggerOptions) {
    // 注意：不用 TS 参数属性（Node 原生 strip-only 模式不支持），显式赋值。
    this.opts = opts;
    if (!existsSync(opts.dir)) mkdirSync(opts.dir, { recursive: true });
    this.file = join(opts.dir, `run-${opts.sessionId}.jsonl`);
    // 续聊（S4b）：重开一个已存在会话的 logger 时，接着已有轮号往下写——否则新轮从 1 起、与历史轮号撞车，
    // 前端按轮号去重会把新消息误当"已渲染"而漏显。只认对话轮（profile_update 不占轮号）。
    if (existsSync(this.file)) {
      try {
        for (const l of readFileSync(this.file, 'utf8').split('\n').filter(Boolean)) {
          const r = JSON.parse(l);
          if (r && r.kind !== 'profile_update' && typeof r.turn === 'number') this.turn = Math.max(this.turn, r.turn);
        }
      } catch { /* 有损坏行就尽力而为 */ }
    }
  }

  /** 追加一轮对话内幕；缺省字段补成空值，返回写入的完整记录。 */
  appendTurn(rec: Partial<TurnRecord>): TurnRecord {
    const full: TurnRecord = {
      ts: new Date().toISOString(),
      sessionId: this.opts.sessionId,
      turn: ++this.turn,
      userInput: rec.userInput ?? '',
      reply: rec.reply ?? '',
      evidence: rec.evidence ?? [],
      recall: rec.recall ?? [],
      hypotheses: rec.hypotheses ?? [],
      conflicts: rec.conflicts ?? [],
      proactiveQuestion: rec.proactiveQuestion ?? null,
      llmCalls: rec.llmCalls ?? 0,
      profileChanges: rec.profileChanges ?? [],
      error: rec.error ?? null,
    };
    appendFileSync(this.file, JSON.stringify(full) + '\n', 'utf8');
    return full;
  }

  /** 追加一次"更新画像"内幕（治慢：各步耗时 + 摘要）。不占对话轮号；kind='profile_update' 区分。 */
  appendProfileUpdate(rec: Partial<Omit<ProfileUpdateRecord, 'kind' | 'ts' | 'sessionId'>>): ProfileUpdateRecord {
    const full: ProfileUpdateRecord = {
      kind: 'profile_update',
      ts: new Date().toISOString(),
      sessionId: this.opts.sessionId,
      trigger: rec.trigger ?? 'manual',
      timings: rec.timings ?? EMPTY_TIMINGS,
      summary: rec.summary ?? EMPTY_PU_SUMMARY,
      llmCalls: rec.llmCalls ?? 0,
      indexError: rec.indexError ?? null,
      error: rec.error ?? null,
    };
    appendFileSync(this.file, JSON.stringify(full) + '\n', 'utf8');
    return full;
  }

  /**
   * 读回本会话最近 n 条记录（对话轮，测试台 /api/logs 用）。
   * 注：更新画像记录（kind='profile_update'）也在同文件里，会被一并读出并强转 TurnRecord——
   * 要区分就看 `kind`（对话轮无此字段）。逐步诊断以直接读 jsonl 文件为准。
   */
  readRecent(n = 50): TurnRecord[] {
    if (!existsSync(this.file)) return [];
    const lines = readFileSync(this.file, 'utf8').split('\n').filter(Boolean);
    return lines.slice(-n).map((l) => JSON.parse(l) as TurnRecord);
  }
}

export function createRunLogger(opts: RunLoggerOptions): RunLogger {
  return new RunLogger(opts);
}
