/**
 * adapter-claude-agent-sdk · adapter-kit 契约接入（adapter contract coverage）。
 *
 * 一份 kit（../../../tests/adapter-kit）覆盖多个适配器；这里是 Claude Agent SDK 侧的薄驱动：
 *   - 测试直接调用 hook 处理函数并提供构造的 UserPromptSubmit / PostToolUse input 对象——不启动实际 SDK、不触网、不调用模型。
 *   - 读写三路径都从工厂造出的 hooks 里取出对应 handler 调用：
 *       · UserPromptSubmit handler：先存用户原话（spoken）、再召回 → additionalContext（读写同一 hook）；
 *       · PostToolUse handler：存工具结果（tool），只读 tool_response / tool_use_id（tool-result-only ingestion，绝不碰 tool_input）。
 *   - 契约断言由 runAdapterContract 产出（本文件是 *.test.ts，node --test 直接跑）。
 *
 * 与 A（adapter-ai-sdk）的对照：A 是进程内 middleware/onEnd 型，本包是进程内 hooks 型；
 *   两者都不启动宿主运行时，而是通过「直接调用处理函数 + 提供构造事件」在离线 Core 上断言（同一范式）。
 */
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import {
  createMemoWeftCore,
  type ChatMessage,
  type RecalledCognition,
  type RecallInput,
  type ContentType,
} from 'memoweft';
import type {
  HookCallback,
  HookJSONOutput,
  UserPromptSubmitHookInput,
  PostToolUseHookInput,
} from '@anthropic-ai/claude-agent-sdk';
import { createMemoWeftAgentHooks, type MemoWeftAgentHooksOptions } from '../src/index.ts';
import { runAdapterContract } from '../../../tests/adapter-kit/contract.ts';
import type {
  AdapterDriver,
  FaultMode,
  FaultOutcome,
  RecallFixtureItem,
  RecallSurface,
  ToolResultTurnResult,
  UserTurnResult,
} from '../../../tests/adapter-kit/spi.ts';
import { makeFaultyCore } from '../../../tests/adapter-kit/faultyCore.ts';

// ── 离线 core（沿用适配器契约测试配置：stub LLM、空召回器、:memory: 库）──
function stubLLM(reply = 'ok') {
  return {
    callCount: 0,
    async chat(_messages: ChatMessage[]) {
      this.callCount++;
      return reply;
    },
  };
}
const nullRetriever = {
  async indexAll() {},
  async search() {
    return [];
  },
};
function makeCore() {
  return createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
}

// ── hook 事件构造 + handler 取出（不启动真实 SDK）──
// createMemoWeftAgentHooks 期望的 core 面（AgentCore 未导出，从工厂签名取），stub core 据此 cast。
type AgentCoreArg = Parameters<typeof createMemoWeftAgentHooks>[0];
// onRecall 回调收到的召回项类型（透传的 v2 面：id/contentType/score/provenance）。从公开选项类型提取，
//   recall filtering 据此把「透传进 onRecall 的召回对象」当断言源（非驱动自造结果）。
type OnRecallItems = Parameters<NonNullable<MemoWeftAgentHooksOptions['onRecall']>>[0];

const HOOK_BASE = {
  session_id: 'sess-1',
  transcript_path: '/tmp/transcript.jsonl',
  cwd: '/tmp',
} as const;
const hookCtx = () => ({ signal: new AbortController().signal });

/** 构造一个 UserPromptSubmit input（prompt = 本轮用户原文；prompt_id = 一轮稳定 UUID，供幂等 originId）。 */
function upsInput(prompt: string, promptId?: string): UserPromptSubmitHookInput {
  return { ...HOOK_BASE, hook_event_name: 'UserPromptSubmit', prompt, prompt_id: promptId };
}
/** 构造一个 PostToolUse input：tool_response=工具真实返回（应落库）；tool_input=LLM 调用意图/入参（绝不落库，tool-result-only ingestion）。 */
function ptuInput(
  toolResponse: unknown,
  toolInput: unknown,
  toolUseId = 'call-1',
): PostToolUseHookInput {
  return {
    ...HOOK_BASE,
    hook_event_name: 'PostToolUse',
    tool_name: 'get_weather',
    tool_input: toolInput,
    tool_response: toolResponse,
    tool_use_id: toolUseId,
  };
}

