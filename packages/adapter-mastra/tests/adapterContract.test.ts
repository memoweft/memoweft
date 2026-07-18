/**
 * adapter-mastra · adapter-kit 契约接入（assistant-output exclusion…mute semantics）。
 *
 * 一份 kit（../../../tests/adapter-kit）供多个适配器复用；这里是 Mastra Processor 侧的薄驱动：
 *   - 测试【直接调 processor.processInput / processOutputResult】、传入构造的 ProcessInputArgs /
 *     ProcessOutputResultArgs 对象——【不启动真实 Mastra Agent】、不触网、不打模型。
 *   - AD 断言由 runAdapterContract 产出（本文件是 *.test.ts，node --test 直接跑）。
 *
 * 与 A（adapter-ai-sdk）/ openai-agents 的对照：都不启动宿主运行时、都靠「直接调处理函数 + 传入构造事件」
 *   在离线核上断言（同一范式）。本包是 processor 型（processInput 召回注入 system 通道 +
 *   processOutputResult 落库用户原话/工具结果/AI 回复）。
 *
 * tool-result-only ingestion boundary（代码级 by-construction）：
 *   - 写只在 processOutputResult 里发生，且【只】读 result.steps[].toolResults[].payload.result（工具真实返回结果）
 *     与 payload.toolCallId（幂等键）；payload.args（LLM 调用入参）与 result.text（助手回话）从不落成证据。
 *   - result.text 只可能经 recordAssistantReply 进【上下文窗口】（0.6 面·永不落证据）。
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
import type { ProcessInputArgs, ProcessOutputResultArgs } from '@mastra/core/processors';
import { createMemoWeftProcessor, type MemoWeftProcessorOptions } from '../src/index.ts';
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

// createMemoWeftProcessor 期望的 core 面（ProcessorCore 未导出，从工厂签名取）；fake core 据此 cast。
type ProcessorCoreArg = Parameters<typeof createMemoWeftProcessor>[0];
// onRecall 回调收到的召回项类型（透传的 v2 面：id/contentType/score/provenance）。recall filtering/8 据此把
//   「透传进 onRecall 的召回对象」当断言源（非驱动自造结果）。
type OnRecallItems = Parameters<NonNullable<MemoWeftProcessorOptions['onRecall']>>[0];

// ── Mastra 事件构造夹具（传给 processInput/processOutputResult；只填适配器实际读的字段，其余 cast 略）──

/** 一条 user 消息（MastraDBMessage 形状：content.parts 里一个 text part）。 */
function userMessage(text: string, id = 'msg-1', threadId?: string): unknown {
  return { id, role: 'user', threadId, content: { format: 2, parts: [{ type: 'text', text }] } };
}
/** 造 ProcessInputArgs：messages + 空 systemMessages + 可注入的 state（processInput 只读这三者）。 */
function inputArgs(messages: unknown[], state: Record<string, unknown> = {}): ProcessInputArgs {
  return {
    messages,
    systemMessages: [],
    state,
    // 以下字段本适配器 processInput 不读，最小 cast 占位。
    messageList: {},
    abort: () => {
      throw new Error('abort');
    },
  } as unknown as ProcessInputArgs;
}
/** 造 ProcessOutputResultArgs：result + state（+ 可选 messages 供兜底路径）。 */
function outputArgs(
  result: unknown,
  state: Record<string, unknown> = {},
  messages: unknown[] = [],
): ProcessOutputResultArgs {
  return {
    messages,
    systemMessages: [],
    state,
    result,
    messageList: {},
    abort: () => {
      throw new Error('abort');
    },
  } as unknown as ProcessOutputResultArgs;
}
/** 一步 LLMStepResult 形状：toolCalls(调用意图)/toolResults(返回结果)。适配器只该读 toolResults[].payload.result。 */
function step(opts: { toolCalls?: unknown[]; toolResults?: unknown[] }): unknown {
  return { toolCalls: opts.toolCalls ?? [], toolResults: opts.toolResults ?? [] };
}
/** tool_call_output：工具真实返回结果——适配器唯一摄入源（读 payload.result / toolCallId）。 */
function toolResultChunk(result: string, callIntent: string, callId = 'call-1'): unknown {
  // payload.args 刻意带上调用意图串——证明适配器【不读】它（tool-result-only ingestion boundary）。
  return {
    type: 'tool-result',
    payload: { toolCallId: callId, toolName: 'get_weather', result, args: callIntent },
  };
}

/** 从 processInput 的返回值取【注入的 system 消息文本】（{ messages, systemMessages } 形态的末条 system content）。 */
function injectedSystemText(res: unknown): string {
  if (res && typeof res === 'object' && 'systemMessages' in res) {
    const sys = (res as { systemMessages?: unknown }).systemMessages;
    if (Array.isArray(sys) && sys.length > 0) {
      const last = sys[sys.length - 1] as { content?: unknown };
      if (typeof last?.content === 'string') return last.content;
    }
  }
  return '';
}

