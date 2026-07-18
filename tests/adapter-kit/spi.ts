/**
 * adapter-kit · 契约 SPI（用于统一适配器行为）。
 *
 * 一份可复用契约套件喂两个适配器：每个适配器实现一个薄驱动（AdapterDriver，约 30-50 行），
 * 套件（contract.ts）据此生成语义化契约测试。SPI 只依赖 Node 内置模块，与具体适配器 / Core
 * 解耦；驱动负责把真实适配器桥接到这些形状。
 *
 * Adapter contract cases:
 *   assistant-isolation 助手消息流经适配器 → evidence 表零新增（by-construction）
 *   user-ingest 用户消息 → 恰好一条 evidence（spoken）
 *   tool-result-ingest 工具结果 → evidence 标 source=tool（实际执行：+1 条 tool 证据；助手生成的工具调用意图/入参不落库）
 *   recall-rendering recall 呈现含置信度与冲突状态，格式锁 golden 快照
 *   fabricated-id LLM 输出的虚构 evidenceId 被丢弃（不存在模型输出写入证据路径的适配器声明 N/A）
 *   degradation 记忆层抛错/超时 → 适配器降级「无记忆但对话不中断」、经注入 logger 记一条（契约；throw+timeout 都实际执行）
 *   content-type-filter 召回带 contentTypes 过滤 → 返回项只含请求类型（证明过滤端到端透传）
 *   provenance 召回带 explain → 返回项带 provenance，每条含 allowCloudRead/allowInference 授权位（隐私加固）
 *   mute-recall mute 某认知后再召回 → 该认知不再被召回、其它仍在；mute 不改变 confidence（结构化召回适配器适用，文本注入适配器声明 N/A）
 */

/** 召回呈现面自述类型：A=文本块（注入 prompt），B=结构化 JSON（structuredContent）。 */
export type RecallSurfaceKind = 'text-block' | 'structured-json';

/**
 * 一条召回解释（provenance）夹具，镜像 core 的 `RecalledEvidence`（src/pipeline/conversation.ts:44-53）。
 * 字段一律宽 `string`（与本 SPI「只依赖 node 内置、不 import core 枚举」的解耦惯例一致）。
 * allowCloudRead/allowInference 是证据级隐私授权位：解释型召回契约断言其存在；适配器据此按 tier 预筛
 * allowCloudRead=false 的受限项会隐去 summary，但仍保留授权位元数据。
 */
export interface ProvenanceFixtureItem {
  evidenceId: string;
  /** EvidenceRelation：'support' | 'contradict'。 */
  relation: string;
  /** 证据简报（summary，无则回退 rawContent）；云受限项在 B tier 预筛后可能被隐去。 */
  summary: string;
  /** SourceKind：'spoken' | 'tool' | 'observed' 等。 */
  sourceKind: string;
  /** 授权位：宿主转发云模型前据此自筛（observed/tool 默认 allowCloudRead=false）。 */
  allowCloudRead: boolean;
  allowInference: boolean;
}

/** 一条召回夹具。credStatus 用真实枚举（含一条 'conflicted' 走冲突路径）。 */
export interface RecallFixtureItem {
  id: string;
  content: string;
  confidence: number;
  /** 真实枚举，见 src/cognition/model.ts:29。 */
  credStatus: 'candidate' | 'low' | 'limited' | 'stable' | 'conflicted';
  score: number;
  /** 认知类型（ContentType 8 值之一，见 src/cognition/model.ts:15-23）。供 content-type-filter contentTypes 过滤。可选，兼容旧构造。 */
  contentType?: string;
  /** 召回解释链（仅 explain 时带）。供授权位断言与结构化适配器的 tier 预筛验证。可选。 */
  provenance?: ProvenanceFixtureItem[];
}

/**
 * 共享召回夹具：两适配器 recall-rendering 快照同源。第三条 credStatus='conflicted' —— 冲突经 credStatus
 * 隐式带出（src/consolidation/confidence.ts），锁进 golden 以证「冲突状态被如实呈现」。
 * 不新增任何冲突措辞（那是契约分岔，不碰 buildKnowledgeBlock / action.ts）。
 */
