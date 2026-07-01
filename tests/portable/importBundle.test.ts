/**
 * 便携记忆包 · 导入（Phase 5-A）。纯离线，用内存库。
 * 覆盖：dryRun 不写、merge 保真落库、幂等去重、往返一致、非法包不污染。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores } from '../../src/store/openStores.ts';
import { exportBundle } from '../../src/portable/exportBundle.ts';
import { importBundle } from '../../src/portable/importBundle.ts';
import type { MemoryBundle } from '../../src/portable/model.ts';

/** 造一个含：2 证据（其一带 originId）+ 1 事件 + 1 活跃认知（挂溯源）+ 1 已失效认知 的源包。 */
function seedSource(): MemoryBundle {
  const s = openStores(':memory:');
  try {
    const e1 = s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '我喜欢喝茶', originId: 'msg-1' });
    const e2 = s.evidenceStore.put({ subjectId: 'owner', sourceKind: 'observed', hostId: 'h', rawContent: '游戏开到3:30' });
    const ev = s.eventStore.put({ subjectId: 'owner', summary: '聊了茶+游戏', occurredAt: e1.occurredAt, evidenceIds: [e1.id, e2.id] });
    s.eventStore.markConsolidated([ev.id]); // settled 记忆：事件已消化、其认知已生成并随包带出
    s.cognitionStore.put({
      subjectId: 'owner', content: '用户喜欢喝茶', contentType: 'preference', formedBy: 'stated',
      confidence: 600, credStatus: 'limited', evidence: [{ evidenceId: e1.id, relation: 'support' }],
    });
    // 一条已失效 + 已问过的认知：验证 invalidAt / askedAt / createdAt 等时间戳保真
    const old = s.cognitionStore.put({ subjectId: 'owner', content: '旧判断', contentType: 'preference', formedBy: 'stated', confidence: 300, credStatus: 'low' });
    s.cognitionStore.update(old.id, { invalidAt: '2026-06-30T00:00:00.000Z', askedAt: '2026-06-29T00:00:00.000Z' });
    return exportBundle('owner', s, { now: '2026-07-02T00:00:00.000Z' });
  } finally {
    s.close();
  }
}

test('importBundle · dryRun：不写库，只返回计划', () => {
  const bundle = seedSource();
  const t = openStores(':memory:');
  try {
    const plan = importBundle(bundle, t, { mode: 'dryRun' });
    assert.equal(plan.valid, true);
    assert.deepEqual(plan.counts, { evidence: 2, events: 1, cognitions: 2, eventEvidence: 2, cognitionEvidence: 1 });
    assert.equal(t.evidenceStore.all().length, 0, 'dryRun 一条都不写');
    assert.equal(t.cognitionStore.all('owner').length, 0);
  } finally {
    t.close();
  }
});

test('importBundle · merge：保真落库（id/时间戳/失效状态/溯源都不丢）', () => {
  const bundle = seedSource();
  const t = openStores(':memory:');
  try {
    const plan = importBundle(bundle, t, { mode: 'merge' });
    assert.equal(plan.counts.evidence, 2);
    assert.equal(plan.counts.cognitions, 2);

    // 保真：按原 id 取回证据 + recordedAt
    const e1 = bundle.data.evidence.find((e) => e.originId === 'msg-1')!;
    const got = t.evidenceStore.get(e1.id);
    assert.ok(got, '按原 id 取回证据');
    assert.equal(got!.recordedAt, e1.recordedAt, 'recordedAt 保真');

    // 失效认知的 invalidAt / askedAt / createdAt 保真
    const oldCog = bundle.data.cognitions.find((c) => c.invalidAt != null)!;
    const gotCog = t.cognitionStore.get(oldCog.id)!;
    assert.equal(gotCog.invalidAt, '2026-06-30T00:00:00.000Z');
    assert.equal(gotCog.askedAt, '2026-06-29T00:00:00.000Z');
    assert.equal(gotCog.createdAt, oldCog.createdAt, 'createdAt 保真');

    // 溯源链保真
    const activeCog = bundle.data.cognitions.find((c) => c.content === '用户喜欢喝茶')!;
    assert.deepEqual(t.cognitionStore.sourcesOf(activeCog.id), [{ evidenceId: e1.id, relation: 'support' }]);

    // 事件覆盖证据保真 + 已标记消化（防下一轮 updateProfile 重复消化）
    const evId = bundle.data.events[0]!.id;
    assert.equal(t.eventStore.evidenceOf(evId).length, 2);
    assert.equal(t.eventStore.unconsolidated('owner').length, 0, '导入事件标 consolidated');
  } finally {
    t.close();
  }
});

test('importBundle · merge 幂等：重复导入不制造重复', () => {
  const bundle = seedSource();
  const t = openStores(':memory:');
  try {
    importBundle(bundle, t, { mode: 'merge' });
    const before = { ev: t.evidenceStore.all().length, cog: t.cognitionStore.all('owner').length, evt: t.eventStore.all('owner').length };
    const plan2 = importBundle(bundle, t, { mode: 'merge' });
    assert.equal(plan2.counts.evidence, 0, '第二次没有新写入');
    assert.equal(plan2.duplicates.evidence, 2, '两条证据都算重复');
    assert.equal(plan2.duplicates.cognitions, 2);
    assert.deepEqual(
      { ev: t.evidenceStore.all().length, cog: t.cognitionStore.all('owner').length, evt: t.eventStore.all('owner').length },
      before,
      '库内条数不变',
    );
  } finally {
    t.close();
  }
});

