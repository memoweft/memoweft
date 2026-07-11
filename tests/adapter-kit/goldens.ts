/**
 * adapter-kit · golden 快照读写（AD-4 baseline）。
 *
 * 语义：文件不存在 → 写入当前值（baseline 建立、本次视为通过）；已存在 → 回读期望值供 assert。
 * 首跑建 baseline、复跑锁格式。本轮只做「锁当前格式」，不新增任何冲突措辞。
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
  if (!existsSync(filePath)) {
    mkdirSync(dirname(filePath), { recursive: true });
    writeFileSync(filePath, actual, 'utf8');
    return { created: true, expected: actual };
  }
  return { created: false, expected: readFileSync(filePath, 'utf8') };
}
