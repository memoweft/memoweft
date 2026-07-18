/**
 * 体验插件注册表。
 *
 * 把两个体验插件（普通助手 / 星瑶）登记在一处，给 server.ts 一个统一的取用口：
 *   - getExperience(id)：按 id 取插件，未知 id 回退普通助手（plain）——切换端点/env 都靠它兜底。
 *   - listExperiences()：列出全部（供前端选择器渲染）。
 *   - EXPERIENCE_IDS / DEFAULT_EXPERIENCE_ID：白名单与默认值（默认从 env MEMOWEFT_EXPERIENCE 读，缺省 plain）。
 *
 * 加第三个体验插件时：新建 xxx.ts 导出一个 MemoWeftPlugin，再在下面 REGISTRY 里加一行即可，
 *   其余（前端列表、切换白名单、env 校验）全自动跟上——注册表是唯一事实源。
 */
import type { MemoWeftPlugin } from 'memoweft';
import { plain } from './plain.ts';
import { xingyao } from './xingyao.ts';

/** 注册表：id → 插件。加插件只动这里（唯一事实源）。 */
const REGISTRY: Record<string, MemoWeftPlugin> = {
  [plain.id]: plain,
  [xingyao.id]: xingyao,
};

/** 全部已注册插件。传给 createMemoWeftCore 以启用插件钩子；experience 类没有钩子，仅供插件管理界面枚举。 */
export const ALL_PLUGINS: MemoWeftPlugin[] = Object.values(REGISTRY);

/** 兜底体验：任何未知 id 都回退到它（普通助手最中性、最不会出错）。 */
export const FALLBACK_EXPERIENCE = plain;

/** 全部合法体验 id（白名单）：切换端点校验、前端列表都以此为准。 */
export const EXPERIENCE_IDS: string[] = Object.keys(REGISTRY);

/**
 * 默认体验 id：env MEMOWEFT_EXPERIENCE（plain | xingyao）；缺省或填了不认识的值 → plain。
 * 只读一次（模块加载时定），server.ts 用它初始化当前激活体验。
 */
export const DEFAULT_EXPERIENCE_ID: string = ((): string => {
  const fromEnv = (process.env.MEMOWEFT_EXPERIENCE ?? '').trim();
  return fromEnv && REGISTRY[fromEnv] ? fromEnv : FALLBACK_EXPERIENCE.id;
})();

/** 按 id 取体验插件；未知 id（含空串）回退普通助手，永不抛错（切换/初始化都靠它兜底）。 */
export function getExperience(id: string): MemoWeftPlugin {
  return REGISTRY[id] ?? FALLBACK_EXPERIENCE;
}

/** 列出全部体验插件（供前端选择器）：只透 id + name，不外泄 systemPrompt 原文。 */
export function listExperiences(): Array<{ id: string; name: string }> {
  return Object.values(REGISTRY).map((p) => ({ id: p.id, name: p.name }));
}

/** 列出全部已注册插件（供插件管理 UI）：id/name/type + 声明的权限名。不外泄 systemPrompt 原文。 */
export function listPlugins(): Array<{
  id: string;
  name: string;
  type: string;
  permissions: string[];
}> {
  return Object.values(REGISTRY).map((p) => ({
    id: p.id,
    name: p.name,
    type: p.type,
    permissions: p.permissions
      ? Object.keys(p.permissions).filter((k) => (p.permissions as Record<string, boolean>)[k])
      : [],
  }));
}
