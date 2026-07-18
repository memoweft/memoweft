// Fast, deterministic linting for Core and its test suite. Type-aware guarantees
// are enforced separately by the TypeScript compiler and workspace typechecks.

import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    // Workspace packages and runnable tools have their own typechecks and tests.
    ignores: [
      '**/dist/**',
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
    // Underscore-prefixed values are intentionally unused. The remaining two
    // rules stay visible as warnings while recommended correctness rules fail CI.
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/ban-ts-comment': 'warn',
    },
  },
);
