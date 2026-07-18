/**
 * adapter-kit golden 快照读写（召回呈现基线）。
 *
 * 语义：文件不存在 → 写入当前值（baseline 建立、本次视为通过）；已存在 → 回读期望值供 assert。
 * 首次运行建立 baseline、复跑锁定格式。本文件只负责锁定当前格式，不新增行为或措辞。
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export interface GoldenResult {
  /** 本次是否新建了 baseline（true=文件原先不存在，已写入 actual）。 */
  created: boolean;
  /** 已存在时回读的期望内容；新建时等于 actual。 */
  expected: string;
}

/** 读/建 golden。新建返回 created:true（调用方据此跳过相等断言）。 */
export function matchGolden(filePath: string, actual: string): GoldenResult {
  const withoutFinalNewline = (value: string) => value.replace(/(?:\r?\n)+$/, '');
  const normalizedActual = withoutFinalNewline(actual);
  const actualFinalNewlines = actual.slice(normalizedActual.length);
  if (!existsSync(filePath)) {
    mkdirSync(dirname(filePath), { recursive: true });
    // Keep golden files friendly to formatters while treating the final newline
    // as a repository-text convention rather than part of the rendered value.
    writeFileSync(filePath, `${normalizedActual}\n`, 'utf8');
    return { created: true, expected: normalizedActual };
  }
  return {
    created: false,
    expected: `${withoutFinalNewline(readFileSync(filePath, 'utf8'))}${actualFinalNewlines}`,
  };
}
