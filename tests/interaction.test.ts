/**
 * 交互层 store 测试(v0.6 · D-0034):interaction_context / semantic_resolution 的 CRUD + 幂等 + subject 隔离 + 便携包接口。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  SqliteInteractionContextStore,
  hashContext,
} from '../src/interaction/interactionContextStore.ts';
import { SqliteSemanticResolutionStore } from '../src/interaction/semanticResolutionStore.ts';
import type { VisibleTurn } from '../src/interaction/model.ts';

const CTX: VisibleTurn[] = [
  { role: 'assistant', content: '你喜欢爬山吧?' },
  { role: 'user', content: '是的' },
];

test('interaction_context：record / get / 字段回读', () => {
  const store = new SqliteInteractionContextStore(':memory:');
  try {
    const c = store.record({ subjectId: 'owner', conversationId: 'conv-1', episodeId: 'ep-1', context: CTX });
    assert.ok(c.id);
    assert.equal(c.subjectId, 'owner');
    assert.equal(c.conversationId, 'conv-1');
    assert.equal(c.episodeId, 'ep-1');
    assert.equal(c.contextHash, hashContext(CTX));
    const got = store.get(c.id);
    assert.ok(got);
    assert.deepEqual(got.context, CTX, 'context_json round-trip');
  } finally {
    store.close();
  }
});

test('interaction_context：record 按 context_hash 幂等(同内容二次 record 不重复落库)', () => {
  const store = new SqliteInteractionContextStore(':memory:');
  try {
    const a = store.record({ subjectId: 'owner', conversationId: 'conv-1', episodeId: 'ep-1', context: CTX });
    const b = store.record({ subjectId: 'owner', conversationId: 'conv-1', episodeId: 'ep-1', context: CTX });
    assert.equal(a.id, b.id, '同内容返回同一条');
    assert.equal(store.all('owner').length, 1, '不重复落库');
    // 内容变 → 新的一条
    const c = store.record({ subjectId: 'owner', conversationId: 'conv-1', episodeId: 'ep-1', context: [...CTX, { role: 'assistant', content: '为什么?' }] });
    assert.notEqual(c.id, a.id);
    assert.equal(store.all('owner').length, 2);
  } finally {
    store.close();
  }
});

test('interaction_context：all(subjectId) 隔离 + byConversation', () => {
  const store = new SqliteInteractionContextStore(':memory:');
  try {
    store.record({ subjectId: 'A', conversationId: 'c1', episodeId: 'e1', context: [{ role: 'user', content: 'a1' }] });
    store.record({ subjectId: 'A', conversationId: 'c2', episodeId: 'e2', context: [{ role: 'user', content: 'a2' }] });
    store.record({ subjectId: 'B', conversationId: 'c3', episodeId: 'e3', context: [{ role: 'user', content: 'b1' }] });
    assert.equal(store.all('A').length, 2);
    assert.equal(store.all('B').length, 1);
    assert.equal(store.all().length, 3, '全 subject');
    assert.equal(store.byConversation('c1').length, 1);
  } finally {
    store.close();
  }
});

test('interaction_context：insert(便携包导入原样落库) + removeBySubject', () => {
  const store = new SqliteInteractionContextStore(':memory:');
  try {
    const original = store.record({ subjectId: 'owner', conversationId: 'c1', episodeId: 'e1', context: CTX });
    const store2 = new SqliteInteractionContextStore(':memory:');
    try {
      store2.insert(original);
      const got = store2.get(original.id);
      assert.ok(got);
      assert.equal(got.id, original.id, '保原 id');
      assert.equal(got.createdAt, original.createdAt, '保原时间戳');
      assert.equal(store2.removeBySubject('owner'), 1);
      assert.equal(store2.get(original.id), null);
    } finally {
      store2.close();
    }
  } finally {
    store.close();
  }
});

test('interaction_context：注入 clock → createdAt = 注入值(铁律 3b:clock 只产时间戳)', () => {
  const fixed = '2026-07-16T08:00:00.000Z';
  const store = new SqliteInteractionContextStore(':memory:', () => new Date(fixed));
  try {
    const c = store.record({ subjectId: 'owner', conversationId: 'c1', episodeId: 'e1', context: CTX });
    assert.equal(c.createdAt, fixed);
  } finally {
    store.close();
  }
});

test('semantic_resolution：put / get / ofEvidence / 字段回读', () => {
  const store = new SqliteSemanticResolutionStore(':memory:');
  try {
    const r = store.put({
      evidenceId: 'ev-1',
      resolvedContent: '用户确认自己喜欢研究 AI',
      responseAct: 'affirm',
      promptAct: 'propose',
      propositionOrigin: 'assistant_proposed',
      assertionStrength: 'explicit',
      resolverVersion: 'v1',
    });
    assert.ok(r.id);
    const got = store.get(r.id);
    assert.ok(got);
    assert.equal(got.resolvedContent, '用户确认自己喜欢研究 AI');
    assert.equal(got.responseAct, 'affirm');
    assert.equal(got.propositionOrigin, 'assistant_proposed');
    assert.equal(got.assertionStrength, 'explicit');
    assert.equal(got.requiredContext, null, '未提供 → null');
    const byEv = store.ofEvidence('ev-1');
    assert.ok(byEv);
    assert.equal(byEv.id, r.id);
    assert.equal(store.ofEvidence('nope'), null);
  } finally {
    store.close();
  }
});

test('semantic_resolution：forEvidenceIds 按证据集过滤(便携包导出) + 空入参 → []', () => {
  const store = new SqliteSemanticResolutionStore(':memory:');
  try {
    store.put({ evidenceId: 'ev-1', resolvedContent: 'r1', resolverVersion: 'v1' });
    store.put({ evidenceId: 'ev-2', resolvedContent: 'r2', resolverVersion: 'v1' });
    store.put({ evidenceId: 'ev-3', resolvedContent: 'r3', resolverVersion: 'v1' });
    assert.equal(store.forEvidenceIds([]).length, 0);
    assert.equal(store.forEvidenceIds(['ev-1', 'ev-3']).length, 2);
    assert.equal(store.forEvidenceIds(['ev-2']).length, 1);
  } finally {
    store.close();
  }
});

test('semantic_resolution：insert(导入) + removeByEvidenceIds', () => {
  const store = new SqliteSemanticResolutionStore(':memory:');
  try {
    const r = store.put({ evidenceId: 'ev-1', resolvedContent: 'r1', resolverVersion: 'v1' });
    const store2 = new SqliteSemanticResolutionStore(':memory:');
    try {
      store2.insert(r);
      assert.equal(store2.get(r.id)?.evidenceId, 'ev-1', '保原 id/字段');
      assert.equal(store2.removeByEvidenceIds(['ev-1']), 1);
      assert.equal(store2.get(r.id), null);
      assert.equal(store2.removeByEvidenceIds([]), 0, '空入参无副作用');
    } finally {
      store2.close();
    }
  } finally {
    store.close();
  }
});
