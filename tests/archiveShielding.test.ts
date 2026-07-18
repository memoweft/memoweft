/**
 * 归档隔离的离线契约。
 * 规则：已归档认知不参与画像更新或主动询问，定期清理也不修改它（数据保留、可恢复）。
 * SqliteCognitionStore.active() 仅返回未失效且未归档项，因此各活动路径统一排除归档项：
 *   consolidate（现有画像 prompt）/ proposeAsk（挑假设）/ revisitConflicts（复看冲突）/ expire（临时类过期）。
 * 全用 :memory: 共享连接 + stub LLM，无运行时残留、不依赖网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores, type StoreBundle } from '../src/store/openStores.ts';
import { createMemoryManagementAPI } from '../src/memory/managementApi.ts';
import { consolidate } from '../src/consolidation/consolidate.ts';
import { proposeAsk } from '../src/asking/proposeAsk.ts';
import { revisitConflicts } from '../src/asking/revisitConflicts.ts';
import { expire } from '../src/background/expire.ts';
import type { ChatMessage } from '../src/llm/client.ts';

/** 快速搭一套 :memory: 库 + 受控管理 API（归档统一走 archiveCognition，同真实路径）。 */
function setup(): { bundle: StoreBundle; api: ReturnType<typeof createMemoryManagementAPI> } {
  const bundle = openStores(':memory:');
  return { bundle, api: createMemoryManagementAPI(bundle) };
}

test('active() 语义：归档的不在 active、仍在 all；恢复归档后回到 active', () => {
  const { bundle, api } = setup();
  try {
    const keep = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });
    const arch = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '用户在学吉他',
      contentType: 'project',
      formedBy: 'stated',
      confidence: 700,
      credStatus: 'limited',
    });
    api.archiveCognition({ cognitionId: arch.id, reason: '过气了' });
    const activeIds = bundle.cognitionStore.active('owner').map((c) => c.id);
    assert.ok(activeIds.includes(keep.id), '未归档的照常 active');
    assert.ok(!activeIds.includes(arch.id), '归档的从 active 消失');
    assert.equal(bundle.cognitionStore.all('owner').length, 2, 'all 仍见全部（数据保留）');
    // 归档可恢复：archivedAt 置回 null 后重新进入 active 集合。
    bundle.cognitionStore.update(arch.id, { archivedAt: null });
    assert.ok(
      bundle.cognitionStore.active('owner').some((c) => c.id === arch.id),
      '恢复归档后重新生效',
    );
  } finally {
    bundle.close();
  }
});

test('consolidate：归档认知不进「现有画像」prompt（画像更新不当现有认知）', async () => {
  const { bundle, api } = setup();
  try {
    const keep = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });
    const arch = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '用户在学吉他（已归档）',
      contentType: 'project',
      formedBy: 'stated',
      confidence: 700,
      credStatus: 'limited',
    });
    api.archiveCognition({ cognitionId: arch.id, reason: 'archive isolation' });
    // 一个待消化的新事件（有合法原话证据），逼 consolidate 真组 prompt、真调（stub）模型。
    const e = bundle.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '今天也喝了茶',
    });
    bundle.eventStore.put({
      subjectId: 'owner',
      summary: '用户又喝茶',
      occurredAt: e.occurredAt,
      evidenceIds: [e.id],
    });
    let prompt = '';
    const stub = {
      callCount: 0,
      async chat(messages: ChatMessage[]) {
        this.callCount++;
        prompt = messages.map((m) => m.content).join('\n');
        return '{}';
      },
    };
    await consolidate('owner', {
      eventStore: bundle.eventStore,
      evidenceStore: bundle.evidenceStore,
      cognitionStore: bundle.cognitionStore,
      llm: stub,
    });
    assert.equal(stub.callCount, 1, '有新事件 → 调了模型');
    assert.ok(prompt.includes(keep.content), '未归档认知在现有画像里');
    assert.ok(!prompt.includes(arch.content), '归档认知不进现有画像 prompt');
  } finally {
    bundle.close();
  }
});

