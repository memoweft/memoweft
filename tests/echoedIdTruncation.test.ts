/**
 * Echoed evidence-id truncation regression coverage.
 *
 * 与 evidenceIdTruncation.test.ts（consolidate 主路径）同根:模型会**模仿提示词示例的 id 形态**、
 * 间歇性把 36 字符 UUID 截成前缀写回,而代码精确匹配 → 产出被静默丢弃。审查发现另外三处**同构、
 * 且原本无任何防护**的路径，本文件固定其关闭后的行为：
 *   - attribute（归因假设）:证据 id 改发短标号 `[e1]`；标号 / 截断前缀都解回真 id;捏造仍丢。
 *   - trends（趋势聚合）:同上。
 *   - cognition_id（consolidate 的 reinforce/correct/conflict 引用已有认知）:加容错 + 认不出就告警。
 * 三处共用 src/llm/echoedId.ts 的 resolveEchoedId。全离线（stub LLM + 内存库）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { SqliteSemanticResolutionStore } from '../src/interaction/semanticResolutionStore.ts';
import { attribute } from '../src/attribution/attribute.ts';
import { aggregateTrends } from '../src/background/trends.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';

const stubOf = (body: string) => ({
  callCount: 0,
  async chat() {
    this.callCount++;
    return body;
  },
});

function captureWarn(): [string[], () => void] {
  const warns: string[] = [];
  const real = console.warn;
  console.warn = (...a: unknown[]) => {
    warns.push(a.map(String).join(' '));
  };
  return [
    warns,
    () => {
      console.warn = real;
    },
  ];
}

// ══════════ attribute ══════════
// 复刻 attribution.test.ts 的场景:「没睡好」现象（≥2 条支撑，过④门槛）+「游戏到3:30」观察证据（唯一候选原因）。

function attrScenario() {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const eSleep = ev.put({
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'h',
    rawContent: '昨晚没睡好',
    occurredAt: '2026-06-23T08:00:00.000Z',
  });
  const eSleep2 = ev.put({
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'h',
    rawContent: '还是没睡好',
    occurredAt: '2026-06-23T08:05:00.000Z',
  });
  const eGame = ev.put({
    subjectId: 'owner',
    sourceKind: 'observed',
    hostId: 'h',
    rawContent: '游戏开到凌晨 3:30',
    occurredAt: '2026-06-23T03:30:00.000Z',
    allowCloudRead: true,
  });
  cog.put({
    subjectId: 'owner',
    content: '用户昨晚没睡好',
    contentType: 'state',
    formedBy: 'stated',
    confidence: 250,
    credStatus: 'low',
    evidence: [
      { evidenceId: eSleep.id, relation: 'support' },
      { evidenceId: eSleep2.id, relation: 'support' },
    ],
  });
  return { ev, cog, eGame };
}

test('attribute · 模型返回短标号 e1 → 假设关联真实 evidence id', async () => {
  const { ev, cog, eGame } = attrScenario();
  try {
    const r = await attribute('owner', {
      evidenceStore: ev,
      cognitionStore: cog,
      llm: stubOf(
        '{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["e1"]}]}',
      ),
    });
    assert.equal(r.hypotheses.length, 1, '标号 e1 应解回唯一候选原因 → 产出假设');
    assert.ok(r.hypotheses[0]!.basedOnEvidenceIds.includes(eGame.id), '溯源挂真证据 id，不是标号');
  } finally {
    ev.close();
    cog.close();
  }
});

test('attribute · 截断兜底：模型把 UUID 截成前 8 位 → 仍解回、假设不再静默丢弃', async () => {
  const { ev, cog, eGame } = attrScenario();
  try {
    const r = await attribute('owner', {
      evidenceStore: ev,
      cognitionStore: cog,
      llm: stubOf(
        `{"hypotheses":[{"content":"可能因为玩游戏太晚导致没睡好","based_on_evidence_ids":["${eGame.id.slice(0, 8)}"]}]}`,
      ),
    });
    assert.equal(r.hypotheses.length, 1, '截断前缀应被唯一前缀解回（此前：精确匹配落空 → 静默丢）');
    assert.ok(r.hypotheses[0]!.basedOnEvidenceIds.includes(eGame.id));
  } finally {
    ev.close();
    cog.close();
  }
});

test('attribute · 护栏：捏造 id（非任何真 id 前缀）→ 被证据白名单拒绝，不产假设', async () => {
  const { ev, cog } = attrScenario();
  try {
    const r = await attribute('owner', {
      evidenceStore: ev,
      cognitionStore: cog,
      llm: stubOf(
        '{"hypotheses":[{"content":"瞎编","based_on_evidence_ids":["fabricated-cause-id"]}]}',
      ),
    });
    assert.equal(r.hypotheses.length, 0, '捏造 id 解不出 → 不硬编假设');
  } finally {
    ev.close();
    cog.close();
  }
});

// ══════════ trends ══════════

function seedStates(ev: SqliteEvidenceStore, cog: SqliteCognitionStore, texts: string[]): string[] {
  const ids: string[] = [];
  texts.forEach((t, i) => {
    const e = ev.put({
      subjectId: 'u',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: t,
      occurredAt: `2026-06-2${i}T08:00:00.000Z`,
    });
    cog.put({
      subjectId: 'u',
      content: `用户${t}`,
      contentType: 'state',
      formedBy: 'stated',
      confidence: 250,
      credStatus: 'low',
      evidence: [{ evidenceId: e.id, relation: 'support' }],
    });
    ids.push(e.id);
  });
  return ids;
}

test('trends · 标号路：模型写 e1..e4 → 趋势产出、溯源挂真证据 id', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const ids = seedStates(ev, cog, ['很烦', '又没睡好', '提不起劲', '还是很累']);
    const r = await aggregateTrends(
      'u',
      {
        evidenceStore: ev,
        cognitionStore: cog,
        llm: stubOf(
          '{"trends":[{"content":"用户最近这段时间持续情绪低落","based_on_evidence_ids":["e1","e2","e3","e4"]}]}',
        ),
      },
      new Date('2026-06-30T00:00:00.000Z'),
    );
    assert.equal(r.trends.length, 1, '标号应解回 → 聚出趋势');
    const linked = new Set(cog.sourcesOf(r.trends[0]!.id).map((l) => l.evidenceId));
    assert.ok(
      ids.every((id) => linked.has(id)),
      '溯源挂 4 条真证据 id',
    );
  } finally {
    ev.close();
    cog.close();
  }
});

test('trends · 截断兜底：模型把 UUID 截成前 8 位 → 趋势不再静默丢弃', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const ids = seedStates(ev, cog, ['很烦', '又没睡好', '提不起劲', '还是很累']);
    const trunc = JSON.stringify(ids.map((id) => id.slice(0, 8)));
    const r = await aggregateTrends(
      'u',
      {
        evidenceStore: ev,
        cognitionStore: cog,
        llm: stubOf(
          `{"trends":[{"content":"用户最近这段时间持续情绪低落","based_on_evidence_ids":${trunc}}]}`,
        ),
      },
      new Date('2026-06-30T00:00:00.000Z'),
    );
    assert.equal(r.trends.length, 1, '截断前缀应被解回（此前：精确匹配落空 → 静默丢）');
  } finally {
    ev.close();
    cog.close();
  }
});

test('trends · 护栏：捏造 id → 不聚趋势', async () => {
  const ev = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    seedStates(ev, cog, ['很烦', '又没睡好', '提不起劲', '还是很累']);
    const r = await aggregateTrends(
      'u',
      {
        evidenceStore: ev,
        cognitionStore: cog,
        llm: stubOf(
          '{"trends":[{"content":"瞎编","based_on_evidence_ids":["fake-a","fake-b","fake-c"]}]}',
        ),
      },
      new Date('2026-06-30T00:00:00.000Z'),
    );
    assert.equal(r.trends.length, 0, '捏造 id 解不出 → 不硬编趋势');
  } finally {
    ev.close();
    cog.close();
  }
});

// ══════════ cognition_id（consolidate reinforce）══════════

interface CStores {
  ev: SqliteEvidenceStore;
  evt: SqliteEventStore;
  cog: SqliteCognitionStore;
  sr: SqliteSemanticResolutionStore;
}
function cFresh(): CStores {
  return {
    ev: new SqliteEvidenceStore(':memory:'),
    evt: new SqliteEventStore(':memory:'),
    cog: new SqliteCognitionStore(':memory:'),
    sr: new SqliteSemanticResolutionStore(':memory:'),
  };
}
function cClose(s: CStores) {
  s.ev.close();
  s.evt.close();
  s.cog.close();
  s.sr.close();
}
function cDeps(s: CStores, stub: { callCount: number; chat(): Promise<string> }) {
  return {
    eventStore: s.evt,
    evidenceStore: s.ev,
    cognitionStore: s.cog,
    semanticResolutionStore: s.sr,
    llm: stub,
  };
}
/** 造一条已有认知 + 一个带新证据的事件（印证它）；返回认知与证据 id。 */
function reinforceSetup(s: CStores) {
  const cogId = s.cog.put({
    subjectId: 'u',
    content: '用户喜欢喝茶',
    contentType: 'preference',
    formedBy: 'stated',
    confidence: 600,
    credStatus: 'limited',
  }).id;
  const e = s.ev.put({
    subjectId: 'u',
    sourceKind: 'spoken',
    hostId: 'h',
    rawContent: '我还是很爱喝茶',
    occurredAt: '2026-06-01T08:00:00.000Z',
  });
  s.evt.put({
    subjectId: 'u',
    summary: '用户重申爱喝茶',
    occurredAt: '2026-06-01T08:00:00.000Z',
    evidenceIds: [e.id],
  });
  return { cogId, eId: e.id };
}