export const RECALL_FIXTURE: RecallFixtureItem[] = [
  // contentType 覆盖 3 个不同值（preference/goal/fact）→ 供 content-type-filter 过滤（按某一类型过滤挡掉其它）。
  {
    id: 'c1',
    content: 'Prefers concise answers',
    confidence: 820,
    credStatus: 'stable',
    score: 0.9,
    contentType: 'preference',
  },
  {
    id: 'c2',
    content: 'Might be learning Rust',
    confidence: 220,
    credStatus: 'candidate',
    score: 0.7,
    contentType: 'goal',
  },
  // c3 带 provenance：一条 support（spoken, allowCloudRead:true）+ 一条 contradict（tool, allowCloudRead:false），
  // 混合授权位供 provenance 断言 + B tier 预筛验证（受限项隐 summary、留授权位）。
  {
    id: 'c3',
    content: 'Home timezone',
    confidence: 500,
    credStatus: 'conflicted',
    score: 0.6,
    contentType: 'fact',
    provenance: [
      {
        evidenceId: 'e-tz-1',
        relation: 'support',
        summary: 'Said their home timezone is UTC+8',
        sourceKind: 'spoken',
        allowCloudRead: true,
        allowInference: true,
      },
      {
        evidenceId: 'e-tz-2',
        relation: 'contradict',
        summary: 'Tool logged activity at 03:00 local time',
        sourceKind: 'tool',
        allowCloudRead: false,
        allowInference: true,
      },
    ],
  },
];

/** recallSurface 返回：rendered 自述类型 + 结构化 items。 */
export interface RecallSurface {
  kind: RecallSurfaceKind;
  /** A：注入进 prompt 的文本块；B：structuredContent 的 JSON 串。golden 锁这个。 */
  rendered: string;
  /** 结构化召回项，供字段级不变量断言（不依赖 golden）。contentType/provenance 仅对应契约启用时出现。 */
  items: Array<{
    id: string;
    content: string;
    confidence: number;
    credStatus: string;
    score?: number;
    /** content-type-filter：contentTypes 过滤后，每项应携带其类型供断言过滤透传。 */
    contentType?: string;
    /** provenance：explain 召回时携带的解释链（每条含授权位）。 */
    provenance?: ProvenanceFixtureItem[];
  }>;
}

/** 故障注入模式；契约执行 'throw' 与 'timeout'，'slow' 供驱动验证成功但延迟的响应。 */
export type FaultMode = 'throw' | 'timeout' | 'slow';

/** runWithFaultyCore 结果。 */
export interface FaultOutcome {
  /** 记忆层故障/超时时适配器是否降级为「无记忆但对话不中断」。 */
  degraded: boolean;
  /** 降级是否经注入 logger 记了一条结构化事件（契约 ：注入 logger 时应为 true）。 */
  logged: boolean;
}

/** 某条 AD 对本适配器是否适用 + 理由（N/A 声明位）。 */
export interface Applicability {
  status: 'applicable' | 'na';
  reason: string;
}

/** 摄入一轮用户原话的结果（user-ingest）。 */
export interface UserTurnResult {
  /** evidence 表增量（期望 +1）。 */
  delta: number;
  /** 落库证据的来源种类（期望 'spoken'）。 */
  sourceKind?: string;
  /** 落库证据的原话（期望 === 传入文本）。 */
  content?: string;
}

/**
 * tool-result-ingest 共享夹具：一轮里 LLM 发起工具调用（意图/入参）+ 工具返回结果。
 * TOOL_CALL_INTENT 含 'get_weather' 这类只出现在【调用侧】的标识串，TOOL_RESULT 不含它——
 * 便于校验「落库的只有工具返回结果、绝无助手生成的调用意图/入参」。
 */
export const TOOL_RESULT_FIXTURE = '{"city":"Xiamen","tempC":31,"sky":"sunny"}';
export const TOOL_CALL_INTENT_FIXTURE = '{"tool":"get_weather","arguments":{"city":"Xiamen"}}';

