import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

test('reply honesty is enforced before model output enters the conversation window', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'memoweft-reply-honesty-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const logDir = join(root, 'logs');
  const dbPath = join(root, 'testbench-evidence.db');
  await mkdir(logDir, { recursive: true });

  const script = String.raw`
    import { join } from 'node:path';
    import { pathToFileURL } from 'node:url';

    const serverUrl = pathToFileURL(join(process.cwd(), 'testbench/server.mjs')).href;
    const conversationUrl = pathToFileURL(join(process.cwd(), 'src/pipeline/conversation.ts')).href;
    const {
      REPLY_PERSONA,
      createReplyHonestyGuard,
      enforceReplyMemoryHonesty,
      replyMakesUnverifiedMemoryClaim,
    } = await import(serverUrl + '?reply-honesty-child=' + Date.now());
    const { Conversation } = await import(conversationUrl + '?reply-honesty-child=' + Date.now());

    const unsafe = [
      '这件事我已经存好了。',
      '放心，以后再聊到这个我还能接得上。',
      '我会把这个放进长期记忆里。',
      '下次你提起来，我肯定知道你说的是谁。',
      '已经替你归档，我绝不会忘。',
      '这个设定从现在起会一直保留。',
      '这条已写进我的资料库。',
      '我会牢牢记在心里。',
      '以后你换个说法，我也能认出来。',
      '这事我会放在心上，往后你不用再提醒我。',
      '下回再绕着说，我也能对上号。',
      '往后不必重新解释，我能接着聊。',
      "I've saved that for next time.",
      "Don't worry, I won't forget this.",
      'This is now in my long-term memory.',
      "I've got that on file now.",
      "I’ve tucked that away for later.",
      "That’ll stay with me.",
      "You won't have to explain that again next time.",
      'Consider it remembered.',
    ];
    const safe = [
      '我明白了，原来这个昵称有这样的来历。',
      '我记得你之前说过更喜欢乌龙茶，这次是在补充口味。',
      '我知道了，你的意思是不公开对方的名字。',
      '当前轮是否形成长期记忆仍在后台验证中。',
      '这句话已保存为 Evidence，但这不代表已经写入长期记忆。',
      '你可以记住这个步骤，下次自己就能复现。',
      'I understand what you mean.',
      'I remember that you previously said you prefer tea.',
      'It has not been added to long-term memory yet.',
    ];

    let calls = 0;
    const queued = [...unsafe, safe[1]];
    const inner = {
      async chat() {
        calls += 1;
        return queued.shift();
      },
      get callCount() {
        return calls;
      },
      tier: 'local',
      usage: { promptTokens: 7, completionTokens: 3, totalTokens: 10, callsWithUsage: 1 },
    };
    const guarded = createReplyHonestyGuard(inner);
    const guardedReplies = [];
    for (let index = 0; index < unsafe.length + 1; index += 1) {
      guardedReplies.push(await guarded.chat([]));
    }

    let windowCallCount = 0;
    const windowCalls = [];
    const windowInner = {
      async chat(messages) {
        windowCallCount += 1;
        windowCalls.push(messages);
        return windowCallCount === 1
          ? '放心，我会一直记着这件事。'
          : '好的，我理解你的补充。';
      },
      get callCount() {
        return windowCallCount;
      },
    };
    let evidenceSequence = 0;
    const conversation = new Conversation({
      store: {
        put(input) {
          evidenceSequence += 1;
          return {
            ...input,
            id: 'evidence-' + evidenceSequence,
            summary: input.rawContent,
            occurredAt: input.occurredAt ?? '2026-08-11T00:00:00.000Z',
          };
        },
      },
      retriever: {},
      cognitionStore: {},
      llm: createReplyHonestyGuard(windowInner),
      systemPrompt: REPLY_PERSONA,
    });
    const firstWindowTurn = await conversation.handle('我想补充一件事', {
      skipNativeCognitionRecall: true,
    });
    await conversation.handle('再补充一点', { skipNativeCognitionRecall: true });

    process.stdout.write('REPLY_HONESTY_RESULT=' + JSON.stringify({
      unsafe: unsafe.map((value) => ({
        value,
        classified: replyMakesUnverifiedMemoryClaim(value),
        rewritten: enforceReplyMemoryHonesty(value),
      })),
      safe: safe.map((value) => ({
        value,
        classified: replyMakesUnverifiedMemoryClaim(value),
        rewritten: enforceReplyMemoryHonesty(value),
      })),
      guardedReplies,
      guardedCallCount: guarded.callCount,
      guardedTier: guarded.tier,
      guardedUsage: guarded.usage,
      conversationWindow: {
        firstReply: firstWindowTurn.reply,
        secondCallMessages: windowCalls[1],
      },
      persona: REPLY_PERSONA,
    }));
    process.exit(0);
  `;

  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', script], {
    cwd: process.cwd(),
    encoding: 'utf8',
    timeout: 15_000,
    env: {
      ...process.env,
      MEMOWEFT_EXPERIENCE_UI: 'off',
      MEMOWEFT_TESTBENCH_DB_PATH: dbPath,
      MEMOWEFT_TESTBENCH_LOG_DIR: logDir,
    },
  });

  assert.equal(
    child.status,
    0,
    `reply-honesty child failed\nstdout: ${child.stdout}\nstderr: ${child.stderr}`,
  );
  const resultLine = child.stdout
    .split(/\r?\n/)
    .find((line) => line.startsWith('REPLY_HONESTY_RESULT='));
  assert.ok(resultLine, `child did not report reply honesty result\nstdout: ${child.stdout}`);
  const result = JSON.parse(resultLine.slice('REPLY_HONESTY_RESULT='.length));

  for (const item of result.unsafe) {
    assert.equal(item.classified, true, `unverified persistence claim escaped: ${item.value}`);
    assert.match(item.rewritten, /^(?:我明白你的意思了。|I understand what you mean\.)$/);
  }
  for (const item of result.safe) {
    assert.equal(item.classified, false, `honest reply was rejected: ${item.value}`);
    assert.equal(item.rewritten, item.value);
  }

  assert.deepEqual(
    result.guardedReplies.slice(0, result.unsafe.length),
    result.unsafe.map((item) =>
      /[\p{Script=Han}]/u.test(item.value) ? '我明白你的意思了。' : 'I understand what you mean.',
    ),
  );
  assert.equal(
    result.guardedReplies.at(-1),
    '我记得你之前说过更喜欢乌龙茶，这次是在补充口味。',
    'an accepted-memory recall must remain available to the natural reply',
  );
  assert.equal(result.guardedCallCount, result.unsafe.length + 1);
  assert.equal(result.guardedTier, 'local');
  assert.deepEqual(result.guardedUsage, {
    promptTokens: 7,
    completionTokens: 3,
    totalTokens: 10,
    callsWithUsage: 1,
  });
  assert.equal(result.conversationWindow.firstReply, '我明白你的意思了。');
  assert.ok(
    result.conversationWindow.secondCallMessages.some(
      (message) => message.role === 'assistant' && message.content === '我明白你的意思了。',
    ),
    'the sanitized reply, not the raw persistence promise, must enter working memory',
  );
  assert.ok(
    result.conversationWindow.secondCallMessages.every(
      (message) => !message.content.includes('我会一直记着这件事'),
    ),
  );

  assert.match(result.persona, /当前用户消息[^]*后台验证尚未完成/);
  assert.match(result.persona, /Evidence、后台身份与语义验证并自动 Apply/);
  assert.match(result.persona, /可以说“我记得你之前说过/);
  assert.doesNotMatch(result.persona, /ta 说的会被记下来/);
});