/** 从工厂返回的 hooks 里取出两个 handler（省略 matcher，单元素数组）。 */
function handlersOf(
  core: AgentCoreArg,
  opts?: MemoWeftAgentHooksOptions,
): { ups: HookCallback; ptu: HookCallback } {
  const { hooks } = createMemoWeftAgentHooks(core, opts);
  return { ups: hooks.UserPromptSubmit![0]!.hooks[0]!, ptu: hooks.PostToolUse![0]!.hooks[0]! };
}

/** 从 hook 返回值里抽注入面文本（additionalContext）；降级/空召回时返回 undefined。 */
function contextOf(out: HookJSONOutput): string | undefined {
  const so = (out as { hookSpecificOutput?: { additionalContext?: string } }).hookSpecificOutput;
  return so?.additionalContext;
}

// ── knowledge-block rendering、recall filtering、provenance privacy 离线 fake recall core：按 Core 门面 recall 语义（createCore.ts:359-380）──
//   contentTypes → 后过滤 fixture（allow 名单）；explain → 逐条附 provenance（证据链带授权位）。
//   据 input 真读选项行事 → 端到端证明适配器把 contentTypes/explain 透传进了 core.recall（非驱动自造结果）。
//   ingestUserMessage 是 UserPromptSubmit handler 先跑的写步骤，这里 no-op（本组测试只观测召回面）。
function fakeRecallCore(fixture: RecallFixtureItem[]) {
  return {
    async ingestUserMessage() {
      return {};
    },
    async recall(input: RecallInput): Promise<RecalledCognition[]> {
      let rows = fixture.slice();
      if (input.contentTypes?.length) {
        const allow = new Set<string>(input.contentTypes);
        rows = rows.filter((f) => f.contentType !== undefined && allow.has(f.contentType));
      }
      const items = rows.map((f) => {
        const item: Record<string, unknown> = {
          id: f.id,
          content: f.content,
          confidence: f.confidence,
          credStatus: f.credStatus,
          score: f.score,
          contentType: f.contentType,
        };
        // explain 附 provenance（照 createCore.ts:373-379）——支撑/反证链，每条含授权位；仅经 onRecall 交宿主。
        if (input.explain && f.provenance) item.provenance = f.provenance;
        return item;
      });
      return items as unknown as RecalledCognition[];
    },
  };
}

