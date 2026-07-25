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

/** 一个只按当前待固化事件回 new[] 的脚本 LLM；每次 chat 返回队列里的下一段。 */
function scriptedLlm(newItemsByTurn: Array<(eid: string) => string>) {
  let turn = 0;
  return {
    lastEvidenceId: '',
    async chat() {
      const fn = newItemsByTurn[turn++] ?? (() => '[]');
      return `{"new":${fn(this.lastEvidenceId)}}`;
    },
  };
}

test('现状复现：模型把「立场反转」误判成 new → 库里并存两条极性相反的 active 认知（A5）', async () => {
  const s = fresh();
  try {
    // 第一轮：用户说爱喝咖啡 → 落一条 preference。
    const e1 = seed(s, '我超爱喝咖啡，每天好几杯', '2026-07-01T10:00:00.000Z');
    const llm = scriptedLlm([
      () => `[{"content":"用户爱喝咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e1}"]}]`,
      () => `[{"content":"用户不再喝咖啡了","content_type":"preference","formed_by":"stated","support_evidence_ids":["${'__E2__'}"]}]`,
    ]);
    llm.lastEvidenceId = e1;
    await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm });
    assert.equal(s.cog.active('u').length, 1, '第一轮后应有一条"爱喝咖啡"');

    // 第二轮：用户反转说戒了咖啡，但模型把它当成【新认知】（而非 conflict/correct）。
    const e2 = seed(s, '我把咖啡戒了，再也不喝了', '2026-08-01T10:00:00.000Z');
    // 脚本第二段里把占位符替换成真 e2。
    llm.lastEvidenceId = e2;
    const llm2 = {
      async chat() {
        return `{"new":[{"content":"用户不再喝咖啡了","content_type":"preference","formed_by":"stated","support_evidence_ids":["${e2}"]}]}`;
      },
    };
    await consolidate('u', { eventStore: s.evt, evidenceStore: s.ev, cognitionStore: s.cog, llm: llm2 });

    const active = s.cog.active('u');
    // 缺陷现状：两条极性相反的认知并存，且都不是 conflicted/contested。
    assert.equal(active.length, 2, 'A5 复现：并存两条认知');
    const contents = active.map((c) => c.content).sort();
    assert.ok(
      active.some((c) => /爱喝咖啡/.test(c.content)) && active.some((c) => /不再喝咖啡/.test(c.content)),
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
