/**
 * 权重计算 —— D-007（基础分加法 × 放大系数乘法，≥1）。
 * 对应决策：D-007（公式）/ D-008（整数千分制，全程整数无浮点；最终权重为派生排序分，不设上限——见 D-007/D-008 修订）/ D-003·D-009（实时算，不入库）。
 * 阶段：TASK-05 实现。
 *
 * 关键不变量（自检）：
 * - 全程整数运算，最终 `× / 1000` 截断确定（同输入同输出，D-008 可复现）。
 * - 权重恒 >0（基础分>0 × 放大系数≥1000 / 1000 ≥ 基础分 >0）。
 * - 来源主动性 w1 最大 → 用户主动说的话权重天然高于观测（D-004 防污染）。
 * - 权重只用于召回排序（D-019），不入库（D-003/D-009：表里无 weight 字段）。
 */

import type { Event } from './model.ts';
import type { EventStore } from './store.ts';
import { config } from '../config.ts';

const W = config.weight;

/** 基础分（加法主导）：0~1000，恒 >0。w1·来源 + w2·时效 + w3·纠正，按系数和归一。 */
function baseScore(event: Event): number {
  const src = event.source_type === 'user' ? W.source.user : W.source.observed;
  const temporal = event.temporal_orientation === 'long_term' ? W.temporal.long_term : W.temporal.present;
  const correction = event.event_form === 'correction' ? W.correction.yes : W.correction.no;
  const weighted = W.w1 * src + W.w2 * temporal + W.w3 * correction;
  // 整数除法归一到 0~1000（来源/时效恒 >0，故基础分恒 >0）
  return Math.floor(weighted / (W.w1 + W.w2 + W.w3));
}

/**
 * 放大系数（乘法）：≥1000。1000 基线 + 重复度放大 + 关联广度放大。
 * TODO: 重复度目前用同 topic 条数近似，精确版需操作B（一对一判异同），后续优化。
 */
function amplifier(event: Event, store: EventStore): number {
  // 重复度近似：同 topic 条数 - 1（减去自己；唯一则 0）
  const repetition = Math.max(0, store.countByTopic(event.topic) - 1);
  const repBoost = Math.min(repetition * W.repetitionStep, W.repetitionCap);

  // 关联广度：related_event_ids 个数（现成字段，不查库）
  const assoc = event.related_event_ids.length;
  const assocBoost = Math.min(assoc * W.associationStep, W.associationCap);

  return 1000 + repBoost + assocBoost;
}

/**
 * 计算一条 Event 的权重（D-007）。纯整数计算 + 一次只读 SQL 计数，不调模型。
 * @returns 整数权重，恒 >0；为派生排序分，**不设上限**（高基础分+多重复/关联可 >1000）。
 */
export function computeWeight(event: Event, store: EventStore): number {
  const base = baseScore(event);
  const amp = amplifier(event, store);
  // 整数千分制合成：base × amp / 1000，截断为整数（确定性）
  return Math.floor((base * amp) / 1000);
}
