import { createHash } from 'node:crypto';
import type { VisibleTurn } from './model.ts';

/** sha256(JSON.stringify(context))，供写入与便携包校验共享同一规范化算法。 */
export function hashContext(context: VisibleTurn[]): string {
  return createHash('sha256').update(JSON.stringify(context)).digest('hex');
}
