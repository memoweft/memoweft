/**
 * eval 套件 · 认知纪律三条的真模型端到端观察层（integration testing 观察窗，非硬护栏）。
 *
 * 与离线层（cognition-discipline.eval.test.ts）配套：那边用 stub 断死行为、进 npm test 护栏；
 * 这边打【真模型】走完整 consolidate，把「真模型判没判对三条纪律」当 integration testing 观察点【打印出来】，
 * 断言【刻意宽松】（照 conflict.e2e.ts 体例）——主断言只管管线跑通、有结构化产出，
 * 不写脆断言赌真模型每次判定（真模型会抖，这里是给人看的观察窗）。
 *
 * 【离线跑不了】：用 .eval.e2e.ts 后缀 → 走 test:e2e 的 *.e2e.ts glob，不进 tests/**\/*.test.ts
 * 护栏计数。没配 MEMOWEFT_LLM_*（兼容旧名 DLA_LLM_*）时整组 skip，离线 CI 不红。
 *
 * 编号 EVAL-E##（e2e 观察窗，不计入 C/M/T 三档的 6–7 配额；离线层已满配 21 条）。
 * 观察点分别对照：E01/E04 冲突纪律、E02 情绪封顶纪律、E03 记≠信纪律。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../../src/evidence/store.ts';
import { SqliteEventStore } from '../../src/event/store.ts';
import { SqliteCognitionStore } from '../../src/cognition/store.ts';
import { NullRetriever } from '../../src/retrieval/nullRetriever.ts';
import { updateProfile } from '../../src/consolidation/updateProfile.ts';
import { OpenAICompatClient } from '../../src/llm/client.ts';

// 没配对话模型（新名 MEMOWEFT_LLM_* 或旧名 DLA_LLM_*）→ 整组跳过（离线 CI 不跑）。
const HAS_LLM = Boolean(process.env.MEMOWEFT_LLM_BASE_URL || process.env.DLA_LLM_BASE_URL);

test(
  'EVAL-E01 冲突纪律 integration testing：早睡偏好 vs 凌晨打游戏（矛盾非纠正 → 期望暴露不消解）',
  { skip: !HAS_LLM },
  async () => {
    const ev = new SqliteEvidenceStore(':memory:');
    const evt = new SqliteEventStore(':memory:');
    const cog = new SqliteCognitionStore(':memory:');
    const llm = new OpenAICompatClient();
    try {
      const prior = cog.put({
        subjectId: 'owner',
        content: '用户喜欢早睡',
        contentType: 'preference',
        formedBy: 'stated',
        confidence: 600,
        credStatus: 'limited',
      });
      ev.put({
        subjectId: 'owner',
        sourceKind: 'observed',
        hostId: 'h',
        rawContent: '凌晨3点还在打游戏',
        allowCloudRead: true,
      });
      const r = await updateProfile('owner', {
        evidenceStore: ev,
        eventStore: evt,
        cognitionStore: cog,
        retriever: new NullRetriever(),
        llm,
      });

      // 主断言（宽松）：管线跑通、有结构化产出。
      assert.ok(r.distilled.event, 'distill 跑通');
      assert.equal(r.consolidated.processedEvents, 1, 'consolidate 处理了这一个新事件');
      assert.ok(r.timings.totalMs >= 0, '带 timings');

      // 观察点：把真模型对 conflict 的判定打印出来（命中=理想，判成别的记 integration testing 观察）。
      const after = cog.get(prior.id);
      const active = cog.active('owner');
      console.log('\n===== EVAL-E01 冲突纪律观察 =====');
      console.log('consolidate 分类:', {
        created: r.consolidated.created.length,
        reinforced: r.consolidated.reinforced,
        corrected: r.consolidated.corrected,
        conflicted: r.consolidated.conflicted,
      });
      console.log(
        '原认知 credStatus:',
        after?.credStatus,
        '| 仍活跃:',
        active.some((c) => c.id === prior.id),
        '| 失效:',
        Boolean(after?.invalidAt),
      );
      console.log(
        '命中冲突（conflicted 且两条都留）:',
        after?.credStatus === 'conflicted' && active.some((c) => c.id === prior.id),
      );
      console.log('================================\n');

      // 宽松兜底：无论判成哪类，原认知都不该凭空消失（conflict 保留、correct 也只标失效不删）。
      assert.ok(after, '原认知仍在库');
    } finally {
      ev.close();
      evt.close();
      cog.close();
    }
  },
);

test(
  'EVAL-E02 情绪封顶 integration testing：反复说累（情绪 → 期望落临时档、不升 stable）',
  { skip: !HAS_LLM },
  async () => {
    const ev = new SqliteEvidenceStore(':memory:');
    const evt = new SqliteEventStore(':memory:');
    const cog = new SqliteCognitionStore(':memory:');
    const llm = new OpenAICompatClient();
    try {
      ev.put({
        subjectId: 'owner',
        sourceKind: 'spoken',
        hostId: 'h',
        rawContent: '今天好累，什么都不想干',
      });
      const r = await updateProfile('owner', {
        evidenceStore: ev,
        eventStore: evt,
        cognitionStore: cog,
        retriever: new NullRetriever(),
        llm,
      });

      assert.ok(r.distilled.event, 'distill 跑通');
      assert.equal(r.consolidated.processedEvents, 1, 'consolidate 处理了新事件');

      // 观察点：真模型判成的类型 + credStatus。若判为 state，断言封顶硬约束（这条不宽松：封顶是系统自算、与模型判定无关）。
      const active = cog.active('owner');
      const states = active.filter((c) => c.contentType === 'state');
      console.log('\n===== EVAL-E02 情绪封顶观察 =====');
      console.log(
        '活跃认知:',
        active.map((c) => `(${c.contentType}/${c.credStatus}) ${c.content}`),
      );
      console.log('其中 state 条数:', states.length);
      console.log('================================\n');
      for (const st of states) {
        assert.notEqual(
          st.credStatus,
          'stable',
          '被判为 state 的情绪永不 stable（系统封顶硬约束）',
        );
        assert.notEqual(st.credStatus, 'limited', '被判为 state 的情绪永不 limited');
      }
    } finally {
      ev.close();
      evt.close();
      cog.close();
    }
  },
);

test(
  'EVAL-E03 记≠信 integration testing：亲述事实 → 系统自算置信在合法区间（不采信模型自报）',
  { skip: !HAS_LLM },
  async () => {
    const ev = new SqliteEvidenceStore(':memory:');
    const evt = new SqliteEventStore(':memory:');
    const cog = new SqliteCognitionStore(':memory:');
    const llm = new OpenAICompatClient();
    try {
      ev.put({
        subjectId: 'owner',
        sourceKind: 'spoken',
        hostId: 'h',
        rawContent: '我每周六参加社区合唱排练，这件事我很确定',
      });
      const r = await updateProfile('owner', {
        evidenceStore: ev,
        eventStore: evt,
        cognitionStore: cog,
        retriever: new NullRetriever(),
        llm,
      });

      assert.ok(r.distilled.event, 'distill 跑通');
      const active = cog.active('owner');
      console.log('\n===== EVAL-E03 记≠信观察 =====');
      console.log(
        '活跃认知:',
        active.map((c) => `(${c.contentType}/${c.formedBy}/conf=${c.confidence}) ${c.content}`),
      );
      console.log('================================\n');

      // 硬约束（与模型判定无关）：凡落库认知，置信必在系统合法区间 (0,1000]，绝不是模型自报的 999/瞎报值原样透传。
      for (const c of active) {
        assert.ok(
          c.confidence > 0 && c.confidence <= 1000,
          `置信落系统合法区间（实际 ${c.confidence}）`,
        );
      }
    } finally {
      ev.close();
      evt.close();
      cog.close();
    }
  },
);

test(
  'EVAL-E04 冲突纪律对照 integration testing：显式纠正（更正一下…）→ 期望允许收敛（旧失效）',
  { skip: !HAS_LLM },
  async () => {
    const ev = new SqliteEvidenceStore(':memory:');
    const evt = new SqliteEventStore(':memory:');
    const cog = new SqliteCognitionStore(':memory:');
    const llm = new OpenAICompatClient();
    try {
      const prior = cog.put({
        subjectId: 'owner',
        content: '用户喜欢喝茶',
        contentType: 'preference',
        formedBy: 'stated',
        confidence: 600,
        credStatus: 'limited',
      });
      ev.put({
        subjectId: 'owner',
        sourceKind: 'spoken',
        hostId: 'h',
        rawContent: '更正一下，我现在不喝茶了，改喝咖啡',
      });
      const r = await updateProfile('owner', {
        evidenceStore: ev,
        eventStore: evt,
        cognitionStore: cog,
        retriever: new NullRetriever(),
        llm,
      });

      assert.ok(r.distilled.event, 'distill 跑通');
      assert.equal(r.consolidated.processedEvents, 1, 'consolidate 处理了新事件');

      const after = cog.get(prior.id);
      console.log('\n===== EVAL-E04 纠正对照观察 =====');
      console.log('consolidate 分类:', {
        created: r.consolidated.created.length,
        corrected: r.consolidated.corrected,
        conflicted: r.consolidated.conflicted,
      });
      console.log('原认知 失效:', Boolean(after?.invalidAt), '| credStatus:', after?.credStatus);
      console.log(
        '命中纠正（corrected≥1 且旧失效）:',
        r.consolidated.corrected >= 1 && Boolean(after?.invalidAt),
      );
      console.log('================================\n');

      // 宽松兜底：原认知仍在库（correct 只标失效不删，可溯源保留）。
      assert.ok(after, '原认知仍在库（纠正也只标失效不删）');
    } finally {
      ev.close();
      evt.close();
      cog.close();
    }
  },
);
