/**
 * 召回解释：core.recall({ explain: true }) 让每条召回认知带上支撑证据链（provenance）。
 * 缺省不传 explain → 无 provenance、零额外查询、行为不变。纯离线（stub LLM + 词匹配 retriever）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import type { ChatMessage } from '../src/llm/client.ts';
import type { Retriever } from '../src/retrieval/retriever.ts';

/** 简易词匹配召回器（同 clockInjection）：按共享词打分。 */
function wordRetriever(): Retriever {
  let items: Array<{ id: string; text: string }> = [];
  const words = (s: string) => new Set(s.toLowerCase().split(/\W+/).filter(Boolean));
  return {
    async indexAll(next) {
      items = [...next];
    },
    async search(query, topK) {
      const q = words(query);
      return items
        .map((it) => ({ id: it.id, score: [...words(it.text)].filter((w) => q.has(w)).length }))
        .filter((h) => h.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, topK);
    },
  };
}

/** stub：consolidate 出一条 new preference，并引用 prompt 中的真实 evidence id。 */
function makeStub() {
  return {
    callCount: 0,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      if (!/JSON/.test(sys)) return 'The user likes tea.';
      const um = body
        .split('\n')
        .map((l) => l.match(/^\s+- \[([^\]]+)\] /))
        .find(Boolean);
      const eid = um ? um[1]! : 'x';
      return JSON.stringify({
        new: [
          {
            content: 'User likes tea',
            content_type: 'preference',
            formed_by: 'stated',
            support_evidence_ids: [eid],
          },
        ],
        reinforce: [],
        correct: [],
        conflict: [],
      });
    },
  };
}

test(' core.recall({ explain: true }) → 召回认知带 provenance 支撑证据链', async () => {
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: makeStub(),
    retriever: wordRetriever(),
  });
  try {
    await core.ingestUserMessage({ content: 'I like tea', subjectId: 'u' });
    await core.updateProfile({ subjectId: 'u' });

    const withExpl = await core.recall({ query: 'tea drink like', subjectId: 'u', explain: true });
    assert.ok(withExpl.length >= 1, '召回到至少一条认知');
    const c = withExpl.find((h) => h.content.includes('tea')) ?? withExpl[0]!;
    assert.ok(
      Array.isArray(c.provenance) && c.provenance.length >= 1,
      'explain=true → 带 provenance 证据链',
    );
    const p = c.provenance![0]!;
    assert.ok(p.evidenceId && p.summary.length > 0, 'provenance 项带 evidenceId + 证据 summary');
    assert.equal(p.relation, 'support', '支撑关系');
    assert.ok(p.summary.toLowerCase().includes('tea'), 'summary 是那条真实证据原话（含 tea）');
    assert.equal(p.sourceKind, 'spoken', 'sourceKind 是原始证据的来源种类');
    // 隐私加固：带授权位供宿主转发云模型前自筛（对齐 buildMemoryGraph）。
    assert.equal(typeof p.allowCloudRead, 'boolean', 'provenance 带 allowCloudRead 授权位');
    assert.equal(typeof p.allowInference, 'boolean', 'provenance 带 allowInference 授权位');
  } finally {
    core.close();
  }
});

test(' 回归：不传 explain → 无 provenance（零额外工作、行为不变）', async () => {
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: makeStub(),
    retriever: wordRetriever(),
  });
  try {
    await core.ingestUserMessage({ content: 'I like tea', subjectId: 'u' });
    await core.updateProfile({ subjectId: 'u' });
    const noExpl = await core.recall({ query: 'tea drink like', subjectId: 'u' });
    assert.ok(noExpl.length >= 1, '照常召回');
    assert.ok(
      noExpl.every((h) => h.provenance === undefined),
      '不传 explain → 每条都无 provenance',
    );
  } finally {
    core.close();
  }
});

/** stub：consolidate 出两条不同类型认知（preference + state），供 contentType 过滤测试。 */
function makeStub2() {
  return {
    callCount: 0,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      if (!/JSON/.test(sys)) return 'The user likes tea and feels tired.';
      const um = body
        .split('\n')
        .map((l) => l.match(/^\s+- \[([^\]]+)\] /))
        .find(Boolean);
      const eid = um ? um[1]! : 'x';
      return JSON.stringify({
        new: [
          {
            content: 'User likes tea',
            content_type: 'preference',
            formed_by: 'stated',
            support_evidence_ids: [eid],
          },
          {
            content: 'User feels tired',
            content_type: 'state',
            formed_by: 'stated',
            support_evidence_ids: [eid],
          },
        ],
        reinforce: [],
        correct: [],
        conflict: [],
      });
    },
  };
}

