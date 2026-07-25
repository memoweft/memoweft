/**
 * 配置向导 buildEnvResponse · 离线单测（模型路由 tier 字段 + 风险提醒）。
 * 纯字符串函数，不起服务、不碰库、不碰网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { buildEnvResponse } from '../src/genEnv.ts';

const CHAT = { llmBaseUrl: 'https://api.example.com/v1', llmApiKey: 'k', llmModel: 'chat-m' };
const envOf = (body: Record<string, unknown>): string => {
  const [, out] = buildEnvResponse(body);
  return 'env' in out ? out.env : '';
};

function loadGeneratedEnv(env: string): Record<string, string | undefined> {
  const dir = mkdtempSync(join(tmpdir(), 'memoweft-gen-env-'));
  const path = join(dir, '.env');
  const keys = [
    'MEMOWEFT_LLM_BASE_URL',
    'MEMOWEFT_LLM_API_KEY',
    'MEMOWEFT_LLM_MODEL',
    'MEMOWEFT_WRITE_LLM_API_KEY',
    'MEMOWEFT_EMBED_API_KEY',
  ] as const;
  const previous = new Map(keys.map((key) => [key, process.env[key]]));
  try {
    writeFileSync(path, env, 'utf8');
    process.loadEnvFile(path);
    return Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  } finally {
    for (const key of keys) {
      const value = previous.get(key);
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(dir, { recursive: true, force: true });
  }
}

test('gen-env：配了本地写模型 + tier=local → 产出 TIER=local 行 + 本地私密消化提示', () => {
  const [code, out] = buildEnvResponse({
    ...CHAT,
    writeBaseUrl: 'http://127.0.0.1:11434/v1',
    writeApiKey: 'wk',
    writeModel: 'local-m',
    writeTier: 'local',
  });
  assert.equal(code, 200);
  const env = 'env' in out ? out.env : '';
  assert.match(env, /^MEMOWEFT_WRITE_LLM_TIER=local$/m, '产出实际 TIER=local env 行');
  assert.ok(env.includes('本地私密消化'), '带本地私密消化提示');
});

test('gen-env：配了写模型但 tier=cloud（缺省）→ 产出 TIER=cloud 行 + 风险提醒', () => {
  const env = envOf({
    ...CHAT,
    writeBaseUrl: 'https://api.example.com/v1',
    writeApiKey: 'wk',
    writeModel: 'cloud-write',
    // 不传 writeTier → 缺省 cloud
  });
  assert.match(env, /^MEMOWEFT_WRITE_LLM_TIER=cloud$/m, '缺省产出实际 TIER=cloud env 行');
  assert.ok(env.includes('tier=cloud：行为观察'), '带 cloud 风险提醒');
  assert.ok(env.includes('改 TIER=local'), '提示如何切本地');
});

test('gen-env：非法 writeTier 值 → 保守回落 cloud', () => {
  const env = envOf({
    ...CHAT,
    writeBaseUrl: 'x',
    writeApiKey: 'y',
    writeModel: 'z',
    writeTier: 'GPU',
  });
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

test('gen-env：含反斜杠、换行、空格、# 和双引号的值可由 Node 无损加载', () => {
  const apiKey = 'line one\\path\nline two # "quoted"';
  const [code, out] = buildEnvResponse({ ...CHAT, llmApiKey: apiKey });
  assert.equal(code, 200);
  assert.ok('env' in out);
  assert.equal(loadGeneratedEnv(out.env).MEMOWEFT_LLM_API_KEY, apiKey);
});

test('gen-env：纯双引号包裹和中间双引号值均可由 Node 无损加载', () => {
  const apiKey = '"abc" and "mid"';
  const [code, out] = buildEnvResponse({ ...CHAT, llmApiKey: apiKey });
  assert.equal(code, 200);
  assert.ok('env' in out);
  assert.equal(loadGeneratedEnv(out.env).MEMOWEFT_LLM_API_KEY, apiKey);
});

test('gen-env：纯单引号包裹和中间单引号值均可由 Node 无损加载', () => {
  const apiKey = "'abc' and 'mid'";
  const [code, out] = buildEnvResponse({ ...CHAT, llmApiKey: apiKey });
  assert.equal(code, 200);
  assert.ok('env' in out);
  assert.equal(loadGeneratedEnv(out.env).MEMOWEFT_LLM_API_KEY, apiKey);
});

test('gen-env：可选空值也显式引用，Node 加载后仍为空串', () => {
  const [code, out] = buildEnvResponse({ ...CHAT, writeBaseUrl: 'http://write.example/v1' });
  assert.equal(code, 200);
  assert.ok('env' in out);
  assert.match(out.env, /^MEMOWEFT_WRITE_LLM_API_KEY=''$/m);
  assert.equal(loadGeneratedEnv(out.env).MEMOWEFT_WRITE_LLM_API_KEY, '');
});

test('gen-env：同时含单、双引号的值明确拒绝，避免生成被篡改的 .env', () => {
  const [code, out] = buildEnvResponse({ ...CHAT, llmApiKey: 'both \' and " quotes' });
  assert.equal(code, 400);
  assert.ok('error' in out);
  assert.match(out.error, /MEMOWEFT_LLM_API_KEY/);
});
