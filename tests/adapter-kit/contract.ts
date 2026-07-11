/**
 * adapter-kit · 参数化契约套件（AD-1…AD-6）。
 *
 * 用法（各适配器包的接入测试里）：
 *   import { runAdapterContract } from '../../../tests/adapter-kit/contract.ts';
 *   runAdapterContract(myDriver, { goldenDir: join(import.meta.dirname, 'golden') });
 *
 * 本文件【不是】独立测试（文件名非 *.test.ts）：AD 断言由各适配器的接入测试驱动，
 * 不被 core 根 `npm test` 的 test-glob 误跑（该 glob 只收 tests 下的 *.test.ts）。
 *
 * 本轮打绿：AD-1（助手→0）、AD-2（用户→+1，含 A 的 originId 幂等）。
 * baseline 快照：AD-4（当前召回呈现格式，含一条 conflicted 项）。
 * N/A 声明位：AD-3（无 tool 入口 / SourceKind 无 'tool'）、AD-5（无 LLM→evidenceId 回捞）、
 *             AD-6 的超时/logger 部分（超时/日志属后续契约）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import type { AdapterDriver } from './spi.ts';
import { RECALL_FIXTURE } from './spi.ts';
import { matchGolden } from './goldens.ts';

export interface ContractOptions {
  /** golden 快照落盘目录（各适配器传自己的 tests/golden 绝对路径）。 */
  goldenDir: string;
}

/** 挂一整套 AD-1…AD-6 测试到当前 node:test 运行。由各适配器接入测试调用。 */
export function runAdapterContract(driver: AdapterDriver, opts: ContractOptions): void {
  const tag = (ad: string, status: 'applicable' | 'na', title: string) =>
    `[${driver.name}] ${ad} [${status}] ${title}`;

  // ── AD-1：助手消息流经 → evidence 零新增 ────────────────────────────────
  test(tag('AD-1', 'applicable', '助手消息流经适配器 → evidence 表零新增'), async () => {
    const delta = await driver.ingestAssistantTurn('Great, here is a Rust roadmap for you...');
    assert.equal(delta, 0, 'AD-1：助手侧内容不得落任何证据');
  });

  // ── AD-2：用户消息 → 恰好 +1 spoken ────────────────────────────────────
  test(tag('AD-2', 'applicable', '用户消息 → 恰好一条 spoken 证据'), async () => {
    const { delta, sourceKind, content } = await driver.ingestUserTurn('I want to learn Rust');
    assert.equal(delta, 1, 'AD-2：恰好新增一条证据');
    if (sourceKind !== undefined) assert.equal(sourceKind, 'spoken', 'AD-2：用户消息存为 spoken');
    if (content !== undefined) assert.equal(content, 'I want to learn Rust', 'AD-2：存的是用户原话');
  });

  // ── AD-2 幂等（可选，A 专属：onEnd 多次触发仍一条）───────────────────────
  const idempotent = driver.ingestUserTurnIdempotent?.bind(driver);
  if (idempotent) {
    test(tag('AD-2', 'applicable', '幂等：稳定 originId 多次触发仍恰好一条'), async () => {
      const delta = await idempotent('I want to learn Rust', 3);
      assert.equal(delta, 1, 'AD-2 幂等：同 originId 触发 3 次仍只落一条');
    });
  }

  // ── AD-3：工具结果 → source=tool（本轮 N/A）────────────────────────────
  test(tag('AD-3', driver.applicability.ad3.status, driver.applicability.ad3.reason), () => {
    assert.equal(driver.applicability.ad3.status, 'na', 'AD-3 本轮 N/A：SourceKind 无 tool 值（契约冻结）');
    assert.ok(driver.applicability.ad3.reason.length > 0, 'N/A 须声明理由');
  });

  // ── AD-4：召回呈现格式 golden 快照（baseline）──────────────────────────
  test(tag('AD-4', 'applicable', 'recall 呈现含置信度/冲突状态，锁 golden'), async () => {
    const en = await driver.recallSurface(RECALL_FIXTURE, 'en');
    // 结构不变量（不依赖 golden）：含 conflicted 项、条数一致。
    assert.ok(en.items.some((i) => i.credStatus === 'conflicted'), 'AD-4：召回如实带出 conflicted 项');
    assert.equal(en.items.length, RECALL_FIXTURE.length, 'AD-4：召回项条数一致');

    if (en.kind === 'text-block') {
      const gEn = matchGolden(join(opts.goldenDir, `ad4-${driver.name}-en.txt`), en.rendered);
      if (!gEn.created) assert.equal(en.rendered, gEn.expected, 'AD-4：en 文本块格式对齐 golden');
      const zh = await driver.recallSurface(RECALL_FIXTURE, 'zh');
      const gZh = matchGolden(join(opts.goldenDir, `ad4-${driver.name}-zh.txt`), zh.rendered);
      if (!gZh.created) assert.equal(zh.rendered, gZh.expected, 'AD-4：zh 文本块格式对齐 golden');
    } else {
      const g = matchGolden(join(opts.goldenDir, `ad4-${driver.name}.json`), en.rendered);
      if (!g.created) assert.equal(en.rendered, g.expected, 'AD-4：structuredContent 形状对齐 golden');
    }
  });

  // ── AD-5：LLM 虚构 evidenceId 被丢弃（本轮 N/A）────────────────────────
  test(tag('AD-5', driver.applicability.ad5.status, driver.applicability.ad5.reason), () => {
    assert.equal(driver.applicability.ad5.status, 'na', 'AD-5 本轮 N/A：无 LLM 输出→evidenceId 回捞落库路径');
    assert.ok(driver.applicability.ad5.reason.length > 0, 'N/A 须声明理由');
  });

  // ── AD-6：记忆层故障 → 降级 + logger ──────────────────────────────────
  if (driver.applicability.ad6.status === 'applicable') {
    test(tag('AD-6', 'applicable', '记忆层抛错 → 降级「无记忆但对话不中断」'), async () => {
      const out = await driver.runWithFaultyCore('throw');
      assert.equal(out.degraded, true, 'AD-6：抛错时降级、不中断');
    });
    // 超时 / logger 部分本轮 N/A：无适配器层超时面、无注入 logger（属后续契约 §21.3）。
    test(tag('AD-6', 'na', 'logger 注入面待契约（超时/日志属后续）'), async () => {
      const out = await driver.runWithFaultyCore('throw');
      assert.equal(out.logged, false, 'AD-6：本轮无 logger 注入面');
    });
  } else {
    test(tag('AD-6', 'na', driver.applicability.ad6.reason), () => {
      assert.equal(driver.applicability.ad6.status, 'na');
      assert.ok(driver.applicability.ad6.reason.length > 0, 'N/A 须声明理由');
    });
  }
}
