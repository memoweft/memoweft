/**
 * 受控记忆管理 API（架构归位·批次2）：7 个操作的正路 + 关键拒绝路。
 * 全用 :memory: 共享连接（openStores），无运行时残留；不依赖网络与模型。
 *
 * 验的纪律：
 *  - 每个真实变更都落审计行（management_log）且带 reason；被拒绝的操作不落行、不改数据。
 *  - merge 仅同 subjectId；链按 (evidenceId, relation) 去重；source 标失效不硬删。
 *  - archive / invalidate 后，共享召回函数（retrieval/recall.ts）召不出。
 *  - checkIntegrity 能查出手工构造的孤儿链；旧库缺 archived_at 列自动补、不抛错。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from '../src/store/nodeSqliteDriver.ts';
import { openStores, type StoreBundle } from '../src/store/openStores.ts';
import { createMemoryManagementAPI } from '../src/memory/managementApi.ts';
import { SqliteCognitionStore } from '../src/cognition/store.ts';
import { computeConfidence } from '../src/consolidation/confidence.ts';
import { recallCognitions } from '../src/retrieval/recall.ts';

/** 快速搭一套 :memory: 库 + 管理 API。 */
function setup(): { bundle: StoreBundle; api: ReturnType<typeof createMemoryManagementAPI> } {
  const bundle = openStores(':memory:');
  return { bundle, api: createMemoryManagementAPI(bundle) };
}

/** 播一条最普通的认知（可过召回全部门槛：stated + 高置信 + preference 不衰减）。 */
function seedCognition(bundle: StoreBundle, over: Record<string, unknown> = {}) {
  return bundle.cognitionStore.put({
    subjectId: 'owner',
    content: '用户喜欢喝茶',
    contentType: 'preference',
    formedBy: 'stated',
    confidence: 900,
    credStatus: 'stable',
    ...over,
  } as Parameters<StoreBundle['cognitionStore']['put']>[0]);
}

function seedEvidence(bundle: StoreBundle, raw = '我喜欢喝茶') {
  return bundle.evidenceStore.put({
    subjectId: 'owner',
    sourceKind: 'spoken',
    hostId: 'test',
    rawContent: raw,
  });
}

test('invalidateCognition：标 invalidAt + 审计行带 reason', () => {
  const { bundle, api } = setup();
  try {
    const c = seedCognition(bundle);
    const updated = api.invalidateCognition({ cognitionId: c.id, reason: 'user_rejected' });
    assert.ok(updated?.invalidAt, 'invalidAt 应被写入');
    const log = bundle.managementLog.list(c.id);
    assert.equal(log.length, 1, '恰好一行审计');
    assert.equal(log[0]!.op, 'invalidate');
    assert.equal(log[0]!.reason, 'user_rejected');
    // 不存在的 id：返回 null、不落审计。
    assert.equal(api.invalidateCognition({ cognitionId: 'no-such', reason: 'x' }), null);
    assert.equal(bundle.managementLog.list('no-such').length, 0, '没发生的事不留痕');
  } finally {
    bundle.close();
  }
});

test('updateEvidenceAuthorization：授权翻转 + 审计 detail 记 before/after', () => {
  const { bundle, api } = setup();
  try {
    const e = seedEvidence(bundle);
    const updated = api.updateEvidenceAuthorization({ evidenceId: e.id, allowCloudRead: false, reason: '隐私收紧' });
    assert.equal(updated?.allowCloudRead, false);
    const log = bundle.managementLog.list(e.id);
    assert.equal(log.length, 1);
    assert.equal(log[0]!.op, 'update_authorization');
    const detail = log[0]!.detail as { before: { allowCloudRead: boolean }; after: { allowCloudRead: boolean } };
    assert.equal(detail.before.allowCloudRead, true, '改前快照在 detail 里');
    assert.equal(detail.after.allowCloudRead, false, '改后快照在 detail 里');
  } finally {
    bundle.close();
  }
});

test('updateEvidenceAuthorization：零变更（没传位/传相同值）→ 原样返回、不落审计', () => {
  const { bundle, api } = setup();
  try {
    const e = seedEvidence(bundle); // 缺省 allowCloudRead=true
    const r1 = api.updateEvidenceAuthorization({ evidenceId: e.id, reason: '手滑没改' });
    assert.equal(r1?.allowCloudRead, true, '没传位 → 原样返回');
    const r2 = api.updateEvidenceAuthorization({ evidenceId: e.id, allowCloudRead: true, reason: '传了相同值' });
    assert.equal(r2?.allowCloudRead, true);
    assert.equal(bundle.managementLog.list(e.id).length, 0, '零变更不落审计（只记真实发生的变更）');
  } finally {
    bundle.close();
  }
});

