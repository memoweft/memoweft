// MemoWeft ESLint · 松档关卡（flat config）
//
// 定档口径（作者已拍板，Q1 任务书）：
//   - 只上 eslint recommended + @typescript-eslint recommended，**不上** strict-type-checked /
//     recommended-type-checked —— 松档先把关卡立起来，避免逼出大量 src 存量修改。
//   - 先只关 src/ 与 tests/；dist / node_modules / testbench / apps / plugins / examples / logs
//     等非 core 目录一律 ignore。
//   - lint 扫出的 src/ 存量问题记「发现待办」，不在本批修。
//
// 说明：这是零 type-checking 的松档（不传 parserOptions.project），跑得快、不碰算法。

import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    // 先只关 src/ 与 tests/：把其余目录全排掉。
    ignores: [
      'dist/**',
      'node_modules/**',
      'testbench/**',
      'apps/**',
      'plugins/**',
      'examples/**',
      'logs/**',
      '**/*.mjs',
      '**/*.js',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // 松档收口：把只命中【存量】的两条规则降为 warn，让关卡不被存量卡死（存量进「发现待办」）。
    //   - no-unused-vars：认下划线前缀（`_x`）为有意未用而豁免；其余未用降 warn、不阻断退出码。
    //   - ban-ts-comment：放行缺说明的 @ts-expect-error（存量 tests/store.test.ts 两处），降 warn。
    // 关卡仍以 error 拦真问题（no-undef / no-redeclare / no-constant-condition 等 recommended error 级）。
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/ban-ts-comment': 'warn',
    },
  },
);
