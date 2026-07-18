/**
 * HashEmbedder（Phase 1 · §14.1）：确定性测试用嵌入器。
 *
 * 用途：给黄金集 / demo / CI 提供**零网络、零成本、完全可复现**的 embedder。
 * 真实语义靠云端臂（OpenAICompatEmbedder）；这条确定性臂只做**词面回归**——
 * 同输入恒同输出，不打网络、不读系统时间、不掷随机数。
 *
 * 算法 = feature hashing（词袋哈希）：
 *   1. tokenize：lowercase；拉丁/数字按 Unicode 连续段切词（`\p{L}+|\p{N}+`）；
 *      CJK 连续汉字段**额外**切成 char-bigram + 单字——保证"饮食"这类 2 字中文词
 *      与含它的文本有 token 重叠（本项目中文召回的关键，见 DECISIONS D-0001 同源理据）。
 *   2. 每个 token 用 FNV-1a 32 位哈希（纯位运算、不引依赖）映射到维度 [0, dim)，权重累加（TF）。
 *   3. 对向量做 L2 归一化；空文本 / 无 token → 全零向量（归一化后仍全零）。
 *
 * 注意：这是 tests/ 下的测试夹具，不属于公共 API，也不从 src 依赖任何内部函数。
 */
import type { Embedder } from '../../src/retrieval/embedder.ts';

/** 默认维度。构造参数可配（见 HashEmbedder 构造器）。 */
export const DEFAULT_DIM = 256;

/**
 * FNV-1a 32 位哈希：纯位运算、无依赖、跨平台确定。
 * 用 Math.imul 做 32 位乘法（避免 JS number 溢出到 53 位精度区）；`>>> 0` 归一到无符号 32 位。
 */
export function fnv1a32(str: string): number {
  let h = 0x811c9dc5; // FNV offset basis (2166136261)
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193); // FNV prime (16777619)
  }
  return h >>> 0;
}

const HAN = /\p{Script=Han}/u;

/**
 * 把一个"字母/数字连续段"再拆成 token：
 *  - 汉字子段：单字 + 相邻 char-bigram 全都发出（"饮食" → 饮, 食, 饮食）。
 *  - 非汉字子段（拉丁/数字）：整段作为一个 token。
 */
function tokensFromRun(run: string): string[] {
  const out: string[] = [];
  // run 内部按"是否汉字"再分段：\p{Script=Han}+ 抓汉字连续段，其余抓非汉字连续段。
  const segments = run.match(/\p{Script=Han}+|[^\p{Script=Han}]+/gu) ?? [];
  for (const seg of segments) {
    if (HAN.test(seg)) {
      const chars = Array.from(seg); // 按 code point 切，兼顾扩展区汉字（代理对）
      for (const c of chars) out.push(c); // 单字
      for (let i = 0; i + 1 < chars.length; i++) out.push(chars[i]! + chars[i + 1]!); // char-bigram
    } else {
      out.push(seg);
    }
  }
  return out;
}

/** lowercase 后按 `\p{L}+|\p{N}+` 取连续段，再逐段细切（汉字段走 bigram+单字）。 */
export function tokenize(text: string): string[] {
  const runs = text.toLowerCase().match(/\p{L}+|\p{N}+/gu) ?? [];
  const out: string[] = [];
  for (const run of runs) out.push(...tokensFromRun(run));
  return out;
}

/**
 * 确定性词袋哈希嵌入器。
 * 实现 src/retrieval/embedder.ts 的 Embedder 接口；embed 把纯同步计算包成 Promise.resolve。
 */
export class HashEmbedder implements Embedder {
  /** 向量维度（恒定，构造时确定）。 */
  readonly dim: number;
  private _callCount = 0;

  constructor(dim: number = DEFAULT_DIM) {
    if (!Number.isInteger(dim) || dim <= 0) {
      throw new Error(`HashEmbedder dim 必须是正整数，收到 ${dim}`);
    }
    this.dim = dim;
  }

  /** 累计 embed 调用次数（最小实现·可选·观测）：本地计算不涉及网络，仅供 CI 计数。 */
  get callCount(): number {
    return this._callCount;
  }

  /** 单条文本 → 已 L2 归一化的向量；无 token → 全零向量。 */
  private embedOne(text: string): number[] {
    const vec = new Array<number>(this.dim).fill(0);
    for (const token of tokenize(text)) {
      const idx = fnv1a32(token) % this.dim;
      vec[idx] = (vec[idx] ?? 0) + 1; // TF 累加
    }
    let sumSq = 0;
    for (const v of vec) sumSq += v * v;
    if (sumSq === 0) return vec; // 空文本 / 无 token：全零，不做除法
    const norm = Math.sqrt(sumSq);
    return vec.map((v) => v / norm);
  }

  /** 把一组文本编码成向量。纯同步计算，包成 resolved Promise（满足接口的异步签名）。 */
  embed(texts: string[]): Promise<number[][]> {
    if (texts.length === 0) return Promise.resolve([]);
    this._callCount++;
    return Promise.resolve(texts.map((t) => this.embedOne(t)));
  }
}
