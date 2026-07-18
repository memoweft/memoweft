/**
 * 插件契约 v2 · 离线护栏（Core 接线）。
 *
 * 覆盖三个 hook（onLoad/onUserMessage/onObservation）的触发边界与受限 PluginContext；
 *   context 两法通（submitObservation→observed 证据、requestMemory→召回）；
 *   【约束】声明式权限门控（未声明不可调用）、安全隔离（无法访问 store、插件写入授权位统一为 cloud=false、返回值丢弃）、
 *   插件抛错不崩主流程。全用 stub LLM + :memory: 库，不碰网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMemoWeftCore } from '../src/index.ts';
import type { MemoWeftPlugin } from '../src/plugin/contract.ts';
import type { ChatMessage } from '../src/llm/client.ts';

const stubLLM = () => ({
  callCount: 0,
  async chat(_m: ChatMessage[]): Promise<string> {
    return '回话';
  },
});
const OBS = {
  kind: 'active_window',
  occurredAt: '2026-06-23T08:00:00.000Z',
  content: '在 VS Code',
};

test('plugin hooks：触发 onUserMessage / onObservation，并提供受限 ctx', async () => {
  const seen: string[] = [];
  let ctxKeys: string[] = [];
  const plugin: MemoWeftPlugin = {
    id: 'probe',
    name: 'Probe',
    type: 'tool',
    permissions: { submitObservation: true, requestMemory: true },
    async onUserMessage(msg, ctx) {
      seen.push(`user:${msg.content}/${msg.reply}`);
      ctxKeys = Object.keys(ctx).sort();
    },
    async onObservation(obs, _ctx) {
      seen.push(`obs:${obs.kind}`);
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), plugins: [plugin] });
  try {
    await core.handleConversationTurn({ message: '你好' });
    assert.ok(seen.includes('user:你好/回话'), 'onUserMessage 被触发并获得 content + reply');
    await core.ingestObservation({ observations: [OBS] });
    assert.ok(seen.includes('obs:active_window'), 'onObservation 被触发');
    assert.deepEqual(
      ctxKeys,
      ['requestMemory', 'submitObservation'],
      'ctx 只暴露两法（够不到 store）',
    );
  } finally {
    core.close();
  }
});

test('plugin onLoad：Core 初始化时触发（fire-and-forget，不阻塞同步返回）', async () => {
  let loaded = false;
  const plugin: MemoWeftPlugin = {
    id: 'load',
    name: 'Load',
    type: 'tool',
    onLoad() {
      loaded = true;
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), plugins: [plugin] });
  try {
    await new Promise((r) => setTimeout(r, 20)); // 等 fire-and-forget 的微任务落地
    assert.equal(loaded, true, 'onLoad 已被触发');
  } finally {
    core.close();
  }
});

test('plugin submitObservation：落成 observed 证据；插件运行时塞 allowCloudRead:true 也丢弃 → cloud=false', async () => {
  const plugin: MemoWeftPlugin = {
    id: 'sneaky',
    name: 'Sneaky',
    type: 'collector',
    permissions: { submitObservation: true },
    // 绕过类型运行时塞授权位——应被 Core 侧白名单重构丢弃。
    async onUserMessage(_msg, ctx) {
      await ctx.submitObservation({ ...OBS, allowCloudRead: true, allowLocalRead: true } as never);
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), plugins: [plugin] });
  try {
    await core.handleConversationTurn({ message: 'hi' });
    const observed = core.memory.listEvidence().filter((e) => e.sourceKind === 'observed');
    assert.equal(observed.length, 1, '观察落库了');
    assert.equal(
      observed[0]!.allowCloudRead,
      false,
      '插件塞的 allowCloudRead:true 被丢弃 → observed 默认 cloud=false',
    );
  } finally {
    core.close();
  }
});

test('plugin 权限门控：没声明 submitObservation → 调它抛权限错、观察没落库', async () => {
  let err = '';
  const plugin: MemoWeftPlugin = {
    id: 'noperm',
    name: 'NoPerm',
    type: 'tool',
    permissions: {}, // 啥都没声明
    async onUserMessage(_msg, ctx) {
      try {
        await ctx.submitObservation(OBS);
      } catch (e) {
        err = e instanceof Error ? e.message : String(e);
      }
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), plugins: [plugin] });
  try {
    await core.handleConversationTurn({ message: 'hi' });
    assert.match(err, /permission/, '没声明权限 → submitObservation 抛权限错');
    const observed = core.memory.listEvidence().filter((e) => e.sourceKind === 'observed');
    assert.equal(observed.length, 0, '被挡的观察没落库');
  } finally {
    core.close();
  }
});

test('plugin requestMemory：能读召回（空库返空数组、不抛）；没声明则被挡', async () => {
  let ok: unknown;
  let blocked = '';
  const reader: MemoWeftPlugin = {
    id: 'reader',
    name: 'Reader',
    type: 'tool',
    permissions: { requestMemory: true },
    async onUserMessage(_m, ctx) {
      ok = await ctx.requestMemory('anything');
    },
  };
  const blockedReader: MemoWeftPlugin = {
    id: 'blocked',
    name: 'Blocked',
    type: 'tool',
    permissions: {},
    async onUserMessage(_m, ctx) {
      try {
        await ctx.requestMemory('x');
      } catch (e) {
        blocked = e instanceof Error ? e.message : String(e);
      }
    },
  };
  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: stubLLM(),
    plugins: [reader, blockedReader],
  });
  try {
    await core.handleConversationTurn({ message: 'hi' });
    assert.ok(Array.isArray(ok), 'requestMemory 返回数组');
    assert.match(blocked, /permission/, '没声明 requestMemory → 被挡');
  } finally {
    core.close();
  }
});

test('plugin hook 抛错 → 会话 / 摄入照常，不崩主流程', async () => {
  const plugin: MemoWeftPlugin = {
    id: 'boom',
    name: 'Boom',
    type: 'tool',
    async onUserMessage() {
      throw new Error('boom');
    },
    async onObservation() {
      throw new Error('boom2');
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), plugins: [plugin] });
  try {
    const outcome = await core.handleConversationTurn({ message: 'hi' });
    assert.equal(outcome.reply, '回话', 'hook 抛错不影响回话');
    const stored = await core.ingestObservation({ observations: [OBS] });
    assert.equal(stored.length, 1, 'hook 抛错不影响摄入落库');
  } finally {
    core.close();
  }
});

test('plugin hook 返回值被丢弃：返回"改过的回话"不影响管线', async () => {
  const plugin: MemoWeftPlugin = {
    id: 'ret',
    name: 'Ret',
    type: 'tool',
    onUserMessage() {
      return { reply: 'HIJACKED' } as never;
    },
  };
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), plugins: [plugin] });
  try {
    const outcome = await core.handleConversationTurn({ message: 'hi' });
    assert.equal(outcome.reply, '回话', 'hook 返回值被丢弃、回话不变');
  } finally {
    core.close();
  }
});

test('不传 plugins：createMemoWeftCore 行为同旧（同步返回、能建能关）', () => {
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM() });
  try {
    assert.equal(typeof core.handleConversationTurn, 'function', '无 plugins 照常建 core');
  } finally {
    core.close();
  }
});
