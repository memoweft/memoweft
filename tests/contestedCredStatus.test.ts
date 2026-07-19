/**
 * 中间态 contested：带少量反证但支撑仍占优的认知，不再与「一比一对峙」同等对待。
 *
 * 此前 deriveCredStatus 只要 contradictCount>0 就一律 conflicted：一条 6 支撑 1 反证的认知，
 * 和一条 1 支撑 1 反证的认知状态完全相同。computeConfidence 早就算出了两者的差别
 * （support − contradict×penalty），是 credStatus 把它抹平了。
 *
 * 判据（人类拍板）：supportCount > contradictCount → contested，否则 conflicted。
 * 与置信度公式解耦——`stated` 类支撑加分封顶 200，6 支撑 1 反证也只有 680，永远够不到
 * stable(750)，用置信度阈值做判据在结构上就走不通。
 *
 * 纯离线（stub LLM + 词匹配 retriever）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { deriveCredStatus } from '../src/consolidation/confidence.ts';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import type { ChatMessage } from '../src/llm/client.ts';
import type { Retriever } from '../src/retrieval/retriever.ts';

// ── 单元：判据本身 ──

test('deriveCredStatus：支撑压倒反证 → contested；对峙或反证占优 → conflicted', () => {
  // 6 支撑 1 反证：支撑压倒 → 有争议但仍成立
  assert.equal(deriveCredStatus(680, 1, 'fact', undefined, 6), 'contested');
  // 2 支撑 1 反证：仍是支撑多
  assert.equal(deriveCredStatus(520, 1, 'fact', undefined, 2), 'contested');
  // 1 支撑 1 反证：一比一对峙 → 不消解、暴露为冲突
  assert.equal(deriveCredStatus(480, 1, 'fact', undefined, 1), 'conflicted');
  // 2 支撑 2 反证：对峙
  assert.equal(deriveCredStatus(360, 2, 'fact', undefined, 2), 'conflicted');
  // 1 支撑 2 反证：反证占优
  assert.equal(deriveCredStatus(360, 2, 'fact', undefined, 1), 'conflicted');
});

test('deriveCredStatus：不传 supportCount → 退回旧行为（保守判 conflicted）', () => {
  // 向后兼容：外部既有调用方是三参数调用。不知道支撑数时【不能假设】支撑压倒反证，
  // 保守判 conflicted 才是对的——这也正是 tests/eval 里既有断言仍然成立的原因。
  assert.equal(deriveCredStatus(800, 1, 'fact'), 'conflicted');
  assert.equal(deriveCredStatus(900, 3, 'preference'), 'conflicted');
});

test('deriveCredStatus：无反证时不受影响，contested 只在有反证时出现', () => {
  assert.equal(deriveCredStatus(800, 0, 'fact', undefined, 6), 'stable');
  assert.equal(deriveCredStatus(520, 0, 'fact', undefined, 2), 'limited');
  assert.equal(deriveCredStatus(320, 0, 'fact', undefined, 1), 'low');
  // 临时类封顶规则不被 contested 绕过
  assert.equal(deriveCredStatus(1000, 0, 'state', undefined, 9), 'low');
});

// ── 端到端：写路径真的产出 contested ──

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

function makeStub() {
  return {
    callCount: 0,
    async chat(msgs: ChatMessage[]): Promise<string> {
      this.callCount++;
      const sys = msgs[0]?.content ?? '';
      const body = msgs[1]?.content ?? '';
      if (!/JSON/.test(sys)) return 'ok';
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

function freshCore() {
  const dir = mkdtempSync(join(tmpdir(), 'mw-contested-'));
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

/** 建一条认知，并把它的链配成 support×n / contradict×m。 */
async function seedWithLinks(
  core: ReturnType<typeof createMemoWeftCore>,
  dbPath: string,
  supports: number,
  contradicts: number,
) {
  await core.ingestUserMessage({ content: 'I like tea', subjectId: 'u' });
  await core.updateProfile({ subjectId: 'u' });
  const id = core.memory.listCognitions({ subjectId: 'u' })[0]!.id;

  const extra: Array<{ evidenceId: string; relation: 'support' | 'contradict' }> = [];
  const existing = core.memory.listCognitions({ subjectId: 'u' })[0]!.sources.length;
  for (let i = existing; i < supports; i++) {
    const e = await core.ingestUserMessage({ content: `tea again ${i}`, subjectId: 'u' });
    extra.push({ evidenceId: e.id, relation: 'support' });
  }
  for (let i = 0; i < contradicts; i++) {
    const e = await core.ingestUserMessage({ content: `coffee instead ${i}`, subjectId: 'u' });
    extra.push({ evidenceId: e.id, relation: 'contradict' });
  }
  const store = new SqliteCognitionStore(dbPath);
  try {
    if (extra.length) store.addEvidence(id, extra);
  } finally {
    store.close();
  }
  return id;
}

