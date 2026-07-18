/**
 * adapter-openai-agents · adapter-kit 契约接入（assistant-output exclusion…mute semantics）。
 *
 * 一份 kit（../../../tests/adapter-kit）供多个适配器复用；这里是 OpenAI Agents SDK 侧的薄驱动：
 *   - 测试【直接调 callModelInputFilter / persistToolOutputs / run 包装器的输入捕获】、传入构造的
 *     ModelInputData / RunItem 对象——【不启动真实 SDK】、不触网、不打模型。
 *     （run 包装器的后半会动态 import `@openai/agents` 并跑真实 `run`，那会打模型；故只测它 SDK 无关的那半：
 *      ① 召回注入 = 直接调 `mw.callModelInputFilter`；② 用户原话捕获 = 用导出的 `spokenTextFromRunInput` +
 *      `core.ingestUserMessage`（复刻 runner.ts:334-342 的 ② 步，注入前捕获）；③ 工具结果 = 直接调
 *      `mw.persistToolOutputs`，传入构造的 RunItem。）
 *   - AD 断言由 runAdapterContract 产出（本文件是 *.test.ts，node --test 直接跑）。
 *
 * 与 A（adapter-ai-sdk）/ claude-agent-sdk 的对照：三者都不启动宿主运行时、都靠「直接调处理函数 +
 *   传入构造事件」在离线核上断言（同一范式）。本包是 run-wrapper 型（RunConfig.callModelInputFilter 召回 +
 *   扫 RunResult.newItems 写工具结果），claude 是进程内 hooks 型。assistant-output exclusion 守behavioral boundary / graceful degradation 隔离 recall 降级
 *   并沿用同一组跨适配器契约断言。
 *
 * tool-result-only ingestion boundary（代码级 by-construction）：③ persistToolOutputs 只认 `tool_call_output_item`（工具真实【返回结果】），
 *   只读其 `output` / `rawItem.callId`；`tool_call_item`（LLM 的调用意图/入参）是独立 item 类型，从不进入作用域。
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
  Agent,
  AgentInputItem,
  CallModelInputFilterArgs,
  ModelInputData,
  RunItem,
} from '@openai/agents';
import {
  createMemoWeftRunner,
  spokenTextFromRunInput,
  type MemoWeftRunnerOptions,
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

// createMemoWeftRunner 期望的 core 面（RunnerCore = Pick<MemoWeftCore,'recall'|'ingestUserMessage'|'ingestToolResult'>
//   未导出，从工厂签名取），fake core 据此 cast。
type RunnerCoreArg = Parameters<typeof createMemoWeftRunner>[0];
// onRecall 回调收到的召回项类型（透传的 v2 面：id/contentType/score/provenance）。从公开选项类型提取，
//   recall filtering/8 据此把「透传进 onRecall 的召回对象」当断言源（非驱动自造结果）。
type OnRecallItems = Parameters<NonNullable<MemoWeftRunnerOptions['onRecall']>>[0];

// ── RunItem 构造夹具（传给 persistToolOutputs；adapter 只按 type 分派，非 output 项只需 type 字段）──
/** message_output_item：助手回话——非 output 项，适配器绝不摄入（tool-result-only ingestion boundary）。 */
function messageOutputItem(text: string): RunItem {
  return {
    type: 'message_output_item',
    rawItem: { role: 'assistant', status: 'completed', content: [{ type: 'output_text', text }] },
  } as unknown as RunItem;
}
/** tool_call_item：LLM 的工具【调用意图/入参】（含 'get_weather' 标识串）——非 output 项，适配器绝不读（tool-result-only ingestion boundary）。 */
function toolCallItem(callIntent: string): RunItem {
  return {
    type: 'tool_call_item',
    rawItem: {
      type: 'function_call',
      name: 'get_weather',
      callId: 'call-1',
      arguments: callIntent,
      status: 'completed',
    },
  } as unknown as RunItem;
}
/** reasoning_item：助手推理——非 output 项，适配器绝不摄入。 */
function reasoningItem(text: string): RunItem {
  return {
    type: 'reasoning_item',
    rawItem: { type: 'reasoning', content: text },
  } as unknown as RunItem;
}
/** tool_call_output_item：工具【真实返回结果】——适配器【唯一】摄入的 item 类型（读 output + rawItem.callId）。 */
function toolCallOutputItem(output: string, callId: string): RunItem {
  return {
    type: 'tool_call_output_item',
    output,
    rawItem: {
      type: 'function_call_result',
      name: 'get_weather',
      callId,
      status: 'completed',
      output,
    },
  } as unknown as RunItem;
}

