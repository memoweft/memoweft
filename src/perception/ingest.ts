/**
 * 通用观察摄入口（地图 cell 4 ① 感知 / cell 9 边界 · 阶段 4-A 档1）。
 *
 * MemoWeft 只定义"观察怎么进来"——把外部采集器（活动窗口 / 设备…）标准化好的 Observation
 * 落成 sourceKind='observed' 的证据。MemoWeft 不在库里写"怎么从操作系统抓"（cell 9：那是外挂采集器的活）。
 *
 * 授权（隐私默认本地，四步定案 #4）：observed 证据默认 { local:true, cloud:false, inference:true }。
 *   - cloud=false → 被 evidence/privacy.ts 的 filterCloudReadable 在写路径三处挡住，默认不上云。
 *   - inference=true → 仍允许变成画像（靠本地模型；本版走"路线 A"用手动授权上云的测试数据验证）。
 *   - Observation 显式给了某授权位 → 尊重显式值（测试台"允许上云"勾选 = 显式 allowCloudRead:true）。
 *
 * ⚠️ 本版只做摄入口闭环 + 验证；真采集器留骨架（collectors/activeWindow.ts）。
 */
import { config } from '../config.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { Evidence } from '../evidence/model.ts';

/** 一条观察（开放可扩展：kind + 结构化 meta，新增信号类型不改接口）。 */
export interface Observation {
  /** 观察种类。本版固定 'active_window'；以后可加 'clipboard' / 'device' …（可扩展关键）。 */
  kind: string;
  /** 观察发生时刻（ISO，必带——cell 7：每条证据必带精确时间）。 */
  occurredAt: string;
  /** 标准化后的人类可读串，例："在 VS Code（DLA_rebuild）停留约 40 分钟"。 */
  content: string;
  /** 幂等键（同一窗口会话不重复落）；缺省 = 不去重。 */
  originId?: string | null;
  /** 原始结构化字段（app/title/durationSec…），开放、不写死。本版仅承载、不落库（Evidence 无 meta 列，避免碰表结构）。 */
  meta?: Record<string, unknown>;
  /** 授权位可选；不传走 observed 保守默认（config.observedDefaults）。显式传则尊重。 */
  allowLocalRead?: boolean;
  allowCloudRead?: boolean;
  allowInference?: boolean;
}

export interface IngestDeps {
  evidenceStore: EvidenceStore;
  /** 落库归属的宿主标识；默认 config.identity.hostId。 */
  hostId?: string;
}

export interface IngestResult {
  /** 本次新落库的 observed 证据。 */
  stored: Evidence[];
  /** 因 originId 幂等命中被跳过的条数。 */
  skipped: number;
}

/**
 * 批量摄入观察 → observed 证据。带 originId 的幂等（已存在则跳过、计入 skipped）。
 * 授权位优先级：Observation 显式 > config.observedDefaults。
 */
export function ingestObservations(
  subjectId: string,
  observations: Observation[],
  deps: IngestDeps,
): IngestResult {
  const hostId = deps.hostId ?? config.identity.hostId;
  const def = config.observedDefaults;
  const stored: Evidence[] = [];
  let skipped = 0;

  for (const obs of observations) {
    // 幂等：带 originId 且已存在 → 跳过，不重复落库。
    if (obs.originId && deps.evidenceStore.findByOrigin(obs.originId)) {
      skipped++;
      continue;
    }
    const ev = deps.evidenceStore.put({
      subjectId,
      sourceKind: 'observed',
      hostId,
      originId: obs.originId ?? null,
      occurredAt: obs.occurredAt,
      rawContent: obs.content,
      // 授权：显式 > observed 保守默认。不套 put 的通用默认（那是 spoken 用的、会默认上云）。
      allowLocalRead: obs.allowLocalRead ?? def.allowLocalRead,
      allowCloudRead: obs.allowCloudRead ?? def.allowCloudRead,
      allowInference: obs.allowInference ?? def.allowInference,
    });
    stored.push(ev);
  }

  return { stored, skipped };
}
