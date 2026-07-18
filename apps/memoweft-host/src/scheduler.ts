/**
 * 后台画像更新调度器（Host 自建模块）。
 *
 * 这是 Host 职责，不属于 Core：`core.updateProfile()` 的实现位于 Core，
 * 但"什么时候调、攒够几条调、空闲多久调"是宿主的调度策略。语义移植自
 * testbench 的 scheduleBackgroundUpdate（server.mjs），重写成 Host 干净版、去掉调试落盘。
 *
 * 写路径需要多次模型调用，因此对话完成后先累计待处理数量，再按批量或空闲阈值更新画像。
 *   - 攒够 config.profileUpdate.batchSize 条新对话 → 立即排更新（清掉空闲计时）。
 *   - 否则重置空闲计时，歇够 config.profileUpdate.idleMinutes 没新消息 → 更新一次。
 *   - 两者先到先触发。
 * 单飞锁：同一用户的画像更新【不能并发】（否则重复消化同一批事件）。进行中再来 → 过 10s 重试、别丢这批。
 * fire-and-forget：调度不阻塞 /api/chat 回复——聊天先返回，画像后台慢慢消化。
 */
import { config } from 'memoweft';

/** 一条【本轮新增理解】的精简信号（记忆气泡就地织进聊天流用）。
 *  只带够织气泡的最小三样：id（去重）/ content（气泡正文）/ credStatus（把握度档，前端映射到用户词）。
 *  不塞整段画像——只新增几条，气泡够用即可。 */
export interface NewCognitionNote {
  id: string;
  content: string;
  /** 可信状态（candidate/low/limited/stable/conflicted）：前端按此定"还没确认/比较确定"等把握度档。
   *  刚生成的认知无衰减，credStatus 即当档——气泡就地反映足够，不必再算 effectiveConfidence。 */
  credStatus: string;
}

/** 上次更新结果摘要（供 /api/bg-status 给前端看"刚整理了什么"）。 */
export interface LastUpdateSummary {
  /** 完成时刻（ISO）。 */
  at: string;
  /** 新增了几条对你的理解。 */
  created: number;
  /** 强化/纠正/需确认的冲突条数（透传自 consolidate 结果）。 */
  reinforced: number;
  corrected: number;
  conflicted: number;
  /** 本轮新增理解的精简列表（记忆气泡用）：只 id/content/credStatus，不塞整段画像。
   *  前端轮询 bg-status 发现新的（按 id 去重、记住已织过的）就织进聊天流。 */
  newCognitions: NewCognitionNote[];
}

/** /api/bg-status 返回体：后台整理状态面。 */
export interface BgStatus {
  /** 正在整理中（写路径跑着）。 */
  profileUpdating: boolean;
  /** 攒了几条新对话还没整理（达阈值或空闲后才触发）。 */
  pendingSinceUpdate: number;
  /** 上次整理结果摘要；从没整理过 = null。 */
  lastUpdate: LastUpdateSummary | null;
}

/** 调度器依赖：只要 Core 的 updateProfile 一个能力（其余状态自持）。 */
export interface SchedulerDeps {
  /** 触发一次画像整理（内部即 core.updateProfile()）。
   *  created 是本轮新增认知（取 id/content/credStatus 供气泡；其余 UpdateProfileResult 字段这里用不到，宽松结构即可）。 */
  updateProfile: () => Promise<{
    consolidated: {
      created: Array<{ id: string; content: string; credStatus: string }>;
      reinforced: number;
      corrected: number;
      conflicted: number;
    };
  }>;
}

/** refreshNow() 的返回：用户"立即整理"完的即时结果（供 /api/refresh 透出，前端提示"新增几条"）。 */
export interface RefreshOutcome {
  /** 实际执行成功；false = 后台正忙、这次没抢到单飞锁（前端提示"正在整理中，稍等"）。 */
  ran: boolean;
  /** 成功时的本轮摘要（= 最新的 lastUpdate）；ran=false 时为 null。 */
  summary: LastUpdateSummary | null;
}

export interface ProfileScheduler {
  /** 每轮 chat 完调一次：累加计数、按攒批/空闲策略排更新（不阻塞调用方）。 */
  onTurn(): void;
  /** 用户主动"立即整理记忆"（不等攒批）：走【同一把单飞锁】跑一次 core.updateProfile——
   *  与后台调度绝不并发（否则重复消化同一批事件）。后台正忙则返回 ran:false（不排队、不抢，前端提示稍等）。
   *  成功后清 pendingSinceUpdate（这批已整理）+ 清空闲计时（不必再等空闲触发）。 */
  refreshNow(): Promise<RefreshOutcome>;
  /** 当前状态（供 /api/bg-status）。 */
  status(): BgStatus;
  /** 收尾：清空闲计时器（进程退出时调，别留悬挂 timer）。 */
  dispose(): void;
}

