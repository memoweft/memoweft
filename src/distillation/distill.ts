/**
 * 事件化（地图 cell 4 写路径）：把"还没整理成事件"的近期对话 → 总结成一个带情境的事件。
 *
 * 红线：只总结【用户的话 + 情境】，不含助手回话、不加 AI 推测评价（禁止系统自证 + 记≠改画像）。
 */
import type { EvidenceStore } from '../evidence/store.ts';
import type { EventStore } from '../event/store.ts';
import type { Event } from '../event/model.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import { filterReadableByTier } from '../evidence/privacy.ts';
import { sourceLabel } from '../evidence/sourceLabel.ts';
import { resolveLang, type MemoWeftConfig } from '../config.ts';
import { DISTILL_PROMPT } from './prompts.ts';

export interface DistillDeps {
  evidenceStore: EvidenceStore;
  eventStore: EventStore;
  llm: LLMClient;
  /** 可注入配置（config 去单例）：不传 = 用全局单例；传了则语言等文本产出跟随它——
   *  与 consolidate / attribute 口径一致（否则运行期改语言会出现 event 旧语言、cognition 新语言的错位）。 */
  config?: MemoWeftConfig;
}

export interface DistillResult {
  event: Event | null;
  /** 本轮开始时未覆盖的证据数（起始待处理量）。 */
  pendingCount: number;
  /** 【挂账信号·档2】当前写模型 tier 读不到、因而没被消化的证据数（= pending 里 tier 不可读的那些）。
   *  >0 表示"有证据卡着、当前模型 tier 消化不了"——配本地写模型 / 授权上云可解（供向导/宿主提示用）。
   *  只看读取权（云/本地），不含 inference=false（那是用户对某条证据的推理授权撤销，非配置缺口）。 */
  tierBlockedCount: number;
  llmCalls: number;
}

export async function distill(subjectId: string, deps: DistillDeps): Promise<DistillResult> {
  const evidence = deps.evidenceStore.all().filter((e) => e.subjectId === subjectId);
  const covered = new Set(deps.eventStore.coveredEvidenceIds(subjectId));
  const pending = evidence
    .filter((e) => !covered.has(e.id))
    .sort((a, b) => a.occurredAt.localeCompare(b.occurredAt));

  if (pending.length === 0) return { event: null, pendingCount: 0, tierBlockedCount: 0, llmCalls: 0 };

  // 隐私关（按当前写模型 tier 筛）+ 推理门（allowInference）：只把【当前模型可读】且【可推画像】的原话
  //   喂给 LLM 建事件。tier=cloud 筛 allowCloudRead；tier=local 筛 allowLocalRead（本地模型能读 observed）。
  //   inference 门：event summary 会喂进 consolidate 画像，故 inference=false 的证据连事件都不进
  //   （防其内容经 summary 间接渗进画像；与 consolidate/attribute 三处一致）。tier 绑在 deps.llm 上，缺省 'cloud'。
  const tier = deps.llm.tier ?? 'cloud';
  const readable = filterReadableByTier(pending, tier); // 当前 tier 读得到的
  const digestible = readable.filter((e) => e.allowInference); // 读得到且可推画像的 → 真喂给 LLM
  const tierBlockedCount = pending.length - readable.length; // 挂账信号：tier 读不到的（配本地/授权可解）
  // 本批无一条【当前模型可消化】→ 不拿空材料调模型、不建 event。被挡的证据（tier 不可读 / inference=false）
  //   【不算已覆盖】、留在 pending 下轮再扫——换本地模型 / 授权上云 / 重开推理授权后才被补消化。
  if (digestible.length === 0) return { event: null, pendingCount: pending.length, tierBlockedCount, llmCalls: 0 };

  const lang = resolveLang(deps.config);
  // 来源感知（D-0018）:每行带来源标注,让 distill 的事件描述保留"这是用户亲口 / 行为观察 / 工具返回"的区分,
  //   下游 consolidate 据此定 formedBy(observed/tool 不被误当 stated)。
  const lines = digestible.map((e) => `(${e.occurredAt.slice(0, 16)}) ${sourceLabel(e.sourceKind, lang)}${e.rawContent}`).join('\n');
  const userHead = lang === 'zh'
    ? '按时间顺序的材料（每行带来源标注；[行为观察] / [工具返回] 不是用户原话）：'
    : "Material in chronological order (each line is tagged with its source; [observed behavior] / [tool result] are NOT the user's own words):";
  const messages: ChatMessage[] = [
    { role: 'system', content: DISTILL_PROMPT.text[lang] },
    { role: 'user', content: `${userHead}\n${lines}` },
  ];

  const before = deps.llm.callCount;
  const summary = (await deps.llm.chat(messages)).trim();
  const llmCalls = deps.llm.callCount - before;

  // 覆盖修复（D8）：event 只覆盖【真消化进 summary 的】证据；被挡的不覆盖、留 pending 可再扫
  //   （否则 observed 与对话证据混批时会被静默标"已覆盖"却从未消化、且再也扫不到——卖点被架空）。
  const event = deps.eventStore.put({
    subjectId,
    summary,
    occurredAt: digestible[0]!.occurredAt,
    evidenceIds: digestible.map((e) => e.id),
  });

  return { event, pendingCount: pending.length, tierBlockedCount, llmCalls };
}
