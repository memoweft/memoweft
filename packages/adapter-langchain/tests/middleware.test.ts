/**
 * adapter-langchain · v1 Agent Middleware 的 adapter-kit 契约接入（assistant-output exclusion…mute semantics，name='langchain-mw'）。
 *
 * 与同包的 adapterContract.test.ts（retriever+callback 型，name='langchain'）并存：本文件测 v1 middleware 面。
 *   - 直接驱动 `buildMemoWeftHooks(core, opts)` 返回的纯函数 hook（beforeAgent / wrapModelCall / wrapToolCall /
 *     afterAgent），传入构造的 state / request / handler——【不启动真实 createAgent】、不触网、不打模型
 *     测试聚焦处理逻辑而非 SDK 对象；createMiddleware 的接线由 typecheck 保证。
 *
 * tool-result-only ingestion boundary（代码级 by-construction）：wrapToolCall【只】读 handler 返回的 ToolMessage.content（工具真实返回结果），
 *   【绝不】读 request.toolCall（LLM 的调用意图/入参）；afterAgent 的 AI 回复只经 recordAssistantReply 进上下文窗口、永不落证据。
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
import { buildMemoWeftHooks, type MemoWeftMiddlewareOptions } from '../src/index.ts';
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

// ── 离线 core（stub LLM + 空召回器，:memory: 库）──
function stubLLM(reply = 'ok') {
  return {
    callCount: 0,
    async chat(_m: ChatMessage[]) {
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

type HooksCoreArg = Parameters<typeof buildMemoWeftHooks>[0];
type OnRecallItems = Parameters<NonNullable<MemoWeftMiddlewareOptions['onRecall']>>[0];

// ── 构造的 BaseMessage-like（只填 hook 实际读的字段：getType/content/id）──
function humanMsg(text: string, id = 'msg-1'): unknown {
  return { content: text, id, getType: () => 'human' };
}
function aiMsg(text: string): unknown {
  return { content: text, getType: () => 'ai' };
}
function toolMsg(content: string): unknown {
  return { content, getType: () => 'tool' };
}
/** system 消息（带 concat：返回内容拼接后的新 system 消息，供 wrapModelCall 临时注入）。 */
function sysMsg(content = ''): { content: string; concat(x: string): unknown; getType(): string } {
  return {
    content,
    concat(x: string) {
      return sysMsg(content + x);
    },
    getType: () => 'system',
  };
}
function sysContent(req: unknown): string {
  const s = (req as { systemMessage?: { content?: unknown } })?.systemMessage;
  return typeof s?.content === 'string' ? s.content : '';
}

// ── Recall injection, filtering, and provenance privacy: offline fake recall core
//    follows Core recall semantics (contentTypes filtering and explain provenance).
function fakeRecallCore(fixture: RecallFixtureItem[]): HooksCoreArg {
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
  } as unknown as HooksCoreArg;
}

