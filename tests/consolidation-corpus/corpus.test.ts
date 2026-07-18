/**
 * 固化质量评测语料库 · 结构校验。
 *
 * 这套断言只管【语料结构本身合法、可被真模型评测器消费】——不跑模型、不碰 src。
 * 语料本体在 corpus.json（期望值独立于被测模型生成）。评测器另建，按
 * corpus.json 的锁定结构喂 seed→messages 走真实 updateProfile 固化，再拿 expect 比对。
 *
 * 与既有真模型场景（tests/eval/cognition-discipline.eval.e2e.ts 的 E01~E04）同源：
 * 那 4 条是种子，这里是它的泛化扩充——把 6 条认知纪律各铺 ≥4 个脚本化场景。
 *
 * 校验项对应公开语料约束：
 *   - 覆盖：场景数 ∈[30,50]、id 唯一、每 discipline ≥4、中文 ≥1/3。
 *   - expect 合法：conflict/correct 布尔、newCognitions.min≤max、gists 数组、
 *     chitchat-negative 零新增（max===0）。
 *   - 取值合法：seed/messages 字段结构 + contentType/credStatus/formedBy/sourceKind 落真实模型枚举。
 *
 * 取值枚举锚定 src/cognition/model.ts 与 src/evidence/model.ts（只读对照，不 import 避免耦合）：
 *   ContentType   = fact|preference|goal|project|state|trait|hypothesis|trend
 *   FormedBy      = stated|observed|ruled|confirmed|inferred   ← confirmed 用于标记 AI 命题被用户附和后的形成方式
 *   CredStatus    = candidate|low|limited|stable|conflicted
 *   SourceKind    = spoken|inferred|observed   ← 真实模型无 'tool'；语料按真实写路径对齐
 *   consolidate 能【产出】的新认知类型只有 6 个（见 consolidate.ts VALID_TYPES，无 hypothesis/trend），
 *   故 expect.newCognitions.types 收敛到这 6 个。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// ---- 真实模型枚举（在测试内维护副本，避免反向依赖 src 内部路径）----
const CONTENT_TYPES = [
  'fact',
  'preference',
  'goal',
  'project',
  'state',
  'trait',
  'hypothesis',
  'trend',
];
/** consolidate 实际能产出的新认知类型（VALID_TYPES）——不含 hypothesis/trend。 */
const PRODUCIBLE_TYPES = ['fact', 'preference', 'goal', 'project', 'state', 'trait'];
/** 对齐 src/cognition/model.ts 的 FormedBy 联合。'confirmed' 表示附和 AI 提出的命题
 *  而形成 → 底分 280、封顶 480<limited）；本镜像当初漏更新，导致任何用 confirmed 的 seed 会被误判非法。 */
const FORMED_BY = ['stated', 'observed', 'ruled', 'confirmed', 'inferred'];
const CRED_STATUS = ['candidate', 'low', 'limited', 'stable', 'conflicted'];
/** 对齐 src/interaction/model.ts 的 ResponseAct 与 consolidate.ts 的 VALID_RESPONSE_ACT（非法值会被静默收敛成 null）。 */
const RESPONSE_ACT = ['affirm', 'negate', 'select', 'elaborate', 'ask', 'none', 'other'];
const SOURCE_KIND = ['spoken', 'inferred', 'observed'];
const DISCIPLINES = [
  'conflict',
  'correct',
  'emotion-cap',
  'fact-vs-belief',
  'no-over-inference',
  'chitchat-negative',
  'short-reply',
];
const LANGS = ['zh', 'en', 'mixed'];

interface Expect {
  conflict: boolean;
  correct: boolean;
  /** formedBy（可选）：created 的形成方式允许集；附和应为 confirmed，不应改写为 stated。 */
  newCognitions: { min: number; max: number; types?: string[]; formedBy?: string[] };
  /** resolutions（可选）：语义解析的期望。responseAct 是带 AI 上文原话的解析允许集。 */
  resolutions?: { responseAct?: string[] };
  shouldFormGists: string[];
  shouldNotFormGists: string[];
}
interface Seed {
  content: string;
  contentType: string;
  formedBy: string;
  confidence: number;
  credStatus: string;
}
interface Message {
  sourceKind: string;
  rawContent: string;
  /** AI 前一句（可选）：短回答场景的信息载体——"是"/"后者" 本身零信息，命题在 AI 那句里。
   *  评测器落它进 evidence 的 preceding_ai_context 列 → distill/consolidate 作只读上下文注入（共用真证据 id，
   *  永不铸独立条目，确保助手输出与解析结果都不进入证据链）。 */
  precedingAiContext?: string;
}
interface Scenario {
  id: string;
  discipline: string;
  lang: string;
  title: string;
  seed?: Seed[];
  messages: Message[];
  expect: Expect;
}

