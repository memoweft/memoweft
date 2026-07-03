/**
 * 校验便携记忆包（Phase 5-A）。纯函数，不触库。
 *
 * 分级：
 *  - errors（致命，valid=false，绝不导入）：格式/版本/必需字段不对；溯源引用悬空。
 *  - warnings（软提示，可导入）：subject 混入；correctsEvidenceId 指向包外；旧 schemaVersion。
 *
 * V1 只做结构 + 引用完整性校验，不逐字段深校（生产方是自家 exportBundle）。
 */
import { BUNDLE_FORMAT, BUNDLE_SCHEMA_VERSION, type MemoryBundle, type ValidateResult } from './model.ts';

export function validateBundle(bundle: unknown): ValidateResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (bundle == null || typeof bundle !== 'object') {
    return { valid: false, errors: ['bundle 不是对象'], warnings };
  }
  const b = bundle as Partial<MemoryBundle>;

  if (b.format !== BUNDLE_FORMAT) {
    errors.push(`format 应为 "${BUNDLE_FORMAT}"，实为 ${JSON.stringify(b.format)}`);
  }
  if (typeof b.schemaVersion !== 'number') {
    errors.push('schemaVersion 缺失或非数字');
  } else if (b.schemaVersion > BUNDLE_SCHEMA_VERSION) {
    errors.push(`schemaVersion=${b.schemaVersion} 高于本版本支持的 ${BUNDLE_SCHEMA_VERSION}（请升级 MemoWeft 再导入）`);
  } else if (b.schemaVersion < BUNDLE_SCHEMA_VERSION) {
    warnings.push(`schemaVersion=${b.schemaVersion} 低于当前 ${BUNDLE_SCHEMA_VERSION}（按旧结构导入）`);
  }
  if (typeof b.subjectId !== 'string' || b.subjectId === '') {
    errors.push('subjectId 缺失');
  }

  const data = b.data;
  if (data == null || typeof data !== 'object') {
    errors.push('data 缺失');
    return { valid: false, errors, warnings };
  }
  const arrays: Array<[string, unknown]> = [
    ['evidence', data.evidence],
    ['events', data.events],
    ['eventEvidence', data.eventEvidence],
    ['cognitions', data.cognitions],
    ['cognitionEvidence', data.cognitionEvidence],
  ];
  for (const [name, arr] of arrays) {
    if (!Array.isArray(arr)) errors.push(`data.${name} 应为数组`);
  }
  if (errors.length > 0) return { valid: false, errors, warnings };

  // 每个元素必须有非空字符串 id / 端点：防 undefined 混进 Set 掩盖引用检查，也防 undefined 落库。
  const badId = (x: { id?: unknown }) => typeof x.id !== 'string' || x.id === '';
  if (data.evidence.some(badId)) errors.push('data.evidence 存在缺 id 的元素');
  if (data.events.some(badId)) errors.push('data.events 存在缺 id 的元素');
  if (data.cognitions.some(badId)) errors.push('data.cognitions 存在缺 id 的元素');
  for (const l of data.eventEvidence) {
    if (typeof l.eventId !== 'string' || typeof l.evidenceId !== 'string') { errors.push('data.eventEvidence 存在非法端点'); break; }
  }
  for (const l of data.cognitionEvidence) {
    if (typeof l.cognitionId !== 'string' || typeof l.evidenceId !== 'string') { errors.push('data.cognitionEvidence 存在非法端点'); break; }
  }
  if (errors.length > 0) return { valid: false, errors, warnings };

  // 到这里五个数组都在、元素 id 都是非空字符串。
  const evidenceIds = new Set(data.evidence.map((e) => e.id));
  const eventIds = new Set(data.events.map((e) => e.id));
  const cognitionIds = new Set(data.cognitions.map((c) => c.id));

  // 包内 id 必须唯一（否则 merge 阶段会撞 PRIMARY KEY 抛错）。
  if (evidenceIds.size !== data.evidence.length) errors.push('data.evidence 存在重复 id');
  if (eventIds.size !== data.events.length) errors.push('data.events 存在重复 id');
  if (cognitionIds.size !== data.cognitions.length) errors.push('data.cognitions 存在重复 id');

  // 引用完整性（致命）：join 行指向的两端都必须在包内。
  for (const link of data.eventEvidence) {
    if (!eventIds.has(link.eventId)) errors.push(`eventEvidence 指向不存在的 event: ${link.eventId}`);
    if (!evidenceIds.has(link.evidenceId)) errors.push(`eventEvidence 指向不存在的 evidence: ${link.evidenceId}`);
  }
  for (const link of data.cognitionEvidence) {
    if (!cognitionIds.has(link.cognitionId)) errors.push(`cognitionEvidence 指向不存在的 cognition: ${link.cognitionId}`);
    if (!evidenceIds.has(link.evidenceId)) errors.push(`cognitionEvidence 指向不存在的 evidence: ${link.evidenceId}`);
  }

  // 软告警：subject 混入（包声明是 A，却夹了 B 的行）。
  for (const e of data.evidence) {
    if (e.subjectId !== b.subjectId) warnings.push(`evidence ${e.id} 的 subjectId(${e.subjectId}) 与包(${b.subjectId})不一致`);
  }
  for (const e of data.events) {
    if (e.subjectId !== b.subjectId) warnings.push(`event ${e.id} 的 subjectId(${e.subjectId}) 与包不一致`);
  }
  for (const c of data.cognitions) {
    if (c.subjectId !== b.subjectId) warnings.push(`cognition ${c.id} 的 subjectId(${c.subjectId}) 与包不一致`);
  }

  // 软告警：correctsEvidenceId 指向包外（非致命——导入后目标库可能已有那条）。
  for (const e of data.evidence) {
    if (e.correctsEvidenceId != null && !evidenceIds.has(e.correctsEvidenceId)) {
      warnings.push(`evidence ${e.id} 的 correctsEvidenceId(${e.correctsEvidenceId}) 不在包内`);
    }
  }

  // unconsolidatedEventIds（保真 consolidated 标记）：若存在，须是数组且指向包内事件。
  const unconsIds = data.unconsolidatedEventIds;
  if (unconsIds !== undefined) {
    if (!Array.isArray(unconsIds)) {
      errors.push('data.unconsolidatedEventIds 应为数组');
    } else {
      for (const id of unconsIds) if (!eventIds.has(id)) warnings.push(`unconsolidatedEventIds 含未知 event: ${id}`);
    }
  }

  return { valid: errors.length === 0, errors, warnings };
}