// ── callModelInputFilter 调用夹具 ──
/** 构造一个末条为 user 消息、无 instructions 的 ModelInputData（无 instructions → 注入块无前导空行，对齐 golden）。 */
function modelInputWithUser(prompt: string): ModelInputData {
  return { input: [{ role: 'user', content: prompt } as AgentInputItem] };
}
/** 包成 CallModelInputFilterArgs（recallInject 只读 args.modelData，agent/context 不用，最小 cast）。 */
function filterArgs(modelData: ModelInputData): CallModelInputFilterArgs {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- upstream CallModelInputFilterArgs accepts an Agent with an unconstrained output generic; this fixture never reads it
  return { modelData, agent: {} as unknown as Agent<unknown, any>, context: undefined };
}

/**
 * 复刻 run 包装器的 ② 步（用户原话捕获，runner.ts:334-342）——SDK 无关的那半：
 *   用导出的 `spokenTextFromRunInput` 从 run() 的 input 实参提【注入前】原话 → `core.ingestUserMessage`。
 * 不跑 run 包装器的后半（动态 import `@openai/agents` + 真实 `run`）——本套件不触网、不打模型。
 * originId 即宿主 run 时经 options.memoweft.spokenOriginId 传入的稳定幂等键（此处直传）。
 */
async function ingestSpokenViaWrapperCapture(
  core: RunnerCoreArg,
  input: string | AgentInputItem[],
  spokenOriginId: string | null,
  subjectId?: string,
): Promise<void> {
  const spoken = spokenTextFromRunInput(input);
  if (spoken !== null) {
    await core.ingestUserMessage({ content: spoken, originId: spokenOriginId, subjectId });
  }
}

// ── recall injection, filtering, and provenance privacy 离线 fake recall core：遵循 Core 门面 recall 语义（createCore.ts recall）──
//   contentTypes → 后过滤 fixture（allow 名单）；explain → 逐条附 provenance（证据链带授权位）。
//   据 input 真读选项行事 → 端到端证明适配器把 contentTypes/explain 透传进了 core.recall（非驱动自造结果）。
//   ingestUserMessage/ingestToolResult 是写路径，本组测试只观测召回面 → no-op。
function fakeRecallCore(fixture: RecallFixtureItem[]): RunnerCoreArg {
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
    async ingestUserMessage() {
      return {} as never;
    },
    async ingestToolResult() {
      return {} as never;
    },
  } as unknown as RunnerCoreArg;
}