test('mergeCognition：target 已失效/已归档 → 拒绝，零副作用', () => {
  const { bundle, api } = setup();
  try {
    const e = seedEvidence(bundle);
    const source = seedCognition(bundle, { evidence: [{ evidenceId: e.id, relation: 'support' }] });
    const deadTarget = seedCognition(bundle, { content: '用户喜欢喝咖啡' });
    api.invalidateCognition({ cognitionId: deadTarget.id, reason: '先弄死目标' });
    assert.throws(
      () => api.mergeCognition({ sourceId: source.id, targetId: deadTarget.id, reason: '想合并' }),
      /已失效/,
      '合并进已失效 target 应被拒绝',
    );
    const archivedTarget = seedCognition(bundle, { content: '用户喜欢喝可乐' });
    api.archiveCognition({ cognitionId: archivedTarget.id, reason: '先归档目标' });
    assert.throws(
      () => api.mergeCognition({ sourceId: source.id, targetId: archivedTarget.id, reason: '想合并' }),
      /已归档/,
      '合并进已归档 target 应被拒绝',
    );
    // 零副作用：source 原样（链还在、没被标失效），拒绝不落 merge 审计。
    assert.equal(bundle.cognitionStore.sourcesOf(source.id).length, 1, 'source 链一条没少');
    assert.equal(bundle.cognitionStore.get(source.id)!.invalidAt, null, 'source 没被标失效');
    assert.ok(!bundle.managementLog.list(source.id).some((l) => l.op === 'merge'), '拒绝不落 merge 审计');
  } finally {
    bundle.close();
  }
});

