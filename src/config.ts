/**
 * 可调参数集中地（地图 cell 11 / 16：散落参数收一处，运行后按真实体验校准）。
 * 阶段 0：身份、隐私默认、回话窗口。
 */

export interface MemoWeftConfig {
  /** v1 单人单宿主，恒定值；多用户 / 多宿主时由调用方传入覆盖。 */
  identity: { subjectId: string; hostId: string };
  /** 隐私模式：true → 证据默认不允许上云端模型。 */
  privacyMode: boolean;
  /** 证据授权默认值（cloud_read 跟随 privacyMode，见 cloudReadDefault）。 */
  evidenceDefaults: { allowLocalRead: boolean; allowInference: boolean };
  /** observed（行为观察）证据的保守默认授权（4-A）：本地可读、默认不上云、可推画像。
   *  由 put 按 sourceKind 套用（最后防线）；ingestObservations 显式传值属双保险。故 spoken 行为不变。 */
  observedDefaults: { allowLocalRead: boolean; allowCloudRead: boolean; allowInference: boolean };
  /** 回话带最近几轮（阶段 0：简单轮数窗口，非召回）。 */
  workingMemory: { maxTurns: number };
  /** 召回（阶段 1b + 4-B 衰减门控）：注入回话的相关认知条数 + 有效置信门槛。 */
  retrieval: {
    topK: number;
    /** 有效置信（衰减后）低于此值的认知不注入回话——淡了的情绪/过气的假设别硬塞（规则 8）。 */
    minEffectiveConfidence: number;
    /** 召回相似度门控：query 与认知的余弦分低于此值 → 直接不注入（防 top-k 把不相关认知也召回硬塞，STATE.md 已标缺）。
     *  0 = 关闭（默认，先不改现有 dogfood 行为）；dogfood 时照 turn.recall 里的真实分数设一个能挡住噪声的下限（值随嵌入器变）。 */
    minSimilarity: number;
  };
  /** 画像把握度算法参数（阶段 1a；MemoWeft 自算，非 LLM 自报。运行后校准）。 */
  consolidation: {
    /** 按形成方式的起步分（推测最低——难点 1：特质做不到，只当假设）。 */
    baseByFormedBy: { stated: number; observed: number; ruled: number; inferred: number };
    /** 每多一条支持证据加分、封顶条数。 */
    supportStep: number;
    supportCap: number;
    /** 每条反对证据扣分。 */
    contradictPenalty: number;
    /** 把握度下限（恒 >0）。 */
    minConfidence: number;
    /** cred_status 阈值：≥stable 稳定 / ≥limited 有限 / ≥low 低置信 / 否则候选。 */
    credThresholds: { stable: number; limited: number; low: number };
    /** 临时类内容（如 state）：置信封顶、永不进"稳定/有限"（分型时间策略 v1，cell 8 规则 8）。 */
    transientTypes: string[];
    transientCap: number;
  };
  /** M4 归因（阶段 3）：从现象 + 时间窗证据推可解释假设（cell 8 规则 6）。 */
  attribution: {
    /** 归因时间窗：从现象发生时刻回看多少小时拉候选证据（贴合"昨晚"这类）。 */
    windowHours: number;
    /** 假设把握度封顶：假设只敢低声说（规则 6 / 难点 1），不让它越攒越像定论。 */
    hypothesisCap: number;
    /** 一次 attribute 最多归因几个【最近未归因】现象（=1：只解释用户当下抱怨，防归因爆炸）。 */
    maxPhenomenaPerRun: number;
    /** 单条假设最多挂几条【原因】证据（硬封顶，防 LLM 乱挂一堆不相关证据撑置信）。 */
    maxCausesPerHypothesis: number;
    /** ④治脑补：现象要【攒够 / 反复出现】≥N 条支撑证据才归因，别每句"好累"就推因果（N 可配，dogfood 后调）。 */
    minPhenomenonSupport: number;
  };
  /** 周期后台（阶段 4-B）：分型衰减 + 自然过期 + 跨会话趋势（落地 cell 8 规则 8 / cell 12）。 */
  background: {
    /** 各类型半衰期（天）：缺省/≤0 = 不衰减（明确偏好/fact 几乎不忘）。有效置信 = confidence × 2^(-age/半衰期)，读时算不持久化。 */
    halfLifeDays: Record<string, number>;
    /** 临时类自然过期阈值（天，距上次印证 updatedAt）：列了才会过期标 invalidAt；没列 = 永不自动失效。 */
    expireAfterDays: Record<string, number>;
    /** 跨会话趋势：看近多少天的状态。 */
    trendWindowDays: number;
    /** 窗口内同类状态证据至少出现几次才算"趋势"（规则筛频率，保证真有重复）。 */
    trendMinCount: number;
  };
  /** M5 带证据主动询问（阶段 3）：拿不准就带证据问；时机保守（cell 12 开放问题）。 */
  asking: {
    /** 一轮最多产几个提问（保守：1）。 */
    maxAsks: number;
    /** 只问把握度落在此区间的假设：太低=瞎猜别烦、太高=没必要问。 */
    confidenceBand: { min: number; max: number };
    /** 只问这些可信状态的假设（保守：候选 / 低置信）。 */
    askableStatuses: string[];
  };
  /** 画像更新触发策略（治"勤"·核心①，2026-07-01）：别每次聊完就更新，攒批 / 歇久了才更新。 */
  profileUpdate: {
    /** 攒够 N 条新对话就排一次画像更新。 */
    batchSize: number;
    /** 或空闲这么多分钟没新消息，就更新一次（与 batchSize 先到先触发）。 */
    idleMinutes: number;
  };
  // 采集参数（多久采一次 / 碎片阈值）已随真实采集迁出 Core，属采集插件自持
  //   （plugins/collector-active-window/，见 boundaries.md §4.1）——Core config 不再承载。
}