test('importBundle · 往返一致：A 导出 → B 导入 → B 再导出，data 深等', () => {
  const bundle = seedSource();
  const t = openStores(':memory:');
  try {
    importBundle(bundle, t, { mode: 'merge' });
    const round = exportBundle('owner', t, { now: bundle.exportedAt });
    assert.deepEqual(normalize(round.data), normalize(bundle.data), '往返后三层数据完全一致');
  } finally {
    t.close();
  }
});

test('importBundle · 非法包一条都不写（不污染库）', () => {
  const bundle = seedSource();
  const t = openStores(':memory:');
  try {
    const bad: MemoryBundle = { ...bundle, format: 'nope' };
    const plan = importBundle(bad, t, { mode: 'merge' });
    assert.equal(plan.valid, false);
    assert.equal(t.evidenceStore.all().length, 0, '非法包一条都不写');
  } finally {
    t.close();
  }
});

test('importBundle · originId 跨血缘撞车：证据跳过 + 悬空溯源丢弃 + 告警（绝不写悬空引用）', () => {
  // 目标库已有一条 originId=dup 的证据（id 与包里那条不同）——模拟同一段对话两边各自摄入、uuid 不同。
  const t = openStores(':memory:');
  const src = openStores(':memory:');
  try {
    t.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '库里已有的原话', originId: 'dup' });

    // 源包：一条带 originId=dup 的证据 Y + 一条挂它做溯源的认知。
    const y = src.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '包里的原话', originId: 'dup' });
    src.cognitionStore.put({
      subjectId: 'owner', content: '基于 Y 的判断', contentType: 'preference', formedBy: 'stated',
      confidence: 500, credStatus: 'limited', evidence: [{ evidenceId: y.id, relation: 'support' }],
    });
    const bundle = exportBundle('owner', src, { now: '2026-07-02T00:00:00.000Z' });

    const plan = importBundle(bundle, t, { mode: 'merge' });

    assert.equal(plan.valid, true);
    assert.equal(plan.counts.evidence, 0, 'Y 因 originId 撞车没写入');
    assert.equal(plan.duplicates.evidence, 1);
    assert.equal(t.evidenceStore.get(y.id), null, 'Y 的 id 不在目标库');
    assert.ok(plan.warnings.some((w) => w.includes('originId')), '给出 originId 撞车告警');

    // 认知本身是新的 → 落库；但它指向 Y 的溯源被丢弃（绝不写悬空引用）。
    const cog = t.cognitionStore.all('owner').find((c) => c.content === '基于 Y 的判断')!;
    assert.ok(cog, '新认知照常落库（不丢用户判断）');
    assert.equal(plan.counts.cognitionEvidence, 0, '指向悬空 Y 的溯源链被丢');
    assert.deepEqual(t.cognitionStore.sourcesOf(cog.id), [], '库里该认知无悬空溯源');
  } finally {
    t.close();
    src.close();
  }
});

test('importBundle · 悬空 correctsEvidenceId 落库前置空（绝不写悬空纠正指针）', () => {
  const t = openStores(':memory:');
  const src = openStores(':memory:');
  try {
    // 目标库已有 originId=dup 的证据（id 与包里的不同）。
    t.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '库里已有', originId: 'dup' });
    // 源包：B（originId=dup，导入时撞车被丢）+ A（correctsEvidenceId 指向 B）。
    const b = src.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '被纠正的旧原话', originId: 'dup' });
    const a = src.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '纠正后的新原话', correctsEvidenceId: b.id });
    const bundle = exportBundle('owner', src, { now: '2026-07-02T00:00:00.000Z' });

    const plan = importBundle(bundle, t, { mode: 'merge' });
    const gotA = t.evidenceStore.get(a.id)!;
    assert.ok(gotA, 'A 照常落库（不丢用户数据）');
    assert.equal(gotA.correctsEvidenceId, null, '指向被丢弃 B 的纠正指针被置空');
    assert.equal(t.evidenceStore.get(b.id), null, 'B 因 originId 撞车没落库');
    assert.ok(plan.warnings.some((w) => w.includes('correctsEvidenceId')), '给出置空告警');
  } finally {
    t.close();
    src.close();
  }
});

test('importBundle · 保真 consolidated：源包未消化事件，导入后仍未消化（不漏消化）', () => {
  const src = openStores(':memory:');
  const t = openStores(':memory:');
  try {
    const e = src.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'h', rawContent: '还没消化的原话' });
    src.eventStore.put({ subjectId: 'owner', summary: '未消化事件', occurredAt: e.occurredAt, evidenceIds: [e.id] }); // 不 markConsolidated
    const bundle = exportBundle('owner', src, { now: '2026-07-02T00:00:00.000Z' });
    assert.equal(bundle.data.unconsolidatedEventIds.length, 1, '导出记下未消化事件');

    importBundle(bundle, t, { mode: 'merge' });
    assert.equal(t.eventStore.unconsolidated('owner').length, 1, '导入后仍未消化 → 目标库 updateProfile 还会消化它');
  } finally {
    src.close();
    t.close();
  }
});

/** 排序归一：规避 rowid 平手导致的顺序抖动，让深等只比内容。 */
function normalize(data: MemoryBundle['data']) {
  const byId = <T extends { id: string }>(a: T, b: T) => a.id.localeCompare(b.id);
  const byJson = <T>(a: T, b: T) => JSON.stringify(a).localeCompare(JSON.stringify(b));
  return {
    evidence: [...data.evidence].sort(byId),
    events: [...data.events].sort(byId),
    cognitions: [...data.cognitions].sort(byId),
    eventEvidence: [...data.eventEvidence].sort(byJson),
    cognitionEvidence: [...data.cognitionEvidence].sort(byJson),
    unconsolidatedEventIds: [...data.unconsolidatedEventIds].sort(),
  };
}
