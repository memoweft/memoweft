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
import type { SemanticResolutionStore } from '../interaction/semanticResolutionStore.ts';
import type { Retriever } from '../retrieval/retriever.ts';
import type { LLMClient } from '../llm/client.ts';
import type { Transaction } from '../store/transaction.ts';
import type { MemoWeftConfig } from '../config.ts';
import type { Clock } from '../clock.ts';

export interface UpdateProfileDeps {
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  cognitionStore: CognitionStore;
  /** 语义解析 store（v0.6 Phase 2·D-0034，可选）：透传给 consolidate 落 semantic_resolution；不接 = 不落解析。 */
  semanticResolutionStore?: SemanticResolutionStore;
  retriever: Retriever;
  llm: LLMClient;
  /** 事务器（可选）：接了共享连接就传它，consolidate 的写入会原子化（崩在中间整段回滚）。见 store/openStores.ts。
   *  注意只作用于 consolidate 内部那段【同步】写——索引重建是读路径优化、故意放在事务外（失败不回滚画像）。 */
  transaction?: Transaction;
  /** 可注入配置（P2-5 config 去单例）：不传 = 用全局单例；传了则透传给 consolidate / attribute。 */
  config?: MemoWeftConfig;
  /** 可注入时钟（Phase 4）：透传给 consolidate / attribute 作显式时间戳/归因窗口上界；缺省真实系统时间。 */
  clock?: Clock;
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
  /** distill 结果。含【挂账信号】`distilled.tierBlockedCount`：当前写模型 tier 读不到、没被消化的证据数——
   *  >0 表示"有 observed 卡着、当前模型消化不了"，供向导/宿主提示（配本地写模型 / 授权上云可解）。 */
  distilled: DistillResult;
  consolidated: ConsolidateResult;
  /** M4 归因结果（自动并进）：对新现象产出的可解释假设。 */
  attributed: AttributeResult;
  indexed: number;
  /** 召回索引重建失败的原因（如嵌入器未启动）；null = 成功。索引是读路径优化，失败不回滚画像。 */
  indexError: string | null;
  /** 各步耗时(ms)，治慢诊断用（2026-07-01）——测试台落盘看"慢在哪步"。 */
  timings: UpdateProfileTimings;
  /** 写路径仪表（决策 D4 · 只观测不动刀）：从 consolidate 结果透传的"画像多大 / prompt 多大"，
   *  供测试台落盘、给 11-A 膨胀债画 dogfood 曲线。两值均 0 = 本轮 consolidate 未执行（无新事件）。 */
  metrics: { profileSize: number; promptChars: number };
}

export async function updateProfile(subjectId: string, deps: UpdateProfileDeps): Promise<UpdateProfileResult> {
  const t0 = Date.now();
  const distilled = await distill(subjectId, {
    evidenceStore: deps.evidenceStore,
    eventStore: deps.eventStore,
    llm: deps.llm,
    config: deps.config, // 透传注入配置（缺省=单例）：event 摘要语言与 consolidate/attribute 一致
  });
  const t1 = Date.now();
  const consolidated = await consolidate(subjectId, {
    eventStore: deps.eventStore,
    evidenceStore: deps.evidenceStore,
    cognitionStore: deps.cognitionStore,
    semanticResolutionStore: deps.semanticResolutionStore, // v0.6 Phase 2：落语义解析（缺省 undefined = 不落）
    llm: deps.llm,
    transaction: deps.transaction, // 有共享连接就把 consolidate 的写入原子化；没有则 undefined = 直接跑
    config: deps.config, // 透传注入配置（缺省=单例）
    clock: deps.clock, // 透传注入时钟（缺省=系统时间）
  });
  const t2 = Date.now();
  // M4 归因（自动并进）：对刚沉淀出的新现象推可解释假设。内部自带节流，无现象/无原因时不调模型。
  const attributed = await attribute(subjectId, {
    evidenceStore: deps.evidenceStore,
    cognitionStore: deps.cognitionStore,
    llm: deps.llm,
    config: deps.config, // 透传注入配置（缺省=单例）
    clock: deps.clock, // 透传注入时钟（缺省=系统时间）
  });
  const t3 = Date.now();
  // 重建召回索引：只索引【未失效】的认知（被纠正/失效的不再被召回；含新产假设）。
  // 索引是读路径优化（cell 4）——嵌入器挂了也不该让已落库的画像更新失败（呼应 conversation 的"召回失败不挡"）。
  // 排除 muted（D-0023 召回负反馈·对抗审查加固）：静音认知仍 active（consolidation/attribute 照常见它、仍演化），
  //   但【不进召回索引】——否则它永久占 top-K 检索槽、门控后跳过又不补足 topK，会饿死同话题其它召回。
  //   recall.ts 的 `if (c.mutedAt) continue` 门控留作【刚静音、索引尚未重建】那段窗口的守门（双保险）。
  const cogs = deps.cognitionStore.active(subjectId).filter((c) => !c.mutedAt);
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
    // 写路径仪表（D4 只观测）：从 consolidate 结果原样透传，方便落盘处不用钻进 consolidated 里挖。
    metrics: { profileSize: consolidated.profileSize, promptChars: consolidated.promptChars },
    timings: {
      distillMs: t1 - t0,
      consolidateMs: t2 - t1,
      attributeMs: t3 - t2,
      indexMs: t4 - t3,
      totalMs: t4 - t0,
    },
  };
}
