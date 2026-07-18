/**
 * adapter-kit 参数化契约套件。
 *
 * 用法（各适配器包的接入测试里）：
 *   import { runAdapterContract } from '../../../tests/adapter-kit/contract.ts';
 *   runAdapterContract(myDriver, { goldenDir: join(import.meta.dirname, 'golden') });
 *
 * 本文件不是独立测试（文件名非 *.test.ts）：契约断言由各适配器的接入测试驱动，
 * 不被 core 根 `npm test` 的 test-glob 误跑（该 glob 只收 tests 下的 *.test.ts）。
 *
 * Contract coverage: assistant-isolation（助手→0）、user-ingest（用户→+1，含 originId 幂等）。
 * tool-result-ingest：两适配器均 applicable——工具返回结果 → 恰好一条 tool 证据，
 *             且助手生成的工具调用意图/入参不落库。
 * baseline 快照：recall-rendering（当前召回呈现格式，含一条 conflicted 项）。
 * degradation（降级契约）：两适配器均 applicable——记忆层抛错 / 召回超时 → 降级「无记忆但对话不中断」
 *             + 经注入 logger 记一条结构化事件（throw / timeout 两模式都实际执行）。
 * 扩展召回：content-type-filter 验证 contentTypes 透传；provenance 验证解释链授权位；
 *             mute-recall 验证 mute 后召回消失且 confidence 不变。
 * N/A 声明位：fabricated-id（不存在从 LLM 输出写入 evidenceId 的路径）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import type { AdapterDriver } from './spi.ts';
import { RECALL_FIXTURE, TOOL_RESULT_FIXTURE, TOOL_CALL_INTENT_FIXTURE } from './spi.ts';
import { matchGolden } from './goldens.ts';

export interface ContractOptions {
  /** golden 快照落盘目录（各适配器传自己的 tests/golden 绝对路径）。 */
  goldenDir: string;
}

