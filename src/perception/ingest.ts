/**
 * 通用观察摄入口（observation mode）。
 *
 * MemoWeft 只定义"观察怎么进来"——把外部采集器（活动窗口 / 设备…）标准化好的 Observation
 * 落成 sourceKind='observed' 的证据。MemoWeft 不在库里写"怎么从操作系统抓"（采集由宿主适配器负责）。
 *
 * 授权默认值：observed 证据默认 { local:true, cloud:false, inference:true }。
 *   - cloud=false → 被 evidence/privacy.ts 的 filterCloudReadable 在写路径三处挡住，默认不进入内建云写模型 prompt。
 *   - inference=true → 仍允许用于画像推导；是否可进入内建云写模型 prompt 由 allowCloudRead 独立控制。
 *   - Observation 显式给了某授权位 → 尊重显式值（测试台"允许上云"勾选 = 显式 allowCloudRead:true）。
 *
 * 操作系统采集由外部 collector 负责；本模块仅定义标准化观察的摄入边界。
 */
import { config, type MemoWeftConfig } from '../config.ts';
import type { EvidenceStore } from '../evidence/store.ts';
import type { Evidence } from '../evidence/model.ts';

/** 一条观察（开放可扩展：kind + 结构化 meta，新增信号类型不改接口）。 */
export interface Observation {
  /** 观察种类；当前 collector 使用 'active_window'，接口允许宿主定义其它信号类型。 */
  kind: string;
  /** 观察发生时刻（ISO，必带——evidence contract：每条证据必带精确时间）。 */
  occurredAt: string;
  /** 标准化后的人类可读串，例："在 VS Code（memoweft）停留约 40 分钟"。 */
  content: string;
  /** 幂等键（同一窗口会话不重复落）；缺省 = 不去重。 */
  originId?: string | null;
  /** 原始结构化字段（app/title/durationSec…），开放且不持久化；Evidence 的公共存储契约不包含 meta。 */
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
  /** 可注入配置（config 去单例）：不传 = 用全局单例（identity.hostId / observedDefaults）。 */
  config?: MemoWeftConfig;
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
  const cfg = deps.config ?? config; // 可注入配置（缺省=单例）
  const hostId = deps.hostId ?? cfg.identity.hostId;
  const def = cfg.observedDefaults;
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
      // 授权：显式 > observed 保守默认（双保险：put 已按 sourceKind='observed' 兜底不进入内建云写模型 prompt，这里再显式传一次等价）。
      allowLocalRead: obs.allowLocalRead ?? def.allowLocalRead,
      allowCloudRead: obs.allowCloudRead ?? def.allowCloudRead,
      allowInference: obs.allowInference ?? def.allowInference,
    });
    stored.push(ev);
  }

  return { stored, skipped };
}
