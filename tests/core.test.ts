/**
 * createMemoWeftCore 统一入口。
 * 用 stub LLM / 伪 retriever，不依赖网络与嵌入器；库用 :memory: 或临时文件（用完即清）。
 *
 * 验的契约：
 *  - 工厂缺 .env 仍可初始化（LLM 缺失延迟到模型请求时报告；嵌入缺失回退 KeywordRetriever）。
 *  - subjectId 缺省 config.identity.subjectId；ingestUserMessage 幂等（originId）。
 *  - handleConversationTurn 同 conversationId 复用实例（窗口连续，不重建）。
 *  - core.recall 走共享召回门控；portable / graph 是绑好 deps 的薄封装。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createMemoWeftCore } from '../src/core/createCore.ts';
import { openStores } from '../src/store/openStores.ts';
import { VectorRetriever } from '../src/retrieval/vectorRetriever.ts';
import { config } from '../src/config.ts';
import { OpenAICompatClient, type ChatMessage } from '../src/llm/client.ts';

/** stub LLM：记录收到的 messages，回固定话（既有测试同款手法）。 */
function stubLLM(replyText = '好的。') {
  const calls: ChatMessage[][] = [];
  return {
    calls,
    callCount: 0,
    async chat(messages: ChatMessage[]) {
      this.callCount++;
      calls.push(messages);
      return replyText;
    },
  };
}

const nullRetriever = {
  async indexAll() {},
  async search() {
    return [];
  },
};

test('工厂能建：不注入任何模型件也不崩（缺配降级），close 正常关闭', () => {
  const core = createMemoWeftCore({ dbPath: ':memory:' });
  assert.ok(core.memory, '受控管理 API 就位');
  assert.ok(core.portable, '便携包薄封装就位');
  assert.ok(core.graph, '图谱薄封装就位');
  core.close(); // 不抛错 = 资源关闭正常
});

test('ingestUserMessage：缺省 subjectId/sourceKind 走 config，originId 幂等', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
  try {
    const ev = await core.ingestUserMessage({ content: '我喜欢喝茶', originId: 'msg-1' });
    assert.equal(ev.subjectId, config.identity.subjectId, 'subjectId 缺省单例配置');
    assert.equal(ev.sourceKind, 'spoken', '用户消息默认亲口');
    assert.equal(ev.rawContent, '我喜欢喝茶');
    const dup = await core.ingestUserMessage({ content: '我喜欢喝茶', originId: 'msg-1' });
    assert.equal(dup.id, ev.id, '同 originId 幂等：不重复落库');
  } finally {
    core.close();
  }
});

test('ingestObservation：observed 证据落库，默认不上云（observedDefaults）', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
  try {
    const stored = await core.ingestObservation({
      observations: [
        {
          kind: 'active_window',
          occurredAt: new Date().toISOString(),
          content: '在 VS Code 停留约 40 分钟',
        },
      ],
    });
    assert.equal(stored.length, 1);
    assert.equal(stored[0]!.sourceKind, 'observed');
    assert.equal(stored[0]!.allowCloudRead, false, 'observed 保守默认：不上云');
    assert.equal(stored[0]!.subjectId, config.identity.subjectId);
  } finally {
    core.close();
  }
});

