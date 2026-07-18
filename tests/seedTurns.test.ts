/**
 * 续聊种子：打开旧会话时把最近几轮种回工作记忆，
 * 续聊的 prompt 上下文里要带上这些种子轮。用伪 retriever / 伪 llm，纯离线。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteEvidenceStore } from '../src/evidence/store.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { Conversation } from '../src/pipeline/conversation.ts';

test('Conversation.seedTurns：旧会话最近几轮种回工作记忆 → 续聊带进 prompt 上下文', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    let captured: Array<{ role: string; content: string }> = [];
    const llm = {
      callCount: 0,
      async chat(messages: Array<{ role: string; content: string }>) {
        this.callCount++;
        captured = messages;
        return '记得，你叫小明。';
      },
    };
    const retriever = {
      async indexAll() {},
      async search() {
        return [];
      },
    };

    const convo = new Conversation({
      store,
      retriever,
      cognitionStore: cog,
      llm,
      seedTurns: [
        { role: 'user', content: '我叫小明' },
        { role: 'assistant', content: '你好小明' },
      ],
    });
    await convo.handle('还记得我叫啥吗');

    const contents = captured.map((m) => m.content);
    assert.ok(
      contents.some((c) => c.includes('我叫小明')),
      '种子里的用户话进了上下文',
    );
    assert.ok(
      contents.some((c) => c.includes('你好小明')),
      '种子里的助手话也进了上下文',
    );
    assert.equal(captured[captured.length - 1]!.content, '还记得我叫啥吗', '当前消息排在最末');
    assert.ok(
      captured.findIndex((m) => m.content === '我叫小明') < captured.length - 1,
      '种子排在当前消息之前',
    );
  } finally {
    store.close();
    cog.close();
  }
});

test('Conversation.systemPrompt：宿主注入的人设进入 system 消息，语气与角色由宿主负责', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    let captured: Array<{ role: string; content: string }> = [];
    const llm = {
      callCount: 0,
      async chat(messages: Array<{ role: string; content: string }>) {
        this.callCount++;
        captured = messages;
        return '好的。';
      },
    };
    const retriever = {
      async indexAll() {},
      async search() {
        return [];
      },
    };

    const convo = new Conversation({
      store,
      retriever,
      cognitionStore: cog,
      llm,
      systemPrompt: '你有长期记忆，别说会忘。',
    });
    await convo.handle('你好');

    assert.equal(captured[0]!.role, 'system');
    assert.ok(
      captured[0]!.content.includes('你有长期记忆，别说会忘。'),
      '宿主人设进了 system 提示',
    );
  } finally {
    store.close();
    cog.close();
  }
});

test('Conversation.seedTurns：缺省不传 → 全新会话，上下文只有本轮消息（行为同旧）', async () => {
  const store = new SqliteEvidenceStore(':memory:');
  const cog = new SqliteCognitionStore(':memory:');
  try {
    let captured: Array<{ role: string; content: string }> = [];
    const llm = {
      callCount: 0,
      async chat(messages: Array<{ role: string; content: string }>) {
        this.callCount++;
        captured = messages;
        return '好的。';
      },
    };
    const retriever = {
      async indexAll() {},
      async search() {
        return [];
      },
    };

    const convo = new Conversation({ store, retriever, cognitionStore: cog, llm });
    await convo.handle('你好');

    // 只有 system + 本轮 user，两条；没有种子轮。
    assert.equal(captured.length, 2, '无种子 → 只有 system + 本轮消息');
    assert.equal(captured[captured.length - 1]!.content, '你好');
  } finally {
    store.close();
    cog.close();
  }
});
