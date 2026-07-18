/**
 * 写适配器离线护栏：不打真模型，直接调 persist helper / onEnd 回调。
 * Test coverage:
 *  - createPersistOnEnd 造的回调真调 core.ingestUserMessage，存的是【用户原话】、不掺助手回话；
 *  - 稳定 originId 透传（用于幂等）；不传云读取授权位（ingestUserMessage 存 spoken，不接受授权覆盖）；
 *  - 空串 / 全空白用户话不落库；
 *  - ingest 抛错不外抛（走 onError / 静默吞，不崩宿主主流程）；
 *  - onEnd 事件对象不被使用（用户原话来自闭包捕获，不从响应读取）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { UserMessageInput, Evidence } from 'memoweft';
import { createPersistOnEnd, persistUserTurn } from '../src/persistOnEnd.ts';

/** 造一个只实现 ingestUserMessage 的假 core：记录每次收到的入参。 */
function fakeCore(opts: { throwOnIngest?: boolean } = {}) {
  const ingested: UserMessageInput[] = [];
  return {
    ingested,
    core: {
      async ingestUserMessage(input: UserMessageInput): Promise<Evidence> {
        ingested.push(input);
        if (opts.throwOnIngest) throw new Error('db down');
        return { id: 'ev1', ...input } as unknown as Evidence;
      },
    },
  };
}

test('onEnd 回调存用户原话、带 originId、不掺助手回话', async () => {
  const { core, ingested } = fakeCore();
  const onEnd = createPersistOnEnd(core, {
    userMessage: 'I want to learn Rust',
    originId: 'turn-42',
    subjectId: 'alice',
  });

  // 模拟 SDK 传进来的 onEnd 事件：全是结果侧字段（含助手回话 text）。回调应【无视】它。
  const assistantReply = 'Great, here is a Rust roadmap...';
  await onEnd({ text: assistantReply, content: [], steps: [], responseMessages: [] });

  assert.equal(ingested.length, 1, '落了一条');
  const rec = ingested[0]!;
  assert.equal(rec.content, 'I want to learn Rust', '存的是用户原话');
  assert.notEqual(rec.content, assistantReply, '没把助手回话存进去');
  assert.equal(rec.originId, 'turn-42', 'originId 透传（幂等靠它）');
  assert.equal(rec.subjectId, 'alice');
});

test('spoken 摄入不传云读取授权位', async () => {
  const { core, ingested } = fakeCore();
  await createPersistOnEnd(core, { userMessage: 'hi', originId: 't1' })({});
  const rec = ingested[0]!;
  assert.equal('allowCloudRead' in rec, false, '不显式传 allowCloudRead');
  assert.equal('allowLocalRead' in rec, false);
  assert.equal('allowInference' in rec, false);
  // sourceKind 不传 → Core 缺省 spoken
  assert.equal(rec.sourceKind, undefined);
});

test('空串 / 全空白用户话不落库', async () => {
  const { core, ingested } = fakeCore();
  await persistUserTurn(core, { userMessage: '' });
  await persistUserTurn(core, { userMessage: '   \n  ' });
  assert.equal(ingested.length, 0, '没有用户原话可存');
});

test('ingest 抛错走 onError、不外抛', async () => {
  const { core } = fakeCore({ throwOnIngest: true });
  const errors: unknown[] = [];
  const onEnd = createPersistOnEnd(core, {
    userMessage: 'x',
    originId: 't1',
    onError: (e) => errors.push(e),
  });
  await assert.doesNotReject(onEnd({}), '落库失败不崩');
  assert.equal(errors.length, 1, 'onError 收到错误');
});

test('ingest 抛错且无 onError → 静默吞、不外抛', async () => {
  const { core } = fakeCore({ throwOnIngest: true });
  const onEnd = createPersistOnEnd(core, { userMessage: 'x' });
  await assert.doesNotReject(onEnd({}));
});

test('persistUserTurn 直接落一条（originId 缺省 null）', async () => {
  const { core, ingested } = fakeCore();
  await persistUserTurn(core, { userMessage: 'hello' });
  assert.equal(ingested[0]!.content, 'hello');
  assert.equal(ingested[0]!.originId, null);
});
