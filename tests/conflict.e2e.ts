/**
 * conflict 路径 · 真模型端到端 integration testing（提示7）。
 *
 * 背景：conflict（矛盾但非明确纠正 → 两条都留、标 conflicted、暴露不消解，见 consolidate.ts 的 SYSTEM）
 * 是最难让 LLM 稳定判对的一环。This suite exercises the path against a configured model endpoint.
 *
 * 【离线跑不了】：必须打真模型，故用 .e2e.ts 后缀（不是 .test.ts）——默认 `npm test`
 * （glob tests/**\/*.test.ts）不会碰它，66 计数不变。显式带模型配置跑：`npm run test:e2e`。
 * 没配 MEMOWEFT_LLM_*（兼容旧名 DLA_LLM_*）时整组 skip。
 *
 * 场景：画像里先有一条认知『用户喜欢早睡』；再喂一条【行为矛盾但非明确纠正】的观察证据
 * （observed『凌晨3点还在打游戏』）。跑 distill+consolidate（updateProfile），观察模型判成什么。
 *
 * 断言【刻意宽松】：主断言只管【管线跑通、有结构化产出】；把『判没判对 conflict』当 integration testing
 * 观察点【打印出来】，不写脆断言——真模型判定会抖，这里是给人看的观察窗，不是回归护栏。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteEventStore } from '../src/event/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { NullRetriever } from '../src/retrieval/nullRetriever.ts';
import { updateProfile } from '../src/consolidation/updateProfile.ts';
import { OpenAICompatClient } from '../src/llm/client.ts';

// 没配对话模型（新名 MEMOWEFT_LLM_* 或旧名 DLA_LLM_*）→ 整组跳过（离线 CI 不跑）。
const HAS_LLM = Boolean(process.env.MEMOWEFT_LLM_BASE_URL || process.env.DLA_LLM_BASE_URL);

test(
  'conflict 端到端 · 真模型 integration testing：早睡偏好 vs 凌晨3点打游戏（矛盾非纠正）',
  { skip: !HAS_LLM },
  async () => {
    const ev = new SqliteEvidenceStore(':memory:');
    const evt = new SqliteEventStore(':memory:');
    const cog = new SqliteCognitionStore(':memory:');
    const llm = new OpenAICompatClient(); // 从 env 读真配置（loadLLMConfig 双前缀兼容）
    try {
      // 画像里先坐着一条现有认知：用户喜欢早睡（偏好，用户亲口说过）。
      const prior = cog.put({
        subjectId: 'owner',
        content: '用户喜欢早睡',
        contentType: 'preference',
        formedBy: 'stated',
        confidence: 600,
        credStatus: 'limited',
      });

      // 新证据：一条【观察】到的行为，与『喜欢早睡』矛盾，但用户【没有明确纠正/否定】自己的偏好。
      // 这正是 conflict 该覆盖的情形（行为观察 vs 旧偏好）——期望模型标 conflict、不当成 correct。
      // allowCloudRead: true —— observed 默认【不上云】（config.observedDefaults.allowCloudRead=false，隐私设计：
      //   偷窥类行为观察默认不喂云模型）。被测 mimo 是云 tier，不显式授权则这条证据会被 distill 的
      //   filterReadableByTier 滤掉 → 无可消化材料、不建 event → 主断言（r.distilled.event）失败。
      //   本 integration testing 的意图就是让云模型真读到这条 observed 跑 conflict，故显式选择上云（与 bench/eval-consolidation 同做法）。
      ev.put({
        subjectId: 'owner',
        sourceKind: 'observed',
        hostId: 'h',
        rawContent: '凌晨3点还在打游戏',
        allowCloudRead: true,
      });

      // 一键 distill + consolidate + attribute + 索引（索引用 NullRetriever，不打嵌入器）。
      const r = await updateProfile('owner', {
        evidenceStore: ev,
        eventStore: evt,
        cognitionStore: cog,
        retriever: new NullRetriever(),
        llm,
      });

      // —— 主断言：管线跑通、有结构化产出（宽松，不赌模型具体判成哪类）——
      assert.ok(r.distilled.event, 'distill 把新证据整理成了事件（管线第一步跑通）');
      assert.equal(r.consolidated.processedEvents, 1, 'consolidate 处理了这一个新事件');
      assert.equal(r.indexError, null, '索引无错（NullRetriever 恒成功）');
      assert.ok(r.timings.totalMs >= 0, '带各步耗时 timings');

      // —— integration testing 观察点：把模型对 conflict 的判定【打印出来】，人看『判没判对』——
      const priorAfter = cog.get(prior.id);
      const active = cog.active('owner');
      const isConflicted = priorAfter?.credStatus === 'conflicted';
      const priorStillActive = active.some((c) => c.id === prior.id);
      console.log('\n===== conflict integration testing 观察 =====');
      console.log('事件摘要      :', r.distilled.event?.summary);
      console.log('consolidate 分类:', {
        created: r.consolidated.created.length,
        reinforced: r.consolidated.reinforced,
        corrected: r.consolidated.corrected,
        conflicted: r.consolidated.conflicted,
      });
      console.log(
        '原认知『喜欢早睡』credStatus:',
        priorAfter?.credStatus,
        '| 还活跃:',
        priorStillActive,
        '| 已失效:',
        Boolean(priorAfter?.invalidAt),
      );
      console.log('活跃认知条数  :', active.length);
      console.log(
        '活跃认知内容  :',
        active.map((c) => `(${c.contentType}/${c.credStatus}) ${c.content}`),
      );
      console.log(
        '判定是否命中 conflict（credStatus=conflicted 且两条都留）:',
        isConflicted && priorStillActive,
      );
      console.log(
        '（命中=理想；判成 correct/new/无操作 都记为 integration testing 观察，不算测试失败）',
      );
      console.log('================================\n');

      // 宽松兜底：无论模型判成哪类，原认知都不该【凭空消失】（conflict 与 correct 都保留旧的可溯源）。
      assert.ok(priorAfter, '原认知仍在库（conflict 保留、correct 也只标失效不删）');
    } finally {
      ev.close();
      evt.close();
      cog.close();
    }
  },
);