test('cognition_id · 截断容错：模型把 cognition_id 截成前 8 位 → reinforce 仍生效', async () => {
  const s = cFresh();
  try {
    const { cogId, eId } = reinforceSetup(s);
    const body = JSON.stringify({
      reinforce: [{ cognition_id: cogId.slice(0, 8), support_evidence_ids: ['e1'] }],
    });
    const r = await consolidate('u', cDeps(s, stubOf(body)));
    assert.equal(
      r.reinforced,
      1,
      '截断的 cognition_id 应被解回 → reinforce 生效（此前：get 落空 → 静默跳过）',
    );
    assert.ok(
      s.cog.sourcesOf(cogId).some((l) => l.evidenceId === eId),
      '新证据挂到了真认知上',
    );
  } finally {
    cClose(s);
  }
});

test('cognition_id · 护栏 + 告警：认不出的 cognition_id → 不 reinforce 且落告警', async () => {
  const s = cFresh();
  const [warns, restore] = captureWarn();
  try {
    reinforceSetup(s);
    const body = JSON.stringify({
      reinforce: [{ cognition_id: 'totally-fake-cog-id', support_evidence_ids: ['e1'] }],
    });
    const r = await consolidate('u', cDeps(s, stubOf(body)));
    assert.equal(r.reinforced, 0, '认不出的 cognition_id → 不 reinforce');
    assert.ok(
      warns.some((w) => w.includes('[memoweft/consolidate]') && w.includes('reinforce')),
      `应落一条 reinforce 的 cognition_id 告警，实得：${JSON.stringify(warns)}`,
    );
  } finally {
    restore();
    cClose(s);
  }
});

test('cognition_id · 写对完整 id 时零变化（精确匹配优先）', async () => {
  const s = cFresh();
  try {
    const { cogId } = reinforceSetup(s);
    const r = await consolidate(
      'u',
      cDeps(
        s,
        stubOf(
          JSON.stringify({ reinforce: [{ cognition_id: cogId, support_evidence_ids: ['e1'] }] }),
        ),
      ),
    );
    assert.equal(r.reinforced, 1);
  } finally {
    cClose(s);
  }
});