test(' core.memory.muteCognition → 该认知不再召回，但仍 active + confidence 不变；unmute 恢复召回', async () => {
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: makeStub(),
    retriever: wordRetriever(),
  });
  try {
    await core.ingestUserMessage({ content: 'I like tea', subjectId: 'u' });
    await core.updateProfile({ subjectId: 'u' });
    const q = 'tea drink like';

    const c0 = core.memory
      .listCognitions({ subjectId: 'u' })
      .find((c) => c.content.includes('tea'))!;
    assert.ok(c0?.id, '先有该认知');
    assert.ok(
      (await core.recall({ query: q, subjectId: 'u' })).some((h) => h.id === c0.id),
      '静音前能召回',
    );

    // 静音
    const muted = core.memory.muteCognition({
      cognitionId: c0.id,
      muted: true,
      reason: '这条召回没用',
    });
    assert.ok(muted?.mutedAt, 'muteCognition(muted:true) → mutedAt 置上');
    assert.equal(muted!.confidence, c0.confidence, '静音只改变召回资格，不改变 confidence');
    assert.ok(
      !(await core.recall({ query: q, subjectId: 'u' })).some((h) => h.id === c0.id),
      '静音后不再召回该认知',
    );
    // 但仍 active（仍在 listCognitions、仍参与画像演化），区别于 archive 从全部活动路径排除
    const listed = core.memory.listCognitions({ subjectId: 'u' });
    assert.ok(
      listed.some((c) => c.id === c0.id && c.mutedAt),
      '静音认知仍在 listCognitions（仍 active，仅从召回结果排除）',
    );

    // 取消静音 → 恢复召回
    const unmuted = core.memory.muteCognition({ cognitionId: c0.id, muted: false, reason: '恢复' });
    assert.equal(unmuted?.mutedAt, null, 'muteCognition(muted:false) → mutedAt 清空');
    assert.ok(
      (await core.recall({ query: q, subjectId: 'u' })).some((h) => h.id === c0.id),
      '取消静音后又能召回',
    );

    // 不存在的 id → null（不抛）
    assert.equal(
      core.memory.muteCognition({ cognitionId: 'nope', muted: true, reason: 'x' }),
      null,
      '不存在的认知 → null',
    );
  } finally {
    core.close();
  }
});

test(' 索引排除（安全复审加固）：muted 认知重建索引后不进检索索引，但仍 active', async () => {
  // 记录型 retriever：留住最近一次 indexAll 收到的 id 集，用来验证 muted 是否被排除出索引。
  let lastIndexed: string[] = [];
  const ret: Retriever = {
    async indexAll(items) {
      lastIndexed = items.map((i) => i.id);
    },
    async search(query, topK) {
      return lastIndexed.slice(0, topK).map((id) => ({ id, score: 1 }));
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: makeStub(), retriever: ret });
  try {
    await core.ingestUserMessage({ content: 'I like tea', subjectId: 'u' });
    await core.updateProfile({ subjectId: 'u' });
    const c0 = core.memory
      .listCognitions({ subjectId: 'u' })
      .find((c) => c.content.includes('tea'))!;
    assert.ok(lastIndexed.includes(c0.id), '未静音时在检索索引里');

    core.memory.muteCognition({ cognitionId: c0.id, muted: true, reason: '没用' });
    // 触发重建索引（ingest 新事件 → updateProfile 不早退）
    await core.ingestUserMessage({ content: 'hello there friend', subjectId: 'u' });
    await core.updateProfile({ subjectId: 'u' });
    assert.ok(
      !lastIndexed.includes(c0.id),
      '静音后重建索引【不含】它——不占 top-K 检索槽（不饿死同话题召回）',
    );
    // 但仍 active（仍在 listCognitions、consolidation 仍能见它）
    assert.ok(
      core.memory.listCognitions({ subjectId: 'u' }).some((c) => c.id === c0.id && c.mutedAt),
      '仍在 listCognitions（仍 active、仅从索引/召回排除）',
    );
  } finally {
    core.close();
  }
});

test(' core.recall({ contentTypes }) → 只召回指定类型；不传 = 全类型；结果带 contentType', async () => {
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: makeStub2(),
    retriever: wordRetriever(),
  });
  try {
    await core.ingestUserMessage({ content: 'I like tea and I feel tired', subjectId: 'u' });
    await core.updateProfile({ subjectId: 'u' });
    const q = 'tea tired like feel drink';

    const all = await core.recall({ query: q, subjectId: 'u' });
    const types = new Set(all.map((h) => h.contentType));
    assert.ok(types.has('preference') && types.has('state'), '不传 contentTypes → 两类都召回');
    assert.ok(
      all.every((h) => typeof h.contentType === 'string'),
      '每条召回结果带 contentType（ 暴露类型）',
    );

    const onlyPref = await core.recall({ query: q, subjectId: 'u', contentTypes: ['preference'] });
    assert.ok(
      onlyPref.length >= 1 && onlyPref.every((h) => h.contentType === 'preference'),
      'contentTypes=[preference] → 只 preference',
    );

    const onlyState = await core.recall({ query: q, subjectId: 'u', contentTypes: ['state'] });
    assert.ok(
      onlyState.length >= 1 && onlyState.every((h) => h.contentType === 'state'),
      'contentTypes=[state] → 只 state',
    );

    const none = await core.recall({ query: q, subjectId: 'u', contentTypes: ['goal'] });
    assert.equal(none.length, 0, 'contentTypes=[goal]（无此类）→ 空（后过滤欠填的极端情形）');
  } finally {
    core.close();
  }
});
