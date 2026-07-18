/**
 * 图谱 payload 构建。纯离线，使用内存数据库。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores } from '../../src/store/openStores.ts';
import { buildMemoryGraph } from '../../src/graph/buildMemoryGraph.ts';
import type { MemoryGraphNode, MemoryGraphEdge } from '../../src/graph/model.ts';

/** 造：2 证据（1 spoken + 1 observed）+ 1 事件 + 2 活跃认知（preference/hypothesis 各挂 1 证据）+ 1 失效认知。 */
function seed() {
  const s = openStores(':memory:');
  const e1 = s.evidenceStore.put({
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'h',
    rawContent: '我喜欢喝茶',
  });
  const e2 = s.evidenceStore.put({
    subjectId: 'owner',
    sourceKind: 'observed',
    hostId: 'h',
    rawContent: '游戏开到3:30',
  });
  const ev = s.eventStore.put({
    subjectId: 'owner',
    summary: '聊了茶+游戏',
    occurredAt: e1.occurredAt,
    evidenceIds: [e1.id, e2.id],
  });
  const cPref = s.cognitionStore.put({
    subjectId: 'owner',
    content: '用户喜欢喝茶',
    contentType: 'preference',
    formedBy: 'stated',
    confidence: 600,
    credStatus: 'limited',
    evidence: [{ evidenceId: e1.id, relation: 'support' }],
  });
  const cHyp = s.cognitionStore.put({
    subjectId: 'owner',
    content: '可能熬夜打游戏导致没睡好',
    contentType: 'hypothesis',
    formedBy: 'inferred',
    confidence: 200,
    credStatus: 'low',
    evidence: [{ evidenceId: e2.id, relation: 'support' }],
  });
  const cOld = s.cognitionStore.put({
    subjectId: 'owner',
    content: '旧判断',
    contentType: 'fact',
    formedBy: 'stated',
    confidence: 500,
    credStatus: 'limited',
  });
  s.cognitionStore.update(cOld.id, { invalidAt: '2026-06-30T00:00:00.000Z' });
  return { s, e1, e2, ev, cPref, cHyp, cOld };
}

const byKind = (nodes: MemoryGraphNode[], kind: string) => nodes.filter((n) => n.kind === kind);
const edgeKinds = (edges: MemoryGraphEdge[], kind: string) => edges.filter((e) => e.kind === kind);

test('global 默认：subject + 活跃认知 + 展开的证据/事件链', () => {
  const { s, e1, e2, ev, cPref, cHyp, cOld } = seed();
  try {
    const g = buildMemoryGraph('owner', s, { now: '2026-07-02T00:00:00.000Z' });

    assert.equal(byKind(g.nodes, 'subject').length, 1, '一个 subject 中心节点');
    const cogIds = byKind(g.nodes, 'cognition').map((n) => n.id);
    assert.ok(cogIds.includes(cPref.id) && cogIds.includes(cHyp.id), '两条活跃认知在图里');
    assert.ok(!cogIds.includes(cOld.id), '失效认知默认不出现');

    assert.equal(edgeKinds(g.edges, 'belongs_to_subject').length, 2);
    // evidence / event 展开
    assert.equal(byKind(g.nodes, 'evidence').length, 2);
    assert.equal(byKind(g.nodes, 'event').length, 1);
    // supports：e1→cPref、e2→cHyp
    const supports = edgeKinds(g.edges, 'supports');
    assert.ok(supports.some((e) => e.source === e1.id && e.target === cPref.id));
    assert.ok(supports.some((e) => e.source === e2.id && e.target === cHyp.id));
    // distilled_into：e1→ev、e2→ev
    const di = edgeKinds(g.edges, 'distilled_into');
    assert.ok(di.some((e) => e.source === e1.id && e.target === ev.id));
    assert.ok(di.some((e) => e.source === e2.id && e.target === ev.id));

    assert.equal(g.stats.activeCognitionCount, 2);
    assert.equal(g.stats.hypothesisCount, 1);
    assert.equal(g.stats.observedEvidenceCount, 1, 'e2 是 observed');
    assert.equal(g.generatedAt, '2026-07-02T00:00:00.000Z');
    assert.equal(g.scope, 'global');
  } finally {
    s.close();
  }
});

test('includeEvidence=false：只留 subject + cognition，无 evidence/event', () => {
  const { s } = seed();
  try {
    const g = buildMemoryGraph('owner', s, { includeEvidence: false });
    assert.equal(byKind(g.nodes, 'evidence').length, 0);
    assert.equal(byKind(g.nodes, 'event').length, 0);
    assert.ok(
      g.edges.every((e) => e.kind === 'belongs_to_subject'),
      '只剩归属边',
    );
    assert.equal(g.depth, 1, '未展开 → depth 1');
  } finally {
    s.close();
  }
});

