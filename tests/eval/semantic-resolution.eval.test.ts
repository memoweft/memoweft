/**
 * 语义解析 resolver（v0.6 Phase 2·D-0034）—— 确定性 + 结构墙。
 *
 * consolidate 对每条真证据落一份 semantic_resolution（resolvedContent + response_act / prompt_act /
 * proposition_origin / assertion_strength）。用 scripted stub LLM（返回带 resolutions 的 JSON）确定性驱动，
 * 断言字段落对 + 幂等 + 非法枚举收敛 null。**核心自证（3a/3d）**：resolved_content 是解释、不是证据——
 * 永不进 consolidate 的 support 白名单；伪造 / AI-上文 evidence_id 结构性被丢弃。
 *
 * Phase 2 边界：resolver 只 produce+store，**不碰 formedBy**（那是 Phase 3）。全离线（stub LLM），进 npm test。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openStores } from '../../src/store/openStores.ts';
import { consolidate } from '../../src/consolidation/consolidate.ts';

/** stub LLM：每次 chat 返回固定 JSON 文本。 */
function stubReturning(json: string) {
  return { callCount: 0, async chat() { this.callCount++; return json; } };
}

/** seed 一条 spoken 证据 + 覆盖它的未消化事件（consolidate 从事件取 utterances）。 */
function seedUtterance(s: ReturnType<typeof openStores>, rawContent: string, precedingAiContext?: string) {
  const e = s.evidenceStore.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent, precedingAiContext });
  s.eventStore.put({ subjectId: 'u', summary: 'evt', occurredAt: e.occurredAt, evidenceIds: [e.id] });
  return e;
}

const CASES = [
  { name: '是 → affirm/explicit', preceding: '你喜欢爬山吗?', raw: '是', resolved: '用户确认喜欢爬山',
    res: { response_act: 'affirm', prompt_act: 'propose', proposition_origin: 'assistant_proposed', assertion_strength: 'explicit' } },
  { name: '不是 → negate', preceding: '你是素食者吧?', raw: '不是', resolved: '用户否认是素食者',
    res: { response_act: 'negate', prompt_act: 'propose', proposition_origin: 'assistant_proposed', assertion_strength: 'explicit' } },
  { name: '后者 → select', preceding: '喝茶还是咖啡?', raw: '后者', resolved: '用户选咖啡',
    res: { response_act: 'select', prompt_act: 'ask', proposition_origin: 'assistant_proposed', assertion_strength: 'explicit' } },
  { name: '可能吧 → weak', preceding: '你偏内向?', raw: '可能吧', resolved: '用户含糊认可偏内向',
    res: { response_act: 'affirm', prompt_act: 'propose', proposition_origin: 'assistant_proposed', assertion_strength: 'weak' } },
  { name: '澄清 → elaborate/user_stated', preceding: '你做游戏?', raw: '像素风独立游戏', resolved: '用户澄清在做像素风独立游戏',
    res: { response_act: 'elaborate', prompt_act: 'ask', proposition_origin: 'user_stated', assertion_strength: 'explicit' } },
];

for (const c of CASES) {
  test(`resolver：${c.name}`, async () => {
    const s = openStores(':memory:');
    try {
      const e = seedUtterance(s, c.raw, c.preceding);
      const stub = stubReturning(JSON.stringify({
        new: [{ content: '某认知', content_type: 'preference', formed_by: 'confirmed', support_evidence_ids: [e.id] }],
        resolutions: [{ evidence_id: e.id, resolved_content: c.resolved, ...c.res }],
      }));
      await consolidate('u', { eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore, semanticResolutionStore: s.semanticResolutionStore, transaction: s.transaction, llm: stub });
      const r = s.semanticResolutionStore.ofEvidence(e.id);
      assert.ok(r, '落了 resolution');
      assert.equal(r.resolvedContent, c.resolved);
      assert.equal(r.responseAct, c.res.response_act);
      assert.equal(r.promptAct, c.res.prompt_act);
      assert.equal(r.propositionOrigin, c.res.proposition_origin);
      assert.equal(r.assertionStrength, c.res.assertion_strength);
      assert.match(r.resolverVersion, /^consolidate@/, 'resolverVersion 绑 prompt 版本');
    } finally {
      s.close();
    }
  });
}

test('结构墙（3a/3d）：伪造 evidence_id 的 resolution 被丢弃；resolved_content 永不进 support 白名单', async () => {
  const s = openStores(':memory:');
  try {
    const e = seedUtterance(s, '是', '你喜欢爬山吗?');
    const stub = stubReturning(JSON.stringify({
      new: [{ content: '用户喜欢爬山', content_type: 'preference', formed_by: 'confirmed', support_evidence_ids: [e.id] }],
      resolutions: [
        { evidence_id: e.id, resolved_content: '用户确认喜欢爬山', response_act: 'affirm' },
        { evidence_id: 'fake-ai-context-id', resolved_content: 'AI 编造的命题', response_act: 'affirm' }, // 伪造 id（模拟 AI 上文冒充证据）
      ],
    }));
    const out = await consolidate('u', { eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore, semanticResolutionStore: s.semanticResolutionStore, transaction: s.transaction, llm: stub });
    // 3d：伪造 evidence_id 不在 validEvidence 白名单 → 丢弃
    assert.equal(s.semanticResolutionStore.ofEvidence('fake-ai-context-id'), null, '伪造 evidence_id 不落 resolution');
    assert.ok(s.semanticResolutionStore.ofEvidence(e.id), '真证据 resolution 照常落库');
    // 3a：所形成认知的 support 链只引真证据 id，绝不引 resolution / resolved_content
    const cog = out.created[0];
    assert.ok(cog, '认知已形成');
    const sources = s.cognitionStore.sourcesOf(cog.id);
    assert.ok(sources.length > 0 && sources.every((l) => l.evidenceId === e.id), 'support 只引真证据，不含伪造 id / 解析内容');
  } finally {
    s.close();
  }
});