const raw = readFileSync(new URL('./corpus.json', import.meta.url), 'utf8');
const corpus = JSON.parse(raw) as { scenarios: Scenario[] };
const scenarios = corpus.scenarios;

const isNonEmptyString = (v: unknown): boolean => typeof v === 'string' && v.trim().length > 0;
const isNonNegInt = (v: unknown): boolean => Number.isInteger(v) && (v as number) >= 0;

// ============================================================================
// 覆盖要求
// ============================================================================

test('顶层结构：scenarios 为数组，场景数 ∈ [30,50]', () => {
  assert.ok(Array.isArray(scenarios), 'scenarios 必须是数组');
  assert.ok(
    scenarios.length >= 30 && scenarios.length <= 50,
    `场景数应 ∈[30,50]，实际 ${scenarios.length}`,
  );
});

test('id 唯一且格式为 CC-###', () => {
  const seen = new Set<string>();
  for (const s of scenarios) {
    assert.ok(isNonEmptyString(s.id), `场景缺 id：${JSON.stringify(s.title)}`);
    assert.match(s.id, /^CC-\d{3}$/, `id 格式应为 CC-###，实际 ${s.id}`);
    assert.ok(!seen.has(s.id), `id 重复：${s.id}`);
    seen.add(s.id);
  }
});

test('每场景 discipline / lang / title 取值合法', () => {
  for (const s of scenarios) {
    assert.ok(DISCIPLINES.includes(s.discipline), `[${s.id}] discipline 非法：${s.discipline}`);
    assert.ok(LANGS.includes(s.lang), `[${s.id}] lang 非法：${s.lang}`);
    assert.ok(isNonEmptyString(s.title), `[${s.id}] title 缺失`);
  }
});

test('每个 discipline 覆盖 ≥4 个场景（六类纪律都不留缺口）', () => {
  const counts: Record<string, number> = {};
  for (const d of DISCIPLINES) counts[d] = 0;
  for (const s of scenarios) counts[s.discipline] = (counts[s.discipline] ?? 0) + 1;
  for (const d of DISCIPLINES) {
    const c = counts[d] ?? 0;
    assert.ok(c >= 4, `discipline "${d}" 只有 ${c} 个（要求 ≥4）`);
  }
});

test('中文场景（lang==="zh"）占比 ≥ 1/3', () => {
  const zh = scenarios.filter((s) => s.lang === 'zh').length;
  assert.ok(zh * 3 >= scenarios.length, `中文占比不足 1/3：${zh}/${scenarios.length}`);
});

// ============================================================================
// expect 字段合法性
// ============================================================================

test('expect.conflict / expect.correct 均为布尔', () => {
  for (const s of scenarios) {
    assert.equal(typeof s.expect?.conflict, 'boolean', `[${s.id}] expect.conflict 非布尔`);
    assert.equal(typeof s.expect?.correct, 'boolean', `[${s.id}] expect.correct 非布尔`);
  }
});

test('expect.newCognitions.{min,max} 为非负整数且 min ≤ max', () => {
  for (const s of scenarios) {
    const n = s.expect?.newCognitions;
    assert.ok(n && typeof n === 'object', `[${s.id}] 缺 newCognitions`);
    assert.ok(isNonNegInt(n.min), `[${s.id}] newCognitions.min 非非负整数：${n.min}`);
    assert.ok(isNonNegInt(n.max), `[${s.id}] newCognitions.max 非非负整数：${n.max}`);
    assert.ok(n.min <= n.max, `[${s.id}] newCognitions.min(${n.min}) > max(${n.max})`);
  }
});

test('expect.newCognitions.types(若有)⊆ consolidate 可产出的 6 类', () => {
  for (const s of scenarios) {
    const types = s.expect?.newCognitions?.types;
    if (types === undefined) continue;
    assert.ok(Array.isArray(types), `[${s.id}] newCognitions.types 应为数组`);
    for (const t of types) {
      assert.ok(PRODUCIBLE_TYPES.includes(t), `[${s.id}] newCognitions.types 含不可产出类型：${t}`);
    }
  }
});