/** 将完整适配器契约挂到当前 node:test 运行；由各适配器接入测试调用。 */
export function runAdapterContract(driver: AdapterDriver, opts: ContractOptions): void {
  const tag = (ad: string, status: 'applicable' | 'na', title: string) =>
    `[${driver.name}] ${ad} [${status}] ${title}`;

  // ── assistant-isolation：助手消息流经 → evidence 零新增 ────────────────────────────────
  test(
    tag('assistant-isolation', 'applicable', '助手消息流经适配器 → evidence 表零新增'),
    async () => {
      const delta = await driver.ingestAssistantTurn('Great, here is a Rust roadmap for you...');
      assert.equal(delta, 0, 'assistant-isolation：助手侧内容不得落任何证据');
    },
  );

  // ── user-ingest：用户消息 → 恰好 +1 spoken ────────────────────────────────────
  test(tag('user-ingest', 'applicable', '用户消息 → 恰好一条 spoken 证据'), async () => {
    const { delta, sourceKind, content } = await driver.ingestUserTurn('I want to learn Rust');
    assert.equal(delta, 1, 'user-ingest：恰好新增一条证据');
    if (sourceKind !== undefined)
      assert.equal(sourceKind, 'spoken', 'user-ingest：用户消息存为 spoken');
    if (content !== undefined)
      assert.equal(content, 'I want to learn Rust', 'user-ingest：存的是用户原话');
  });

  // ── user-ingest 幂等（可选，A 专属：onEnd 多次触发仍一条）───────────────────────
  const idempotent = driver.ingestUserTurnIdempotent?.bind(driver);
  if (idempotent) {
    test(tag('user-ingest', 'applicable', '幂等：稳定 originId 多次触发仍恰好一条'), async () => {
      const delta = await idempotent('I want to learn Rust', 3);
      assert.equal(delta, 1, 'user-ingest 幂等：同 originId 触发 3 次仍只落一条');
    });
  }

  // ── tool-result-ingest：工具结果 → source=tool，助手生成的调用意图不落库 ────────────
  if (driver.applicability.ad3.status === 'applicable') {
    const ingestTool = driver.ingestToolResult?.bind(driver);
    assert.ok(ingestTool, 'tool-result-ingest applicable 的适配器必须实现 ingestToolResult 驱动');
    test(
      tag(
        'tool-result-ingest',
        'applicable',
        '工具结果 → 恰好一条 tool 证据；助手生成的调用意图/入参不落库',
      ),
      async () => {
        const r = await ingestTool!(TOOL_RESULT_FIXTURE, TOOL_CALL_INTENT_FIXTURE);
        assert.equal(r.delta, 1, 'tool-result-ingest：恰好新增一条证据（只有工具返回结果落库）');
        if (r.sourceKind !== undefined)
          assert.equal(r.sourceKind, 'tool', 'tool-result-ingest：工具结果存为 tool 证据');
        if (r.content !== undefined)
          assert.equal(
            r.content,
            TOOL_RESULT_FIXTURE,
            'tool-result-ingest：存的是工具返回结果原文',
          );
        assert.equal(
          r.callIntentExcluded,
          true,
          'tool-result-ingest：助手生成的工具调用意图/入参未落成证据',
        );
      },
    );
  } else {
    test(tag('tool-result-ingest', 'na', driver.applicability.ad3.reason), () => {
      assert.equal(driver.applicability.ad3.status, 'na');
      assert.ok(driver.applicability.ad3.reason.length > 0, 'N/A 须声明理由');
    });
  }

  // ── recall-rendering：召回呈现格式 golden 快照（baseline）──────────────────────────
  test(
    tag('recall-rendering', 'applicable', 'recall 呈现含置信度/冲突状态，锁 golden'),
    async () => {
      const en = await driver.recallSurface(RECALL_FIXTURE, 'en');
      // 结构不变量（不依赖 golden）：含 conflicted 项、条数一致。
      assert.ok(
        en.items.some((i) => i.credStatus === 'conflicted'),
        'recall-rendering：召回如实带出 conflicted 项',
      );
      assert.equal(en.items.length, RECALL_FIXTURE.length, 'recall-rendering：召回项条数一致');

      if (en.kind === 'text-block') {
        const gEn = matchGolden(join(opts.goldenDir, `ad4-${driver.name}-en.txt`), en.rendered);
        if (!gEn.created)
          assert.equal(en.rendered, gEn.expected, 'recall-rendering：en 文本块格式对齐 golden');
        const zh = await driver.recallSurface(RECALL_FIXTURE, 'zh');
        const gZh = matchGolden(join(opts.goldenDir, `ad4-${driver.name}-zh.txt`), zh.rendered);
        if (!gZh.created)
          assert.equal(zh.rendered, gZh.expected, 'recall-rendering：zh 文本块格式对齐 golden');
      } else {
        const g = matchGolden(join(opts.goldenDir, `ad4-${driver.name}.json`), en.rendered);
        if (!g.created)
          assert.equal(
            en.rendered,
            g.expected,
            'recall-rendering：structuredContent 形状对齐 golden',
          );
      }
    },
  );

  // ── fabricated-id：LLM 虚构 evidenceId 被丢弃（当前适配器 N/A）────────────────────────
  test(
    tag('fabricated-id', driver.applicability.ad5.status, driver.applicability.ad5.reason),
    () => {
      assert.equal(
        driver.applicability.ad5.status,
        'na',
        'fabricated-id 本轮 N/A：不存在从 LLM 输出写入 evidenceId 的路径',
      );
      assert.ok(driver.applicability.ad5.reason.length > 0, 'N/A 须声明理由');
    },
  );

  // ── degradation：记忆层故障 → 降级 + logger（降级契约）─────────────────────
  if (driver.applicability.ad6.status === 'applicable') {
    // 抛错：记忆层内部故障 → 降级「无记忆但对话不中断」+ 经注入 logger 记一条。
    test(
      tag(
        'degradation',
        'applicable',
        '记忆层抛错 → 降级「无记忆但对话不中断」+ 注入 logger 记一条',
      ),
      async () => {
        const out = await driver.runWithFaultyCore('throw');
        assert.equal(out.degraded, true, 'degradation：抛错时降级、不中断');
        assert.equal(out.logged, true, 'degradation：降级经注入 logger 记一条结构化事件');
      },
    );
    // 超时：recall 超阈（默认 200ms）视为失败 → 同样降级 + 记一条（timeout 模式由适配器超时器有界赢下）。
    test(
      tag(
        'degradation',
        'applicable',
        '记忆层召回超时 → 降级「无记忆但对话不中断」+ 注入 logger 记一条',
      ),
      async () => {
        const out = await driver.runWithFaultyCore('timeout');
        assert.equal(out.degraded, true, 'degradation：召回超时时降级、不中断');
        assert.equal(out.logged, true, 'degradation：超时降级经注入 logger 记一条结构化事件');
      },
    );
  } else {
    test(tag('degradation', 'na', driver.applicability.ad6.reason), () => {
      assert.equal(driver.applicability.ad6.status, 'na');
      assert.ok(driver.applicability.ad6.reason.length > 0, 'N/A 须声明理由');
    });
  }

  // ── content-type-filter：contentTypes 过滤端到端透传──────────────────
  if (driver.applicability.ad7.status === 'applicable') {
    const filtered = driver.recallSurfaceFiltered?.bind(driver);
    assert.ok(
      filtered,
      'content-type-filter applicable 的适配器必须实现 recallSurfaceFiltered 驱动',
    );
    test(
      tag('content-type-filter', 'applicable', 'contentTypes 过滤透传 → 召回项全部为请求类型'),
      async () => {
        // 选一个夹具里确实存在的 contentType；夹具含 3 个不同类型，按其一过滤应挡掉其它。
        const want = 'preference';
        assert.ok(
          RECALL_FIXTURE.some((f) => f.contentType === want),
          'content-type-filter 前置：夹具须含该 contentType',
        );
        const surface = await filtered!(RECALL_FIXTURE, [want], 'en');
        assert.ok(
          surface.items.length > 0,
          'content-type-filter：按存在的类型过滤应召回到至少一项',
        );
        for (const it of surface.items) {
          assert.equal(
            it.contentType,
            want,
            'content-type-filter：contentTypes 过滤端到端透传——返回项全部为请求类型',
          );
        }
      },
    );
  } else {
    test(tag('content-type-filter', 'na', driver.applicability.ad7.reason), () => {
      assert.equal(driver.applicability.ad7.status, 'na');
      assert.ok(driver.applicability.ad7.reason.length > 0, 'N/A 须声明理由');
    });
  }

  // ── provenance：explain 带出 provenance 含授权位（隐私加固）──────
  if (driver.applicability.ad8.status === 'applicable') {
    const explained = driver.recallSurfaceExplained?.bind(driver);
    assert.ok(explained, 'provenance applicable 的适配器必须实现 recallSurfaceExplained 驱动');
    test(
      tag(
        'provenance',
        'applicable',
        'explain 召回带出 provenance，每条含 allowCloudRead/allowInference 授权位',
      ),
      async () => {
        const surface = await explained!(RECALL_FIXTURE, 'en');
        const withProv = surface.items.filter(
          (i) => Array.isArray(i.provenance) && i.provenance!.length > 0,
        );
        assert.ok(withProv.length > 0, 'provenance：explain 时至少一项带 provenance');
        for (const it of withProv) {
          for (const p of it.provenance!) {
            assert.ok(
              typeof p.evidenceId === 'string' && p.evidenceId.length > 0,
              'provenance：provenance 元素带 evidenceId',
            );
            assert.equal(
              typeof p.allowCloudRead,
              'boolean',
              'provenance：provenance 元素带 allowCloudRead 授权位',
            );
            assert.equal(
              typeof p.allowInference,
              'boolean',
              'provenance：provenance 元素带 allowInference 授权位',
            );
          }
        }
      },
    );
  } else {
    test(tag('provenance', 'na', driver.applicability.ad8.reason), () => {
      assert.equal(driver.applicability.ad8.status, 'na');
      assert.ok(driver.applicability.ad8.reason.length > 0, 'N/A 须声明理由');
    });
  }

  // ── mute-recall：静音后退出召回且 confidence 不变；结构化召回适配器适用 ──
  if (driver.applicability.ad9.status === 'applicable') {
    const muteAndRecall = driver.muteAndRecall?.bind(driver);
    assert.ok(muteAndRecall, 'mute-recall applicable 的适配器必须实现 muteAndRecall 驱动');
    test(
      tag(
        'mute-recall',
        'applicable',
        'mute 某认知 → 该 id 不再被召回、其它项仍在；mute 不改变 confidence',
      ),
      async () => {
        const muteId = RECALL_FIXTURE[0]!.id; // mute 第一项
        const others = RECALL_FIXTURE.filter((f) => f.id !== muteId).map((f) => f.id);
        const out = await muteAndRecall!(RECALL_FIXTURE, muteId);
        assert.ok(
          !out.recalledIds.includes(muteId),
          'mute-recall：被 mute 的认知不再出现在召回结果',
        );
        for (const id of others) {
          assert.ok(out.recalledIds.includes(id), `mute-recall：未 mute 的认知（${id}）仍被召回`);
        }
        // 静音只改变召回资格；若驱动带回前后置信度，两者必须严格相等。
        if (out.mutedConfidenceBefore !== undefined && out.mutedConfidenceAfter !== undefined) {
          assert.equal(
            out.mutedConfidenceAfter,
            out.mutedConfidenceBefore,
            'mute-recall：mute 不改变被静音项的 confidence',
          );
        }
      },
    );
  } else {
    test(tag('mute-recall', 'na', driver.applicability.ad9.reason), () => {
      assert.equal(driver.applicability.ad9.status, 'na');
      assert.ok(driver.applicability.ad9.reason.length > 0, 'N/A 须声明理由');
    });
  }
}
