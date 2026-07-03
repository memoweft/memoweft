/**
 * 主链路编排 —— 串起七步：Perception→Attention→Event→Association→Conflict→Calibration→Action。
 * 对应决策：D-014（主链路）/ D-015（七步接口 + 三裁定）。
 * 阶段：TASK-02 实现最短闭环（①③⑦ 真做，②④⑤⑥ 占位）。
 * 注意（D-015 裁定二）：每轮两次大模型调用（③解析、⑦回话），不得为省调用合并。
 */

import type { Event, SourceType } from '../event/model.ts';
import type { EventStore } from '../event/store.ts';
import type { LLMClient } from '../llm/client.ts';
import { perception } from './perception.ts';
import { attention } from './attention.ts';
import { eventMaker } from './eventMaker.ts';
import { association } from './association.ts';
import { conflict } from './conflict.ts';
import { calibration } from './calibration.ts';
import { action, windowReply } from './action.ts';
import { WorkingMemory, type Turn } from './workingMemory.ts';

/** 跑一轮主链路需要的依赖（存储 + 模型客户端）。 */
export interface PipelineDeps {
  store: EventStore;
  llm: LLMClient;
}

/** 一轮主链路的产出（含调试信息，供验收/测试台观察）。 */
export interface PipelineResult {
  /** 最终回应（⑦ 产出）。 */
  response: string;
  /** 本轮是否落库了 Event（被 ② Attention 拦下则为 false）。 */
  admitted: boolean;
  /** 落库 Event 的 id（admitted=false 时为 null）。 */
  eventId: string | null;
  /** 落库的 Event（admitted=false 时为 null）。 */
  event: Event | null;
  /** 本轮大模型被调用的次数（验收3：应为 2）。 */
  llmCalls: number;
}

/**
 * 跑一轮主链路最短闭环。
 * ①③⑦ 真做；②④⑤⑥ 走占位（直通）。
 *
 * @param userInput  用户这句话
 * @param deps       存储 + 模型客户端
 * @param source_type 来源（默认 'user'）
 */
export async function runPipeline(
  userInput: string,
  deps: PipelineDeps,
  source_type: SourceType = 'user',
): Promise<PipelineResult> {
  const callsBefore = deps.llm.callCount;

  // ① 感知
  const rawInput = perception(userInput, source_type);

  // ② 注意力（占位：恒进）
  const { admit } = attention(rawInput);
  if (!admit) {
    return {
      response: '',
      admitted: false,
      eventId: null,
      event: null,
      llmCalls: deps.llm.callCount - callsBefore,
    };
  }

  // ③ 事件化（LLM 第 1 次：解析语义 → 落库）
  const { event, eventId } = await eventMaker(rawInput, deps.store, deps.llm);

  // ④ 关联召回：最短闭环路径不召回（恒空）。真实召回在对话窗口路径（createConversation）里，
  //    按 D-024 用补全意图触发，见下方 handle()。此处保持 TASK-02 "恰好两次调用" 语义不变。
  const recalled: Event[] = [];

  // ⑤ 冲突（占位：无冲突）
  const conflictResult = conflict(event, recalled);

  // ⑥ 校准（占位：不探测，直接回应）
  calibration(event, conflictResult);

  // ⑦ 行动（LLM 第 2 次：生成回应）
  const response = await action(rawInput, event, recalled, deps.llm);

  return {
    response,
    admitted: true,
    eventId,
    event,
    llmCalls: deps.llm.callCount - callsBefore,
  };
}

// ───────────────────────────────────────────────────────────────────────────
// TASK-03（D-024）：短期对话窗口层 —— 默认轻量对话，按需才下沉到记忆链路。
// 这是新的【主入口】；上面的 runPipeline 退为"下沉"时复用的重型步骤来源。
// ───────────────────────────────────────────────────────────────────────────

/** 一轮对话被沉淀为长期 Event 的记录。 */
export interface Sedimented {
  /** 来自哪条滑出的对话轮。 */
  turn: Turn;
  eventId: string;
  event: Event;
}