/** 摄入一轮「工具调用意图 + 工具返回结果」的结果（tool-result-ingest）。 */
export interface ToolResultTurnResult {
  /** evidence 表增量（期望 +1：只有工具返回结果落库）。 */
  delta: number;
  /** 落库证据的来源种类（期望 'tool'）。 */
  sourceKind?: string;
  /** 落库证据的原文（期望 === 传入的工具返回结果 payload）。 */
  content?: string;
  /** 助手输出排除校验：本轮 LLM 的工具调用意图/入参【未】落成任何证据（期望 true）。 */
  callIntentExcluded: boolean;
}

/** muteAndRecall 结果（召回负反馈）。 */
export interface MuteAndRecallResult {
  /** mute 后再召回，实际召回到的认知 id 列表（被 mute 的 id 应【不在】其中）。 */
  recalledIds: string[];
  /** 被 mute 项 mute【前】confidence（可选：驱动能取到才带；带则 mute-recall 断言前后相等）。 */
  mutedConfidenceBefore?: number;
  /** 被 mute 项 mute【后】confidence（应 === before；静音只改变召回资格）。 */
  mutedConfidenceAfter?: number;
}

/** 薄驱动 SPI：每适配器实现一个。 */
export interface AdapterDriver {
  /** 适配器标识（测试名用），如 'ai-sdk' | 'mcp'。 */
  name: string;
  /** user-ingest：摄入一轮用户原话 → evidence 增量（期望 +1）+ 落库形状。 */
  ingestUserTurn(text: string): Promise<UserTurnResult>;
  /** assistant-isolation：让一条助手消息流经适配器 → evidence 增量（期望 0）。 */
  ingestAssistantTurn(text: string): Promise<number>;
  /** user-ingest 幂等（A 专属）：同一轮用户原话 + 稳定 originId 触发 times 次 → 总增量（期望仍 1）。 */
  ingestUserTurnIdempotent?(text: string, times: number): Promise<number>;
  /** tool-result-ingest（applicable 时必实现）：摄入一轮「工具调用意图 + 工具返回结果」→ 只落 result（+1 tool）、意图不落库。
   *  @param resultPayload 工具执行的返回结果（应落库）。@param callIntent LLM 的工具调用意图/入参（助手输出，不应落库）。 */
  ingestToolResult?(resultPayload: string, callIntent: string): Promise<ToolResultTurnResult>;
  /** recall-rendering：按夹具召回，返回呈现面。lang 供 A 出 en/zh 两份；B 忽略。 */
  recallSurface(fixture: RecallFixtureItem[], lang?: 'en' | 'zh'): Promise<RecallSurface>;
  /** degradation：对故障 Core 跑读路径，报告降级/日志。 */
  runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome>;
  /** content-type-filter（applicable 时必实现）：适配器带 contentTypes 调召回 → 返回项只含匹配类型（证明过滤端到端透传）。
   *  @param contentTypes 请求的认知类型白名单（应透传进 core.recall 并据此过滤）。lang 供 A 出 en/zh；B 忽略。 */
  recallSurfaceFiltered?(
    fixture: RecallFixtureItem[],
    contentTypes: string[],
    lang?: 'en' | 'zh',
  ): Promise<RecallSurface>;
  /** provenance（applicable 时必实现）：适配器带 explain 调召回 → 返回项带 provenance（每条含 allowCloudRead/allowInference 授权位）。
   *  A 经 onRecall 回调交宿主自筛（provenance 绝不进注入 prompt）；B 经 memoweft_recall 输出并按 tier 预筛。 */
  recallSurfaceExplained?(fixture: RecallFixtureItem[], lang?: 'en' | 'zh'): Promise<RecallSurface>;
  /** mute-recall（结构化召回适配器适用）：mute 某认知后再召回 → 返回召回到的 id 列表 + 被 mute 项 mute 前后 confidence。
   *  @param muteId 要 mute 的认知 id（应从 recalledIds 消失、其它项仍在）。若带回前后 confidence，两者必须相等。 */
  muteAndRecall?(fixture: RecallFixtureItem[], muteId: string): Promise<MuteAndRecallResult>;
  /** 可选契约的适用性声明；不具备相应集成路径时必须给出 N/A 理由。 */
  applicability: {
    ad3: Applicability;
    ad5: Applicability;
    ad6: Applicability;
    ad7: Applicability;
    ad8: Applicability;
    ad9: Applicability;
  };
}