test('core.recall：走共享召回门控（invalid / archived 不出，正常召回带 id）', async () => {
  // 召回要求认知在 core 自己的库里：先在临时文件库播数据，再让 core 开同一个库。
  const dir = mkdtempSync(join(tmpdir(), 'mw-core-'));
  const dbPath = join(dir, 'core.db');
  try {
    const seed = openStores(dbPath);
    const keep = seed.cognitionStore.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });
    const dead = seed.cognitionStore.put({
      subjectId: 'owner',
      content: '用户喜欢咖啡（已失效）',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });
    const filed = seed.cognitionStore.put({
      subjectId: 'owner',
      content: '用户在学吉他（已归档）',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });
    seed.cognitionStore.update(dead.id, { invalidAt: new Date().toISOString() });
    seed.cognitionStore.update(filed.id, { archivedAt: new Date().toISOString() });
    seed.close();

    const retriever = {
      async indexAll() {},
      async search() {
        return [
          { id: keep.id, score: 0.9 },
          { id: dead.id, score: 0.9 },
          { id: filed.id, score: 0.9 },
        ];
      },
    };
    const core = createMemoWeftCore({ dbPath, llm: stubLLM(), retriever });
    try {
      const out = await core.recall({ query: '喝点什么好' });
      assert.equal(out.length, 1, '失效 / 归档都被共享门控挡掉');
      assert.equal(out[0]!.content, '用户喜欢喝茶');
      assert.equal(out[0]!.id, keep.id, '召回结果带认知 id');
    } finally {
      core.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true }); // 临时库用完即清
  }
});

test(' 无 embedder 兜底 KeywordRetriever：不注入 embedder/retriever → keyword 召回非空（且 embedReady=false）', async () => {
  // 契约（重评估）：没配 embedder 时，recall 不再恒空——走 FTS5 关键词兜底。
  //   embedReady 仍 false（专表"语义/向量召回"），但召回本身能出结果。
  const dir = mkdtempSync(join(tmpdir(), 'mw-d0017-'));
  const dbPath = join(dir, 'core.db');
  // 隔离测试机 .env 的嵌入配置：置空 EMBED_* → loadEmbedConfig 返回 null → 走兜底（loadEnvFile 尊重已存在 env,不覆盖）。
  const EMBED_KEYS = [
    'MEMOWEFT_EMBED_BASE_URL',
    'MEMOWEFT_EMBED_API_KEY',
    'MEMOWEFT_EMBED_MODEL',
    'DLA_EMBED_BASE_URL',
    'DLA_EMBED_API_KEY',
    'DLA_EMBED_MODEL',
  ];
  const saved = Object.fromEntries(EMBED_KEYS.map((k) => [k, process.env[k]]));
  for (const k of EMBED_KEYS) process.env[k] = '';
  try {
    const seed = openStores(dbPath);
    seed.cognitionStore.put({
      subjectId: config.identity.subjectId,
      content: '用户喜欢徒步旅行',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });
    seed.close();
    // 不注入 embedder、不注入 retriever + EMBED_* 置空 → createCore 走 KeywordRetriever 兜底（非 NullRetriever）。
    const core = createMemoWeftCore({ dbPath, llm: stubLLM() });
    try {
      assert.equal(core.health().embedReady, false, '无 embedder → embedReady=false（无语义召回）');
      await core.updateProfile(); // 无新证据/事件 → distill/consolidate 早退,但 indexAll 把已有认知喂进 FTS
      const out = await core.recall({ query: '徒步旅行' });
      assert.ok(out.length >= 1, ':无 embedder 也能经 keyword 召回（非恒空）');
      assert.equal(out[0]!.content, '用户喜欢徒步旅行', 'keyword 命中正确认知');
    } finally {
      core.close(); // KeywordRetriever 连接已关闭（不抛错）
    }
  } finally {
    for (const k of EMBED_KEYS) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
    rmSync(dir, { recursive: true, force: true });
  }
});

test('handleConversationTurn：同 conversationId 复用实例，窗口跨轮连续', async () => {
  const llm = stubLLM();
  const core = createMemoWeftCore({ dbPath: ':memory:', llm, retriever: nullRetriever });
  try {
    const first = await core.handleConversationTurn({
      message: '第一轮的话',
      systemPrompt: '你是测试人设。',
    });
    assert.equal(first.reply, '好的。');
    assert.ok(first.storedEvidence.id, '用户消息已存为证据');

    await core.handleConversationTurn({ message: '第二轮的话' }); // 缺省同一个 'default' 会话
    const secondMessages = llm.calls[1]!;
    assert.ok(
      secondMessages.some((m) => m.content === '第一轮的话'),
      '第二轮 prompt 带上第一轮窗口 → 实例被复用而非重建',
    );
    assert.ok(
      secondMessages.some((m) => m.role === 'system' && m.content.includes('你是测试人设')),
      '首建时的 systemPrompt 持续生效',
    );
  } finally {
    core.close();
  }
});

