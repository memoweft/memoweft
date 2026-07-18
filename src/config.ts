/**
 * Central configuration for identity, privacy, recall, consolidation, and background behavior.
 */

/** 库产出与文案的语言。缺省 'en'（进英文市场默认）。 */
export type Lang = 'zh' | 'en';

export interface MemoWeftConfig {
  /** v1 单人单宿主，恒定值；多用户 / 多宿主时由调用方传入覆盖。 */
  identity: { subjectId: string; hostId: string };
  /** 库产出语言（提示词 / 兜底文案 / 事件摘要等）：缺省 'en'；env `MEMOWEFT_LANG=zh` 切中文；宿主可运行期改 `config.language`。
   *  只影响文本产出，绝不进置信度自算（confidence.ts 不吃它）。 */
  language?: Lang;
  /** 隐私模式：true → 证据默认不进入 MemoWeft 内建云写模型 prompt。 */
  privacyMode: boolean;
  /** 证据授权默认值（cloud_read 跟随 privacyMode，见 cloudReadDefault）。 */
  evidenceDefaults: { allowLocalRead: boolean; allowInference: boolean };
  /** observed（行为观察）证据的保守默认授权：可进入本地写模型 prompt、默认不进入内建云写模型 prompt、可推画像。
   *  由 put 按 sourceKind 套用（最后防线）；ingestObservations 显式传值属双保险。故 spoken 行为不变。 */
  observedDefaults: { allowLocalRead: boolean; allowCloudRead: boolean; allowInference: boolean };
  /** tool（工具执行结果）证据的保守默认授权：可进入本地写模型 prompt、默认不进入内建云写模型 prompt、可推画像。
   *  工具返回值常含敏感外部数据（网页/文件/API 响应），与 observed 同级保守；由 put 按 sourceKind 套用（最后防线）。 */
  toolDefaults: { allowLocalRead: boolean; allowCloudRead: boolean; allowInference: boolean };
  /** 会话带最近几轮：简单轮数窗口，非召回。 */
  workingMemory: { maxTurns: number };
  /** 召回与衰减门控：注入回话的相关认知条数 + 有效置信门槛。 */
  retrieval: {
    topK: number;
    /** 有效置信（衰减后）低于此值的认知不注入对话，避免召回已衰减的情绪或过期假设。 */
    minEffectiveConfidence: number;
    /** 召回相似度门控：query 与认知的余弦分低于此值 → 直接不注入（避免 top-k 返回不相关认知）。
     *  0 = 关闭（默认，不改现有行为）。想开时按【你的 embedder】的真实分定——值随嵌入器变（vector 余弦 vs keyword -bm25 vs hybrid RRF 不同量纲，只对 vector 余弦语义清晰）。
     *  默认阈值为 0.55；实际分布取决于 embedder 和语料，应使用代表性数据调参。降低阈值通常提高召回，高阈值通常减少噪声。
     *  0.5 更保守（砍 ~3%）；≥0.6 开始误杀 gold。请在自己的 embedder 与语料上重新校准。 */
    minSimilarity: number;
  };
  /** 画像把握度算法参数（a；MemoWeft 自算，非 LLM 自报。运行后校准）。 */
  consolidation: {
    /** 按形成方式设置基础分；推断的初始置信最低，特质只能作为待验证假设。 */
    baseByFormedBy: {
      stated: number;
      observed: number;
      ruled: number;
      confirmed: number;
      inferred: number;
    };
    /** 每多一条支持证据加分、封顶条数。 */
    supportStep: number;
    supportCap: number;
    /** 每条反对证据扣分。 */
    contradictPenalty: number;
    /** 把握度下限（恒 >0）。 */
    minConfidence: number;
    /** cred_status 阈值：≥stable 稳定 / ≥limited 有限 / ≥low 低置信 / 否则候选。 */
    credThresholds: { stable: number; limited: number; low: number };
    /** 临时类内容（如 state）：置信封顶、永不进"稳定/有限"（分型时间策略）。 */
    transientTypes: string[];
    transientCap: number;
  };
  /** 归因：从现象和时间窗证据推导可解释、可推翻的假设。 */
  attribution: {
    /** 归因时间窗：从现象发生时刻回看多少小时拉候选证据（贴合"昨晚"这类）。 */
    windowHours: number;
    /** 假设置信度上限：防止推断因支撑累积而被提升为确定结论。 */
    hypothesisCap: number;
    /** 一次 attribute 最多归因几个【最近未归因】现象（=1：只解释用户当下抱怨，防归因爆炸）。 */
    maxPhenomenaPerRun: number;
    /** 单条假设最多挂几条【原因】证据（硬封顶，防 LLM 乱挂一堆不相关证据撑置信）。 */
    maxCausesPerHypothesis: number;
    /** 归因最小支撑数：现象至少有 N 条支撑证据才允许推导原因，避免从单次状态推断因果。 */
    minPhenomenonSupport: number;
  };
  /** 周期后台：分型衰减 + 自然过期 + 跨会话趋势。 */
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
  /** 带证据主动询问：拿不准时提供证据并保守选择提问时机。 */
  asking: {
    /** 一轮最多产几个提问（保守：1）。 */
    maxAsks: number;
    /** 只问把握度落在此区间的假设：太低=瞎猜别烦、太高=没必要问。 */
    confidenceBand: { min: number; max: number };
    /** 只问这些可信状态的假设（保守：候选 / 低置信）。 */
    askableStatuses: string[];
  };
  /** 画像更新触发策略：积累足够新材料或超过空闲阈值后更新，避免每轮重复计算。 */
  profileUpdate: {
    /** 攒够 N 条新对话就排一次画像更新。 */
    batchSize: number;
    /** 或空闲这么多分钟没新消息，就更新一次（与 batchSize 先到先触发）。 */
    idleMinutes: number;
  };
  // 采集参数（多久采一次 / 碎片阈值）已随真实采集迁出 Core，属采集插件自持
  //   （plugins/collector-active-window/）；Core config 不承载操作系统采集策略。
}

