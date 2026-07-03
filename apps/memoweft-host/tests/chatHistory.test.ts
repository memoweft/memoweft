/**
 * chatHistory 多对话落盘（批次5 步4）：验 append/read/list/archive/newId + 文件名 sanitize。
 * 纯文件系统，不依赖网络/模型/库。用系统临时目录，测完清掉、无残留。
 */
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, existsSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createChatHistory } from '../src/chatHistory.ts';

const dirs: string[] = [];
function freshDir(): string {
  const d = mkdtempSync(join(tmpdir(), 'mw-host-hist-'));
  dirs.push(d);
  return d;
}
afterEach(() => {
  for (const d of dirs.splice(0)) {
    try { rmSync(d, { recursive: true, force: true }); } catch { /* 清不掉忽略 */ }
  }
});

test('一对话一 jsonl：append 后 read 按写入顺序回读', () => {
  const h = createChatHistory(freshDir());
  h.append('c1', { role: 'user', content: '你好', ts: '2026-07-03T00:00:00.000Z' });
  h.append('c1', { role: 'assistant', content: '你也好', ts: '2026-07-03T00:00:01.000Z' });
  const turns = h.read('c1');
  assert.equal(turns.length, 2);
  assert.equal(turns[0]!.role, 'user');
  assert.equal(turns[0]!.content, '你好');
  assert.equal(turns[1]!.role, 'assistant');
});

test('多对话互不串：c1 / c2 各自独立', () => {
  const h = createChatHistory(freshDir());
  h.append('c1', { role: 'user', content: 'A 的话', ts: '2026-07-03T00:00:00.000Z' });
  h.append('c2', { role: 'user', content: 'B 的话', ts: '2026-07-03T00:00:00.000Z' });
  assert.equal(h.read('c1').length, 1);
  assert.equal(h.read('c1')[0]!.content, 'A 的话');
  assert.equal(h.read('c2')[0]!.content, 'B 的话');
});

test('read 不存在的对话 → 空数组，不报错', () => {
  const h = createChatHistory(freshDir());
  assert.deepEqual(h.read('nope'), []);
});

test('list：列出所有未归档对话，含 id/预览/最后活跃/归档位；预览取首条 user', () => {
  const h = createChatHistory(freshDir());
  h.append('c1', { role: 'user', content: '第一句就是我说的', ts: '2026-07-03T00:00:00.000Z' });
  h.append('c1', { role: 'assistant', content: '收到', ts: '2026-07-03T00:00:01.000Z' });
  const list = h.list();
  assert.equal(list.length, 1);
  assert.equal(list[0]!.id, 'c1');
  assert.equal(list[0]!.preview, '第一句就是我说的');
  assert.equal(list[0]!.archived, false);
  assert.equal(typeof list[0]!.lastActiveMs, 'number');
});

test('archive = 软移除：数据不删、默认列表不列、includeArchived 才见', () => {
  const h = createChatHistory(freshDir());
  h.append('c1', { role: 'user', content: '留着别删', ts: '2026-07-03T00:00:00.000Z' });
  h.archive('c1');
  // 默认列表不列已归档
  assert.equal(h.list().length, 0, '默认 list 不含归档');
  // 连归档一起列时能看到，且标 archived
  const withArch = h.list({ includeArchived: true });
  assert.equal(withArch.length, 1);
  assert.equal(withArch[0]!.archived, true);
  // 数据仍能读回（软移除不删内容）
  assert.equal(h.read('c1')[0]!.content, '留着别删');
});

test('archive 不存在的对话不报错（从没聊过就归档）', () => {
  const h = createChatHistory(freshDir());
  assert.doesNotThrow(() => h.archive('never'));
});

test('newId 递增不撞：连取两个 id 不相等', () => {
  const h = createChatHistory(freshDir());
  assert.notEqual(h.newId(), h.newId());
});

test('文件名 sanitize：含路径穿越/非法字符的 id 收敛到安全文件、不逃出目录', () => {
  const dir = freshDir();
  const h = createChatHistory(dir);
  // 用带 '/' 和 '..' 的恶意 id：不应在 dir 外写文件；read/append 自洽（同一 sanitize 后名）。
  h.append('../evil/x', { role: 'user', content: '穿越尝试', ts: '2026-07-03T00:00:00.000Z' });
  // 目录外不应出现 evil 目录
  assert.ok(!existsSync(join(dir, '..', 'evil')), '不应逃出目录写文件');
  // 同一 id 仍能自洽读回
  assert.equal(h.read('../evil/x')[0]!.content, '穿越尝试');
});

test('read 容错：坏行跳过，不毁整段', () => {
  const dir = freshDir();
  const h = createChatHistory(dir);
  // 直接往文件里塞一行坏 JSON + 一行好数据，验证只读回好的。
  h.append('c1', { role: 'user', content: '好数据', ts: '2026-07-03T00:00:00.000Z' });
  // 追加一行非法 JSON（直接写文件）
  appendFileSync(join(dir, 'c1.jsonl'), '{坏行不是JSON\n', { encoding: 'utf-8' });
  h.append('c1', { role: 'assistant', content: '又一条好的', ts: '2026-07-03T00:00:02.000Z' });
  const turns = h.read('c1');
  assert.equal(turns.length, 2, '坏行跳过，两条好数据都在');
  assert.equal(turns[0]!.content, '好数据');
  assert.equal(turns[1]!.content, '又一条好的');
});

test('续写已归档对话 = 自动恢复（不新建空文件、不遮蔽历史）：步4 审查 open 分叉根治', () => {
  const h = createChatHistory(freshDir());
  h.append('c1', { role: 'user', content: '旧历史一', ts: '2026-07-03T00:00:00.000Z' });
  h.append('c1', { role: 'assistant', content: '旧历史二', ts: '2026-07-03T00:00:01.000Z' });
  h.archive('c1'); // 归档：只剩 .archived
  // 再往 c1 写（= open 已归档对话后续聊）→ 应恢复归档、续写在同一份历史上，不新建空活跃遮蔽旧历史。
  h.append('c1', { role: 'user', content: '恢复后新说的', ts: '2026-07-03T00:00:02.000Z' });
  const turns = h.read('c1');
  assert.equal(turns.length, 3, '旧 2 条 + 新 1 条都在，历史没被遮蔽/分叉');
  assert.equal(turns[0]!.content, '旧历史一');
  assert.equal(turns[2]!.content, '恢复后新说的');
  assert.equal(h.list().length, 1, '恢复后回到活跃列表（不再是归档态）');
});

test('重复归档不丢历史（must-fix）：归档→续写→再归档，旧历史不被覆盖', () => {
  const h = createChatHistory(freshDir());
  h.append('c1', { role: 'user', content: 'A 内容', ts: '2026-07-03T00:00:00.000Z' });
  h.archive('c1'); // 第一次归档：.archived = [A]
  h.append('c1', { role: 'user', content: 'B 内容', ts: '2026-07-03T00:00:01.000Z' }); // 续写 → 恢复归档 + 追加
  h.archive('c1'); // 第二次归档
  // 旧的 A 不该被第二次归档覆盖——read 回退读归档应拿到 A + B。
  const turns = h.read('c1');
  assert.equal(turns.length, 2, '归档里应有 A + B，A 没被第二次归档覆盖');
  assert.equal(turns[0]!.content, 'A 内容');
  assert.equal(turns[1]!.content, 'B 内容');
});
