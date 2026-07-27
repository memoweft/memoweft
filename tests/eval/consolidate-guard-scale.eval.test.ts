/**
 * consolidate 第一道 A5 护栏（guardHits · topK shortlist）的【规模行为】离线夹具。
 *
 * 背景（§15.3 eval 规模盲区）：护栏对每条 new 候选，先按余弦从 existing 画像里取【前 topK】条
 *   （默认 3）做极性判，命中即挂反证走 conflict 语义、不新建相反行（把并存矛盾拉回可见）。
 *   但 `.slice(0, topK)` 是硬截断：当同一话题下【近义但不矛盾】的旧认知足够多时，真正极性相反的
 *   那条旧认知会因余弦排名被挤出 topK、【根本不进极性判】→ 护栏静默漏判 → 新认知照常入库、
 *   矛盾并存不被拦。这正是需要 reconcile 第二道【全画像】护栏兜底的根因（reconcile.ts 抓这条残留）。
 *
 * 现有 eval 语料单一 subject 下 existing 认知 ≤2 条，真矛盾那条永远落在 topK 之内、护栏必命中——
 *   这条 topK 截断漏判在小场景里【结构上无法触发】，此前只能靠 dogfood(gpt-4o) 兜底。
 *
 * 本套用例全离线（stub embedder 给可控余弦矩阵、scripted LLM 区分主固化请求与极性判请求），
 *   把漏判的【触发条件】与【机制正确性】各钉一条，讲清「为什么这是规模盲区」：
 *     - CT01：规模下漏判——existing 塞进 3 条高余弦近义句 + 1 条真矛盾（余弦排第 4），topK=3
 *       把真矛盾挤出 shortlist → 护栏漏判、new 照常入库、旧认知反证数为 0。
 *     - CT02：topK 够大则命中——同 existing、topK=4 让真矛盾进 shortlist → 护栏命中、挂反证、不新建。
 *       证明护栏机制本身是对的，漏判纯粹是 topK 截断造成（诚实分级，不把护栏说成不工作）。
 *     - CT03：小场景天然不暴露——existing 只有那条真矛盾（无近义句干扰），topK=3 下它必在 shortlist、
 *       护栏必命中。这就是 §15.3「小场景 eval 抓不到规模缺陷」的直接机制解释。
 *
 * 断言语义、不锚魔数（threshold 用 GUARD_DEFAULT_SIMILARITY）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../../src/evidence/store.ts';
import { SqliteEventStore } from '../../src/event/store.ts';
import { SqliteCognitionStore } from '../../src/cognition/store.ts';
import { consolidate } from '../../src/consolidation/consolidate.ts';
import { GUARD_DEFAULT_SIMILARITY } from '../../src/consolidation/contradiction.ts';
import type { Embedder } from '../../src/retrieval/embedder.ts';
import type { LLMClient, ChatMessage } from '../../src/llm/client.ts';

/** stub embedder：按 content 文本返回预置向量（余弦矩阵完全可控）；给不出即抛（防夹具悄悄退化）。 */
function stubEmbedder(byText: ReadonlyMap<string, readonly number[]>): Embedder {
  return {
    async embed(texts: string[]): Promise<number[][]> {
      return texts.map((t) => {
        const v = byText.get(t);
        if (!v) throw new Error(`stubEmbedder: 无预置向量 for ${JSON.stringify(t)}`);
        return [...v];
      });
    },
  };
}

/**
 * scripted LLM：区分两类请求。
 *   - 主固化请求（CONSOLIDATE_PROMPT）：返回固定 new 候选 JSON。
 *   - 护栏极性判请求（judgeContradiction，user 以 "Statement A" / "陈述甲" 起头）：
 *     仅当被判的 existing 那条是【真矛盾】内容时返回 contradicts=true，其余（近义句）返回 false。
 */
function scriptedLLM(newJson: string, realConflictContent: string): LLMClient {
  let calls = 0;
  return {
    get callCount() {
      return calls;
    },
    async chat(messages: ChatMessage[]): Promise<string> {
      calls++;
      const user = messages[messages.length - 1]?.content ?? '';
      const isJudge = user.includes('Statement A') || user.includes('陈述甲');
      if (isJudge) return `{"contradicts": ${user.includes(realConflictContent)}}`;
      return newJson;
    },
  };
}

/** 2D 单位向量（deg 角）：用夹角精确控制余弦（cos(夹角)）。 */
function unit(deg: number): number[] {
  const r = (deg * Math.PI) / 180;
  return [Math.cos(r), Math.sin(r)];
}

/** 内容常量：真矛盾旧认知 + 近义但不矛盾旧认知 + 极性反转的新候选。用整句、互不为子串，供 scripted judge 精确识别。 */
const REAL_CONFLICT = '用户爱吃辣、无辣不欢';
const NEIGHBORS = ['用户喜欢川菜馆子', '用户常点麻辣香锅', '用户偏好重口味菜系'];
const CANDIDATE = '用户现在完全戒辣、一点都不碰';

