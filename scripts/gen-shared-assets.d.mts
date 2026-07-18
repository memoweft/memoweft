/**
 * 类型声明:供 tests/shared/shared-assets.test.ts 在 strict typecheck 下 import。
 * 运行时实现见同名 gen-shared-assets.mjs（纯 JS，导入 TS 实现生成语言中立共享资产）。
 */

/** 生成全部共享资产:{ 相对路径 → 资产对象 }(async,仅因 HashEmbedder.embed)。 */
export declare function buildSharedAssets(): Promise<Record<string, unknown>>;

/** 稳定序列化(递归按键排序,末尾补换行),与 --check 逐字比对同源。 */
export declare function stableStringify(value: unknown): string;

/** shared/ 目录的绝对路径。 */
export declare const SHARED: string;