export const config: MemoWeftConfig = {
  identity: { subjectId: 'owner', hostId: 'local' }, // host_id 不是查询键；旧库保持可读，新证据使用中性的 local 默认值。
  // 默认使用英文；只有 MEMOWEFT_LANG=zh 才切换中文，其它值或未设置时均为英文。
  language: process.env.MEMOWEFT_LANG === 'zh' ? 'zh' : 'en',
  privacyMode: false,
  evidenceDefaults: { allowLocalRead: true, allowInference: true },
  observedDefaults: { allowLocalRead: true, allowCloudRead: false, allowInference: true },
  toolDefaults: { allowLocalRead: true, allowCloudRead: false, allowInference: true },
  workingMemory: { maxTurns: 8 },
  retrieval: { topK: 5, minEffectiveConfidence: 80, minSimilarity: 0 },
  consolidation: {
    baseByFormedBy: { stated: 600, observed: 350, ruled: 450, confirmed: 280, inferred: 200 }, // confirmed（附和，）夹 inferred/observed 之间：自然封顶 280+支持满200=480<limited500 → 纯附和顶天"低置信"
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
    maxCausesPerHypothesis: 2, // 单条假设最多挂 2 条原因证据（integration testing 暴露：会乱挂一堆无关证据）
    minPhenomenonSupport: 2, // 至少两条支撑才允许归因，避免从偶发状态推断因果
  },
  asking: {
    maxAsks: 1, // 保守：一轮最多问 1 个，别烦用户
    confidenceBand: { min: 100, max: 400 }, // 将信将疑才问；太低不敢问、太高没必要问
    askableStatuses: ['candidate', 'low'], // 主动询问仅面向低置信假设。
  },
  profileUpdate: {
    batchSize: 12, // 批量整理可减少画像重复发送，并为引用消解保留更完整的上下文；空闲阈值仍提供及时更新兜底
    idleMinutes: 30, // 或空闲 30 分钟没动静就更新一次（与 batchSize 先到先触发）
  },
  background: {
    // 半衰期（天）：情绪/假设忘得快，目标/项目中等，趋势/特质慢；fact/preference 不列=不衰减（明确偏好不自动忘）。
    halfLifeDays: { state: 1.5, hypothesis: 2, goal: 14, project: 14, trend: 7, trait: 60 },
    // 自然过期（天，距上次印证）：情绪/假设/趋势会过期失效；未列出的类型不会自动失效。
    expireAfterDays: { state: 7, hypothesis: 14, trend: 30 },
    trendWindowDays: 14, // 看近两周
    trendMinCount: 3, // 窗口内状态证据 ≥3 次才聚趋势
  },
};

/** allow_cloud_read 的默认值：跟随配置——隐私模式下默认不进入内建云写模型 prompt。 */
export function cloudReadDefault(c: MemoWeftConfig = config): boolean {
  return !c.privacyMode;
}

/** 取当前库语言（缺省=全局单例；singleton 默认 'en'，`MEMOWEFT_LANG=zh` 切中文）。
 *  语言只决定文本产出用哪套常量，绝不流入置信度自算 / 认知判定。 */
export function resolveLang(c: MemoWeftConfig = config): Lang {
  return c.language ?? 'en';
}

/** @deprecated 用 MemoWeftConfig；保留旧名兼容已引用 DlaConfig 的宿主。 */
export type DlaConfig = MemoWeftConfig;