test('dropConversation：丢弃实例后同 conversationId 重建，换 systemPrompt 才生效', async () => {
  const llm = stubLLM();
  const core = createMemoWeftCore({ dbPath: ':memory:', llm, retriever: nullRetriever });
  try {
    await core.handleConversationTurn({
      message: '一',
      conversationId: 'x',
      systemPrompt: '人设A。',
    });
    assert.ok(
      llm.calls[0]!.some((m) => m.role === 'system' && m.content.includes('人设A')),
      '首建用人设A',
    );

    // 不 drop：复用旧实例，新 systemPrompt B 被【忽略】——这正是插件切换审查抓出的坑（也是行为冒烟的假阳性来源）。
    await core.handleConversationTurn({
      message: '二',
      conversationId: 'x',
      systemPrompt: '人设B。',
    });
    assert.ok(
      llm.calls[1]!.some((m) => m.role === 'system' && m.content.includes('人设A')),
      '不 drop → 复用旧实例、仍人设A',
    );
    assert.ok(
      !llm.calls[1]!.some((m) => m.role === 'system' && m.content.includes('人设B')),
      '不 drop → 新人设B 被忽略',
    );

    // drop 后：同 id 重建实例，新 systemPrompt C 生效、窗口重置（没传 seedTurns → 不带旧轮）。
    core.dropConversation('x');
    await core.handleConversationTurn({
      message: '三',
      conversationId: 'x',
      systemPrompt: '人设C。',
    });
    assert.ok(
      llm.calls[2]!.some((m) => m.role === 'system' && m.content.includes('人设C')),
      'drop 后 → 重建实例、人设C 生效',
    );
    assert.ok(!llm.calls[2]!.some((m) => m.content === '一'), 'drop 后窗口重建、不带旧轮');
  } finally {
    core.close();
  }
});

test('updateProfile：空库早退不调模型，返回 timings/metrics 形状完整', async () => {
  const llm = stubLLM();
  const core = createMemoWeftCore({ dbPath: ':memory:', llm, retriever: nullRetriever });
  try {
    const r = await core.updateProfile();
    assert.equal(r.distilled.event, null, '无未整理证据 → 不建事件');
    assert.equal(llm.callCount, 0, '空库全程不调模型');
    assert.equal(typeof r.timings.totalMs, 'number');
    assert.deepEqual(r.metrics, { profileSize: 0, promptChars: 0 });
  } finally {
    core.close();
  }
});

test('portable / graph 薄封装：subjectId 缺省单例，导出→校验→dryRun 导入闭环', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
  const other = createMemoWeftCore({
    dbPath: ':memory:',
    llm: stubLLM(),
    retriever: nullRetriever,
  });
  try {
    await core.ingestUserMessage({ content: '我喜欢喝茶' });
    const bundle = core.portable.exportBundle();
    assert.equal(bundle.subjectId, config.identity.subjectId, '导出缺省 subject');
    assert.equal(bundle.metadata.counts.evidence, 1);
    assert.ok(core.portable.validateBundle(bundle).valid, '自家导出的包校验通过');

    const plan = other.portable.importBundle(bundle, { mode: 'dryRun' });
    assert.equal(plan.valid, true);
    assert.equal(plan.counts.evidence, 1, 'dryRun 算出将写入 1 条');

    const payload = core.graph.buildMemoryGraph();
    assert.equal(payload.subjectId, config.identity.subjectId);
    assert.ok(
      payload.nodes.some((n) => n.kind === 'subject'),
      '图谱含 subject 中心节点',
    );
  } finally {
    core.close();
    other.close();
  }
});

