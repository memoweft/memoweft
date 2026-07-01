/**
 * 写路径一键更新（地图 cell 4）：把"未整理"的近期对话沉淀成事件，从全部事件重算画像，
 * 再对新现象做归因（M4），并重建召回索引（让新画像/假设马上能被回话召回）。
 *
 * 解决 UX 缺口：distill / consolidate / 归因 / 索引本是多步，合成一个；宿主只调这一个。
 * 归因（M4）自动并进（用户拍板）：consolidate 出新 state 现象后顺带跑 attribute，假设直接进画像；
 *   "是否开口问"（M5）仍独立、手动——MemoWeft 给理解、宿主定表达（cell 9）。
 *   成本可控：attribute 内部只挑最近一条未归因现象、无现象/无原因时不调模型。
 *
 * 治慢（2026-07-01）：返回各步耗时 `timings`（ms），供测试台落盘诊断"慢在哪步"（AGENTS.md 内幕必落盘）。
 */
import { distill, type DistillResult } from '../distillation/distill.ts';
import { consolidate, type ConsolidateResult } from './consolidate.ts';
import { attribute, type AttributeResult } from '../attribution/attribute.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { EventStore } from '../event/store.ts';
import type { CognitionStore } from '../cognition/store.ts';
import type { Retriever } from '../retrieval/retriever.ts';
import type { LLMClient } from '../llm/client.ts';

export interface UpdateProfileDeps {
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  cognitionStore: CognitionStore;
  retriever: Retriever;
  llm: LLMClient;
}

/** 各步耗时(ms)，治慢诊断用。 */
export interface UpdateProfileTimings {
  distillMs: number;
  consolidateMs: number;
  attributeMs: number;
  indexMs: number;
  totalMs: number;
}

export interface UpdateProfileResult {
  distilled: DistillResult;
  consolidated: ConsolidateResult;
  /** M4 归因结果（自动并进）：对新现象产出的可解释假设。 */
  attributed: AttributeResult;
  indexed: number;
  /** 召回索引重建失败的原因（如嵌入器未启动）；null = 成功。索引是读路径优化，失败不回滚画像。 */
  indexError: string | null;
  /** 各步耗时(ms)，治慢诊断用（2026-07-01）——测试台落盘看"慢在哪步"。 */
  timings: UpdateProfileTimings;
}

export async function updateProfile(subjectId: string, deps: UpdateProfileDeps): Promise<UpdateProfileResult> {
  const t0 = Date.now();
  const distilled = await distill(subjectId, {
    evidenceStore: deps.evidenceStore,
    eventStore: deps.eventStore,
    llm: deps.llm,
  });
  const t1 = Date.now();
  const consolidated = await consolidate(subjectId, {
    eventStore: deps.eventStore,
    evidenceStore: deps.evidenceStore,
    cognitionStore: deps.cognitionStore,
    llm: deps.llm,
  });
  const t2 = Date.now();
  // M4 归因（自动并进）：对刚沉淀出的新现象推可解释假设。内部自带节流，无现象/无原因时不调模型。
  const attributed = await attribute(subjectId, {
    evidenceStore: deps.evidenceStore,
    cognitionStore: deps.cognitionStore,
    llm: deps.llm,
  });
  const t3 = Date.now();
  // 重建召回索引：只索引【未失效】的认知（被纠正/失效的不再被召回；含新产假设）。
  // 索引是读路径优化（cell 4）——嵌入器挂了也不该让已落库的画像更新失败（呼应 conversation 的"召回失败不挡"）。
  const cogs = deps.cognitionStore.active(subjectId);
  let indexed = 0;
  let indexError: string | null = null;
  try {
    await deps.retriever.indexAll(cogs.map((c) => ({ id: c.id, text: c.content })));
    indexed = cogs.length;
  } catch (e) {
    indexError = e instanceof Error ? e.message : String(e);
  }
  const t4 = Date.now();

  return {
    distilled,
    consolidated,
    attributed,
    indexed,
    indexError,
    timings: {
      distillMs: t1 - t0,
      consolidateMs: t2 - t1,
      attributeMs: t3 - t2,
      indexMs: t4 - t3,
      totalMs: t4 - t0,
    },
  };
}