/** 读一条认知的当前状态与链构成（走 listCognitions，不依赖尚未合并的 explainCognition）。 */
function peek(core: ReturnType<typeof createMemoWeftCore>, id: string) {
  const c = core.memory.listCognitions({ subjectId: 'u' }).find((x) => x.id === id)!;
  const support = c.sources.filter((l) => l.relation === 'support');
  return {
    credStatus: c.credStatus,
    supportCount: support.length,
    contradictCount: c.sources.filter((l) => l.relation === 'contradict').length,
    firstSupportId: support[0]?.evidenceId,
  };
}

test('端到端：删证据后重算——支撑仍占优的认知落到 contested 而非 conflicted', async () => {
  const { core, dbPath, cleanup } = freshCore();
  try {
    const id = await seedWithLinks(core, dbPath, 4, 1);
    // 触发 managementApi 的重算路径：删掉一条【支撑】证据，剩 3 支撑 1 反证，支撑仍占优。
    core.memory.removeEvidenceSafely({
      evidenceId: peek(core, id).firstSupportId!,
      force: true,
      reason: 'test',
    });

    const after = peek(core, id);
    assert.ok(after.supportCount > after.contradictCount, '前提：支撑仍占优');
    assert.equal(after.credStatus, 'contested', '支撑占优 → contested');
  } finally {
    cleanup();
  }
});

test('端到端：反证追平后落回 conflicted（中间态不是单向棘轮）', async () => {
  const { core, dbPath, cleanup } = freshCore();
  try {
    const id = await seedWithLinks(core, dbPath, 2, 1);
    const before = peek(core, id);
    assert.equal(before.supportCount, 2);
    assert.equal(before.contradictCount, 1);

    // 删掉一条支撑 → 1 支撑 1 反证，回到一比一对峙
    core.memory.removeEvidenceSafely({
      evidenceId: before.firstSupportId!,
      force: true,
      reason: 'test',
    });

    const after = peek(core, id);
    assert.equal(after.supportCount, after.contradictCount, '前提：追平');
    assert.equal(after.credStatus, 'conflicted', '追平 → 落回 conflicted');
  } finally {
    cleanup();
  }
});

// ── 下游：contested 不该被当成 conflicted 静默吞掉 ──

test('图谱：contested 单独标色、计入 contestedCount，且仍被 onlyConflicts 选中', async () => {
  const { core, dbPath, cleanup } = freshCore();
  try {
    const id = await seedWithLinks(core, dbPath, 4, 1);
    // 直接往 store 加链【不会】重算 credStatus——重算只发生在写路径上，而目前没有任何 API
    //   能"给指定认知补证据并重算"（那正是 A6 reinforceCognition 要补的缺口）。
    //   这里借删除路径触发一次重算：删掉一条支撑后剩 3 支撑 1 反证，支撑仍占优。
    core.memory.removeEvidenceSafely({
      evidenceId: peek(core, id).firstSupportId!,
      force: true,
      reason: 'test',
    });
    assert.equal(peek(core, id).credStatus, 'contested', '前提：已落到 contested');

    const g = core.graph.buildMemoryGraph({ subjectId: 'u' });
    const node = g.nodes.find((n) => n.kind === 'cognition')!;
    assert.equal(node.colorKey, 'contested', '单独标色，不混进 conflicted');
    assert.equal(g.stats.contestedCount, 1, '单独计数');
    assert.equal(g.stats.conflictedCount, 0, 'conflictedCount 语义保持严格');

    // onlyConflicts 必须仍然选中它：A3 之前这条认知【就是】conflicted、会被选中，
    // 改完让它从这个视图里消失就是回归——用户要看的是"有争议的记忆"。
    const only = core.graph.buildMemoryGraph({ subjectId: 'u', onlyConflicts: true });
    assert.equal(
      only.nodes.filter((n) => n.kind === 'cognition').length,
      1,
      'onlyConflicts 包含 contested',
    );
  } finally {
    cleanup();
  }
});