test('幂等：同证据二次 consolidate 不重复落 resolution', async () => {
  const s = openStores(':memory:');
  try {
    const e = seedUtterance(s, '是', '你喜欢爬山吗?');
    const mkStub = () => stubReturning(JSON.stringify({
      new: [{ content: '用户喜欢爬山', content_type: 'preference', formed_by: 'confirmed', support_evidence_ids: [e.id] }],
      resolutions: [{ evidence_id: e.id, resolved_content: '用户确认喜欢爬山', response_act: 'affirm' }],
    }));
    await consolidate('u', { eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore, semanticResolutionStore: s.semanticResolutionStore, transaction: s.transaction, llm: mkStub() });
    // 再 seed 一个覆盖同证据的新事件 + 再 consolidate（模拟同证据被再次处理）
    s.eventStore.put({ subjectId: 'u', summary: 'evt2', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    await consolidate('u', { eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore, semanticResolutionStore: s.semanticResolutionStore, transaction: s.transaction, llm: mkStub() });
    assert.equal(s.semanticResolutionStore.forEvidenceIds([e.id]).length, 1, '同证据只落一份解析（幂等）');
  } finally {
    s.close();
  }
});

test('非法枚举值 → 落 null（收敛，不写脏数据）', async () => {
  const s = openStores(':memory:');
  try {
    const e = seedUtterance(s, '嗯', '你喜欢爬山吗?');
    const stub = stubReturning(JSON.stringify({
      new: [{ content: '某认知', content_type: 'preference', formed_by: 'confirmed', support_evidence_ids: [e.id] }],
      resolutions: [{ evidence_id: e.id, resolved_content: '用户确认', response_act: 'wat', prompt_act: 'bogus', proposition_origin: 'nonsense', assertion_strength: 'huh' }],
    }));
    await consolidate('u', { eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore, semanticResolutionStore: s.semanticResolutionStore, transaction: s.transaction, llm: stub });
    const r = s.semanticResolutionStore.ofEvidence(e.id);
    assert.ok(r, 'resolved_content 有效 → 仍落库');
    assert.equal(r.responseAct, null, '非法 response_act → null');
    assert.equal(r.promptAct, null);
    assert.equal(r.propositionOrigin, null);
    assert.equal(r.assertionStrength, null);
  } finally {
    s.close();
  }
});

test('Phase 2 边界：无 semanticResolutionStore → 不落解析、consolidate 行为同旧', async () => {
  const s = openStores(':memory:');
  try {
    const e = seedUtterance(s, '是', '你喜欢爬山吗?');
    const stub = stubReturning(JSON.stringify({
      new: [{ content: '用户喜欢爬山', content_type: 'preference', formed_by: 'confirmed', support_evidence_ids: [e.id] }],
      resolutions: [{ evidence_id: e.id, resolved_content: '用户确认', response_act: 'affirm' }],
    }));
    // 不传 semanticResolutionStore
    const out = await consolidate('u', { eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore, transaction: s.transaction, llm: stub });
    assert.equal(out.created.length, 1, '认知照常形成（不落解析不影响主流程）');
    assert.equal(s.semanticResolutionStore.ofEvidence(e.id), null, '未接 store → 不落解析');
  } finally {
    s.close();
  }
});

test('来源收窄：[行为观察] 证据不落解析——结构保证，不靠提示词自觉', async () => {
  const s = openStores(':memory:');
  try {
    // 一条 spoken（用户真说的）+ 一条 observed（行为观察，不是用户在说话）；两条都显式放行隐私门，
    // 确保 observed 真进了 prompt/validEvidence——否则测的就是隐私门而不是来源收窄了。
    const spoken = s.evidenceStore.put({ subjectId: 'u', sourceKind: 'spoken', hostId: 'h', rawContent: '是', precedingAiContext: '你喜欢爬山吗?', allowCloudRead: true });
    const observed = s.evidenceStore.put({ subjectId: 'u', sourceKind: 'observed', hostId: 'h', rawContent: '凌晨3点还在打游戏', allowCloudRead: true });
    s.eventStore.put({ subjectId: 'u', summary: 'evt', occurredAt: spoken.occurredAt, evidenceIds: [spoken.id, observed.id] });
    // LLM 不听话：对 observed 也编了一份「这句在回应什么」——对行为观察本就无意义。
    const stub = stubReturning(JSON.stringify({
      new: [{ content: '用户喜欢爬山', content_type: 'preference', formed_by: 'confirmed', support_evidence_ids: [spoken.id] }],
      resolutions: [
        { evidence_id: spoken.id, resolved_content: '用户确认喜欢爬山', response_act: 'affirm' },
        { evidence_id: observed.id, resolved_content: '用户在打游戏', response_act: 'elaborate' },
      ],
    }));
    await consolidate('u', { eventStore: s.eventStore, evidenceStore: s.evidenceStore, cognitionStore: s.cognitionStore, semanticResolutionStore: s.semanticResolutionStore, transaction: s.transaction, llm: stub });
    assert.ok(s.semanticResolutionStore.ofEvidence(spoken.id), '[用户说] 照常落解析');
    assert.equal(s.semanticResolutionStore.ofEvidence(observed.id), null, '[行为观察] 不落解析——模型不听话也进不了表');
  } finally {
    s.close();
  }
});