export const config: MemoWeftConfig = {
  identity: { subjectId: 'owner', hostId: 'testbench' },
  privacyMode: false,
  evidenceDefaults: { allowLocalRead: true, allowInference: true },
  observedDefaults: { allowLocalRead: true, allowCloudRead: false, allowInference: true },
  workingMemory: { maxTurns: 8 },
  retrieval: { topK: 5, minEffectiveConfidence: 80, minSimilarity: 0 },
  consolidation: {
    baseByFormedBy: { stated: 600, observed: 350, ruled: 450, inferred: 200 },
    supportStep: 40,
    supportCap: 5,
    contradictPenalty: 120,
    minConfidence: 50,
    credThresholds: { stable: 750, limited: 500, low: 300 },
    transientTypes: ['state'],
    transientCap: 300,
  },
  attribution: {
    windowHours: 24, // 现象（如"昨晚没睡好"）回看 24h，能捞到"凌晨 3:30 玩游戏"
    hypothesisCap: 250, // 假设封顶 250：稳落在候选/低置信带，配合 asking.confidenceBand
    maxPhenomenaPerRun: 1, // 只归因最近一条未归因现象（防一次扫全部 state 爆炸出噪声假设）
    maxCausesPerHypothesis: 2, // 单条假设最多挂 2 条原因证据（dogfood 暴露：会乱挂一堆无关证据）
    minPhenomenonSupport: 2, // ④治脑补：现象攒够≥2 条支撑（≈反复出现两次）才归因，偶发一次不推（dogfood 后调）
  },
  asking: {
    maxAsks: 1, // 保守：一轮最多问 1 个，别烦用户
    confidenceBand: { min: 100, max: 400 }, // 将信将疑才问；太低不敢问、太高没必要问
    askableStatuses: ['candidate', 'low'], // 只问低置信假设（规则 6）
  },
  profileUpdate: {
    batchSize: 5,    // 核心①：攒够 5 条新对话才更新画像（别一聊完就算，太勤又费；dogfood 后调）
    idleMinutes: 30, // 或空闲 30 分钟没动静就更新一次（与 batchSize 先到先触发）
  },
  background: {
    // 半衰期（天）：情绪/假设忘得快，目标/项目中等，趋势/特质慢；fact/preference 不列=不衰减（明确偏好不自动忘）。
    halfLifeDays: { state: 1.5, hypothesis: 2, goal: 14, project: 14, trend: 7, trait: 60 },
    // 自然过期（天，距上次印证）：情绪/假设/趋势会过期失效；其余不列=永不自动失效（规则 8）。
    expireAfterDays: { state: 7, hypothesis: 14, trend: 30 },
    trendWindowDays: 14, // 看近两周
    trendMinCount: 3, // 窗口内状态证据 ≥3 次才聚趋势
  },
};

/** allow_cloud_read 的默认值：跟随配置——隐私模式下默认不上云。 */
export function cloudReadDefault(c: MemoWeftConfig = config): boolean {
  return !c.privacyMode;
}

/** @deprecated 用 MemoWeftConfig；保留旧名兼容已引用 DlaConfig 的宿主。 */
export type DlaConfig = MemoWeftConfig;
