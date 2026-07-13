/**
 * adapter-ai-sdk · adapter-kit 契约接入（AD-1…AD-6）。
 *
 * 一份 kit（../../../tests/adapter-kit）喂两个适配器；这里是 Vercel AI SDK 侧的薄驱动：
 *   - 读路径经真 in-memory core（stub LLM + 空召回器）数 evidence 增量；
 *   - 召回呈现走真 middleware.transformParams 抽出注入文本块（AD-4 文本块 golden）。
 * 不打真模型、不触网。AD 断言由 runAdapterContract 产出（本文件是 *.test.ts，node --test 直接跑）。
 */
import { fileURLToPath } from 'node:url';
import { createMemoWeftCore, type ChatMessage, type RecalledCognition, type RecallInput, type ContentType } from 'memoweft';
import type { LanguageModelMiddleware, ModelMessage } from 'ai';
import {
  createMemoWeftMiddleware,
  createPersistOnEnd,
  persistToolResults,
  type MemoWeftMiddlewareOptions,
} from '../src/index.ts';
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

// ── 离线 core（同 mcp server.test.ts 手法：stub LLM + 空召回器，:memory: 库）──
function stubLLM(reply = 'ok') {
  return {
    callCount: 0,
    async chat(_messages: ChatMessage[]) {
      this.callCount++;
      return reply;
    },
  };
}
const nullRetriever = { async indexAll() {}, async search() { return []; } };
function makeCore() {
  return createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM(), retriever: nullRetriever });
}

// ── SDK middleware 入参形状（照 recallMiddleware.test.ts）──
type TransformArg = Parameters<NonNullable<LanguageModelMiddleware['transformParams']>>[0];
type TextMsg = { role: string; content: Array<{ type: string; text: string }> };
const MODEL = {} as unknown as TransformArg['model'];
function paramsWith(userText: string): TransformArg['params'] {
  return {
    prompt: [
      { role: 'system', content: 'You are helpful.' },
      { role: 'user', content: [{ type: 'text', text: userText }] },
    ],
  } as unknown as TransformArg['params'];
}

// onRecall 回调收到的召回项类型（中间件透传的 v2 面：id/contentType/score/provenance）。从公开选项类型提取，
//   不依赖中间件内部未导出的 RecalledLike——AD-7/8 据此把「透传进 onRecall 的召回对象」当断言源。
type OnRecallItems = Parameters<NonNullable<MemoWeftMiddlewareOptions['onRecall']>>[0];

