/**
 * 配置向导 buildEnvResponse · 离线单测（第 6 步·档2 tier 字段 + 风险提醒）。
 * 纯字符串函数，不起服务、不碰库、不碰网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildEnvResponse } from '../src/genEnv.ts';

const CHAT = { llmBaseUrl: 'https://api.example.com/v1', llmApiKey: 'k', llmModel: 'chat-m' };
const envOf = (body: Record<string, unknown>): string => {
  const [, out] = buildEnvResponse(body);
  return 'env' in out ? out.env : '';
};

test('gen-env：配了本地写模型 + tier=local → 产出 TIER=local 行 + 本地私密消化提示', () => {
  const [code, out] = buildEnvResponse({
    ...CHAT,
    writeBaseUrl: 'http://127.0.0.1:11434/v1', writeApiKey: 'wk', writeModel: 'local-m', writeTier: 'local',
  });
  assert.equal(code, 200);
  const env = 'env' in out ? out.env : '';
  assert.match(env, /^MEMOWEFT_WRITE_LLM_TIER=local$/m, '产出实际 TIER=local env 行');
  assert.ok(env.includes('本地私密消化'), '带本地私密消化提示');
});

test('gen-env：配了写模型但 tier=cloud（缺省）→ 产出 TIER=cloud 行 + 风险提醒', () => {
  const env = envOf({
    ...CHAT,
    writeBaseUrl: 'https://api.example.com/v1', writeApiKey: 'wk', writeModel: 'cloud-write',
    // 不传 writeTier → 缺省 cloud
  });
  assert.match(env, /^MEMOWEFT_WRITE_LLM_TIER=cloud$/m, '缺省产出实际 TIER=cloud env 行');
  assert.ok(env.includes('tier=cloud：行为观察'), '带 cloud 风险提醒');
  assert.ok(env.includes('改 TIER=local'), '提示如何切本地');
});

test('gen-env：非法 writeTier 值 → 保守回落 cloud', () => {
  const env = envOf({ ...CHAT, writeBaseUrl: 'x', writeApiKey: 'y', writeModel: 'z', writeTier: 'GPU' });
  assert.match(env, /^MEMOWEFT_WRITE_LLM_TIER=cloud$/m, '非法值回落 cloud（不误判 local）');
});

test('gen-env：只配对话模型（无写模型）→ 不产出实际 TIER 行，但带隐私提醒', () => {
  const env = envOf({ ...CHAT });
  assert.ok(!/^MEMOWEFT_WRITE_LLM_TIER=/m.test(env), '无写模型 → 不产出实际 TIER env 行');
  assert.ok(env.includes('隐私提醒'), '带"没有本地写模型 observed 不消化"的隐私提醒');
  assert.ok(env.includes('MEMOWEFT_WRITE_LLM_TIER=local'), '提醒里指出如何配本地消化');
});

test('gen-env：缺对话模型必填项 → [400, error]', () => {
  const [code, out] = buildEnvResponse({ writeBaseUrl: 'x', writeTier: 'local' });
  assert.equal(code, 400);
  assert.ok('error' in out, '返回 error');
});