test('shouldFormGists / shouldNotFormGists 为字符串数组', () => {
  for (const s of scenarios) {
    for (const key of ['shouldFormGists', 'shouldNotFormGists'] as const) {
      const arr = s.expect?.[key];
      assert.ok(Array.isArray(arr), `[${s.id}] expect.${key} 应为数组`);
      for (const g of arr) {
        assert.ok(isNonEmptyString(g), `[${s.id}] expect.${key} 含空串`);
      }
    }
  }
});

test('chitchat-negative：零新增认知（newCognitions.max===0）且 shouldFormGists 为空', () => {
  const chit = scenarios.filter((s) => s.discipline === 'chitchat-negative');
  assert.ok(chit.length >= 4, '纯闲聊负例应 ≥4');
  for (const s of chit) {
    assert.equal(s.expect.newCognitions.max, 0, `[${s.id}] 纯闲聊应零新增（max===0）`);
    assert.equal(s.expect.newCognitions.min, 0, `[${s.id}] 纯闲聊 min 也应为 0`);
    assert.equal(s.expect.shouldFormGists.length, 0, `[${s.id}] 纯闲聊不该有 shouldFormGists`);
    assert.ok(
      s.expect.shouldNotFormGists.length >= 1,
      `[${s.id}] 纯闲聊应给出至少一条“不该形成”的负例`,
    );
  }
});

// ============================================================================
// discipline ↔ expect 语义自洽（对齐 consolidated.conflicted/corrected 的真实含义）
// ============================================================================

test('conflict 场景：expect.conflict===true 且 correct===false（暴露不裁决）', () => {
  for (const s of scenarios.filter((s) => s.discipline === 'conflict')) {
    assert.equal(s.expect.conflict, true, `[${s.id}] conflict 场景应期望 conflict=true`);
    assert.equal(s.expect.correct, false, `[${s.id}] conflict 场景不应期望 correct（矛盾非纠正）`);
  }
});

test('correct 场景：expect.correct===true 且 conflict===false（收敛而非挂起）', () => {
  for (const s of scenarios.filter((s) => s.discipline === 'correct')) {
    assert.equal(s.expect.correct, true, `[${s.id}] correct 场景应期望 correct=true`);
    assert.equal(
      s.expect.conflict,
      false,
      `[${s.id}] correct 场景不应期望 conflict（显式纠正非矛盾并存）`,
    );
  }
});

test('非 conflict/correct 纪律：expect.conflict===false 且 correct===false', () => {
  // short-reply 覆盖「信息只在 AI 回复里的短回答」，期望产生新认知且不触发冲突或纠正。
  // negate 在存在旧认知时走 correct 路；此类场景应使用对应 discipline，不能放宽这里的不变量。
  const others = [
    'emotion-cap',
    'fact-vs-belief',
    'no-over-inference',
    'chitchat-negative',
    'short-reply',
  ];
  for (const s of scenarios.filter((s) => others.includes(s.discipline))) {
    assert.equal(s.expect.conflict, false, `[${s.id}] ${s.discipline} 不该期望 conflict`);
    assert.equal(s.expect.correct, false, `[${s.id}] ${s.discipline} 不该期望 correct`);
  }
});

test('conflict / correct 场景必须带非空 seed（否则无旧认知可冲突/可纠正）', () => {
  for (const s of scenarios.filter(
    (s) => s.discipline === 'conflict' || s.discipline === 'correct',
  )) {
    assert.ok(
      Array.isArray(s.seed) && s.seed.length >= 1,
      `[${s.id}] ${s.discipline} 需预置 seed 旧认知`,
    );
  }
});

test('no-over-inference 场景：shouldNotFormGists 非空（防过度推断是重点）', () => {
  const noi = scenarios.filter((s) => s.discipline === 'no-over-inference');
  assert.ok(noi.length >= 4, 'no-over-inference 应 ≥4');
  for (const s of noi) {
    assert.ok(
      s.expect.shouldNotFormGists.length >= 1,
      `[${s.id}] no-over-inference 必须给出该防的过度推断`,
    );
  }
});

// ============================================================================
// seed / messages 字段结构与取值合法性
// ============================================================================

