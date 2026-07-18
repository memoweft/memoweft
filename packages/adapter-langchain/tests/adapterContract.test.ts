/**
 * adapter-langchain · adapter-kit 契约接入（assistant-output exclusion…mute semantics）。
 *
 * 一份 kit（../../../tests/adapter-kit）供多个适配器复用；这里是 LangChain（`@langchain/core`）侧的薄驱动：
 *   - 测试【直接 new MemoWeftRetriever / MemoWeftWriteCallback 调其方法、传入构造对象】——【不跑真实链】、
 *     不触网、不打模型。召回走 `retriever._getRelevantDocuments(query)`（BaseRetriever 的检索主体，
 *     invoke 的内核），写走 `writeCallback.handleToolEnd(output, runId)` 与导出的 `persistUserTurn(core, …)`。
 *   - AD 断言由 runAdapterContract 产出（本文件是 *.test.ts，node --test 直接跑）。
 *
 * 与 A（adapter-ai-sdk）/ openai-agents / claude-agent-sdk 的对照：都不启动宿主运行时、都靠「直接调处理函数 +
 *   传入构造事件」在离线核上断言（同一范式）。本包是 retriever-读 + callback/闭包-写 型（框架行为：LangChain callbacks
 *   是【仅观察】，CallbackManager 丢弃 handler 返回值 → 召回注入不能走 callback，必须走 BaseRetriever/Runnable）。
 *   assistant-output exclusion 验证behavioral boundary，graceful degradation 隔离 recall 降级，provenance privacy 验证 provenance 不进入注入块。
 *
 * tool-result-only ingestion boundary（代码级 by-construction·物理隔离）：③ 写回调【只】实现 `handleToolEnd`（工具真实【返回结果】），
 *   【绝不】声明 `handleToolStart`（它给的是调用意图/入参 string）——LangChain 的 CallbackManager 是
 *   `if (handler.handleToolStart) …` 才投递，本类无此方法 → 调用意图物理上进不来（assistant-output exclusion/tool-result-only ingestion 的behavioral boundary断言证这条）。
 *   `@langchain/core@1.2.2` 类型声明中没有 `handleChatModelEnd`；承载助手输出/调用意图的可选 hook 是
 *   `handleLLMEnd`（模型完成输出）/`handleLLMNewToken`（流式 token）/`handleToolStart`+`handleAgentAction`（调用意图）/
 *   `handleChatModelStart`（携带注入过的模型输入）——assistant-output exclusion 断言这些在写回调实例上一律 `undefined`（未声明）。
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
import {
  MemoWeftRetriever,
  MemoWeftWriteCallback,
  formatMemoWeftDocs,
  persistUserTurn,
  type MemoWeftRetrieverOptions,
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

// MemoWeftRetriever 期望的 core 面（RetrieverCore = Pick<MemoWeftCore,'recall'> 未导出，从构造签名取），fake core 据此 cast。
type RetrieverCoreArg = ConstructorParameters<typeof MemoWeftRetriever>[0];
// onRecall 回调收到的召回项类型（透传的 v2 面：id/contentType/score/provenance）。从公开选项类型提取，
//   recall filtering/8 据此把「透传进 onRecall 的召回对象」当断言源（非驱动自造结果）。
type OnRecallItems = Parameters<NonNullable<MemoWeftRetrieverOptions['onRecall']>>[0];

// ── recall injection, filtering, and provenance privacy 离线 fake recall core：遵循 Core 门面 recall 语义（createCore.ts recall）──
//   contentTypes → 后过滤 fixture（allow 名单）；explain → 逐条附 provenance（证据链带授权位）。
//   据 input 真读选项行事 → 端到端证明适配器把 contentTypes/explain 透传进了 core.recall（非驱动自造结果）。
function fakeRecallCore(fixture: RecallFixtureItem[]): RetrieverCoreArg {
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
        // explain 附 provenance——支撑/反证链，每条含授权位；仅经 onRecall 交宿主（绝不进注入块）。
        if (input.explain && f.provenance) item.provenance = f.provenance;
        return item;
      });
      return items as unknown as RecalledCognition[];
    },
  } as unknown as RetrieverCoreArg;
}

const driver: AdapterDriver = {
  name: 'langchain',

  // user-ingestion idempotency：一轮用户原话经宿主闭包 persistUserTurn(core,{text,originId}) → ingestUserMessage(spoken) 落库 → +1 spoken。
  //   原话由宿主在调用点显式持有并传入（LangChain callbacks 仅用于观察，不从事件重新派生；召回注入落在 prompt 拼装侧，
  //   绝不碰这份原话）→ 存进证据的原话永不含召回注入内容。
  async ingestUserTurn(text: string): Promise<UserTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      await persistUserTurn(core, { text, originId: 'turn-1' });
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

  // assistant-output exclusion：助手消息流经适配器 → 零落库（by-construction）。
  //   守behavioral boundary（遵循 openai assistant-output exclusion，非空断言，to ensure assistant-output exclusion 形同虚设）：本适配器【无助手摄入路径】——写回调的【唯一】
  //   写入口是 handleToolEnd（工具真实返回结果），persistUserTurn 只收宿主显式传入的【用户原话】。承载助手输出/
  //   调用意图的 LangChain 回调 hook（handleLLMEnd 模型完成输出 / handleLLMNewToken 流式 token / handleToolStart+
  //   handleAgentAction 调用意图 / handleChatModelStart 携注入过的模型输入）在写回调实例上一律【未声明】(=undefined)
  //   → CallbackManager 的 `if (handler.handleX)` 便不投递 → 助手侧内容物理上进不来（tool-result-only ingestion boundary）。
  //   若将来误声明了其中任一 hook 并借它落库，此断言即红。handleToolEnd 是【仅】有的写 hook（且只收工具返回结果）。
  async ingestAssistantTurn(_text: string): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const cb = new MemoWeftWriteCallback(core);
      // 唯一写 hook 是 handleToolEnd（工具返回结果专用）。
      assert.equal(
        typeof cb.handleToolEnd,
        'function',
        'write callback must expose handleToolEnd (the only write hook)',
      );
      // 承载助手输出 / 调用意图 / 注入过输入的 hook 一律未声明 → 助手侧内容无入口（tool-result-only ingestion boundary·by-construction）。
      //   清单覆盖 @langchain/core 1.x 中所有会携带助手输出/调用意图的可选 hook（与 base.d.ts 对齐），
      //   含 1.x 新增的 handleChatModelStreamEvent / handleAgentEnd / handleChainEnd / handleText——防日后维护者
      //   给写回调加其一并借它落库、却因清单不全而 assistant-output exclusion 仍绿的回归漏洞。
      for (const hook of [
        'handleToolStart', // 工具调用意图/入参 string（tool-result-only ingestion boundary）
        'handleAgentAction', // agent 选择的动作/入参 = 调用意图
        'handleLLMEnd', // 模型完成输出（助手回话）
        'handleLLMNewToken', // 流式助手 token
        'handleChatModelStart', // 携带注入过的模型输入（从该输入重新派生会污染用户原话）
        'handleChatModelStreamEvent', // 1.x 流式助手正文（ChatModelStreamEvent 的 TextDelta.text）
        'handleAgentEnd', // agent 最终答复（AgentFinish.returnValues）
        'handleChainEnd', // 链输出（对话链常即助手最终消息）
        'handleText', // 任意 text（可含助手输出）
      ] as const) {
        assert.equal(
          typeof (cb as unknown as Record<string, unknown>)[hook],
          'undefined',
          `write callback must NOT declare ${hook} — assistant-output exclusion and tool-result-only ingestion are enforced by construction`,
        );
      }
      // 无助手摄入路径 → evidence 表零新增（by construction）。
      return core.memory.listEvidence({}).length - before; // 0
    } finally {
      core.close();
    }
  },

  // user-ingestion idempotency 幂等：同一轮稳定 originId，persistUserTurn 触发多次 → ingestUserMessage put 幂等去重 → 仍一条。
  async ingestUserTurnIdempotent(text: string, times: number): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      for (let i = 0; i < times; i++)
        await persistUserTurn(core, { text, originId: 'stable-turn' });
      return core.memory.listEvidence({}).length - before;
    } finally {
      core.close();
    }
  },

  // tool-result-only ingestion：writeCallback.handleToolEnd(工具返回结果, runId) → 只落工具返回结果为 tool 证据（+1，originId=runId 保幂等）。
  //   callIntentExcluded：写回调【无 handleToolStart】→ LLM 的工具调用意图/入参根本无入口（tool-result-only ingestion boundary，代码级
  //   by-construction）；此处既断言该 hook 未声明，又断言落库证据里无一条含调用意图标识串（'get_weather'）。
  async ingestToolResult(
    resultPayload: string,
    _callIntent: string,
  ): Promise<ToolResultTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const cb = new MemoWeftWriteCallback(core);
      // tool-result-only ingestion boundary·behavioral boundary：调用意图/入参的入口（handleToolStart）根本不存在 → 意图物理上无从落库。
      assert.equal(
        typeof (cb as unknown as Record<string, unknown>).handleToolStart,
        'undefined',
        'tool-result-only ingestion: call intent has no entry — handleToolStart must not be declared',
      );
      // 只传入工具【返回结果】；runId 作稳定幂等键（handleToolEnd 的第 2 形参，实测 .d.ts 无 tool_call_id 形参）。
      await cb.handleToolEnd(resultPayload, 'run-tool-1');
      const after = core.memory.listEvidence({});
      const added = after.filter((e) => !beforeIds.has(e.id));
      // tool-result-only ingestion boundary：新落库证据里，无一条含调用意图标识串（'get_weather'）——意图从无入口，落库自然不含。
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

  // recall injection：new MemoWeftRetriever(fakeRecallCore(fixture)) → _getRelevantDocuments(query) → Document[] →
  //   formatMemoWeftDocs(docs, lang) 中性注入块（遵循 openai/A 出 en/zh golden）。
  //   隐私：buildKnowledgeBlock 只用 content/confidence/credStatus，块绝不含 id/contentType/score/provenance。
  //   注：LangChain 的呈现面是 formatMemoWeftDocs（= buildKnowledgeBlock 原样，带前导分隔空行）——如实 golden 其原样。
  async recallSurface(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const retriever = new MemoWeftRetriever(fakeRecallCore(fixture), { lang });
    const docs = await retriever._getRelevantDocuments('How should I phrase this?');
    return {
      kind: 'text-block',
      rendered: formatMemoWeftDocs(docs, lang),
      items: fixture.map((f) => ({
        id: f.id,
        content: f.content,
        confidence: f.confidence,
        credStatus: f.credStatus,
        score: f.score,
      })),
    };
  },

  // recall filtering：retriever 带 contentTypes（经构造 opts）→ _getRelevantDocuments → fakeRecallCore 后过滤 →
  //   适配器把选项透传进 core.recall → onRecall 只收到匹配类型项。surface.items 取自 onRecall 捕获对象
  //   （非驱动自造）——contentTypes 端到端透传的直接证据。
  async recallSurfaceFiltered(
    fixture: RecallFixtureItem[],
    contentTypes: string[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    let captured: OnRecallItems = [];
    const retriever = new MemoWeftRetriever(fakeRecallCore(fixture), {
      lang,
      contentTypes: contentTypes as ContentType[],
      onRecall: (items) => {
        captured = items;
      },
    });
    const docs = await retriever._getRelevantDocuments('How should I phrase this?');
    return {
      kind: 'text-block',
      rendered: formatMemoWeftDocs(docs, lang),
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

  // provenance privacy：retriever 带 explain（经构造 opts）→ fakeRecallCore 逐条附 provenance → 适配器透传 explain →
  //   经 onRecall 交宿主。surface.items 取自 onRecall 捕获对象，携带 provenance（含 allowCloudRead/allowInference 授权位）。
  //   privacy boundary：provenance 只走 onRecall，formatMemoWeftDocs/buildKnowledgeBlock 不用它 →
  //   绝不进注入块（此处遵循 openai provenance privacy 断言注入块不含任何 provenance summary）。
  async recallSurfaceExplained(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    let captured: OnRecallItems = [];
    const retriever = new MemoWeftRetriever(fakeRecallCore(fixture), {
      lang,
      explain: true,
      onRecall: (items) => {
        captured = items;
      },
    });
    const docs = await retriever._getRelevantDocuments('How should I phrase this?');
    const rendered = formatMemoWeftDocs(docs, lang);
    // privacy boundary：provenance 的证据原文（summary）绝不进入注入块，只经 onRecall 交宿主筛选。
    //   此处断言注入块不含任何 provenance summary（by-construction：buildKnowledgeBlock 只用 content/confidence/credStatus）。
    //   fixture 必须带 provenance summary 断言才有意义。
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
        !rendered.includes(s),
        `injected knowledge block must NOT contain provenance summary ( built-in prompt boundary): "${s}"`,
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

  // graceful degradation：故障 core → _getRelevantDocuments 降级为【返回 []】（不注入）、经注入 logger 记一条。
  //   读路径只调 recall（写路径 ingest 在 handleToolEnd/persistUserTurn，不在此）→ 本路径只会 emit op:'recall' 事件。
  //   recall 套有界超时（recallTimeoutMs=50）→ throw 立即拒、timeout 由超时器有界赢下，均不真 hang；任何情况都不向链抛。
  //   降级判据：返回的 Document[] 为空 = 降级不注入；logger 记了 ≥1 条结构化事件 = logged。
  async runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome> {
    const faulty = makeFaultyCore(mode) as unknown as RetrieverCoreArg;
    const events: Array<{ op?: string }> = [];
    const retriever = new MemoWeftRetriever(faulty, {
      recallTimeoutMs: 50,
      logger: (e) => {
        events.push(e);
      },
    });
    const docs = await retriever._getRelevantDocuments('q');
    // 隔离 recall 降级（遵循 openai/claude；以避免其他 op 掩盖 recall 日志回归）：断言确有一条 recall 面降级事件。
    assert.ok(
      events.some((e) => e.op === 'recall'),
      'faulty-core recall degradation must emit a memory_degraded event with op:recall',
    );
    return { degraded: Array.isArray(docs) && docs.length === 0, logged: events.length > 0 };
  },

  applicability: {
    ad3: {
      status: 'applicable',
      reason:
        'MemoWeftWriteCallback 只实现 handleToolEnd（工具真实返回结果 → +1 tool 证据，originId=runId）；绝不声明 handleToolStart（LLM 调用意图/入参 string），CallbackManager 便不投递 → 意图物理上进不来（tool-result-only ingestion boundary，代码级 by-construction，tool-result-only ingestion）',
    },
    ad5: {
      status: 'na',
      reason:
        'adapter shape (langchain)：召回走 BaseRetriever 返回 Document[]，写路径由 handleToolEnd 读取工具结果并由 persistUserTurn 存用户原话；模型输出不能直接持久化为 evidenceId',
    },
    ad6: {
      status: 'applicable',
      reason:
        '_getRelevantDocuments 里 recall 抛错/超时（recallTimeoutMs 有界）降级为返回 []（不注入）、写走 runIngestWithRetry 失败重试一次仍失败静默吞——都不向链抛，经注入 logger 记一条结构化事件（降级契约覆盖 throw 与 timeout）',
    },
    ad7: {
      status: 'applicable',
      reason:
        'MemoWeftRetriever 把 opts.contentTypes 透传进 core.recall({contentTypes}) → Core 后过滤 → onRecall 只收到匹配类型项（端到端透传）',
    },
    ad8: {
      status: 'applicable',
      reason:
        'opts.explain 透传进 core.recall({explain}) → Core 附 provenance（含 allowCloudRead/allowInference 授权位）→ 经 onRecall 交宿主；provenance 绝不进 Document.pageContent/metadata，也绝不进 formatMemoWeftDocs 注入块（隐私加固）',
    },
    ad9: {
      status: 'na',
      reason:
        'mute semantics (langchain)：retriever 与 callback 不暴露 mute 写口；宿主可直接调用 Core memory.mute，因此本契约项不适用',
    },
  },
};

runAdapterContract(driver, { goldenDir: fileURLToPath(new URL('./golden', import.meta.url)) });
