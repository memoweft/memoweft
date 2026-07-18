/**
 * 采样器工厂离线契约测试：按平台创建采样器 / 未支持平台返回 null。
 * 只验工厂返回什么，不真调采样（不碰真 Win32），故任何 OS 上都能跑。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createForegroundSampler, SUPPORTED_PLATFORMS } from '../src/samplerFactory.ts';

test('samplerFactory：win32 → 采样器函数；未支持平台 → null', () => {
  assert.equal(typeof createForegroundSampler('win32'), 'function', 'win32 返回采样器函数');
  assert.equal(createForegroundSampler('darwin'), null, 'macOS 暂未支持 → null');
  assert.equal(createForegroundSampler('linux'), null, 'Linux 暂未支持 → null');
  assert.equal(createForegroundSampler('freebsd'), null, '未知平台 → null');
});

test('samplerFactory：SUPPORTED_PLATFORMS 里的平台工厂都造得出采样器', () => {
  for (const p of SUPPORTED_PLATFORMS) {
    assert.equal(typeof createForegroundSampler(p), 'function', `声明支持的 ${p} 应真造得出采样器`);
  }
});