const driver: AdapterDriver = {
  name: 'openai-agents',

  // user-ingestion idempotency：一轮用户原话经 run 包装器的输入捕获路径（spokenTextFromRunInput + ingestUserMessage）落库 → +1 spoken。
  //   捕获取自【注入前】的 run input 原文（① 注入只落在 instructions，绝不碰 input）→ 存进证据的原话永不含召回注入内容。
  async ingestUserTurn(text: string): Promise<UserTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      await ingestSpokenViaWrapperCapture(core, text, 'turn-1');
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
  //   守behavioral boundary（遵循 claude assistant-output exclusion，非空断言，to ensure assistant-output exclusion 形同虚设）：适配器从 run 结果的【唯一】写入口是
  //   persistToolOutputs，它先按 type 分派——【只】认 type==='tool_call_output_item'（再对其 output 做空判过滤）。
  //   把 SDK 会 emit 的全部助手侧 item——助手消息(message_output_item)、模型的工具调用意图(tool_call_item)、
  //   推理(reasoning_item)——一并传入，无一是 tool_call_output_item → 在【type 分派】这一步即被排除、摄入 0 条。
  //   (本断言证的是 type 分派拦住助手侧 item[tool-result-only ingestion boundary]；output 空判是另一层守卫、非本断言范围。)
  //   若将来 persistToolOutputs 误认了别的 item 类型，此断言即红。
  async ingestAssistantTurn(text: string): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      const mw = createMemoWeftRunner(core);
      const stored = await mw.persistToolOutputs([
        messageOutputItem(text),
        toolCallItem('{"tool":"get_weather","arguments":{"city":"Xiamen"}}'),
        reasoningItem('The user asked about the weather; I will call get_weather.'),
      ]);
      assert.equal(
        stored,
        0,
        'assistant-output exclusion: adapter must ingest none of message_output_item / tool_call_item / reasoning_item — only tool_call_output_item',
      );
      return core.memory.listEvidence({}).length - before; // 0 by construction
    } finally {
      core.close();
    }
  },

  // user-ingestion idempotency 幂等：同一轮稳定 spokenOriginId，输入捕获路径触发多次 → ingestUserMessage put 幂等去重 → 仍一条。
  async ingestUserTurnIdempotent(text: string, times: number): Promise<number> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({}).length;
      for (let i = 0; i < times; i++)
        await ingestSpokenViaWrapperCapture(core, text, 'stable-turn');
      return core.memory.listEvidence({}).length - before;
    } finally {
      core.close();
    }
  },

  // tool-result-only ingestion：persistToolOutputs 传入 newItems = [tool_call_item(调用意图/入参), tool_call_output_item(结果)] →
  //   只落工具返回结果为 tool 证据（+1）；tool_call_item（含 'get_weather' 意图/入参）根本不被读（tool-result-only ingestion boundary，代码级 by-construction）。
  async ingestToolResult(resultPayload: string, callIntent: string): Promise<ToolResultTurnResult> {
    const core = makeCore();
    try {
      const before = core.memory.listEvidence({});
      const beforeIds = new Set(before.map((e) => e.id));
      const mw = createMemoWeftRunner(core);
      // 次序刻意「意图在前、结果在后」：证明适配器不因位置、只因 type 分派——只有 tool_call_output_item 落库。
      await mw.persistToolOutputs([
        toolCallItem(callIntent),
        toolCallOutputItem(resultPayload, 'call-1'),
      ]);
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

  // recall injection：callModelInputFilter 传入末条 user 的 ModelInputData → 召回 → 返回 ModelInputData.instructions 即注入的知识块（沿用参考适配器的 en/zh golden）。
  //   无入 instructions → 注入块 = buildKnowledgeBlock 去前导空行，与 claude/A golden 内容逐字对齐。
  //   隐私：buildKnowledgeBlock 只用 content/confidence/credStatus，instructions 绝不含 id/contentType/score/provenance。
  async recallSurface(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture);
    const mw = createMemoWeftRunner(core, { lang });
    const out = await mw.callModelInputFilter(
      filterArgs(modelInputWithUser('How should I phrase this?')),
    );
    return {
      kind: 'text-block',
      rendered: out.instructions ?? '',
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
    const mw = createMemoWeftRunner(core, {
      lang,
      contentTypes: contentTypes as ContentType[],
      onRecall: (items) => {
        captured = items;
      },
    });
    const out = await mw.callModelInputFilter(
      filterArgs(modelInputWithUser('How should I phrase this?')),
    );
    return {
      kind: 'text-block',
      rendered: out.instructions ?? '',
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
  //   surface.items 取自 onRecall 捕获对象，携带 provenance（含 allowCloudRead/allowInference 授权位）供断言。
  //   隐私：provenance 只走 onRecall，buildKnowledgeBlock 不用它 → 绝不进注入 instructions。
  async recallSurfaceExplained(
    fixture: RecallFixtureItem[],
    lang: 'en' | 'zh' = 'en',
  ): Promise<RecallSurface> {
    const core = fakeRecallCore(fixture);
    let captured: OnRecallItems = [];
    const mw = createMemoWeftRunner(core, {
      lang,
      explain: true,
      onRecall: (items) => {
        captured = items;
      },
    });
    const out = await mw.callModelInputFilter(
      filterArgs(modelInputWithUser('How should I phrase this?')),
    );
    const rendered = out.instructions ?? '';
    // privacy boundary：provenance 的证据原文（summary）绝不进入注入内容，
    //   instructions——只经 onRecall 交宿主自筛。此处断言注入块不含任何 provenance summary(by-construction:
    //   buildKnowledgeBlock 只用 content/confidence/credStatus)。fixture 必须带 provenance summary 断言才有意义。
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
        `injected instructions must NOT contain provenance summary ( built-in prompt boundary): "${s}"`,
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

  // graceful degradation：故障 core → callModelInputFilter 降级为【原样返回】（不注入）、经注入 logger 记一条。
  //   filter 只调 recall（写路径的 ingest 在 run 包装器里，不在 filter）→ 本路径只会 emit op:'recall' 事件。
  //   recall 套有界超时（recallTimeoutMs=50）→ throw 立即拒、timeout 由超时器有界赢下，均不真 hang；任何情况都不向 SDK 外抛。
  //   降级判据：返回的 ModelInputData 无注入的 instructions（原样）= 降级；logger 记了 ≥1 条结构化事件 = logged。
  async runWithFaultyCore(mode: FaultMode): Promise<FaultOutcome> {
    const faulty = makeFaultyCore(mode) as unknown as RunnerCoreArg;
    const events: Array<{ op?: string }> = [];
    const mw = createMemoWeftRunner(faulty, {
      recallTimeoutMs: 50,
      logger: (e) => {
        events.push(e);
      },
    });
    const out = await mw.callModelInputFilter(filterArgs(modelInputWithUser('q')));
    // 隔离 recall 降级（遵循 claude；以避免其他 op 掩盖 recall 日志回归）：断言确有一条 recall 面降级事件。
    assert.ok(
      events.some((e) => e.op === 'recall'),
      'faulty-core recall degradation must emit a memory_degraded event with op:recall',
    );
    return { degraded: out.instructions === undefined, logged: events.length > 0 };
  },

  applicability: {
    ad3: {
      status: 'applicable',
      reason:
        'persistToolOutputs 只筛 type===tool_call_output_item、只读其 output/rawItem.callId → 落 +1 tool 证据；tool_call_item（LLM 调用意图/入参）是独立 item 类型，绝不解构/引用（tool-result-only ingestion boundary，代码级 by-construction，tool-result-only ingestion）',
    },
    ad5: {
      status: 'na',
      reason:
        'adapter shape (openai-agents)：召回注入走 callModelInputFilter 编辑 instructions，写路径只读取 RunResult.newItems 的 tool_call_output_item；模型输出不能直接持久化为 evidenceId',
    },
    ad6: {
      status: 'applicable',
      reason:
        'callModelInputFilter 里 recall 抛错/超时（recallTimeoutMs 有界）降级为原样返回不注入、run 包装器 ingest 失败重试一次仍失败静默吞——都不向 SDK 抛，经注入 logger 记一条结构化事件（降级契约覆盖 throw 与 timeout）',
    },
    ad7: {
      status: 'applicable',
      reason:
        'createMemoWeftRunner 把 opts.contentTypes 透传进 core.recall({contentTypes}) → Core 后过滤 → onRecall 只收到匹配类型项（端到端透传）',
    },
    ad8: {
      status: 'applicable',
      reason:
        'opts.explain 透传进 core.recall({explain}) → Core 附 provenance（含 allowCloudRead/allowInference 授权位）→ 经 onRecall 交宿主；provenance 绝不进注入 instructions（隐私加固）',
    },
    ad9: {
      status: 'na',
      reason:
        'mute semantics (openai-agents)：run-wrapper 只经 callModelInputFilter 召回并从 newItems 写入工具结果，不暴露 mute 写口；宿主可直接调用 Core memory.mute，因此本契约项不适用',
    },
  },
};

runAdapterContract(driver, { goldenDir: fileURLToPath(new URL('./golden', import.meta.url)) });
