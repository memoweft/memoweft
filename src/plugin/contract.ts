/**
 * 插件契约 v2：插件契约接口与运行时接线。
 *
 * 【它是什么】
 * MemoWeft 插件 = 给同一套记忆底座加"脸/工具/感知"。三类：
 *   - experience：只出回话人设（systemPrompt）。记忆本体不碰，只决定语气/角色。
 *   - tool / collector：经 hook 观察对话/观察流，经受限 PluginContext 请求能力（提交观察 / 读召回）。
 *
 * 【访问约束】
 *   - hook 只能【观察 + 经 ctx 请求】，不能改管线：返回值一律丢弃，不改用户消息 / 回话文本，
 *     不绕记忆规则塞证据。落观察只能走 ctx.submitObservation（强制 observed 默认、cloud=false）。
 *   - PluginContext 是【受限壳】：只暴露 Core 能真执行的两件事（submitObservation / requestMemory），
 *     用闭包给、不交 store。UI / 权限弹窗（requestPermission / emitUIEvent）不在此——那是 Host 的事。
 *   - 声明式权限：permissions 声明要哪些 ctx 能力；没声明就调不到（Core 门控）。Host UI 据此展示 + 启停。
 */
import type { Observation } from '../perception/ingest.ts';
import type { RecalledCognitionItem } from '../retrieval/recall.ts';

/** 插件类别。experience=只 systemPrompt；tool/collector=用 hook 观察/请求。 */
export type PluginType = 'experience' | 'tool' | 'collector';

/** 声明式权限：插件声明要用 PluginContext 的哪些能力。缺省 / 未声明 = 该能力调不到（Core 门控、抛错）。 */
export interface PluginPermissions {
  /** 允许经 ctx.submitObservation 提交观察（落 observed 证据）。 */
  submitObservation?: boolean;
  /** 允许经 ctx.requestMemory 读"与 query 相关"的召回认知。 */
  requestMemory?: boolean;
}

/**
 * 插件提交观察的入参 = `Observation` 去掉三个授权位。
 * 插件【不能】设授权位——一律走 observed 保守默认（local:true / cloud:false / inference:true），
 * 防插件传 allowCloudRead:true 因"显式 > 默认"绕过"observed 默认不进入内建云写模型 prompt"（等价 Host sanitizeObservation）。
 */
export type PluginObservationInput = Omit<
  Observation,
  'allowLocalRead' | 'allowCloudRead' | 'allowInference'
>;

/** hook 看到的用户消息（只读观察用；hook 不能改它）。 */
export interface PluginUserMessage {
  /** 用户原话。 */
  content: string;
  /** 该轮归属 subject。 */
  subjectId: string;
  /** 本轮回话文本（观察用；hook 改它也不影响已发出的回话）。 */
  reply: string;
}

/**
 * 受限上下文：Core 给每个 hook 的能力壳。只暴露 Core 能真执行、且请求式的能力。
 * 绑定当次 subject（v1 单人单宿主 = config.identity.subjectId），故方法不收 subjectId。
 */
export interface PluginContext {
  /** 提交一条观察 → 落 observed 证据（走 observedDefaults，不能设授权位）。需 permissions.submitObservation。 */
  submitObservation(input: PluginObservationInput): Promise<void>;
  /** 读"与 query 相关"的召回认知（走既有召回门控）。需 permissions.requestMemory。 */
  requestMemory(query: string): Promise<RecalledCognitionItem[]>;
}

/**
 * MemoWeft 插件契约 v2。
 * experience 类只填 systemPrompt（Host 按会话选、每轮传，见 createCore.handleConversationTurn）；
 * tool / collector 类填 hook + permissions，经 PluginContext 请求能力。
 */
export interface MemoWeftPlugin {
  /** 稳定机器标识（注册表键、切换用）。别改，改了等于换插件。 */
  id: string;
  /** 给用户看的名字（前端选择器 / 管理页显示）。 */
  name: string;
  /** 插件类别。 */
  type: PluginType;
  /** experience 类：注入回话的人设 / 系统提示。 */
  systemPrompt?: string;
  /** 声明式权限：要用 ctx 的哪些能力。 */
  permissions?: PluginPermissions;
  /** Core 初始化时触发一次（stores/retriever 已就绪；新库 requestMemory 可能空）。 */
  onLoad?(ctx: PluginContext): void | Promise<void>;
  /** 每轮对话完成后触发（观察本轮输入与回复，不改管线）。 */
  onUserMessage?(msg: PluginUserMessage, ctx: PluginContext): void | Promise<void>;
  /** 每条观察落库后触发（只观察，不改管线）。 */
  onObservation?(obs: Observation, ctx: PluginContext): void | Promise<void>;
}
