/**
 * ④ Association 关联召回 —— 召回相关旧 Event + 现算 State + 取相关 Profile。
 * 对应决策：D-019（找相关靠 topic/内容，排序靠权重；起步只做 A1，不上向量）/ D-010 / D-014 / D-024（用补全意图召回）/ D-003（只读）。
 * 阶段：TASK-04 实现 A1（topic 粗筛找相关）。summary 精挑(A2)、权重排序、向量(A3) 均未做。
 *
 * A1 三步：① 让模型在【库里现有 topic】中挑出与意图相关的（开放 topic 体系，避免脆弱字面匹配）
 *         → ② SQL 按选中的 topic 精确 IN 取回 → ③ 时间倒序返回（权重排序留 TASK-05）。
 */

import type { Event } from '../event/model.ts';
import type { EventStore } from '../event/store.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { config } from '../config.ts';
import { computeWeight } from '../event/weight.ts';

/** 召回结果。 */
export interface AssociationResult {
  /** 召回的相关旧 Event（按时间倒序；TODO(TASK-05) 换权重排序）。 */
  recalled: Event[];
  /** 本次模型从现有 topic 中挑出的相关 topic（调试/验收用）。 */
  matchedTopics: string[];
}

/** 让模型在【现有 topic 清单】中挑出与意图相关的（D-019 A1 找相关）。 */
function buildTopicPickPrompt(recallQuery: string, existingTopics: string[]): ChatMessage[] {
  return [
    {
      role: 'system',
      content:
        '你在做记忆召回的"粗筛"。下面给你一段【用户当前意图】和一份【库里已有的话题清单】。' +
        '请判断意图与哪些话题相关（可以是 0 个、1 个或多个；近义/包含也算相关）。' +
        '只能从给定清单里选，不要编造清单外的话题。' +
        '只输出 JSON 数组（话题字符串），不要多余文字、不要代码块标记。例如：["志向","大学"] 或 []',
    },
    {
      role: 'user',
      content:
        `【用户当前意图】\n${recallQuery}\n\n` +
        `【库里已有的话题清单】\n${existingTopics.map((t) => `- ${t}`).join('\n')}`,
    },
  ];
}

/** 解析模型返回的 topic 数组，并与现有清单取交集（防模型编造清单外的）。 */
function parsePickedTopics(text: string, existingTopics: string[]): string[] {
  const start = text.indexOf('[');
  const end = text.lastIndexOf(']');
  if (start === -1 || end === -1 || end < start) return [];
  let arr: unknown;
  try {
    arr = JSON.parse(text.slice(start, end + 1));
  } catch {
    return [];
  }
  if (!Array.isArray(arr)) return [];
  const allowed = new Set(existingTopics);
  const picked = arr.filter((x): x is string => typeof x === 'string' && allowed.has(x));
  return [...new Set(picked)];
}

/**
 * A1 召回：根据【补全后的意图 recallQuery】（D-024，非原始 event）从库里捞相关 Event。
 * 只读 Event 库（D-003），不写不改。
 *
 * @param recallQuery TASK-03 产出的干净意图文字
 * @param store       Event 存储层（只用其只读查询）
 * @param llm         模型客户端（打 topic 粗筛用，一次调用）
 */
export async function association(
  recallQuery: string,
  store: EventStore,
  llm: LLMClient,
): Promise<AssociationResult> {
  // 现有 topic 清单（口子在 store.distinctTopics，将来可截断）
  const existingTopics = store.distinctTopics();
  if (existingTopics.length === 0) {
    return { recalled: [], matchedTopics: [] };
  }

  // ① 模型在现有 topic 中挑相关（A1 找相关）
  const raw = await llm.chat(buildTopicPickPrompt(recallQuery, existingTopics));
  const matchedTopics = parsePickedTopics(raw, existingTopics);
  if (matchedTopics.length === 0) {
    return { recalled: [], matchedTopics: [] };
  }

  // ② SQL 精确 IN 取回（找相关，不动）
  const candidates = store.findByTopics(matchedTopics, config.association.maxCandidates);

  // ③ 权重降序排序（D-007/D-019：权重只排序，不参与找相关）。同权重按时间倒序兜底。
  const recalled = candidates
    .map((e) => ({ e, w: computeWeight(e, store) }))
    .sort((a, b) => b.w - a.w || b.e.timestamp - a.e.timestamp)
    .map((x) => x.e);

  return { recalled, matchedTopics };
}