test('graph：归档认知默认不进图，includeArchived=true 才可见（向 invalid 看齐）', async () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
  try {
    // 不直接摸库：认知经 portable 导入造出来，再走受控 API 归档。
    const bundle = {
      format: 'memoweft-bundle',
      schemaVersion: 1,
      exportedAt: new Date().toISOString(),
      memoWeftVersion: '0.0.0',
      subjectId: config.identity.subjectId,
      source: { hostId: 'test', exportMode: 'full' as const },
      data: {
        evidence: [],
        events: [],
        eventEvidence: [],
        cognitions: [
          {
            id: 'cog-1',
            subjectId: config.identity.subjectId,
            content: '要归档的认知',
            contentType: 'fact' as const,
            formedBy: 'stated' as const,
            confidence: 700,
            credStatus: 'limited' as const,
            scope: null,
            validAt: null,
            invalidAt: null,
            askedAt: null,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          },
        ],
        cognitionEvidence: [],
        unconsolidatedEventIds: [],
      },
      metadata: { counts: { evidence: 0, events: 0, cognitions: 1 }, notes: [] },
    };
    const plan = core.portable.importBundle(bundle, { mode: 'merge' });
    assert.equal(plan.counts.cognitions, 1, '认知导入成功');
    core.memory.archiveCognition({ cognitionId: 'cog-1', reason: '归档看图' });

    const hidden = core.graph.buildMemoryGraph();
    assert.ok(!hidden.nodes.some((n) => n.id === 'cog-1'), '归档默认不进图');
    const shown = core.graph.buildMemoryGraph({ includeArchived: true });
    assert.ok(
      shown.nodes.some((n) => n.id === 'cog-1' && n.colorKey === 'archived'),
      'includeArchived 才可见',
    );
  } finally {
    core.close();
  }
});

test('health：返回 {llmReady, embedReady} 结构；注入 stub / 空召回器 → 都是 false', () => {
  // 注入 stub LLM（非 OpenAICompatClient）+ 空召回器（非 VectorRetriever）→ 两位皆 false。
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
  try {
    const h = core.health();
    assert.equal(typeof h.llmReady, 'boolean', 'llmReady 是布尔');
    assert.equal(typeof h.embedReady, 'boolean', 'embedReady 是布尔');
    // 口径：core 持有 stub（非真模型客户端）+ 空召回器 → 都判 false。
    assert.equal(h.llmReady, false, '注入 stub 非真模型客户端 → 不能聊');
    assert.equal(h.embedReady, false, '空召回器 → 不能语义召回');
  } finally {
    core.close();
  }
});

test('health：注入真 OpenAICompatClient → llmReady=true（对称覆盖 true 分支）', () => {
  // 显式 config 构造 → 只存配置、不触网、不读 env；health() 只做 instanceof、不调 .chat()，零网络零成本。
  const llm = new OpenAICompatClient({ baseUrl: 'http://x', apiKey: 'k', model: 'm' });
  const core = createMemoWeftCore({ dbPath: ':memory:', llm });
  try {
    assert.equal(core.health().llmReady, true, '持有真模型客户端 → 能聊');
  } finally {
    core.close();
  }
});

test('health：注入真 VectorRetriever → embedReady=true（反映实际召回能力）', () => {
  // 用一个不触网的假 embedder 建 VectorRetriever（:memory: 库；不调 embed 就不发请求）。
  const embedder = {
    async embed() {
      return [];
    },
  };
  const retriever = new VectorRetriever(':memory:', embedder);
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever });
  try {
    assert.equal(core.health().embedReady, true, '持有 VectorRetriever → 能语义召回');
  } finally {
    core.close();
    retriever.close(); // 注入的 retriever 归调用方关
  }
});