const driver: AdapterDriver = {
  name: 'claude-agent-sdk',

  // user-message ingestion：一轮用户原话经 UserPromptSubmit handler 落库 → +1 spoken。
  //   注入走返回值 additionalContext、绝不改 input.prompt → 存进证据的原话永不含召回注入内容（by design 干净）。
  async ingestUserTurn(text: string): Promise<UserTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const { ups } = handlersOf(core);
      await ups(upsInput(text, 'turn-1'), undefined, hookCtx());
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
      return {
        delta: after.length - before.length,
        sourceKind: added[0]?.sourceKind,
        content: added[0]?.rawContent,
      };
    } finally {
      core.close();
    }
  },

  // assistant-output exclusion：助手消息流经适配器 → 零落库（by design）。
  //   本适配器只注册 UserPromptSubmit（用户原话）+ PostToolUse（工具结果）两个 hook，【没有】任何读助手消息的路径——
  //   助手回话在 Claude Agent SDK 里经 query() 的 SDKAssistantMessage 流出，不经任何本适配器注册的 hook。
  //   因此没有 handler 接收助手文本：执行一次 in-memory Core 的读写路径（PostToolUse 只处理 tool_response）也不会把助手内容当作证据。
  async ingestAssistantTurn(_text: string): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      // 守结构不变量（非空断言，avoid letting assistant-output exclusion 沦为ineffective）：适配器【只】注册 UserPromptSubmit（用户原话）
      //   + PostToolUse（工具结果）两个 hook，绝无任何会携带助手输出的 hook（如 Stop 的 last_assistant_message、
      //   或读 SDKAssistantMessage 的路径）——助手输出永不成为证据（tool-result-only ingestion）。若将来误加了这类 hook,此断言即红。
      const { hooks } = createMemoWeftAgentHooks(core);
      assert.deepStrictEqual(
        Object.keys(hooks).sort(),
        ['PostToolUse', 'UserPromptSubmit'],
        'assistant-output exclusion: adapter must mount only UserPromptSubmit + PostToolUse, and no hook may carry assistant output',
      );
      // 无助手摄入路径 → 无落库。恒 0（by design）。
      return core.memory.listEvidence({}).length - before;
    } finally {
      core.close();
    }
  },

  // user-message ingestion 幂等：同一轮稳定 prompt_id（→ originId），UserPromptSubmit handler 触发多次 → put 幂等去重 → 仍一条。
  async ingestUserTurnIdempotent(text: string, times: number): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const { ups } = handlersOf(core);
      for (let i = 0; i < times; i++)
        await ups(upsInput(text, 'stable-turn'), undefined, hookCtx());
      return core.memory.listEvidence({}).length - before;
    } finally {
      core.close();
    }
  },

  // tool-result-only ingestion：向 PostToolUse handler 提供 { tool_response=结果, tool_input=调用意图 } →
  //   只落工具返回结果为 tool 证据（+1）；tool_input（'get_weather' 意图/入参）根本不被读（tool-result-only ingestion，代码级 by design）。
  async ingestToolResult(resultPayload: string, callIntent: string): Promise<ToolResultTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const { ptu } = handlersOf(core);
      // tool_input 藏调用意图/入参（JSON 对象），tool_response 是工具返回结果 payload；handler 只读 tool_response。
      await ptu(ptuInput(resultPayload, JSON.parse(callIntent), 'call-1'), 'call-1', hookCtx());
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
      // tool-result-only ingestion：新落库证据里，无一条含调用意图标识串（'get_weather'）。
      const callIntentExcluded = added.every((e) => !e.rawContent.includes('get_weather'));
      return {
        delta: after.length - before.length,
        sourceKind: added[0]?.sourceKind,
        content: added[0]?.rawContent,
        callIntentExcluded,
      };
    } finally {
      core.close();
    }
  },

  // knowledge-block rendering：UserPromptSubmit handler 召回 → 返回值 additionalContext 即注入面文本块（using the adapter contract 出 en/zh golden）。
  //   隐私：buildKnowledgeBlock 只用 content/confidence/credStatus，additionalContext 绝不含 id/contentType/score/provenance。
  async recallSurface(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const core = {
      async ingestUserMessage() {
        return {};
      },
      async recall(): Promise<RecalledCognition[]> {
        return fixture as unknown as RecalledCognition[];
      },
    } as unknown as AgentCoreArg;
    const { ups } = handlersOf(core, { lang });
    const out = await ups(upsInput('How should I phrase this?', 'turn-1'), undefined, hookCtx());
    return {
      kind: 'text-block',
      rendered: contextOf(out) ?? '',
      items: fixture.map((f) => ({
        id: f.id,
        content: f.content,
        confidence: f.confidence,
        credStatus: f.credStatus,
        score: f.score,
      })),
    };
  },

  // recall filtering：带 contentTypes 调召回 → fakeRecallCore 后过滤 → handler 把选项透传进 core.recall → onRecall 只收到匹配类型项。
  //   surface.items 取自 onRecall 捕获对象（非驱动自造）——contentTypes 端到端透传的直接证据。
  async recallSurfaceFiltered(
    fixture: RecallFixtureItem[],
    contentTypes: string[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture) as unknown as AgentCoreArg;
    let captured: OnRecallItems = [];
    const { ups } = handlersOf(core, {
      lang,
      contentTypes: contentTypes as ContentType[],
      onRecall: (items) => {
        captured = items;
      },
    });
    const out = await ups(upsInput('How should I phrase this?', 'turn-1'), undefined, hookCtx());
    return {
      kind: 'text-block',
      rendered: contextOf(out) ?? '',
      items: captured.map((c) => ({
        id: c.id!,
        content: c.content,
        confidence: c.confidence,
        credStatus: c.credStatus,
        score: c.score,
        contentType: c.contentType,
      })),
    };
  },

  // provenance privacy：带 explain 调召回 → fakeRecallCore 逐条附 provenance → handler 透传 explain → 经 onRecall 交宿主。
  //   surface.items 取自 onRecall 捕获对象，携带 provenance（含 allowCloudRead/allowInference 授权位）供断言。
  //   隐私：provenance 只走 onRecall，buildKnowledgeBlock 不用它 → 绝不进注入 additionalContext。
  async recallSurfaceExplained(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture) as unknown as AgentCoreArg;
    let captured: OnRecallItems = [];
    const { ups } = handlersOf(core, {
      lang,
      explain: true,
      onRecall: (items) => {
        captured = items;
      },
    });
    const out = await ups(upsInput('How should I phrase this?', 'turn-1'), undefined, hookCtx());
    return {
      kind: 'text-block',
      rendered: contextOf(out) ?? '',
      items: captured.map((c) => ({
        id: c.id!,
        content: c.content,
        confidence: c.confidence,
        credStatus: c.credStatus,
        score: c.score,
        contentType: c.contentType,
        provenance: c.provenance,
      })),
    };
  },

  // graceful degradation：故障 core → UserPromptSubmit handler 降级不抛、经注入 logger 记一条。
  //   faultyCore 的 recall / ingestUserMessage 都按 mode 故障：handler 先写（ingest）后读（recall），
  //   两步都套有界超时（本处 recall/ingest 都给小阈值）→ throw 立即拒、timeout 由超时器有界赢下，均不真 hang。
  //   降级判据：返回值无 additionalContext（未注入）= 降级；logger 记了 ≥1 条结构化事件 = logged。任何情况都不向 handler 外抛。
  async runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome> {
    const faulty = makeFaultyCore(mode) as unknown as AgentCoreArg;
    const events: Array<{ op?: string }> = [];
    const { ups } = handlersOf(faulty, {
      recallTimeoutMs: 50,
      ingestTimeoutMs: 50, // 有界 ingest：timeout 模式下写步骤不会永挂（默认无 ingest 超时，此处显式给阈值）。
      logger: (e) => {
        events.push(e);
      },
    });
    const out = await ups(upsInput('q', 'turn-1'), undefined, hookCtx());
    // 隔离 recall 降级（avoid letting ingest 降级掩盖 recall 日志回归）：除 logged>0，断言确有一条 recall 面降级事件。
    assert.ok(
      events.some((e) => e.op === 'recall'),
      'faulty-core recall degradation must emit a memory_degraded event with op:recall',
    );
    return { degraded: contextOf(out) === undefined, logged: events.length > 0 };
  },

  applicability: {
    ad3: {
      status: 'applicable',
      reason:
        'PostToolUse hook 只读 tool_response/tool_use_id → ingestToolResult 落 +1 tool 证据；tool_input（LLM 调用意图/入参）绝不解构/引用（tool-result-only ingestion，代码级 by design，tool-result-only ingestion）',
    },
    ad5: {
      status: 'na',
      reason:
        'no model-output ingestion path：hooks 只读构造的 input，注入走返回值 additionalContext，模型输出不能写入证据',
    },
    ad6: {
      status: 'applicable',
      reason:
        'UserPromptSubmit hook 里 recall 抛错/超时降级为不注入、ingest 失败重试一次仍失败静默吞——都不向 SDK 抛，经注入 logger 记一条结构化事件（降级契约覆盖 throw 与 timeout）',
    },
    ad7: {
      status: 'applicable',
      reason:
        'createMemoWeftAgentHooks 把 opts.contentTypes 透传进 core.recall({contentTypes}) → Core 后过滤 → onRecall 只收到匹配类型项（端到端透传）',
    },
    ad8: {
      status: 'applicable',
      reason:
        'opts.explain 透传进 core.recall({explain}) → Core 附 provenance（含 allowCloudRead/allowInference 授权位）→ 经 onRecall 交宿主；provenance 绝不进注入 additionalContext（隐私加固）',
    },
    ad9: {
      status: 'na',
      reason:
        'mute semantics not applicable for claude-agent-sdk：hooks 型读写适配器只经 UserPromptSubmit 召回注入 + PostToolUse 写工具结果，不暴露 mute 写口；mute 负反馈是受控记忆管理（memory.mute），由宿主直接调用 Core，本适配器无此写路径',
    },
  },
};

runAdapterContract(driver, { goldenDir: fileURLToPath(new URL('./golden', import.meta.url)) });
