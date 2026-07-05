/**
 * MemoWeft 版本号（单一真源）。
 *
 * 抽成独立小模块：`src/index.ts`（对外 re-export，兼容旧名 DLA_VERSION）与
 * `src/portable/`（写进 bundle 的 memoWeftVersion）都从这里取，避免二者互相 import 成环。
 */
export const MEMOWEFT_VERSION = '0.4.0';