/** 窗口层处理一轮对话的产出（含调试信息，供验收/测试台观察）。 */
export interface HandleResult {
  /** 给用户的最终回应。 */
  reply: string;
  /** 本轮【回话路径】的模型调用次数：窗口够用=1；需召回=2（验收2/3 看这个）。 */
  replyLlmCalls: number;
  /** 模型是否判断需要召回窗口外的旧记忆。 */
  needRecall: boolean;
  /** 需召回时补全出的检索意图（D-024）。 */
  recallQuery: string | null;
  /** 召回路径的模型调用次数（A1 打 topic 粗筛；不召回时为 0）。 */
  recallLlmCalls: number;
  /** A1 命中的相关 Event（按时间倒序；TODO TASK-05 权重排序）。 */
  recalled: Event[];
  /** A1 模型从现有 topic 中挑出的相关 topic（调试/验收用）。 */
  matchedTopics: string[];
  /** 本轮因窗口超长而滑出的旧轮（从老到新）。 */
  evicted: Turn[];
  /** 滑出后经 Attention 判断、真正落库的沉淀。 */
  sedimented: Sedimented[];
  /** 本轮【沉淀路径】的模型调用次数（滑出轮提炼成 Event 的解析调用）。 */
  sedimentationLlmCalls: number;
}

/** 一个带短期窗口的对话会话。窗口在内存，生命周期 = 会话存活期间。 */
export interface Conversation {
  handle(userInput: string): Promise<HandleResult>;
  /** 暴露窗口给测试台/调试观察（只读快照）。 */
  windowSnapshot(): Turn[];
}

/**
 * 创建一个对话会话（D-024 窗口层）。
 *
 * 流程（每轮 handle）：
 *  1. 取当前窗口（在场，不召回不落库）。
 *  2. 【LLM 第1次】窗口+input → 回话顺带判断是否需召回。
 *  3. 若需召回：association（本阶段占位返回空）→【LLM 第2次】带召回结果再回话。
 *  4. user 轮 + assistant 轮压入窗口；超长则挤出最老的旧轮。
 *  5. 滑出的旧轮"此刻还完整在手"→ Attention 判断 → 值得则 eventMaker 提炼落库（仅此时写库）。
 *
 * @param source_type 滑出沉淀时记到 Event 的来源（默认 'user'）。
 */
export function createConversation(
  deps: PipelineDeps,
  maxTokens?: number,
  source_type: SourceType = 'user',
): Conversation {
  const window = new WorkingMemory(maxTokens);

  async function handle(userInput: string): Promise<HandleResult> {
    // ① 取窗口（在场）
    const ctx = window.getContext();

    // ② 回话顺带判断（回话 LLM 第1次）
    let c0 = deps.llm.callCount;
    let judgment = await windowReply(ctx, userInput, [], deps.llm, false);
    let replyLlmCalls = deps.llm.callCount - c0; // 只算"回话"调用
    const needRecall = judgment.needRecall; // 记下首判：本轮是否触发了召回（验收3）
    const recallQuery = judgment.recallQuery;
    let recalledList: Event[] = [];
    let recallLlmCalls = 0;
    let matchedTopics: string[] = [];

    // ③ 需召回才下沉：用【补全意图 recallQuery】真召回（D-024/D-019 A1）→ 带召回结果再回话（回话 LLM 第2次）
    if (needRecall) {
      const cR = deps.llm.callCount;
      const recallResult = await association(recallQuery ?? userInput, deps.store, deps.llm);
      recallLlmCalls = deps.llm.callCount - cR; // association 打 topic 的调用，单列
      recalledList = recallResult.recalled;
      matchedTopics = recallResult.matchedTopics;

      c0 = deps.llm.callCount;
      judgment = await windowReply(ctx, userInput, recalledList, deps.llm, true);
      replyLlmCalls += deps.llm.callCount - c0;
    }

    const reply = judgment.reply || '（未生成回应）';

    // ④ 压入窗口（user + assistant 两轮），收集滑出的旧轮
    const evicted: Turn[] = [];
    evicted.push(...window.push({ role: 'user', content: userInput }));
    evicted.push(...window.push({ role: 'assistant', content: reply }));

    // ⑤ 滑出才沉淀：仅在此刻判断 + 落库（窗口内对话全程不写库，D-024/D-003）
    const sedimentCallsBefore = deps.llm.callCount;
    const sedimented: Sedimented[] = [];
    for (const turn of evicted) {
      // 只沉淀用户原话（助手回话不是"用户的事实"，不入真相库）
      if (turn.role !== 'user') continue;
      const rawInput = perception(turn.content, source_type);
      if (!attention(rawInput).admit) continue; // 占位恒 admit；真实过滤下阶段
      const { event, eventId } = await eventMaker(rawInput, deps.store, deps.llm);
      sedimented.push({ turn, eventId, event });
    }

    return {
      reply,
      replyLlmCalls,
      needRecall,
      recallQuery,
      recallLlmCalls,
      recalled: recalledList,
      matchedTopics,
      evicted,
      sedimented,
      sedimentationLlmCalls: deps.llm.callCount - sedimentCallsBefore,
    };
  }

  return {
    handle,
    windowSnapshot: () => window.getContext(),
  };
}
