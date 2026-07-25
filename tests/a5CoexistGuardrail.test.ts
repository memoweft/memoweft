/**
 * A5「矛盾画像可并存」护栏（OPEN-DECISIONS #2 · dogfood 2026-07-25 实测 gpt-4o 上
 * P(并存矛盾|一次立场反转)≈25%~40% → 重开、加护栏）。
 *
 * 缺陷：consolidate 的 new 分支写新认知前不比对现有画像的内容/极性——当模型把一句
 * 「对旧认知的反转」误判成 new（而非 conflict/correct）时，库里就并存两条极性相反的
 * 独立 active 认知，且都不带冲突标记，对所有"冲突可见"面（credStatus / 图谱 / revisitConflicts）隐形。
 *
 * 护栏（混合，方案 1）：new 分支入库前，对内存里的现有画像算嵌入相似度筛出同主题候选，
 * 对少量候选做一次聚焦极性判断；命中"相似且极性相反" → 走 conflict 语义（把新证据挂
 * contradict 到旧认知、重算 confidence、由 deriveCredStatus 判 conflicted/contested），
 * 不再新建那条相反行。护栏依赖【可选】：不注入 embedder = 行为同旧（护住 tests/eval 与既有 call site）。
 *
 * 本文件先钉【现状复现】：不开护栏时，反转被误判 new → 并存两条矛盾认知。
 * 全离线（脚本 LLM，不依赖网络）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { createMemoWeftCore } from '../src/index.ts';

interface Stores {
  ev: SqliteEvidenceStore;
  evt: SqliteEventStore;
  cog: SqliteCognitionStore;
}
function fresh(): Stores {
  return {
    ev: new SqliteEvidenceStore(':memory:'),
    evt: new SqliteEventStore(':memory:'),
    cog: new SqliteCognitionStore(':memory:'),
  };
}
function closeAll(s: Stores) {
  s.ev.close();
  s.evt.close();
  s.cog.close();
}

/** 造一条用户主动说的证据 + 覆盖它的事件；返回证据 id。 */
function seed(s: Stores, word: string, at: string): string {
  const e = s.ev.put({
    subjectId: 'u',
    sourceKind: 'spoken',
    hostId: 'h',
    rawContent: word,
    occurredAt: at,
  });
  s.evt.put({
    subjectId: 'u',
    summary: `用户说"${word}"`,
    occurredAt: at,
    evidenceIds: [e.id],
  });
  return e.id;
}

/** 固定返回一段 {"new":[...]} 的脚本 LLM（带 callCount 满足 LLMClient 契约）。 */
function fixedNewLlm(newItems: string) {
  return {
    callCount: 0,
    async chat() {
      this.callCount++;
      return `{"new":${newItems}}`;
    },
  };
}

test('现状复现：模型把「立场反转」误判成 new → 库里并存两条极性相反的 active 认知（A5）', async () => {
  const s = fresh();
  try {
    // 第一轮：用户说爱喝咖啡 → 落一条 preference。
    const e1 = seed(s, '我超爱喝咖啡，每天好几杯', '2026-07-01T10:00:00.000Z');
    await consolidate('u', {
      eventStore: s.evt,
      evidenceStore: s.ev,
      cognitionStore: s.cog,
      llm: fixedNewLlm(
        `[{"content":"用户爱喝咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e1}"]}]`,
      ),
    });
    assert.equal(s.cog.active('u').length, 1, '第一轮后应有一条"爱喝咖啡"');

    // 第二轮：用户反转说戒了咖啡，但模型把它当成【新认知】（而非 conflict/correct）。
    const e2 = seed(s, '我把咖啡戒了，再也不喝了', '2026-08-01T10:00:00.000Z');
    await consolidate('u', {
      eventStore: s.evt,
      evidenceStore: s.ev,
      cognitionStore: s.cog,
      llm: fixedNewLlm(
        `[{"content":"用户不再喝咖啡了","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e2}"]}]`,
      ),
    });

    const active = s.cog.active('u');
    // 缺陷现状：两条极性相反的认知并存，且都不是 conflicted/contested。
    assert.equal(active.length, 2, 'A5 复现：并存两条认知');
    const contents = active.map((c) => c.content).sort();
    assert.ok(
      active.some((c) => /爱喝咖啡/.test(c.content)) &&
        active.some((c) => /不再喝咖啡/.test(c.content)),
      `应并存"爱喝咖啡"与"不再喝咖啡"，实际：${JSON.stringify(contents)}`,
    );
    assert.ok(
      active.every((c) => c.credStatus !== 'conflicted' && c.credStatus !== 'contested'),
      '缺陷现状：两条都不带冲突标记，对"冲突可见"隐形',
    );
  } finally {
    closeAll(s);
  }
});

// ── 护栏开启（注入 embedder + 极性判 llm）───────────────────────────
/** 分流：命中极性判提示词特有标记（同一个人/SAME person 等）→ 返回 {"contradicts":bool}；
 *  其余（consolidate 主调用）→ {"new":[...]}。带 callCount 满足 LLMClient 契约。 */