test('removeEvidenceSafely：有引用不 force → 拒绝并返回 blockers，数据一动不动', () => {
  const { bundle, api } = setup();
  try {
    const e = seedEvidence(bundle);
    const ev = bundle.eventStore.put({ subjectId: 'owner', summary: '聊了喝茶', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    const c = seedCognition(bundle, { evidence: [{ evidenceId: e.id, relation: 'support' }] });

    const r = api.removeEvidenceSafely({ evidenceId: e.id, reason: '想删' });
    assert.equal(r.removed, false, '有引用且未 force → 拒绝');
    assert.equal(r.blockers.length, 2, '影响面：事件链 + 认知链各一条');
    assert.ok(r.blockers.some((b) => b.kind === 'event' && b.id === ev.id));
    assert.ok(r.blockers.some((b) => b.kind === 'cognition' && b.id === c.id && b.relation === 'support'));
    assert.ok(bundle.evidenceStore.get(e.id), '证据还在');
    assert.equal(bundle.managementLog.list(e.id).length, 0, '拒绝不落审计（没改就不留痕）');
  } finally {
    bundle.close();
  }
});

test('removeEvidenceSafely：force → 删证据 + 清关联链 + 审计含 blockers 快照', () => {
  const { bundle, api } = setup();
  try {
    const e = seedEvidence(bundle);
    bundle.eventStore.put({ subjectId: 'owner', summary: '聊了喝茶', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    seedCognition(bundle, { evidence: [{ evidenceId: e.id, relation: 'support' }] });

    const r = api.removeEvidenceSafely({ evidenceId: e.id, reason: '确认要删', force: true });
    assert.equal(r.removed, true);
    assert.equal(bundle.evidenceStore.get(e.id), null, '证据已删');
    assert.ok(api.checkIntegrity().ok, '关联链已清，不留孤儿');
    const log = bundle.managementLog.list(e.id);
    assert.equal(log.length, 1);
    assert.equal(log[0]!.op, 'remove_evidence');
    const detail = log[0]!.detail as { force: boolean; blockers: unknown[] };
    assert.equal(detail.force, true);
    assert.equal(detail.blockers.length, 2, 'blockers 快照进了 detail');
  } finally {
    bundle.close();
  }
});

test('removeEvidenceSafely：无引用 → 不用 force 直接删', () => {
  const { bundle, api } = setup();
  try {
    const e = seedEvidence(bundle);
    const r = api.removeEvidenceSafely({ evidenceId: e.id, reason: '没人引用' });
    assert.equal(r.removed, true);
    assert.equal(r.blockers.length, 0);
    assert.equal(bundle.evidenceStore.get(e.id), null);
  } finally {
    bundle.close();
  }
});

// 批次3 语义变更（用户拍板）：删除审计的 detail 不存内容原文、只存元数据——本用例断言随之更新。
test('removeCognitionSafely：连溯源链删 + 返回影响面 + 审计 detail 只存元数据不存原文', () => {
  const { bundle, api } = setup();
  try {
    const e = seedEvidence(bundle);
    const c = seedCognition(bundle, { evidence: [{ evidenceId: e.id, relation: 'support' }] });
    const r = api.removeCognitionSafely({ cognitionId: c.id, reason: '不要了' });
    assert.equal(r.removed, true);
    assert.equal(r.removedLinks.length, 1, '影响面：被断掉的链');
    assert.equal(r.removedLinks[0]!.evidenceId, e.id);
    assert.equal(bundle.cognitionStore.get(c.id), null, '认知已删');
    assert.equal(bundle.cognitionStore.sourcesOf(c.id).length, 0, '链已删');
    assert.ok(bundle.evidenceStore.get(e.id), '证据本体不动');
    const log = bundle.managementLog.list(c.id);
    assert.equal(log[0]?.op, 'remove_cognition');
    // detail = 元数据（批次3 用户拍板：用户删掉的内容不该在审计里再活一份）。
    const detail = log[0]!.detail as { contentType: string; formedBy: string; credStatus: string; linkCount: number };
    assert.equal(detail.contentType, 'preference');
    assert.equal(detail.formedBy, 'stated');
    assert.equal(detail.credStatus, 'stable');
    assert.equal(detail.linkCount, 1, '断了几条链只留数量');
    assert.ok(!JSON.stringify(detail).includes(c.content), '内容原文不进审计 detail');
  } finally {
    bundle.close();
  }
});

test('managementLog.clear：清空全部审计行（仅恢复出厂用，批次3 用户拍板：出厂=无历史）', () => {
  const { bundle, api } = setup();
  try {
    const c1 = seedCognition(bundle);
    const c2 = seedCognition(bundle, { content: '用户喜欢喝咖啡' });
    api.invalidateCognition({ cognitionId: c1.id, reason: '造两行审计' });
    api.archiveCognition({ cognitionId: c2.id, reason: '造两行审计' });
    assert.equal(bundle.managementLog.list().length, 2, '清空前有审计行');
    assert.equal(bundle.managementLog.clear(), 2, '返回清掉的行数');
    assert.equal(bundle.managementLog.list().length, 0, '清空后无历史');
    assert.equal(bundle.managementLog.clear(), 0, '再清一次 = 0（幂等）');
  } finally {
    bundle.close();
  }
});

test('mergeCognition：链搬家去重、置信度重算、source 标失效不硬删、审计 op=merge', () => {
  const { bundle, api } = setup();
  try {
    const e1 = seedEvidence(bundle, '证据一');
    const e2 = seedEvidence(bundle, '证据二');
    const e3 = seedEvidence(bundle, '证据三');
    const target = seedCognition(bundle, { evidence: [{ evidenceId: e1.id, relation: 'support' }] });
    const source = seedCognition(bundle, {
      content: '用户喜欢喝茶（另一条重复认知）',
      evidence: [
        { evidenceId: e1.id, relation: 'support' }, // 与 target 重复 → 去重丢弃
        { evidenceId: e2.id, relation: 'support' },
        { evidenceId: e3.id, relation: 'contradict' },
      ],
    });

    const r = api.mergeCognition({ sourceId: source.id, targetId: target.id, reason: '重复认知合并' });
    assert.equal(r.merged, true);
    assert.equal(r.movedLinks, 2, 'e2/e3 搬过去');
    assert.equal(r.duplicateLinks, 1, 'e1 重复被去重');

    const links = bundle.cognitionStore.sourcesOf(target.id);
    assert.equal(links.length, 3, '合并后链 = e1 support + e2 support + e3 contradict');
    // 置信度重算：与 consolidate 强化路径同口径（supportCount=2, contradictCount=1）。
    const expected = computeConfidence({ contentType: 'preference', formedBy: 'stated', supportCount: 2, contradictCount: 1 });
    assert.equal(r.target.confidence, expected, '置信度按合并后的链重算');
    assert.equal(r.target.credStatus, 'conflicted', '有反对证据 → 状态随算重导出');
    assert.equal(r.target.content, target.content, 'target 的 content 不动');

    assert.ok(r.source.invalidAt, 'source 标失效');
    assert.ok(bundle.cognitionStore.get(source.id), 'source 不硬删（可追溯）');
    assert.equal(bundle.cognitionStore.sourcesOf(source.id).length, 0, 'source 链已搬空');

    const log = bundle.managementLog.list(source.id);
    assert.equal(log[0]?.op, 'merge');
    const detail = log[0]!.detail as { sourceId: string; targetId: string; confidenceRecomputed: boolean };
    assert.equal(detail.sourceId, source.id);
    assert.equal(detail.targetId, target.id);
    assert.equal(detail.confidenceRecomputed, true, 'detail 注明选了"重算"路');
  } finally {
    bundle.close();
  }
});

test('mergeCognition：跨 subject 拒绝，双方原样、不落审计', () => {
  const { bundle, api } = setup();
  try {
    const a = seedCognition(bundle, { subjectId: 'subject-a' });
    const b = seedCognition(bundle, { subjectId: 'subject-b' });
    assert.throws(
      () => api.mergeCognition({ sourceId: a.id, targetId: b.id, reason: '不该成' }),
      /跨 subject/,
      '不同 subject 的认知不允许合并',
    );
    assert.equal(bundle.cognitionStore.get(a.id)?.invalidAt, null, 'source 没被动过');
    assert.equal(bundle.managementLog.list(a.id).length, 0, '拒绝不留痕');
  } finally {
    bundle.close();
  }
});

test('archive / invalidate 后，共享召回函数召不出（正常那条照常召回）', async () => {
  const { bundle, api } = setup();
  try {
    const keep = seedCognition(bundle, { content: '用户喜欢喝茶' });
    const archived = seedCognition(bundle, { content: '用户在学吉他（已归档）' });
    const dead = seedCognition(bundle, { content: '用户喜欢咖啡（已失效）' });
    api.archiveCognition({ cognitionId: archived.id, reason: '过气了' });
    api.invalidateCognition({ cognitionId: dead.id, reason: '被纠正' });
    assert.ok(bundle.cognitionStore.get(archived.id)?.archivedAt, 'archived_at 已落库');
    assert.equal(bundle.managementLog.list(archived.id)[0]?.op, 'archive');

    // 伪 retriever：三条都"召回"到（高相似度），交给共享门控去筛。
    const retriever = {
      async indexAll() {},
      async search() {
        return [
          { id: keep.id, score: 0.9 },
          { id: archived.id, score: 0.9 },
          { id: dead.id, score: 0.9 },
        ];
      },
    };
    const out = await recallCognitions('喝点什么好', 'owner', { retriever, cognitionStore: bundle.cognitionStore });
    assert.equal(out.length, 1, '归档 / 失效的都被门控挡掉');
    assert.equal(out[0]!.content, '用户喜欢喝茶');
    assert.equal(out[0]!.id, keep.id, '共享召回带回认知 id');
  } finally {
    bundle.close();
  }
});

test('checkIntegrity：手工孤儿链查得出（v1 只报告不修）', () => {
  const { bundle, api } = setup();
  try {
    assert.ok(api.checkIntegrity().ok, '干净库 ok=true');
    // 手工制造脏数据：两张关联表各插一行指向不存在的行。
    bundle.db.prepare('INSERT INTO event_evidence (event_id, evidence_id) VALUES (?,?)').run('ghost-event', 'ghost-evidence');
    bundle.db.prepare("INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) VALUES (?,?,'support')").run('ghost-cog', 'ghost-evidence');
    const report = api.checkIntegrity();
    assert.equal(report.ok, false);
    assert.ok(report.issues.some((i) => i.kind === 'orphan_event_evidence' && i.missing === 'event'));
    assert.ok(report.issues.some((i) => i.kind === 'orphan_cognition_evidence' && i.missing === 'cognition'));
    assert.ok(report.issues.some((i) => i.missing === 'evidence'), 'evidence 端悬空也报');
    // 只报告不修：再查一遍问题还在。
    assert.equal(api.checkIntegrity().ok, false);
  } finally {
    bundle.close();
  }
});

test('旧库（无 archived_at 列）：构造不抛错、自动补列，归档可用', () => {
  const db = new DatabaseSync(':memory:');
  try {
    // 手工建"阶段 3 时代"的旧表：有 asked_at、没 archived_at。
    db.exec(`CREATE TABLE cognition (
      id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, content TEXT NOT NULL,
      content_type TEXT NOT NULL, formed_by TEXT NOT NULL, confidence INTEGER NOT NULL,
      cred_status TEXT NOT NULL, scope TEXT, valid_at TEXT, invalid_at TEXT, asked_at TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );`);
    const store = new SqliteCognitionStore(db); // 幂等迁移在构造里跑：不抛错
    const cols = db.prepare("SELECT name FROM pragma_table_info('cognition')").all() as unknown as Array<{ name: string }>;
    assert.ok(cols.some((c) => c.name === 'archived_at'), '缺列已自动补上');
    const c = store.put({ subjectId: 'owner', content: '旧库里的认知', contentType: 'fact', formedBy: 'stated', confidence: 700, credStatus: 'limited' });
    const archived = store.update(c.id, { archivedAt: new Date().toISOString() });
    assert.ok(archived?.archivedAt, '补列后归档可写可读');
  } finally {
    db.close();
  }
});

// ── 只读列取（批次5 步0 缺口A）：subject 过滤 + sources/effectiveConfidence/evidenceIds 组装 ──

test('listEvidence / listCognitions / listEvents：subject 过滤 + 组装正确，只读不落审计', () => {
  const { bundle, api } = setup();
  try {
    // 播 owner 的数据：一条证据 + 一条认知（挂链）+ 一个事件（覆盖该证据）。
    const e = seedEvidence(bundle, '我喜欢喝茶');
    const c = seedCognition(bundle, { evidence: [{ evidenceId: e.id, relation: 'support' }] });
    const ev = bundle.eventStore.put({ subjectId: 'owner', summary: '聊了喝茶', occurredAt: e.occurredAt, evidenceIds: [e.id] });
    // 播另一个 subject 的数据：用来验证过滤（不该出现在 owner 的列取里）。
    bundle.evidenceStore.put({ subjectId: 'other', sourceKind: 'spoken', hostId: 'test', rawContent: '别人的证据' });
    bundle.cognitionStore.put({ subjectId: 'other', content: '别人的认知', contentType: 'fact', formedBy: 'stated', confidence: 500, credStatus: 'limited' });
    bundle.eventStore.put({ subjectId: 'other', summary: '别人的事件', occurredAt: e.occurredAt, evidenceIds: [] });

    // 缺省 subjectId = cfg.identity.subjectId（'owner'）。
    const evidence = api.listEvidence();
    assert.equal(evidence.length, 1, '只列 owner 的证据（other 被过滤）');
    assert.equal(evidence[0]!.id, e.id);

    const cognitions = api.listCognitions();
    assert.equal(cognitions.length, 1, '只列 owner 的认知');
    assert.equal(cognitions[0]!.id, c.id);
    assert.equal(cognitions[0]!.sources.length, 1, '配上溯源链');
    assert.equal(cognitions[0]!.sources[0]!.evidenceId, e.id);
    assert.equal(typeof cognitions[0]!.effectiveConfidence, 'number', '配上读时算的有效置信');
    // preference 不衰减（halfLife 未配）+ 刚建 → 有效置信 = 原置信。
    assert.equal(cognitions[0]!.effectiveConfidence, c.confidence, '不衰减类型 & 新鲜 → 有效置信=原值');

    const events = api.listEvents();
    assert.equal(events.length, 1, '只列 owner 的事件');
    assert.equal(events[0]!.id, ev.id);
    assert.deepEqual(events[0]!.evidenceIds, [e.id], '配上覆盖的证据 id 列表');

    // 显式传另一个 subject：能取到那份。
    assert.equal(api.listEvidence({ subjectId: 'other' }).length, 1, '显式 subjectId 生效');
    assert.equal(api.listCognitions({ subjectId: 'other' }).length, 1);
    assert.equal(api.listEvents({ subjectId: 'other' }).length, 1);

    // 只读：三个 list 都不该往审计表写行。
    assert.equal(bundle.managementLog.list().length, 0, '列取是只读，不落审计');
  } finally {
    bundle.close();
  }
});

// ── 恢复出厂（批次5 步0 缺口D · 破坏性，只在 :memory: 验证）──

test('resetSubject：清三层 + 清审计 + 调 retriever.indexAll([])，返回计数', () => {
  const bundle = openStores(':memory:');
  // 伪 retriever：记录 indexAll 是否被以空数组调过（验证清索引这一步接上了）。
  let indexAllCalledWith: unknown = 'not-called';
  const retriever = {
    async indexAll(items: Array<{ id: string; text: string }>) { indexAllCalledWith = items; },
    async search() { return []; },
  };
  const api = createMemoryManagementAPI(bundle, undefined, { retriever });
  try {
    // 播数据：证据 + 认知（挂链）+ 事件（覆盖证据）+ 造两行审计。
    const e1 = bundle.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'test', rawContent: '证据一' });
    bundle.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'test', rawContent: '证据二' });
    const c = bundle.cognitionStore.put({ subjectId: 'owner', content: '认知一', contentType: 'fact', formedBy: 'stated', confidence: 700, credStatus: 'limited', evidence: [{ evidenceId: e1.id, relation: 'support' }] });
    const ev1 = bundle.eventStore.put({ subjectId: 'owner', summary: '事件一', occurredAt: e1.occurredAt, evidenceIds: [e1.id] });
    api.invalidateCognition({ cognitionId: c.id, reason: '造审计行' }); // 落一行审计（c 随后被清）

    const r = api.resetSubject({ subjectId: 'owner', reason: '恢复出厂' });
    assert.equal(r.evidenceRemoved, 2, '两条证据全清');
    assert.equal(r.cognitionRemoved, 1, '一条认知清掉');
    assert.equal(r.eventRemoved, 1, '一个事件清掉');
    assert.equal(r.auditRemoved, 1, '审计整表清掉（返回清掉行数）');

    // 三层 + 审计 + 关联表都空了。
    assert.equal(bundle.evidenceStore.all().length, 0, 'evidence 表空');
    assert.equal(bundle.cognitionStore.all('owner').length, 0, 'cognition 表空');
    assert.equal(bundle.eventStore.all('owner').length, 0, 'event 表空');
    assert.equal(bundle.managementLog.list().length, 0, '审计表空（含 reset 自己不留行）');
    assert.equal(bundle.cognitionStore.sourcesOf(c.id).length, 0, '溯源链连带清');
    assert.deepEqual(bundle.eventStore.evidenceOf(ev1.id), [], 'event_evidence 连带清（原事件的覆盖链已空）');

    // 清索引接上了：indexAll 被以空数组调过。
    assert.deepEqual(indexAllCalledWith, [], 'resetSubject 调 retriever.indexAll([]) 清向量索引');
  } finally {
    bundle.close();
  }
});