test('seed(若有)：每条字段结构 + 取值合法', () => {
  for (const s of scenarios) {
    if (s.seed === undefined) continue;
    assert.ok(Array.isArray(s.seed), `[${s.id}] seed 应为数组`);
    for (const c of s.seed) {
      assert.ok(isNonEmptyString(c.content), `[${s.id}] seed.content 缺失`);
      assert.ok(
        CONTENT_TYPES.includes(c.contentType),
        `[${s.id}] seed.contentType 非法：${c.contentType}`,
      );
      assert.ok(FORMED_BY.includes(c.formedBy), `[${s.id}] seed.formedBy 非法：${c.formedBy}`);
      assert.ok(
        CRED_STATUS.includes(c.credStatus),
        `[${s.id}] seed.credStatus 非法：${c.credStatus}`,
      );
      assert.ok(
        Number.isInteger(c.confidence) && c.confidence > 0 && c.confidence <= 1000,
        `[${s.id}] seed.confidence 应为 (0,1000] 整数，实际 ${c.confidence}`,
      );
    }
  }
});

test('messages：每场景至少一条，字段结构 + sourceKind 合法', () => {
  for (const s of scenarios) {
    assert.ok(Array.isArray(s.messages) && s.messages.length >= 1, `[${s.id}] messages 至少一条`);
    for (const m of s.messages) {
      assert.ok(
        SOURCE_KIND.includes(m.sourceKind),
        `[${s.id}] messages.sourceKind 非法：${m.sourceKind}`,
      );
      assert.ok(isNonEmptyString(m.rawContent), `[${s.id}] messages.rawContent 缺失`);
    }
  }
});

test('expect.newCognitions.formedBy(若有)⊆ FormedBy 枚举', () => {
  for (const s of scenarios) {
    const fb = s.expect?.newCognitions?.formedBy;
    if (fb === undefined) continue;
    assert.ok(Array.isArray(fb) && fb.length >= 1, `[${s.id}] newCognitions.formedBy 应为非空数组`);
    for (const f of fb) {
      assert.ok(FORMED_BY.includes(f), `[${s.id}] newCognitions.formedBy 含非法来源：${f}`);
    }
  }
});

test('expect.resolutions.responseAct(若有)⊆ ResponseAct 枚举', () => {
  // 与 formedBy 允许集采用相同约束：非法值在 consolidate.ts 会被静默收敛成 null，评测器的允许集则永不满足 →
  //   该断言永久红，而报告只说「越界: null」不说是语料把 'negate' 写成了 'negative'。
  for (const s of scenarios) {
    const ra = s.expect?.resolutions?.responseAct;
    if (ra === undefined) continue;
    assert.ok(
      Array.isArray(ra) && ra.length >= 1,
      `[${s.id}] resolutions.responseAct 应为非空数组`,
    );
    for (const a of ra) {
      assert.ok(RESPONSE_ACT.includes(a), `[${s.id}] resolutions.responseAct 含非法值：${a}`);
    }
  }
});

test('short-reply：每场景至少一条 message 带非空 precedingAiContext', () => {
  // 短回答（"是"/"后者"）自身零信息，命题只活在 AI 那句里。一条 short-reply 场景
  //   若没有 precedingAiContext，模型手里就只有"是"两个字——什么都解析不出来，这条语料测了个寂寞
  //   （且会以「模型没形成认知」的面目判红，把语料缺陷伪装成模型缺陷）。
  for (const s of scenarios.filter((x) => x.discipline === 'short-reply')) {
    const has = s.messages.some((m) => isNonEmptyString(m.precedingAiContext));
    assert.ok(
      has,
      `[${s.id}] short-reply 场景须至少一条 message 带非空 precedingAiContext（信息只在 AI 那句里）`,
    );
  }
});

// ============================================================================
// 汇总打印（integration testing 观察窗，非断言）——给报告用
// ============================================================================

test('汇总打印：总数 / 各 discipline 计数 / 中文占比', () => {
  const counts: Record<string, number> = {};
  for (const d of DISCIPLINES) counts[d] = 0;
  for (const s of scenarios) counts[s.discipline] = (counts[s.discipline] ?? 0) + 1;
  const zh = scenarios.filter((s) => s.lang === 'zh').length;
  console.log('\n===== 固化质量语料库汇总 =====');
  console.log('场景总数:', scenarios.length);
  console.log('各 discipline 计数:', counts);
  console.log(
    `中文场景: ${zh}/${scenarios.length} (${((zh / scenarios.length) * 100).toFixed(1)}%)`,
  );
  console.log('==============================\n');
  assert.ok(true);
});