function guardAwareLlm(newItems: string, contradicts: boolean) {
  return {
    callCount: 0,
    async chat(messages: Array<{ role: string; content: string }>) {
      this.callCount++;
      // 用【只在极性判提示词里、consolidate 提示词里绝对没有】的标记分流
      //   （consolidate 含 "contradicts/矛盾" 会误判，故不能用它们）。
      const text = messages.map((m) => m.content).join('\n');
      if (/同一个人|SAME person|它们矛盾吗|Do they contradict/i.test(text)) {
        return `{"contradicts": ${contradicts}}`;
      }
      return `{"new":${newItems}}`;
    },
  };
}
/** 玩具嵌入器：含"咖啡/coffee"→[1,0]，含"茶/tea"→[0,1]，其余→[0.5,0.5]。让同主题余弦≈1、跨主题≈0。 */
const topicEmbedder = {
  async embed(texts: string[]): Promise<number[][]> {
    return texts.map((t) =>
      /咖啡|coffee/i.test(t) ? [1, 0] : /茶|tea/i.test(t) ? [0, 1] : [0.5, 0.5],
    );
  },
};

/** 先建"爱喝咖啡"，返回 stores（供各护栏用例接着喂第二轮）。 */
async function seedCoffeeLover() {
  const s = fresh();
  const e1 = seed(s, '我超爱喝咖啡，每天好几杯', '2026-07-01T10:00:00.000Z');
  await consolidate('u', {
    eventStore: s.evt,
    evidenceStore: s.ev,
    cognitionStore: s.cog,
    llm: fixedNewLlm(
      `[{"content":"用户爱喝咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e1}"]}]`,
    ),
  });
  return s;
}

test('护栏命中：反转被误判 new + 相似且极性相反 → 不新建相反行，改把旧认知标 conflicted', async () => {
  const s = await seedCoffeeLover();
  try {
    const e2 = seed(s, '我把咖啡戒了，再也不喝了', '2026-08-01T10:00:00.000Z');
    await consolidate('u', {
      eventStore: s.evt,
      evidenceStore: s.ev,
      cognitionStore: s.cog,
      llm: guardAwareLlm(
        `[{"content":"用户不再喝咖啡了","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e2}"]}]`,
        true,
      ),
      contradictionGuard: { embedder: topicEmbedder, minSimilarity: 0.5 },
    });
    const active = s.cog.active('u');
    assert.equal(active.length, 1, '护栏应阻止并存：只剩旧认知一条');
    assert.match(active[0]!.content, /爱喝咖啡/, '保留的是旧认知"爱喝咖啡"');
    assert.equal(active[0]!.credStatus, 'conflicted', '旧认知被挂反证 → conflicted（冲突可见）');
    assert.ok(!active.some((c) => /不再喝咖啡/.test(c.content)), '不再新建"不再喝咖啡"相反行');
  } finally {
    closeAll(s);
  }
});

test('相似度门：不同主题（茶 vs 咖啡）→ 护栏不触发、正常并存（不误伤）', async () => {
  const s = await seedCoffeeLover();
  try {
    const e2 = seed(s, '我最近爱上喝茶了', '2026-08-01T10:00:00.000Z');
    await consolidate('u', {
      eventStore: s.evt,
      evidenceStore: s.ev,
      cognitionStore: s.cog,
      // 即使极性判会说"矛盾"，茶与咖啡余弦≈0、进不了 shortlist，极性判压根不会被调用。
      llm: guardAwareLlm(
        `[{"content":"用户爱喝茶","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e2}"]}]`,
        true,
      ),
      contradictionGuard: { embedder: topicEmbedder, minSimilarity: 0.5 },
    });
    const active = s.cog.active('u');
    assert.equal(active.length, 2, '不同主题 → 不拦，两条并存');
    assert.ok(
      active.every((c) => c.credStatus !== 'conflicted'),
      '没有误挂冲突',
    );
  } finally {
    closeAll(s);
  }
});

test('极性门：同主题但不矛盾（爱喝咖啡 + 喜欢手冲咖啡）→ 护栏不触发、正常并存（不误伤）', async () => {
  const s = await seedCoffeeLover();
  try {
    const e2 = seed(s, '我特别喜欢手冲咖啡', '2026-08-01T10:00:00.000Z');
    await consolidate('u', {
      eventStore: s.evt,
      evidenceStore: s.ev,
      cognitionStore: s.cog,
      // 同主题（都进 shortlist），但极性判返回 false → 不拦。
      llm: guardAwareLlm(
        `[{"content":"用户喜欢手冲咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e2}"]}]`,
        false,
      ),
      contradictionGuard: { embedder: topicEmbedder, minSimilarity: 0.5 },
    });
    const active = s.cog.active('u');
    assert.equal(active.length, 2, '同主题不矛盾 → 不拦，两条并存');
    assert.ok(
      active.every((c) => c.credStatus !== 'conflicted'),
      '没有误挂冲突',
    );
  } finally {
    closeAll(s);
  }
});

// ── createCore 接线（宿主入口可开护栏）──────────────────────────────
test('createCore 接线：请求 contradictionGuard + 有 embedder → 构造与 no-op updateProfile 不崩', async () => {
  // 注入 stub embedder 让 embedderRef 可用（护栏 deps 得以组装）；无事件的 updateProfile 不触 llm。
  const embedder = {
    async embed(texts: string[]): Promise<number[][]> {
      return texts.map(() => [1, 0]);
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', embedder, contradictionGuard: true });
  try {
    const r = await core.updateProfile({ subjectId: 'u' }); // 无新事件 → 早退空，不调 llm
    assert.equal(r.consolidated.created.length, 0);
  } finally {
    core.close();
  }
});