// ── recall injection, filtering, and provenance privacy 离线 fake recall core：遵循 Core 门面 recall 语义──
//   contentTypes → 后过滤 fixture（allow 名单）；explain → 逐条附 provenance（证据链带授权位）。
//   据 input 真读选项行事 → 端到端证明适配器把 contentTypes/explain 透传进了 core.recall（非驱动自造结果）。
//   ingestUserMessage/ingestToolResult 是写路径，本组只观测召回面 → no-op。不设 recordAssistantReply（召回测试用不到）。
function fakeRecallCore(fixture: RecallFixtureItem[]): ProcessorCoreArg {
  return {
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
        if (input.explain && f.provenance) item.provenance = f.provenance;
        return item;
      });
      return items as unknown as RecalledCognition[];
    },
    async ingestUserMessage() {
      return {} as never;
    },
    async ingestToolResult() {
      return {} as never;
    },
  } as unknown as ProcessorCoreArg;
}

const driver: AdapterDriver = {
  name: 'mastra',

  // user-ingestion idempotency：一轮用户原话经 processInput 捕获（注入前原文）→ processOutputResult 落库 → +1 spoken。
  //   捕获取自【注入前】的 messages（注入只落 system 通道，绝不碰 messages）→ 存进证据的原话永不含召回注入内容。
  async ingestUserTurn(text: string): Promise<UserTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const p = createMemoWeftProcessor(core);
      const state: Record<string, unknown> = {};
      await p.processInput!(inputArgs([userMessage(text)], state));
      await p.processOutputResult!(outputArgs({ text: '', steps: [] }, state));
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

  // assistant-output exclusion：助手侧内容流经适配器 → 零落库（by-construction）。
  //   把 processOutputResult 能获取的助手侧全部传入——result.text（助手回话）+ 一步里的 toolCalls（模型调用意图/入参）——
  //   适配器【只】摄入 toolResults[].payload.result；本例 toolResults 为空 → 落库 0 条。
  //   （result.text 至多经 recordAssistantReply 进上下文窗口、永不落证据；此处无 conversationId → 连上下文都不记。）
  //   若将来 processOutputResult 误摄了 text / toolCalls，此断言即红。
  async ingestAssistantTurn(text: string): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const p = createMemoWeftProcessor(core);
      await p.processOutputResult!(
        outputArgs(
          {
            text,
            steps: [
              step({
                toolCalls: [
                  {
                    payload: {
                      toolCallId: 'c1',
                      toolName: 'get_weather',
                      args: '{"tool":"get_weather"}',
                    },
                  },
                ],
                toolResults: [],
              }),
            ],
          },
          {},
        ),
      );
      return core.memory.listEvidence({}).length - before; // 0 by construction
    } finally {
      core.close();
    }
  },

  // user-ingestion idempotency 幂等：同一轮稳定消息 id（= originId）经捕获→落库路径触发多次 → ingestUserMessage put 幂等去重 → 仍一条。
  async ingestUserTurnIdempotent(text: string, times: number): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const p = createMemoWeftProcessor(core);
      for (let i = 0; i < times; i++) {
        const state: Record<string, unknown> = {};
        await p.processInput!(inputArgs([userMessage(text, 'stable-msg')], state));
        await p.processOutputResult!(outputArgs({ text: '', steps: [] }, state));
      }
      return core.memory.listEvidence({}).length - before; // 1（同 originId）
    } finally {
      core.close();
    }
  },

  // tool-result-only ingestion：processOutputResult 传入一步含 [toolCall(调用意图/入参), toolResult(返回结果)] →
  //   只落工具返回结果为 tool 证据（+1）；调用意图/入参（含 'get_weather'）根本不被读（tool-result-only ingestion boundary，代码级 by-construction）。
  async ingestToolResult(resultPayload: string, callIntent: string): Promise<ToolResultTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const p = createMemoWeftProcessor(core);
      await p.processOutputResult!(
        outputArgs(
          {
            text: '',
            steps: [
              step({
                toolCalls: [
                  { payload: { toolCallId: 'call-1', toolName: 'get_weather', args: callIntent } },
                ],
                toolResults: [toolResultChunk(resultPayload, callIntent, 'call-1')],
              }),
            ],
          },
          {},
        ),
      );
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
      // tool-result-only ingestion boundary：新落库证据里，无一条含调用意图标识串（'get_weather'）。
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

  // recall injection：processInput 传入末条 user 的 ModelInput → 召回 → 注入的 system 消息文本即知识块（沿用参考适配器的 en/zh golden）。
  //   隐私：buildKnowledgeBlock 只用 content/confidence/credStatus，注入文本绝不含 id/contentType/score/provenance。
  async recallSurface(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture);
    const p = createMemoWeftProcessor(core, { lang });
    const res = await p.processInput!(inputArgs([userMessage('How should I phrase this?')]));
    return {
      kind: 'text-block',
      rendered: injectedSystemText(res),
      items: fixture.map((f) => ({
        id: f.id,
        content: f.content,
        confidence: f.confidence,
        credStatus: f.credStatus,
        score: f.score,
      })),
    };
  },

  // recall filtering：带 contentTypes 调召回 → fakeRecallCore 后过滤 → 适配器把选项透传进 core.recall → onRecall 只收到匹配类型项。
  //   surface.items 取自 onRecall 捕获对象（非驱动自造）——contentTypes 端到端透传的直接证据。
  async recallSurfaceFiltered(
    fixture: RecallFixtureItem[],
    contentTypes: string[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture);
    let captured: OnRecallItems = [];
    const p = createMemoWeftProcessor(core, {
      lang,
      contentTypes: contentTypes as ContentType[],
      onRecall: (items) => {
        captured = items;
      },
    });
    const res = await p.processInput!(inputArgs([userMessage('How should I phrase this?')]));
    return {
      kind: 'text-block',
      rendered: injectedSystemText(res),
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

  // provenance privacy：带 explain 调召回 → fakeRecallCore 逐条附 provenance → 适配器透传 explain → 经 onRecall 交宿主。
  //   隐私：provenance 只走 onRecall，buildKnowledgeBlock 不用它 → 绝不进注入 system 文本（此处断言实证）。
  async recallSurfaceExplained(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture);
    let captured: OnRecallItems = [];
    const p = createMemoWeftProcessor(core, {
      lang,
      explain: true,
      onRecall: (items) => {
        captured = items;
      },
    });
    const res = await p.processInput!(inputArgs([userMessage('How should I phrase this?')]));
    const rendered = injectedSystemText(res);
    const provSummaries = captured
      .flatMap((c) => c.provenance ?? [])
      .map((p2) => (p2 as { summary?: unknown }).summary)
      .filter((s): s is string => typeof s === 'string' && s !== '');
    assert.ok(
      provSummaries.length > 0,
      'provenance privacy fixture must carry provenance summaries for the privacy assertion to be meaningful',
    );
    for (const s of provSummaries) {
      assert.ok(
        !rendered.includes(s),
        `injected system message must NOT contain provenance summary ( built-in prompt boundary): "${s}"`,
      );
    }
    return {
      kind: 'text-block',
      rendered,
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

  // graceful degradation：故障 core → processInput 里 recall 抛错/超时（recallTimeoutMs 有界）→ 降级为【原样返回 messages】（不注入）、经 logger 记一条。
  //   processInput 不做写（写在 processOutputResult）→ 本路径只 emit op:'recall'，且 timeout 模式不会 hang（超时器有界赢下）。
  //   降级判据：返回值不是 { messages, systemMessages } 形态（无 system 注入）= 降级；logger 记了 ≥1 条 = logged。
  async runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome> {
    const faulty = makeFaultyCore(mode) as unknown as ProcessorCoreArg;
    const events: Array<{ op?: string }> = [];
    const p = createMemoWeftProcessor(faulty, {
      recallTimeoutMs: 50,
      logger: (e) => {
        events.push(e);
      },
    });
    const res = await p.processInput!(inputArgs([userMessage('q')]));
    assert.ok(
      events.some((e) => e.op === 'recall'),
      'faulty-core recall degradation must emit a memory_degraded event with op:recall',
    );
    const degraded = !(res && typeof res === 'object' && 'systemMessages' in res);
    return { degraded, logged: events.length > 0 };
  },

  applicability: {
    ad3: {
      status: 'applicable',
      reason:
        'processOutputResult 只读 result.steps[].toolResults[].payload.result/toolCallId（工具真实返回结果）落 +1 tool 证据；payload.args（LLM 调用入参）与 result.text（助手回话）绝不读成证据（tool-result-only ingestion boundary，代码级 by-construction，tool-result-only ingestion）',
    },
    ad5: {
      status: 'na',
      reason:
        'adapter shape (mastra)：召回注入走 processInput 返回的 systemMessages，写路径只读取 result.steps 的 toolResults；模型输出不能直接持久化为 evidenceId',
    },
    ad6: {
      status: 'applicable',
      reason:
        'processInput 里 recall 抛错/超时（recallTimeoutMs 有界）降级为原样返回不注入、processOutputResult 写路径失败重试一次仍失败静默吞——都不向 Mastra 抛，经注入 logger 记一条结构化事件（降级契约覆盖 throw 与 timeout）',
    },
    ad7: {
      status: 'applicable',
      reason:
        'createMemoWeftProcessor 把 opts.contentTypes 透传进 core.recall({contentTypes}) → Core 后过滤 → onRecall 只收到匹配类型项（端到端透传）',
    },
    ad8: {
      status: 'applicable',
      reason:
        'opts.explain 透传进 core.recall({explain}) → Core 附 provenance（含 allowCloudRead/allowInference 授权位）→ 经 onRecall 交宿主；provenance 绝不进注入 system 文本（隐私加固）',
    },
    ad9: {
      status: 'na',
      reason:
        'mute semantics (mastra)：processor 不暴露 mute 写口；宿主可直接调用 Core memory.mute，因此本契约项不适用',
    },
  },
};

runAdapterContract(driver, { goldenDir: fileURLToPath(new URL('./golden', import.meta.url)) });
