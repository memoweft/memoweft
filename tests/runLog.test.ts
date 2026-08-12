/**
 * 日志模块 sanity 测试。：自动测是底线护栏。
 * 离线、不调模型、确定。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRunLogger, type TurnRecord } from '../src/obs/runLog.ts';

function freshDir(): string {
  return mkdtempSync(join(tmpdir(), 'dla-runlog-'));
}

test('appendTurn 落盘合法 jsonl，缺省字段补空', () => {
  const dir = freshDir();
  try {
    const log = createRunLogger({ dir, sessionId: 's1' });
    const rec = log.appendTurn({ userInput: '昨晚没睡好', reply: '是不是玩太晚了？' });
    assert.equal(rec.turn, 1);
    assert.equal(rec.sessionId, 's1');
    assert.deepEqual(rec.recall, []);
    assert.equal(rec.proactiveQuestion, null);
    assert.equal(rec.error, null);

    const lines = readFileSync(log.file, 'utf8').split('\n').filter(Boolean);
    assert.equal(lines.length, 1);
    const parsed = JSON.parse(lines[0]!) as TurnRecord;
    assert.equal(parsed.userInput, '昨晚没睡好');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('turn 递增、readRecent 读回顺序正确', () => {
  const dir = freshDir();
  try {
    const log = createRunLogger({ dir, sessionId: 's2' });
    log.appendTurn({ userInput: 'a' });
    log.appendTurn({ userInput: 'b' });
    log.appendTurn({ userInput: 'c' });
    const recent = log.readRecent(2);
    assert.equal(recent.length, 2);
    assert.equal(recent[0]!.userInput, 'b');
    assert.equal(recent[1]!.userInput, 'c');
    assert.equal(recent[1]!.turn, 3);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('Next bridge 结果可随新轮落盘，旧日志没有该字段仍可读回', () => {
  const dir = freshDir();
  try {
    const log = createRunLogger({ dir, sessionId: 'next-bridge' });
    const written = log.appendTurn({
      userInput: '我搬到上海了',
      nextMemory: { status: 'ok', run: { id: 'memory-run-1' }, pipeline: [{ name: 'Review' }] },
    } as Partial<TurnRecord>);
    const writtenWithNext = written as TurnRecord & {
      nextMemory?: { status: string; run?: { id: string } };
    };
    assert.equal(writtenWithNext.nextMemory?.status, 'ok');
    assert.equal(writtenWithNext.nextMemory?.run?.id, 'memory-run-1');

    // 旧记录没有 nextMemory 是正常兼容形态，不需要迁移历史 jsonl。
    const old = log.appendTurn({ userInput: '旧兼容' });
    assert.equal((old as TurnRecord & { nextMemory?: unknown }).nextMemory, undefined);
    const recent = log.readRecent(2);
    assert.equal(
      (recent[0] as TurnRecord & { nextMemory?: { status: string } })?.nextMemory?.status,
      'ok',
    );
    assert.equal((recent[1] as TurnRecord & { nextMemory?: unknown })?.nextMemory, undefined);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('Next bridge 日志拒绝任意顶层字段、循环值与超大诊断对象', () => {
  const dir = freshDir();
  try {
    const log = createRunLogger({ dir, sessionId: 'next-bridge-bounded' });
    const projected = log.appendTurn({
      userInput: '字段投影',
      nextMemory: {
        status: 'ok',
        run: { id: 'kept' },
        arbitraryTopLevel: 'must-not-be-logged',
      },
    } as Partial<TurnRecord>);
    assert.deepEqual((projected as TurnRecord & { nextMemory?: unknown }).nextMemory, {
      status: 'ok',
      run: { id: 'kept' },
    });

    const oversized = 'x'.repeat(140 * 1024);
    const written = log.appendTurn({
      userInput: '边界测试',
      nextMemory: {
        status: 'ok',
        run: { oversized },
        arbitraryTopLevel: 'must-not-be-logged',
      },
    } as Partial<TurnRecord>);
    const nextMemory = (
      written as TurnRecord & {
        nextMemory?: { status: string; code?: string; arbitraryTopLevel?: string };
      }
    ).nextMemory;
    assert.deepEqual(nextMemory, { status: 'failed', code: 'NEXT_MEMORY_LOG_REJECTED' });

    const cyclic: { self?: unknown } = {};
    cyclic.self = cyclic;
    const cyclicWritten = log.appendTurn({
      userInput: '循环值',
      nextMemory: { status: 'ok', run: cyclic },
    } as Partial<TurnRecord>);
    assert.deepEqual((cyclicWritten as TurnRecord & { nextMemory?: unknown }).nextMemory, {
      status: 'failed',
      code: 'NEXT_MEMORY_LOG_REJECTED',
    });

    const lines = readFileSync(log.file, 'utf8').split('\n').filter(Boolean);
    assert.equal(lines.length, 3);
    assert.ok(lines.every((line) => Buffer.byteLength(line, 'utf8') < 128 * 1024));
    assert.ok(lines.every((line) => !line.includes('must-not-be-logged')));
    assert.ok(lines.every((line) => !line.includes(oversized)));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('透视字段齐全（与测试台透视区对齐）', () => {
  const dir = freshDir();
  try {
    const log = createRunLogger({ dir, sessionId: 's3' });
    const rec = log.appendTurn({
      userInput: '挂机而已',
      reply: '好的，我改一下。',
      recall: [{ summary: '昨晚游戏开到3:30', score: 320 }],
      hypotheses: [{ text: '玩游戏太晚→没睡好', confidence: 320, credStatus: '候选' }],
      conflicts: [{ detail: '用户纠正：只是挂机' }],
      proactiveQuestion: null,
      llmCalls: 2,
      profileChanges: [{ detail: '"打游戏=熬夜" 降权' }],
    });
    for (const k of ['recall', 'hypotheses', 'conflicts', 'profileChanges'] as const) {
      assert.ok(Array.isArray(rec[k]), `${k} 应为数组`);
    }
    assert.equal(rec.hypotheses[0]!.credStatus, '候选');
    assert.equal(rec.llmCalls, 2);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('appendProfileUpdate：记录各步耗时与摘要，kind=profile_update，与对话轮同文件', () => {
  const dir = freshDir();
  try {
    const log = createRunLogger({ dir, sessionId: 'p1' });
    log.appendTurn({ userInput: 'hi' });
    const rec = log.appendProfileUpdate({
      trigger: 'manual',
      timings: { distillMs: 10, consolidateMs: 20, attributeMs: 5, indexMs: 1, totalMs: 36 },
      summary: {
        pendingCount: 2,
        created: 1,
        reinforced: 0,
        corrected: 0,
        conflicted: 0,
        hypotheses: 1,
        trends: 0,
        expired: 0,
      },
      llmCalls: 3,
    });
    assert.equal(rec.kind, 'profile_update');
    assert.equal(rec.timings.totalMs, 36);
    assert.equal(rec.summary.hypotheses, 1);
    const lines = readFileSync(log.file, 'utf8').split('\n').filter(Boolean);
    assert.equal(lines.length, 2, '对话轮 + 更新画像 同文件两行');
    const pu = JSON.parse(lines[1]!) as { kind: string; timings: { consolidateMs: number } };
    assert.equal(pu.kind, 'profile_update');
    assert.equal(pu.timings.consolidateMs, 20);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