test('resetSubject：无 retriever 时跳过清索引、库内三层+审计照清（不抛错）', () => {
  const bundle = openStores(':memory:');
  const api = createMemoryManagementAPI(bundle); // 不传 deps → 无 retriever
  try {
    bundle.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'test', rawContent: '证据' });
    bundle.cognitionStore.put({ subjectId: 'owner', content: '认知', contentType: 'fact', formedBy: 'stated', confidence: 700, credStatus: 'limited' });
    const r = api.resetSubject({ subjectId: 'owner' });
    assert.equal(r.evidenceRemoved, 1);
    assert.equal(r.cognitionRemoved, 1);
    assert.equal(bundle.evidenceStore.all().length, 0, '无 retriever 也把库内清干净');
  } finally {
    bundle.close();
  }
});

test('resetSubject：只清指定 subject，不误伤别的 subject', () => {
  const bundle = openStores(':memory:');
  const api = createMemoryManagementAPI(bundle);
  try {
    bundle.evidenceStore.put({ subjectId: 'owner', sourceKind: 'spoken', hostId: 'test', rawContent: 'owner 的证据' });
    bundle.evidenceStore.put({ subjectId: 'keep', sourceKind: 'spoken', hostId: 'test', rawContent: 'keep 的证据' });
    bundle.cognitionStore.put({ subjectId: 'keep', content: 'keep 的认知', contentType: 'fact', formedBy: 'stated', confidence: 700, credStatus: 'limited' });

    const r = api.resetSubject({ subjectId: 'owner' });
    assert.equal(r.evidenceRemoved, 1, '只清了 owner 的一条证据');
    assert.equal(bundle.evidenceStore.all().filter((e) => e.subjectId === 'keep').length, 1, 'keep 的证据没被误伤');
    assert.equal(bundle.cognitionStore.all('keep').length, 1, 'keep 的认知没被误伤');
  } finally {
    bundle.close();
  }
});
