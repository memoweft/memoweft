/**
 * adapter-kit 故障注入 Core 夹具。
 *
 * 三模式：
 *   - throw：立刻抛（验抛错降级）。
 *   - timeout：永不 resolve；由适配器层的显式超时器（默认 200ms）有界结束。
 *   - slow：延迟 slowMs 后 resolve（默认 20ms，短于超时阈值，因此视为成功）。
 *
 * 只实现读/轻写两个方法，供驱动构造适配器；类型松（测试夹具），驱动按各自适配器所需 Core 面 cast。
 */
import type { FaultMode } from './spi.ts';

export interface FaultyCoreOptions {
  /** 'slow' 模式延迟毫秒；缺省 20。 */
  slowMs?: number;
}

/** 造一个按 mode 行事的 { recall, ingestUserMessage }。返回鸭子形状，驱动自行 cast 成适配器要的 Core。 */
export function makeFaultyCore(mode: FaultMode, opts: FaultyCoreOptions = {}) {
  const slowMs = opts.slowMs ?? 20;
  async function faulty<T>(value: T): Promise<T> {
    if (mode === 'throw') throw new Error('memoweft: injected core fault (throw)');
    // 永不 resolve：适配器层超时器（默认 200ms）负责有界结束等待。
    if (mode === 'timeout') return new Promise<T>(() => {});
    if (mode === 'slow') await new Promise((r) => setTimeout(r, slowMs));
    return value;
  }
  return {
    async recall(): Promise<never[]> {
      return faulty<never[]>([]);
    },
    async ingestUserMessage(): Promise<{ id: string }> {
      return faulty<{ id: string }>({ id: 'faulty' });
    },
  };
}
