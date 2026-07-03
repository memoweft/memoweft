/**
 * 体验插件注册表测试（架构归位·批次5 做插件 v1）。
 *
 * 验注册表契约，不验端到端"语气"（那要真模型）：
 *   - getExperience 已知 id 取对、未知 id 回退 plain（永不抛）。
 *   - listExperiences 至少含 plain + xingyao，且只透 id/name（不外泄 systemPrompt）。
 *   - 每个体验插件形状对：id/name/systemPrompt 非空、type==='experience'。
 *
 * 纯 Host 内模块，不 import 'memoweft'、不碰 Core dist——无需先 build，独立可跑。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  getExperience,
  listExperiences,
  EXPERIENCE_IDS,
  FALLBACK_EXPERIENCE,
} from '../src/experiences/index.ts';

test('getExperience：已知 id 取对应插件', () => {
  assert.equal(getExperience('plain').id, 'plain');
  assert.equal(getExperience('xingyao').id, 'xingyao');
});

test('getExperience：未知 id / 空串回退 plain，永不抛', () => {
  assert.equal(getExperience('nope').id, 'plain', '未知 id 回退 plain');
  assert.equal(getExperience('').id, 'plain', '空串回退 plain');
  assert.equal(FALLBACK_EXPERIENCE.id, 'plain', '兜底体验是 plain');
});

test('listExperiences：至少含 plain + xingyao，只透 id/name', () => {
  const list = listExperiences();
  const ids = list.map((e) => e.id);
  assert.ok(ids.includes('plain'), '含 plain');
  assert.ok(ids.includes('xingyao'), '含 xingyao');
  for (const e of list) {
    assert.equal(typeof e.name, 'string', 'name 是字符串');
    assert.ok(e.name.length > 0, 'name 非空');
    // 只透 id/name：不把 systemPrompt 原文列给前端。
    assert.ok(!('systemPrompt' in e), 'listExperiences 不外泄 systemPrompt');
  }
});

test('EXPERIENCE_IDS 与 listExperiences 口径一致（白名单 = 全部）', () => {
  const listed = listExperiences().map((e) => e.id).sort();
  assert.deepEqual([...EXPERIENCE_IDS].sort(), listed);
});

test('每个体验插件形状对：type/systemPrompt/name 非空', () => {
  for (const { id } of listExperiences()) {
    const p = getExperience(id);
    assert.equal(p.type, 'experience', `${id}.type 是 experience`);
    assert.equal(typeof p.systemPrompt, 'string', `${id}.systemPrompt 是字符串`);
    assert.ok(p.systemPrompt.trim().length > 0, `${id}.systemPrompt 非空`);
    assert.ok(p.name.trim().length > 0, `${id}.name 非空`);
  }
});

test('星瑶人设守 naming 护栏：不出现过度承诺话术', () => {
  const prompt = getExperience('xingyao').systemPrompt;
  // 记 ≠ 信（naming.md §2）：星瑶可拟人，但不许替记忆层吹"真正理解/全方位掌握/永远不忘"。
  //   这些短语作为【被禁止说的示例】出现在 prompt 里（"别说…"），所以校验它们是否出现在"别说"的否定语境中，
  //   而不是作为正面承诺——这里退一步只做冒烟级检查：确认 prompt 显式带了"不要把话说满"的护栏措辞。
  assert.ok(prompt.includes('不要') && prompt.includes('说满'), '星瑶 prompt 含"不要把话说满"护栏');
});