const driver: AdapterDriver = {
  name: 'langchain-mw',

  // user-ingestion idempotency：beforeAgent 从 state.messages 取最后一条 human 原话 → ingestUserMessage(spoken) → +1。
  async ingestUserTurn(text: string): Promise<UserTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const hooks = buildMemoWeftHooks(core);
      await hooks.beforeAgent({ messages: [humanMsg(text)] }, {});
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

  // assistant-output exclusion：助手回复经 afterAgent → recordAssistantReply（上下文窗口·非证据）+ 一次模型调用经 wrapModelCall（只读召回）
  //   → evidence 表零新增。给 runtime 带 thread_id 以真触发 recordAssistantReply（证它即便被调用也不落证据）。
  async ingestAssistantTurn(text: string): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const hooks = buildMemoWeftHooks(core);
      const rt = { configurable: { thread_id: 't-ad1' } };
      // 模型调用（只召回、空核 → 无注入、无写）；助手回复经 afterAgent（→ recordAssistantReply，非证据）。
      await hooks.wrapModelCall({ messages: [humanMsg('q')], systemMessage: sysMsg('') }, (r) => {
        void r;
        return aiMsg(text);
      });
      await hooks.afterAgent({ messages: [humanMsg('q'), aiMsg(text)] }, rt);
      return core.memory.listEvidence({}).length - before; // 0 by construction
    } finally {
      core.close();
    }
  },

  // user-ingestion idempotency 幂等：同一 human 消息 id（=originId）经 beforeAgent 触发多次 → put 幂等 → 仍一条。
  async ingestUserTurnIdempotent(text: string, times: number): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const hooks = buildMemoWeftHooks(core);
      for (let i = 0; i < times; i++)
        await hooks.beforeAgent({ messages: [humanMsg(text, 'stable-msg')] }, {});
      return core.memory.listEvidence({}).length - before; // 1
    } finally {
      core.close();
    }
  },

  // tool-result-only ingestion：wrapToolCall 调 handler 拿 ToolMessage 结果 → 只落 result content 为 tool 证据（+1）；
  //   request.toolCall（含 'get_weather' 调用意图/入参）绝不读（tool-result-only ingestion boundary）。originId 用 toolCall.id 保幂等。
  async ingestToolResult(resultPayload: string, callIntent: string): Promise<ToolResultTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const hooks = buildMemoWeftHooks(core);
      // toolCall 带 name/args（调用意图/入参）——证明适配器【不读】它；handler 返回工具真实结果 ToolMessage。
      await hooks.wrapToolCall(
        { toolCall: { id: 'call-1', name: 'get_weather', args: callIntent } },
        (r) => {
          void r;
          return toolMsg(resultPayload);
        },
      );
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
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

  // Recall injection: wrapModelCall adds a system message rendered from the en/zh golden.
  //   隐私：块只用 content/confidence/credStatus，绝不含 id/contentType/score/provenance。
  async recallSurface(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const hooks = buildMemoWeftHooks(fakeRecallCore(fixture), { lang });
    let injected = '';
    await hooks.wrapModelCall(
      { messages: [humanMsg('How should I phrase this?')], systemMessage: sysMsg('') },
      (r) => {
        injected = sysContent(r);
        return aiMsg('ok');
      },
    );
    return {
      kind: 'text-block',
      rendered: injected,
      items: fixture.map((f) => ({
        id: f.id,
        content: f.content,
        confidence: f.confidence,
        credStatus: f.credStatus,
        score: f.score,
      })),
    };
  },

  // recall filtering：带 contentTypes → fakeRecallCore 后过滤 → 适配器透传进 core.recall → onRecall 只收匹配类型项。
  async recallSurfaceFiltered(
    fixture: RecallFixtureItem[],
    contentTypes: string[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    let captured: OnRecallItems = [];
    const hooks = buildMemoWeftHooks(fakeRecallCore(fixture), {
      lang,
      contentTypes: contentTypes as ContentType[],
      onRecall: (items) => {
        captured = items;
      },
    });
    let injected = '';
    await hooks.wrapModelCall(
      { messages: [humanMsg('How should I phrase this?')], systemMessage: sysMsg('') },
      (r) => {
        injected = sysContent(r);
        return aiMsg('ok');
      },
    );
    return {
      kind: 'text-block',
      rendered: injected,
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

  // provenance privacy：带 explain → provenance 经 onRecall 交宿主；断言注入的 system 消息里【不含】任何 provenance summary（隐私保证）。
  async recallSurfaceExplained(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    let captured: OnRecallItems = [];
    const hooks = buildMemoWeftHooks(fakeRecallCore(fixture), {
      lang,
      explain: true,
      onRecall: (items) => {
        captured = items;
      },
    });
    let injected = '';
    await hooks.wrapModelCall(
      { messages: [humanMsg('How should I phrase this?')], systemMessage: sysMsg('') },
      (r) => {
        injected = sysContent(r);
        return aiMsg('ok');
      },
    );
    const provSummaries = captured
      .flatMap((c) => c.provenance ?? [])
      .map((p) => (p as { summary?: unknown }).summary)
      .filter((s): s is string => typeof s === 'string' && s !== '');
    assert.ok(
      provSummaries.length > 0,
      'provenance privacy fixture must carry provenance summaries for the privacy assertion to be meaningful',
    );
    for (const s of provSummaries) {
      assert.ok(
        !injected.includes(s),
        `injected system message must NOT contain provenance summary ( built-in prompt boundary): "${s}"`,
      );
    }
    return {
      kind: 'text-block',
      rendered: injected,
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

  // graceful degradation：故障 core → wrapModelCall 里 recall 抛错/超时（recallTimeoutMs 有界）→ handler 收到【原 request】（不注入）、经 logger 记一条。
  async runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome> {
    const faulty = makeFaultyCore(mode) as unknown as HooksCoreArg;
    const events: Array<{ op?: string }> = [];
    const hooks = buildMemoWeftHooks(faulty, {
      recallTimeoutMs: 50,
      logger: (e) => {
        events.push(e);
      },
    });
    let injected = '';
    await hooks.wrapModelCall({ messages: [humanMsg('q')], systemMessage: sysMsg('') }, (r) => {
      injected = sysContent(r);
      return aiMsg('ok');
    });
    assert.ok(
      events.some((e) => e.op === 'recall'),
      'faulty-core recall degradation must emit a memory_degraded event with op:recall',
    );
    return { degraded: injected === '', logged: events.length > 0 };
  },

  applicability: {
    ad3: {
      status: 'applicable',
      reason:
        'wrapToolCall 调 handler 拿 ToolMessage、只读其 content（工具真实返回结果）→ +1 tool 证据（originId=toolCall.id）；request.toolCall（LLM 调用意图/入参）绝不读（tool-result-only ingestion boundary，代码级 by-construction，tool-result-only ingestion）',
    },
    ad5: {
      status: 'na',
      reason:
        'adapter shape (langchain-mw)：召回走 wrapModelCall 临时注入 systemMessage，写路径由 wrapToolCall 读取工具结果并由 beforeAgent 存用户原话；模型输出不能直接持久化为 evidenceId',
    },
    ad6: {
      status: 'applicable',
      reason:
        'wrapModelCall 里 recall 抛错/超时（recallTimeoutMs 有界）降级为返回原 request（不注入）、写路径失败重试一次仍失败静默吞——都不向 agent 抛，经注入 logger 记一条结构化事件（降级契约覆盖 throw 与 timeout）',
    },
    ad7: {
      status: 'applicable',
      reason:
        'buildMemoWeftHooks 把 opts.contentTypes 透传进 core.recall({contentTypes}) → Core 后过滤 → onRecall 只收到匹配类型项（端到端透传）',
    },
    ad8: {
      status: 'applicable',
      reason:
        'opts.explain 透传进 core.recall({explain}) → Core 附 provenance（含 allowCloudRead/allowInference 授权位）→ 经 onRecall 交宿主；provenance 绝不进注入的 system 消息（隐私加固）',
    },
    ad9: {
      status: 'na',
      reason:
        'mute semantics (langchain-mw)：middleware 不暴露 mute 写口；宿主可直接调用 Core memory.mute，因此本契约项不适用',
    },
  },
};

runAdapterContract(driver, { goldenDir: fileURLToPath(new URL('./golden', import.meta.url)) });
