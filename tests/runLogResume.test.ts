/**
 * 续聊地基（S4b）：重开一个已存在会话的 logger，轮号要接着往下、不从 1 重来（否则与历史撞车）。
 * 写临时目录，跑完清掉；纯离线。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRunLogger } from '../src/obs/runLog.ts';

test('RunLogger：重开已存在会话的 logger → 轮号接着往下（不从 1 重来撞车）', () => {
  const dir = mkdtempSync(join(tmpdir(), 'mwlog-'));
  try {
    const a = createRunLogger({ dir, sessionId: 's-resume' });
    a.appendTurn({ userInput: '一', reply: 'r1' });
    a.appendTurn({ userInput: '二', reply: 'r2' });

    // 重开同一会话（模拟"点开旧会话接着聊"）
    const b = createRunLogger({ dir, sessionId: 's-resume' });
    const t3 = b.appendTurn({ userInput: '三', reply: 'r3' });
    assert.equal(t3.turn, 3, '接着第 3 轮，而不是重开成第 1 轮');
    assert.equal(b.readRecent(10).length, 3, '历史 + 新轮都在同一文件');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
