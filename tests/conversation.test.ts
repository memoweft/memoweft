/**
 * 召回门控：失效或有效置信度过低的认知不得注入回复上下文。
 * 用伪 retriever / 伪 llm，不依赖网络与嵌入器。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { Conversation } from '../src/pipeline/conversation.ts';
import { config } from '../src/config.ts';

test('受信任回复记忆只进本轮 system，不污染用户 Evidence、1.x recall 或下一轮窗口', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const calls: Array<Array<{ role: string; content: string }>> = [];
    let evidenceCountSeenByReplyMemoryHook = 0;
    const llm = {
      callCount: 0,
      async chat(messages: Array<{ role: string; content: string }>) {
        this.callCount++;
        calls.push(messages);
        return `回复 ${this.callCount}`;
      },
    };
    const retriever = {
      async indexAll() {},
      async search() {
        return [];
      },
    };
    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm });

    const firstOptions = {
      originId: 'owner-turn-1',
      loadTrustedReplyMemory: async () => {
        evidenceCountSeenByReplyMemoryHook = store.all().length;
        return [{ content: '2.0 已接受：用户偏好乌龙茶', confidence: 910, credStatus: 'stable' }];
      },
    };
    const first = await convo.handle('这是用户原话', firstOptions);
    await convo.handle('第二轮原话');

    assert.equal(first.storedEvidence.rawContent, '这是用户原话', 'Evidence 仍只保存用户原话');
    assert.equal(
      evidenceCountSeenByReplyMemoryHook,
      1,
      '回复记忆 hook 只在本轮 Evidence 落库后运行',
    );
    assert.equal(first.storedEvidence.originId, 'owner-turn-1');
    assert.deepEqual(first.recall, [], 'TurnOutcome.recall 仍只表示 1.x 召回');
    assert.ok(
      calls[0]![0]!.content.includes('2.0 已接受：用户偏好乌龙茶'),
      '受信任记忆只在本轮的 system 提示中注入',
    );
    assert.equal(calls[0]!.at(-1)?.content, '这是用户原话', '用户原话未被回复记忆替换');
    assert.ok(
      !store.all().some((e) => e.rawContent.includes('2.0 已接受：用户偏好乌龙茶')),
      '受信任记忆绝不写成 Evidence',
    );
    assert.ok(
      !calls[1]!.some((m) => m.content.includes('2.0 已接受：用户偏好乌龙茶')),
      '下一轮窗口不携带上轮 system 记忆',
    );
    assert.ok(
      calls[1]!.some((m) => m.content === '这是用户原话'),
      '下一轮仍只带真实对话历史',
    );
  } finally {
    store.close();
    cog.close();
  }
});

test('受信任回复记忆 hook 不可用时，原 1.x 回话仍只调用一次模型并成功返回', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const llm = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return '1.x 正常回复';
      },
    };
    const retriever = {
      async indexAll() {},
      async search() {
        return [];
      },
    };
    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm });
    const options = {
      originId: 'owner-turn-next-unavailable',
      loadTrustedReplyMemory: async () => {
        throw new Error('Next bridge unavailable');
      },
    };

    const outcome = await convo.handle('还能正常聊天吗？', options);

    assert.equal(outcome.reply, '1.x 正常回复');
    assert.equal(outcome.llmCalls, 1, 'Next 不可用不会制造第二次聊天调用');
    assert.equal(store.all().length, 1, 'Next 不可用也不会丢用户 Evidence');
  } finally {
    store.close();
    cog.close();
  }
});

test('next authority skips native 1.x cognition recall while preserving Evidence and injecting trusted accepted memory', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    const native = cog.put({
      subjectId: 'owner',
      content: '1.x 画像：用户喜欢咖啡',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 900,
      credStatus: 'stable',
    });
    let nativeRecallCalls = 0;
    let chatCalls = 0;
    const prompts: Array<Array<{ role: string; content: string }>> = [];
    const convo = new Conversation({
      store,
      cognitionStore: cog,
      retriever: {
        async indexAll() {},
        async search() {
          nativeRecallCalls++;
          return [{ id: native.id, score: 0.99 }];
        },
      },
      llm: {
        get callCount() {
          return chatCalls;
        },
        async chat(messages: Array<{ role: string; content: string }>) {
          chatCalls++;
          prompts.push(messages);
          return '2.0 authority reply';
        },
      },
    });

    const nextAuthorityOptions = {
      originId: 'next-authority-owner-turn',
      skipNativeCognitionRecall: true,
      loadTrustedReplyMemory: () => [
        { content: '2.0 已接受：用户喜欢乌龙茶', confidence: 920, credStatus: 'stable' },
      ],
    };
    const outcome = await convo.handle('我想喝点什么', nextAuthorityOptions);

    assert.equal(nativeRecallCalls, 0, 'next authority must not query 1.x cognition recall');
    assert.equal(chatCalls, 1, 'next authority retains the single normal chat call');
    assert.deepEqual(outcome.recall, [], 'outcome retains no native 1.x recall');
    assert.equal(store.all().length, 1, 'current user turn still lands as 1.x Evidence');
    assert.ok(prompts[0]![0]!.content.includes('2.0 已接受：用户喜欢乌龙茶'));
    assert.ok(!prompts[0]![0]!.content.includes('1.x 画像：用户喜欢咖啡'));
  } finally {
    store.close();
    cog.close();
  }
});

test('召回门控：失效 / 有效置信过低的认知不注入回话', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    // 三条认知：正常 / 置信过低 / 已失效
    const keep = cog.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });
    const tooLow = cog.put({
      subjectId: 'owner',
      content: '用户此刻有点烦',
      contentType: 'preference',
      formedBy: 'inferred',
      confidence: 60,
      credStatus: 'candidate',
    });
    const dead = cog.put({
      subjectId: 'owner',
      content: '用户喜欢咖啡（已被纠正）',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });
    cog.update(dead.id, { invalidAt: new Date().toISOString() });

    // 伪 retriever：三条都"召回"到（高相似度），交给门控去筛
    const retriever = {
      async indexAll() {},
      async search() {
        return [
          { id: keep.id, score: 0.9 },
          { id: tooLow.id, score: 0.9 },
          { id: dead.id, score: 0.9 },
        ];
      },
    };
    const llm = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return '好的。';
      },
    };

    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm });
    const outcome = await convo.handle('喝点什么好');

    const ids = outcome.recall.map((r) => r.content);
    assert.equal(outcome.recall.length, 1, '只注入 1 条（其余被门控）');
    assert.ok(ids.includes('用户喜欢喝茶'), '正常认知留下');
    assert.ok(!ids.some((c) => c.includes('有点烦')), '有效置信过低 → 不注入');
    assert.ok(!ids.some((c) => c.includes('咖啡')), '已失效 → 不注入');
  } finally {
    store.close();
    cog.close();
  }
});

test('相似度门控：低于 minSimilarity 的召回不注入，避免 top-k 返回不相关认知', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  const savedMinSim = config.retrieval.minSimilarity;
  config.retrieval.minSimilarity = 0.5; // 临时开门控（默认 0 = 关闭）
  try {
    // 两条都高置信（过得了置信门控），差别只在相似度分。
    const near = cog.put({
      subjectId: 'owner',
      content: '用户喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });
    const far = cog.put({
      subjectId: 'owner',
      content: '用户在学吉他',
      contentType: 'project',
      formedBy: 'stated',
      confidence: 600,
      credStatus: 'limited',
    });

    // 伪 retriever：near 相似度高（0.8 > 0.5 留下）、far 相似度低（0.2 < 0.5 被门控挡掉）。
    const retriever = {
      async indexAll() {},
      async search() {
        return [
          { id: near.id, score: 0.8 },
          { id: far.id, score: 0.2 },
        ];
      },
    };
    const llm = {
      callCount: 0,
      async chat() {
        this.callCount++;
        return '好的。';
      },
    };

    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm });
    const outcome = await convo.handle('喝点什么好');

    const ids = outcome.recall.map((r) => r.content);
    assert.equal(outcome.recall.length, 1, '只注入相似度过门的那 1 条');
    assert.ok(ids.includes('用户喜欢喝茶'), '相似度高的留下');
    assert.ok(!ids.some((c) => c.includes('吉他')), '相似度低 → 被门控挡掉');
  } finally {
    config.retrieval.minSimilarity = savedMinSim; // 还原全局配置，别污染其它测试
    store.close();
    cog.close();
  }
});