export function createProfileScheduler(deps: SchedulerDeps): ProfileScheduler {
  let profileUpdating = false;
  let pendingSinceUpdate = 0;
  let bgTimer: ReturnType<typeof setTimeout> | null = null;
  let lastUpdate: LastUpdateSummary | null = null;

  /** 实际执行一次整理（持单飞锁）。正忙返回 false（调用方决定重排），成功返回 true。 */
  async function runUpdate(): Promise<boolean> {
    if (profileUpdating) return false; // 正忙 → 不并发
    profileUpdating = true;
    try {
      const r = await deps.updateProfile();
      const c = r.consolidated;
      lastUpdate = {
        at: new Date().toISOString(),
        created: c.created.length,
        reinforced: c.reinforced,
        corrected: c.corrected,
        conflicted: c.conflicted,
        // 信号：本轮新增理解的精简列表（id 去重 / content 织气泡 / credStatus 定档）。
        //   只带这几条新增的，不塞整段画像；前端按 id 去重、记住已织过的，别重复织。
        newCognitions: c.created.map((x) => ({
          id: x.id,
          content: x.content,
          credStatus: x.credStatus,
        })),
      };
      return true;
    } finally {
      profileUpdating = false;
    }
  }

  /** 触发一轮整理；正忙则过 10s 重试（保留计数，别丢这批）。一次网络抖动不该崩服务。 */
  async function trigger(): Promise<void> {
    const before = pendingSinceUpdate; // 快照：updateProfile 的 await 期间可能有新 turn 累加计数
    try {
      const ok = await runUpdate();
      if (!ok) {
        // 单飞锁被占着 → 过 10s 再排（不清 pendingSinceUpdate，这批还没整理）。
        if (bgTimer) clearTimeout(bgTimer);
        bgTimer = setTimeout(() => {
          bgTimer = null;
          void trigger();
        }, 10_000);
        return;
      }
      // 成功后只扣除本次处理的数量（before），保留 await 期间新增的 turn，确保后续进入画像。
      pendingSinceUpdate = Math.max(0, pendingSinceUpdate - before);
    } catch (e) {
      // 一次 LLM 网络抖动不该崩服务；聊天不受影响，等下一批再试。
      console.error('后台整理记忆失败（已兜底，不崩服务）：', e instanceof Error ? e.message : e);
    }
  }

  return {
    onTurn() {
      pendingSinceUpdate++;
      const { batchSize, idleMinutes } = config.profileUpdate;
      if (pendingSinceUpdate >= batchSize) {
        if (bgTimer) {
          clearTimeout(bgTimer);
          bgTimer = null;
        } // 攒够一批 → 立刻排，清空闲计时
        void trigger();
      } else {
        if (bgTimer) clearTimeout(bgTimer); // 又聊了 → 重置空闲计时
        bgTimer = setTimeout(() => {
          bgTimer = null;
          void trigger();
        }, idleMinutes * 60_000);
      }
    },
    async refreshNow() {
      // 用户主动"立即整理"：走同一把单飞锁（runUpdate 内部 profileUpdating 判并发）。
      //   后台正忙 → runUpdate 返回 false，这里回 ran:false（不排队、不 10s 重试——用户在等结果，
      //   等一个在跑的后台整理完即可，前端提示"正在整理中"让用户稍后再看 bg-status）。
      const before = pendingSinceUpdate; // 快照：updateProfile 的 await 期间可能有新 turn 累加计数
      const ok = await runUpdate();
      if (!ok) return { ran: false, summary: null };
      // 成功后只扣除本次处理的数量（before），保留 await 期间新增的 turn 计数，确保后续进入画像。
      pendingSinceUpdate = Math.max(0, pendingSinceUpdate - before);
      // 只有真攒空了才清空闲计时；若 await 期间有新 turn（计数仍 >0），保留它的 idle 兜底、别断了。
      if (pendingSinceUpdate === 0 && bgTimer) {
        clearTimeout(bgTimer);
        bgTimer = null;
      }
      return { ran: true, summary: lastUpdate };
    },

    status() {
      return { profileUpdating, pendingSinceUpdate, lastUpdate };
    },
    dispose() {
      if (bgTimer) {
        clearTimeout(bgTimer);
        bgTimer = null;
      }
    },
  };
}