/**
 * 跑一遍开护栏的 consolidate：existing 认知按 spec 预置（含各自可控向量），
 *   新候选来自一条未消化 spoken 证据 + 事件，scripted LLM 主固化产出该候选、护栏对同题旧认知极性判。
 */
async function runGuard(opts: {
  existing: Array<{ content: string; vec: number[] }>;
  topK: number;
}) {
  const ev = new SqliteEvidenceStore(':memory:');
  const evt = new SqliteEventStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const byText = new Map<string, number[]>();

  const existingIds = opts.existing.map((x) => {
    byText.set(x.content, x.vec);
    return cog.put({
      subjectId: 'owner',
      content: x.content,
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'stable',
    }).id;
  });

  byText.set(CANDIDATE, unit(0)); // 候选向量：作为余弦原点
  const e = ev.put({
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'h',
    rawContent: CANDIDATE,
  });
  evt.put({
    subjectId: 'owner',
    summary: '用户谈饮食',
    occurredAt: e.occurredAt,
    evidenceIds: [e.id],
  });
  const newJson = JSON.stringify({
    new: [
      {
        content: CANDIDATE,
        content_type: 'preference',
        formed_by: 'stated',
        support_evidence_ids: [e.id],
      },
    ],
  });

  const result = await consolidate('owner', {
    eventStore: evt,
    evidenceStore: ev,
    cognitionStore: cog,
    llm: scriptedLLM(newJson, REAL_CONFLICT),
    contradictionGuard: {
      embedder: stubEmbedder(byText),
      minSimilarity: GUARD_DEFAULT_SIMILARITY,
      topK: opts.topK,
    },
  });

  const contradictCountOf = (id: string) =>
    cog.sourcesOf(id).filter((s) => s.relation === 'contradict').length;
  const close = () => {
    ev.close();
    evt.close();
    cog.close();
  };
  return { result, cog, existingIds, contradictCountOf, close };
}

/** 真矛盾旧认知（余弦 ~0.60，≥阈值进 shortlist）+ N 条近义句（余弦 ~0.97，排在真矛盾之前）。 */
const REAL_CONFLICT_VEC = { content: REAL_CONFLICT, vec: unit(53) }; // cos(53°)≈0.60
const NEIGHBOR_VECS = [
  { content: NEIGHBORS[0]!, vec: unit(5) }, // cos≈0.996
  { content: NEIGHBORS[1]!, vec: unit(10) }, // cos≈0.985
  { content: NEIGHBORS[2]!, vec: unit(15) }, // cos≈0.966
];

test('CT01 规模下 topK 截断漏判：真矛盾被近义句挤出 shortlist → 护栏漏判、矛盾静默并存', async () => {
  // existing = 3 近义（余弦 .996/.985/.966）+ 1 真矛盾（.602）；topK=3 → shortlist 只取前 3 近义。
  const g = await runGuard({ existing: [...NEIGHBOR_VECS, REAL_CONFLICT_VEC], topK: 3 });
  try {
    const realId = g.existingIds[3]!; // 真矛盾那条
    assert.equal(g.result.created.length, 1, '新认知照常入库（护栏没拦住极性反转）');
    assert.equal(g.result.created[0]!.content, CANDIDATE, '入库的正是那条反转候选');
    assert.equal(g.result.conflicted, 0, '护栏未命中：conflicted=0');
    assert.equal(g.contradictCountOf(realId), 0, '真矛盾旧认知没被挂反证 → 矛盾静默并存（漏判）');
  } finally {
    g.close();
  }
});

test('CT02 topK 够大则命中：真矛盾进 shortlist → 护栏挂反证、不新建（机制本身正确）', async () => {
  // 同 existing，仅把 topK 提到 4 → shortlist 容得下真矛盾那条 → 极性判命中。
  const g = await runGuard({ existing: [...NEIGHBOR_VECS, REAL_CONFLICT_VEC], topK: 4 });
  try {
    const realId = g.existingIds[3]!;
    assert.equal(g.result.created.length, 0, '命中护栏 → 不新建相反行');
    assert.equal(g.result.conflicted, 1, '走 conflict 语义、挂反证：conflicted=1');
    assert.equal(g.contradictCountOf(realId), 1, '反证挂到真矛盾旧认知上（冲突拉回可见）');
  } finally {
    g.close();
  }
});

test('CT03 小场景天然不暴露：existing 只有真矛盾一条 → 必在 shortlist、护栏必命中（§15.3 盲区根因）', async () => {
  // 无近义句干扰：existing=[真矛盾]，topK=3 下它稳进 shortlist。现有小场景 eval 就是这个形态 → 测不到漏判。
  const g = await runGuard({ existing: [REAL_CONFLICT_VEC], topK: 3 });
  try {
    const realId = g.existingIds[0]!;
    assert.equal(g.result.created.length, 0, '小场景下护栏命中 → 不新建');
    assert.equal(g.result.conflicted, 1, '小场景下 conflicted=1（漏判在此结构下无法复现）');
    assert.equal(g.contradictCountOf(realId), 1, '真矛盾旧认知被正常挂反证');
  } finally {
    g.close();
  }
});