test('includeInvalid=true：失效认知进图，节点带 invalidAt + colorKey=invalid', () => {
  const { s, cOld } = seed();
  try {
    const g = buildMemoryGraph('owner', s, { includeInvalid: true, includeEvidence: false });
    const node = g.nodes.find((n) => n.id === cOld.id);
    assert.ok(node, '失效认知在图里');
    assert.equal(node!.invalidAt, '2026-06-30T00:00:00.000Z');
    assert.equal(node!.colorKey, 'invalid');
  } finally {
    s.close();
  }
});

test('contentType 过滤：只留 hypothesis，其余计入 hiddenCount', () => {
  const { s, cHyp, cPref } = seed();
  try {
    const g = buildMemoryGraph('owner', s, { contentType: 'hypothesis', includeEvidence: false });
    const cogIds = byKind(g.nodes, 'cognition').map((n) => n.id);
    assert.deepEqual(cogIds, [cHyp.id], '只留假设');
    assert.ok(!cogIds.includes(cPref.id));
    assert.ok(g.stats.hiddenCount >= 1, '被过滤的认知计入 hiddenCount');
  } finally {
    s.close();
  }
});

test('conflicted 认知：colorKey=conflicted 且 stats.conflictedCount 计数', () => {
  const s = openStores(':memory:');
  try {
    const e = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: 'x',
    });
    const c = s.cognitionStore.put({
      subjectId: 'owner',
      content: '有冲突的判断',
      contentType: 'fact',
      formedBy: 'stated',
      confidence: 400,
      credStatus: 'conflicted',
      evidence: [{ evidenceId: e.id, relation: 'contradict' }],
    });
    const g = buildMemoryGraph('owner', s, {});
    const node = g.nodes.find((n) => n.id === c.id)!;
    assert.equal(node.colorKey, 'conflicted');
    assert.equal(g.stats.conflictedCount, 1);
    // contradict 关系 → contradicts 边（虚线）
    const contra = g.edges.find((ed) => ed.kind === 'contradicts');
    assert.ok(contra && contra.dashed === true, 'contradict → contradicts 虚线边');
  } finally {
    s.close();
  }
});

test('onlyCloudBlocked：只留 allowCloudRead=false 的证据', () => {
  const s = openStores(':memory:');
  try {
    // spoken 默认可上云；observed 默认不上云（云端受限）。
    const spoken = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'h',
      rawContent: '亲口',
      allowCloudRead: true,
    });
    const observed = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'observed',
      hostId: 'h',
      rawContent: '观察',
      allowCloudRead: false,
    });
    s.cognitionStore.put({
      subjectId: 'owner',
      content: 'c1',
      contentType: 'fact',
      formedBy: 'stated',
      confidence: 500,
      credStatus: 'limited',
      evidence: [
        { evidenceId: spoken.id, relation: 'support' },
        { evidenceId: observed.id, relation: 'support' },
      ],
    });
    const g = buildMemoryGraph('owner', s, { onlyCloudBlocked: true });
    const evIds = g.nodes.filter((n) => n.kind === 'evidence').map((n) => n.id);
    assert.deepEqual(evIds, [observed.id], '只剩云端受限的那条证据');
  } finally {
    s.close();
  }
});

test('tool 证据：节点 colorKey=tool + stats.toolEvidenceCount 计数（tool-result-ingest）', () => {
  const s = openStores(':memory:');
  try {
    const eTool = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'tool',
      hostId: 'h',
      rawContent: '{"temp":31}',
    });
    const eObs = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'observed',
      hostId: 'h',
      rawContent: '观察',
    });
    s.cognitionStore.put({
      subjectId: 'owner',
      content: 'c1',
      contentType: 'fact',
      formedBy: 'stated',
      confidence: 500,
      credStatus: 'limited',
      evidence: [
        { evidenceId: eTool.id, relation: 'support' },
        { evidenceId: eObs.id, relation: 'support' },
      ],
    });
    const g = buildMemoryGraph('owner', s, {});
    const toolNode = g.nodes.find((n) => n.id === eTool.id)!;
    assert.equal(toolNode.colorKey, 'tool', 'tool 证据有独立着色键');
    assert.equal(toolNode.sourceKind, 'tool');
    assert.equal(g.stats.toolEvidenceCount, 1, 'tool 证据计数');
    assert.equal(g.stats.observedEvidenceCount, 1, 'observed 计数不受影响');
  } finally {
    s.close();
  }
});
