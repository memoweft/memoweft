/**
 * 自然过期（地图 cell 8 规则 8 · 阶段 4-B）：临时类认知老到一定程度 → 自然失效。
 *
 * 只有【临时类】（情绪 state、待验证假设 hypothesis）会自然过期：久没被印证 → 标 invalidAt
 *   （保留可溯源、不再被召回），呼应"此刻的情绪很快就该忘"。
 * 【稳定类】（明确偏好 preference、事实 fact 等）**永不自动失效**——哪怕很久没提（规则 8）。
 *
 * 纪律：失效 = 标 invalidAt 保留，不删（cell 6 失效而非删除）。这是 MemoWeft 自己按规则过期，
 *   不是用户主动删；用户纠正/删除走另路（M6 / 规则 10）。
 */
import { config } from '../config.ts';
import type { CognitionStore } from '../cognition/store.ts';

export interface ExpireDeps {
  cognitionStore: CognitionStore;
}

export interface ExpireResult {
  /** 本次自然过期（标 invalidAt）的认知条数。 */
  expired: number;
}

const DAY_MS = 86_400_000;

/** 把临时类里"距上次印证已超过期阈值"的认知标 invalidAt。稳定类不动。 */
export function expire(subjectId: string, deps: ExpireDeps, now: Date = new Date()): ExpireResult {
  const thresholds = config.background.expireAfterDays;
  let expired = 0;
  for (const c of deps.cognitionStore.active(subjectId)) {
    const days = thresholds[c.contentType];
    if (days == null) continue; // 不在过期名单 → 永不自动失效（明确偏好/事实）
    const ageDays = (now.getTime() - new Date(c.updatedAt).getTime()) / DAY_MS;
    if (ageDays > days) {
      deps.cognitionStore.update(c.id, { invalidAt: now.toISOString() });
      expired++;
    }
  }
  return { expired };
}
