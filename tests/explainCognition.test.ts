/**
 * 按 id 解释一条认知：core.explainCognition({ cognitionId }) 直接返回该认知本体 + 完整溯源链。
 *
 * 与 D-0021 的 core.recall({ explain: true }) 的区别：那条只能靠 query 相似度命中，
 * 指定一条认知问"它凭什么成立"是拿不到的（确认式 UI / 记忆管理页要的正是后者）。
 * provenance 形状、授权位随附、悬挂链跳过三条语义与 D-0021 完全一致（复用同一段富化）。
 *
 * 纯离线（stub LLM + 词匹配 retriever）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import type { ChatMessage } from '../src/llm/client.ts';
import type { Retriever } from '../src/retrieval/retriever.ts';

/** 简易词匹配召回器（同 recallExplain）：按共享词打分。 */
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

/** stub：consolidate 出一条 new preference，引用 prompt 中的真实 evidence id。 */
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

/** 建一个落在临时文件上的 core（用例 2 需要第二个连接直接注入 contradict 链）。 */
function freshCore() {
  const dir = mkdtempSync(join(tmpdir(), 'mw-explain-'));
  const dbPath = join(dir, 'memoweft.sqlite');
  const core = createMemoWeftCore({ dbPath, llm: makeStub(), retriever: wordRetriever() });
  return {
    core,
    dbPath,
    cleanup() {
      try {
        core.close();
      } catch {
        /* 已关则略过 */
      }
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

/** 摄入一句话并固化，返回落成的那条认知 id。 */
async function seedOne(core: ReturnType<typeof createMemoWeftCore>, subjectId = 'u') {
  await core.ingestUserMessage({ content: 'I like tea', subjectId });
  await core.updateProfile({ subjectId });
  const cogs = core.memory.listCognitions({ subjectId });
  assert.ok(cogs.length >= 1, '前置：固化出至少一条认知');
  return cogs[0]!.id;
}

test('explainCognition：按 id 拿到认知本体 + support 溯源链（含授权位）', async () => {
  const { core, cleanup } = freshCore();
  try {
    const id = await seedOne(core);

    const ex = core.explainCognition({ cognitionId: id, subjectId: 'u' });
    assert.ok(ex, '按 id 解释得到结果');
    assert.equal(ex.id, id);
    assert.ok(ex.content.includes('tea'), '带认知内容');
    assert.equal(ex.contentType, 'preference');
    assert.ok(typeof ex.confidence === 'number' && ex.confidence > 0, '带把握度');
    assert.ok(ex.credStatus.length > 0, '带 credStatus');

    assert.ok(Array.isArray(ex.provenance) && ex.provenance.length >= 1, '带溯源链');
    const p = ex.provenance[0]!;
    assert.ok(p.evidenceId && p.summary.length > 0, '溯源项带 evidenceId + 证据 summary');
    assert.equal(p.relation, 'support');
    assert.equal(p.sourceKind, 'spoken');
    // D-0021 的隐私加固：随附授权位，宿主转发云模型前按 tier 自筛。
    assert.equal(typeof p.allowCloudRead, 'boolean', '随附 allowCloudRead 授权位');
    assert.equal(typeof p.allowInference, 'boolean', '随附 allowInference 授权位');

    assert.equal(ex.supportCount, ex.provenance.filter((x) => x.relation === 'support').length);
    assert.equal(ex.contradictCount, 0);
  } finally {
    cleanup();
  }
});

test('explainCognition：support / contradict 两种关系都如实带出并分别计数', async () => {
  const { core, dbPath, cleanup } = freshCore();
  try {
    const id = await seedOne(core);
    // 直接注入一条反证链：conflict 分支要走 LLM 两轮才能构造，而本例要测的是
    // 「解释如实呈现两种关系」，不是 consolidate 怎么判冲突 —— 用 store 精确构造。
    const ev = await core.ingestUserMessage({ content: 'I drank coffee again', subjectId: 'u' });
    const cogStore = new SqliteCognitionStore(dbPath);
    try {
      cogStore.addEvidence(id, [{ evidenceId: ev.id, relation: 'contradict' }]);
    } finally {
      cogStore.close();
    }

    const ex = core.explainCognition({ cognitionId: id, subjectId: 'u' });
    assert.ok(ex);
    const relations = ex.provenance.map((p) => p.relation).sort();
    assert.ok(relations.includes('support'), '支撑链在');
    assert.ok(relations.includes('contradict'), '反证链在（不消解、如实暴露）');
    assert.equal(ex.supportCount, 1);
    assert.equal(ex.contradictCount, 1);
  } finally {
    cleanup();
  }
});

test('explainCognition：不存在的 id → null（不抛错、不造字段）', async () => {
  const { core, cleanup } = freshCore();
  try {
    await seedOne(core);
    assert.equal(core.explainCognition({ cognitionId: 'no-such-id', subjectId: 'u' }), null);
  } finally {
    cleanup();
  }
});

test('explainCognition：跨 subject → null（拿不到别人的认知）', async () => {
  const { core, cleanup } = freshCore();
  try {
    const id = await seedOne(core, 'u');
    assert.equal(
      core.explainCognition({ cognitionId: id, subjectId: 'someone-else' }),
      null,
      'subject 不匹配一律 null，不泄露他人认知',
    );
  } finally {
    cleanup();
  }
});

test('explainCognition：悬挂链跳过——证据已删则不出现在溯源里，也不凭空造字段', async () => {
  const { core, cleanup } = freshCore();
  try {
    const id = await seedOne(core);
    const before = core.explainCognition({ cognitionId: id, subjectId: 'u' });
    assert.ok(before && before.provenance.length >= 1);
    const gone = before.provenance[0]!.evidenceId;

    core.memory.removeEvidenceSafely({ evidenceId: gone, force: true, reason: 'test' });

    const after = core.explainCognition({ cognitionId: id, subjectId: 'u' });
    assert.ok(after, '认知仍在');
    assert.ok(
      !after.provenance.some((p) => p.evidenceId === gone),
      '已删证据不出现在溯源里（同 D-0021：跳过、不凭空造字段）',
    );
  } finally {
    cleanup();
  }
});

test('explainCognition：软删一条证据后 expiredCount 记一笔（撤回台账，与 active 溯源链分开）', async () => {
  const { core, cleanup } = freshCore();
  try {
    const id = await seedOne(core);
    const before = core.explainCognition({ cognitionId: id, subjectId: 'u' });
    assert.ok(before && before.provenance.length >= 1);
    assert.equal(before.expiredCount, 0, '删之前撤回数为 0');
    const gone = before.provenance[0]!.evidenceId;

    core.memory.removeEvidenceSafely({ evidenceId: gone, force: true, reason: 'test' });

    const after = core.explainCognition({ cognitionId: id, subjectId: 'u' });
    assert.ok(after, '认知仍在');
    assert.equal(after.expiredCount, 1, '撤回一条证据 → expiredCount=1（台账记一笔）');
    assert.ok(
      !after.provenance.some((p) => p.evidenceId === gone),
      '被撤回的证据已不在 active 溯源链里（置信度随之下降；台账与 provenance 分开）',
    );
  } finally {
    cleanup();
  }
});

test('explainCognition：archived / muted 的认知照常解释，但如实标出状态', async () => {
  const { core, cleanup } = freshCore();
  try {
    const id = await seedOne(core);

    core.memory.muteCognition({ cognitionId: id, muted: true, reason: 'test' });
    const muted = core.explainCognition({ cognitionId: id, subjectId: 'u' });
    assert.ok(muted, '静音的认知仍能按 id 解释（显式请求不走召回门控）');
    assert.ok(muted.mutedAt, '如实标出 mutedAt');

    core.memory.archiveCognition({ cognitionId: id, reason: 'test' });
    const archived = core.explainCognition({ cognitionId: id, subjectId: 'u' });
    assert.ok(archived, '归档的认知仍能按 id 解释——否则用户点“为什么记得这条”就失效了');
    assert.ok(archived.archivedAt, '如实标出 archivedAt');
  } finally {
    cleanup();
  }
});