test('proposeAsk：归档的低置信假设不被挑中（不被主动问起）', async () => {
  const { bundle, api } = setup();
  try {
    // 两条同样落在"可问带"（confidence 100–400 / credStatus low / 没问过）的假设，归档其一。
    const arch = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '可能熬夜导致没睡好（已归档）',
      contentType: 'hypothesis',
      formedBy: 'inferred',
      confidence: 200,
      credStatus: 'low',
    });
    api.archiveCognition({ cognitionId: arch.id, reason: 'archive isolation' });
    const r1 = await proposeAsk('owner', {
      cognitionStore: bundle.cognitionStore,
      evidenceStore: bundle.evidenceStore,
    });
    assert.equal(r1.proposals.length, 0, '只有归档假设 → 一个都不问');
    const keep = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '可能咖啡喝多了睡不着',
      contentType: 'hypothesis',
      formedBy: 'inferred',
      confidence: 200,
      credStatus: 'low',
    });
    const r2 = await proposeAsk('owner', {
      cognitionStore: bundle.cognitionStore,
      evidenceStore: bundle.evidenceStore,
    });
    assert.equal(r2.proposals.length, 1, '未归档的照常可问');
    assert.equal(r2.proposals[0]!.cognitionId, keep.id, '挑中的是未归档那条');
  } finally {
    bundle.close();
  }
});

test('revisitConflicts：归档的冲突认知不被复看追问', async () => {
  const { bundle, api } = setup();
  try {
    const arch = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶（冲突中·已归档）',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 400,
      credStatus: 'conflicted',
    });
    api.archiveCognition({ cognitionId: arch.id, reason: 'archive isolation' });
    const r1 = await revisitConflicts('owner', {
      cognitionStore: bundle.cognitionStore,
      evidenceStore: bundle.evidenceStore,
    });
    assert.equal(r1.proposals.length, 0, '只有归档冲突 → 不复看');
    const keep = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '用户喜欢喝咖啡（冲突中）',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 400,
      credStatus: 'conflicted',
    });
    const r2 = await revisitConflicts('owner', {
      cognitionStore: bundle.cognitionStore,
      evidenceStore: bundle.evidenceStore,
    });
    assert.equal(r2.proposals.length, 1, '未归档的冲突照常复看');
    assert.equal(r2.proposals[0]!.cognitionId, keep.id);
  } finally {
    bundle.close();
  }
});

test('expire：归档的临时类不被标失效（定期清理不碰，保住可恢复）', () => {
  const { bundle, api } = setup();
  try {
    const arch = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '用户昨晚没睡好（已归档）',
      contentType: 'state',
      formedBy: 'stated',
      confidence: 250,
      credStatus: 'low',
    });
    api.archiveCognition({ cognitionId: arch.id, reason: 'archive isolation' });
    const keep = bundle.cognitionStore.put({
      subjectId: 'owner',
      content: '用户今天有点累',
      contentType: 'state',
      formedBy: 'stated',
      confidence: 250,
      credStatus: 'low',
    });
    // 把"现在"推到 30 天后：state 过期阈值 7 天，两条都早过龄——但归档那条必须不动。
    const future = new Date(Date.now() + 30 * 86_400_000);
    const r = expire('owner', { cognitionStore: bundle.cognitionStore }, future);
    assert.equal(r.expired, 1, '只有未归档的临时类过期');
    assert.ok(bundle.cognitionStore.get(keep.id)!.invalidAt, '未归档的照常标失效');
    const archived = bundle.cognitionStore.get(arch.id)!;
    assert.equal(archived.invalidAt, null, '归档的【没有】被标失效（恢复归档后应还是有效状态）');
    assert.ok(archived.archivedAt, '归档标记原样保留');
  } finally {
    bundle.close();
  }
});
