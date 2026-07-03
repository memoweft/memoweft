/**
 * Experience Plugin 契约 · v1（架构归位·批次5「做插件」第一步）。
 *
 * 【它是什么】
 * 体验插件 = 给同一套 MemoWeft 记忆底座换一张"脸"。同一份对用户的了解，
 * 用「普通助手」的语气回话、还是用「星瑶」的语气回话，只差一段 systemPrompt。
 * 记忆本体（存什么、信不信、会不会忘）全在 Core，插件一概不碰——它只决定回话的人设/语气。
 *
 * 【v1 只做到哪】——用户已拍板，别扩范围：
 *   1) 只落 systemPrompt 级：一个体验插件就是 { id, name, type:'experience', systemPrompt }。
 *      Core 现状（createCore.ts handleConversationTurn）只支持在【首次建会话实例】时注入 systemPrompt，
 *      没有 onUserMessage / onObservation 这类消息级 hook，所以 v1 就只用 systemPrompt 这一个口子。
 *   2) 不改 Core src/：只经 `import 'memoweft'` 用 Core 现成能力，不为插件在 Core 里加任何 hook。
 *   3) type / hooks / permissions / PluginContext 这些【只预留、不实现】（见下方注释），
 *      等契约稳了、真的要做 tool / collector 插件时再落地。
 *
 * 【放这里、不单独开包的原因】
 * 契约还没稳（v1 只覆盖 experience 一类），此刻抽成独立 workspace 包等于把没定型的接口先冻住。
 * 先作为 Host 内模块（apps/memoweft-host/src/experiences/），等接口在真实使用里稳定，再抽独立包。
 *
 * 契约草案出处：docs/架构归位路线.md §7.1（MemoWeftPlugin / PluginContext）。
 * 命名与边界护栏：docs/naming.md §6——星瑶这类体验插件的 systemPrompt 里可以拟人、自称"我"，
 *   但 Host 侧代码注释 / name / UI 文案仍遵 naming：不用"她"指代 MemoWeft 库本体、不说"真正理解你"。
 */

/**
 * MemoWeft 插件接口（v1 只落 experience + systemPrompt）。
 *
 * 路线 §7.1 草案里 MemoWeftPlugin 还有 permissions / onLoad / onUserMessage / onObservation，
 * v1 一律【不实现】，只在下方以可选注释形式预留说明——避免现在就把还没想清楚的形状冻进契约。
 */
export interface MemoWeftPlugin {
  /** 稳定的机器标识（如 'plain' / 'xingyao'）：注册表的键、env / API 切换用的就是它。别改，改了等于换插件。 */
  id: string;

  /** 给用户看的名字（如"普通助手" / "星瑶"）：前端选择器、切换提示里显示的就是这个。 */
  name: string;

  /**
   * 插件类别。v1 只有 'experience'（体验/角色，只提供回话人设）。
   * 'tool'（工具，如 GitHub / 文件）、'collector'（感知采集，如窗口采集）是后续——
   * 它们要动 Core 更多能力（工具权限 / 观察摄入），等契约稳了再加进这个联合类型并实现。
   */
  type: 'experience';

  /**
   * 注入 Core handleConversationTurn 的宿主人设 / 系统提示。
   * Core 语义（createCore.ts）：systemPrompt 仅在该 conversationId【首次建实例】时生效，
   *   后续同会话复用不重建、忽略此参。所以"切换体验后当下这句就换人设"要靠【重建会话实例】来兑现
   *   —— 见 server.ts 切换端点：切完把当前会话标为未激活，下一句 chat 会用 seedTurns 重建窗口 + 新 systemPrompt。
   */
  systemPrompt: string;

  // ── 以下均为 v1【预留、不实现】，只写清将来要做什么，先不落形状 ──
  //
  // permissions?: PluginPermissions;
  //   工具 / 采集类插件才需要的权限声明（能不能读认知、能不能提交观察、要哪些工具权限）。
  //   experience 插件只出人设、不申请任何能力，v1 用不到。等做 tool / collector 时再定义 PluginPermissions。
  //
  // onLoad?(ctx: PluginContext): void | Promise<void>;
  // onUserMessage?(msg: UserMessage, ctx: PluginContext): void | Promise<void>;
  // onObservation?(obs: Observation, ctx: PluginContext): void | Promise<void>;
  //   消息级 / 观察级钩子。Core 现在【没有】这类回调口子（handleConversationTurn 只吃一次性 systemPrompt），
  //   要实现得先给 Core 加消息级 hook —— 那会动 Core src/，超出 v1「不改 Core」的红线，故留后续。
  //
  // PluginContext（受限上下文，路线 §7.1）：将来插件拿到的不是 Core 全权限对象，而是一个受限壳，
  //   只暴露 submitObservation / requestMemory / requestPermission / emitUIEvent 等【请求式】能力，
  //   由 Host 审核、Core 执行（boundaries.md §2.3「插件只能请求，Host 审核，Core 执行记忆规则」）。
  //   v1 没有 hook 也就没有 ctx，等有 tool / collector 且 Core 支持消息级 hook 时再落。
}
