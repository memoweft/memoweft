/**
 * adapter-kit · 故障注入 fake Core（AD-6）。
 *
 * 三模式：
 *   - throw：立刻抛（本轮唯一真跑的模式，验已有的抛错降级）。
 *   - timeout：永不 resolve（须配合适配器层显式超时才有意义——本轮无超时面，套件不跑它，仅留 SPI）。
 *   - slow：延迟后 resolve（同上，留 SPI）。
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
    if (mode === 'timeout') return new Promise<T>(() => {}); // 永不 resolve
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