// ── AD-7/AD-8 离线 fake core：照 Core 门面 recall 语义（createCore.ts:359-380）──
//   contentTypes → 后过滤 fixture（allow 名单）；explain → 逐条附 provenance（证据链带授权位）。
//   据 input 真读选项行事，端到端证明适配器把 contentTypes/explain 透传进了 core.recall（非驱动自造结果）。
function fakeRecallCore(fixture: RecallFixtureItem[]) {
  return {
    async recall(input: RecallInput): Promise<RecalledCognition[]> {
      let rows = fixture.slice();
      if (input.contentTypes?.length) {
        const allow = new Set<string>(input.contentTypes);
        rows = rows.filter((f) => f.contentType !== undefined && allow.has(f.contentType));
      }
      const items = rows.map((f) => {
        const item: Record<string, unknown> = {
          id: f.id, content: f.content, confidence: f.confidence,
          credStatus: f.credStatus, score: f.score, contentType: f.contentType,
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
  name: 'ai-sdk',

  // AD-2：一轮用户原话经 onEnd 落库 → +1 spoken。onEnd 事件携带助手输出但被忽略。
  async ingestUserTurn(text: string): Promise<UserTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const onEnd = createPersistOnEnd(core, { userMessage: text, originId: 'turn-1' });
      await onEnd({ text: 'assistant reply that must not be stored', responseMessages: [] });
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
      return { delta: after.length - before.length, sourceKind: added[0]?.sourceKind, content: added[0]?.rawContent };
    } finally {
      core.close();
    }
  },

  // AD-1：无用户原话的一轮 —— onEnd 事件仅携带助手输出。适配器只存闭包 userMessage（此处空）→ 零落库。
  //   这正是「从不读助手消息」by-construction：事件里的助手内容永不成为证据。
  async ingestAssistantTurn(text: string): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const onEnd = createPersistOnEnd(core, { userMessage: '' });
      await onEnd({ text, responseMessages: [{ role: 'assistant', content: text }] });
      return core.memory.listEvidence({}).length - before;
    } finally {
      core.close();
    }
  },

  // AD-2 幂等：同一轮 stable originId，onEnd 触发多次 → put 幂等去重 → 仍一条。
  async ingestUserTurnIdempotent(text: string, times: number): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const onEnd = createPersistOnEnd(core, { userMessage: text, originId: 'stable-turn' });
      for (let i = 0; i < times; i++) await onEnd({ text: 'reply', responseMessages: [] });
      return core.memory.listEvidence({}).length - before;
    } finally {
      core.close();
    }
  },

  // AD-3：一轮 [user, assistant(tool-call 意图), tool(返回结果)] 经 persistToolResults →
  //   只落工具返回结果为 tool 证据（+1）；assistant 的 tool-call 意图/入参根本不被读（铁律 3a）。
  async ingestToolResult(resultPayload: string, callIntent: string): Promise<ToolResultTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      // callIntent 藏进 assistant 的 tool-call part（意图/入参）；resultPayload 是 tool 消息的返回结果。
      const messages = [
        { role: 'user', content: [{ type: 'text', text: 'What is the weather in Xiamen?' }] },
        { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'call-1', toolName: 'get_weather', input: JSON.parse(callIntent) }] },
        { role: 'tool', content: [{ type: 'tool-result', toolCallId: 'call-1', toolName: 'get_weather', output: { type: 'text', value: resultPayload } }] },
      ] as ModelMessage[];
      await persistToolResults(core, { messages, originIdPrefix: 'turn-1' });
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
      // 铁律 3a：新落库的证据里，无一条含调用意图标识串（'get_weather'）。
      const callIntentExcluded = added.every((e) => !e.rawContent.includes('get_weather'));
      return { delta: after.length - before.length, sourceKind: added[0]?.sourceKind, content: added[0]?.rawContent, callIntentExcluded };
    } finally {
      core.close();
    }
  },

  // AD-4：真 middleware 注入 → 抽出前插的文本块（addToLastUserMessage 把注入块置于 content[0]）。
  async recallSurface(fixture: RecallFixtureItem[], lang: 'en' | 'zh' = 'en'): Promise<RecallSurface> {
    const fakeCore = { recall: async (): Promise<RecalledCognition[]> => fixture as unknown as RecalledCognition[] };
    const mw = createMemoWeftMiddleware(fakeCore, { lang });
    const out = await mw.transformParams!({ type: 'generate', params: paramsWith('How should I phrase this?'), model: MODEL });
    const userMsg = (out.prompt as unknown as TextMsg[]).find((m) => m.role === 'user')!;
    return {
      kind: 'text-block',
      rendered: userMsg.content[0]!.text,
      items: fixture.map((f) => ({ id: f.id, content: f.content, confidence: f.confidence, credStatus: f.credStatus, score: f.score })),
    };
  },

  // AD-7：带 contentTypes 调召回 → fakeRecallCore 后过滤 → 中间件把选项透传进 core.recall → onRecall 只收到匹配类型项。
  //   surface.items 取自 onRecall 捕获的召回对象（非驱动自造）——contentTypes 端到端透传的直接证据。
  async recallSurfaceFiltered(fixture: RecallFixtureItem[], contentTypes: string[], lang: 'en' | 'zh' = 'en'): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture);
    let captured: OnRecallItems = [];
    const mw = createMemoWeftMiddleware(core, {
      lang,
      contentTypes: contentTypes as ContentType[],
      onRecall: (items) => { captured = items; },
    });
    const out = await mw.transformParams!({ type: 'generate', params: paramsWith('How should I phrase this?'), model: MODEL });
    const userMsg = (out.prompt as unknown as TextMsg[]).find((m) => m.role === 'user');
    return {
      kind: 'text-block',
      rendered: userMsg?.content[0]?.text ?? '',
      items: captured.map((c) => ({ id: c.id!, content: c.content, confidence: c.confidence, credStatus: c.credStatus, score: c.score, contentType: c.contentType })),
    };
  },

  // AD-8：带 explain 调召回 → fakeRecallCore 逐条附 provenance → 中间件透传 explain → 经 onRecall 交宿主。
  //   surface.items 取自 onRecall 捕获对象，携带 provenance（含 allowCloudRead/allowInference 授权位）供断言。
  //   隐私（D-0024）：provenance 只走 onRecall，中间件的 buildKnowledgeBlock 不用它 → 绝不进注入 prompt。
  async recallSurfaceExplained(fixture: RecallFixtureItem[], lang: 'en' | 'zh' = 'en'): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture);
    let captured: OnRecallItems = [];
    const mw = createMemoWeftMiddleware(core, {
      lang,
      explain: true,
      onRecall: (items) => { captured = items; },
    });
    const out = await mw.transformParams!({ type: 'generate', params: paramsWith('How should I phrase this?'), model: MODEL });
    const userMsg = (out.prompt as unknown as TextMsg[]).find((m) => m.role === 'user');
    return {
      kind: 'text-block',
      rendered: userMsg?.content[0]?.text ?? '',
      items: captured.map((c) => ({ id: c.id!, content: c.content, confidence: c.confidence, credStatus: c.credStatus, score: c.score, contentType: c.contentType, provenance: c.provenance })),
    };
  },

  // AD-6：故障 core → 读路径降级。抛错/超时被 recallMiddleware catch → 原样返回 params（未注入）= 降级，
  //   并经注入 logger 记一条结构化事件。throw / timeout 两模式都真跑（timeout 由中间件 200ms 超时器有界赢下）。
  async runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome> {
    const faulty = makeFaultyCore(mode) as unknown as Parameters<typeof createMemoWeftMiddleware>[0];
    const events: unknown[] = [];
    const mw = createMemoWeftMiddleware(faulty, { logger: () => events.push(1) });
    const params = paramsWith('q');
    const out = await mw.transformParams!({ type: 'generate', params, model: MODEL });
    return { degraded: out === params, logged: events.length > 0 };
  },

  applicability: {
    ad3: {
      status: 'applicable',
      reason: 'persistToolResults 只读 role:tool 消息的 tool-result → +1 tool 证据；assistant 的 tool-call 意图不读（铁律 3a，AD-3/D-0013）',
    },
    ad5: {
      status: 'na',
      reason: 'AD-5 na(ai-sdk)：onEnd 事件被忽略、只存闭包 userMessage，无 LLM 输出→evidenceId 回捞落库路径',
    },
    ad6: {
      status: 'applicable',
      reason: 'recall 抛错/超时降级为不注入（recallMiddleware withTimeout+catch），经注入 logger 记一条（契约 §16.2）',
    },
    ad7: {
      status: 'applicable',
      reason: 'middleware 把 opts.contentTypes 透传进 core.recall({contentTypes}) → Core 后过滤 → onRecall 只收到匹配类型项（D-0022/D-0024，端到端透传）',
    },
    ad8: {
      status: 'applicable',
      reason: 'middleware 把 opts.explain 透传进 core.recall({explain}) → Core 附 provenance（含 allowCloudRead/allowInference 授权位）→ 经 onRecall 交宿主；provenance 绝不进注入 prompt（D-0021/D-0024 隐私加固）',
    },
    ad9: {
      status: 'na',
      reason: 'AD-9 na(ai-sdk)：读适配器（RAG-as-middleware）只做召回注入、不暴露 mute 写口；mute 负反馈是受控记忆管理（memory.mute），经宿主/B 适配器直调 Core，A 无此写路径（D-0023，仅 B applicable）',
    },
  },
};

runAdapterContract(driver, { goldenDir: fileURLToPath(new URL('./golden', import.meta.url)) });
