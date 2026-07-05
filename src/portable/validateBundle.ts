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
import { resolveLang } from '../config.ts';

export function validateBundle(bundle: unknown): ValidateResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  const lang = resolveLang();

  if (bundle == null || typeof bundle !== 'object') {
    return { valid: false, errors: [lang === 'zh' ? 'bundle 不是对象' : 'bundle is not an object'], warnings };
  }
  const b = bundle as Partial<MemoryBundle>;

  if (b.format !== BUNDLE_FORMAT) {
    errors.push(
      lang === 'zh'
        ? `format 应为 "${BUNDLE_FORMAT}"，实为 ${JSON.stringify(b.format)}`
        : `format should be "${BUNDLE_FORMAT}", but got ${JSON.stringify(b.format)}`,
    );
  }
  if (typeof b.schemaVersion !== 'number') {
    errors.push(lang === 'zh' ? 'schemaVersion 缺失或非数字' : 'schemaVersion is missing or not a number');
  } else if (b.schemaVersion > BUNDLE_SCHEMA_VERSION) {
    errors.push(
      lang === 'zh'
        ? `schemaVersion=${b.schemaVersion} 高于本版本支持的 ${BUNDLE_SCHEMA_VERSION}（请升级 MemoWeft 再导入）`
        : `schemaVersion=${b.schemaVersion} is higher than the ${BUNDLE_SCHEMA_VERSION} supported by this version (upgrade MemoWeft before importing)`,
    );
  } else if (b.schemaVersion < BUNDLE_SCHEMA_VERSION) {
    warnings.push(
      lang === 'zh'
        ? `schemaVersion=${b.schemaVersion} 低于当前 ${BUNDLE_SCHEMA_VERSION}（按旧结构导入）`
        : `schemaVersion=${b.schemaVersion} is lower than the current ${BUNDLE_SCHEMA_VERSION} (importing with the old structure)`,
    );
  }
  if (typeof b.subjectId !== 'string' || b.subjectId === '') {
    errors.push(lang === 'zh' ? 'subjectId 缺失' : 'subjectId is missing');
  }

  const data = b.data;
  if (data == null || typeof data !== 'object') {
    errors.push(lang === 'zh' ? 'data 缺失' : 'data is missing');
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
    if (!Array.isArray(arr)) errors.push(lang === 'zh' ? `data.${name} 应为数组` : `data.${name} should be an array`);
  }
  if (errors.length > 0) return { valid: false, errors, warnings };

  // 每个元素必须有非空字符串 id / 端点：防 undefined 混进 Set 掩盖引用检查，也防 undefined 落库。
  const badId = (x: { id?: unknown }) => typeof x.id !== 'string' || x.id === '';
  if (data.evidence.some(badId)) errors.push(lang === 'zh' ? 'data.evidence 存在缺 id 的元素' : 'data.evidence has an element with a missing id');
  if (data.events.some(badId)) errors.push(lang === 'zh' ? 'data.events 存在缺 id 的元素' : 'data.events has an element with a missing id');
  if (data.cognitions.some(badId)) errors.push(lang === 'zh' ? 'data.cognitions 存在缺 id 的元素' : 'data.cognitions has an element with a missing id');
  for (const l of data.eventEvidence) {
    if (typeof l.eventId !== 'string' || typeof l.evidenceId !== 'string') { errors.push(lang === 'zh' ? 'data.eventEvidence 存在非法端点' : 'data.eventEvidence has an invalid endpoint'); break; }
  }
  for (const l of data.cognitionEvidence) {
    if (typeof l.cognitionId !== 'string' || typeof l.evidenceId !== 'string') { errors.push(lang === 'zh' ? 'data.cognitionEvidence 存在非法端点' : 'data.cognitionEvidence has an invalid endpoint'); break; }
  }
  if (errors.length > 0) return { valid: false, errors, warnings };

  // 到这里五个数组都在、元素 id 都是非空字符串。
  const evidenceIds = new Set(data.evidence.map((e) => e.id));
  const eventIds = new Set(data.events.map((e) => e.id));
  const cognitionIds = new Set(data.cognitions.map((c) => c.id));

  // 包内 id 必须唯一（否则 merge 阶段会撞 PRIMARY KEY 抛错）。
  if (evidenceIds.size !== data.evidence.length) errors.push(lang === 'zh' ? 'data.evidence 存在重复 id' : 'data.evidence has duplicate ids');
  if (eventIds.size !== data.events.length) errors.push(lang === 'zh' ? 'data.events 存在重复 id' : 'data.events has duplicate ids');
  if (cognitionIds.size !== data.cognitions.length) errors.push(lang === 'zh' ? 'data.cognitions 存在重复 id' : 'data.cognitions has duplicate ids');

  // 引用完整性（致命）：join 行指向的两端都必须在包内。
  for (const link of data.eventEvidence) {
    if (!eventIds.has(link.eventId)) errors.push(lang === 'zh' ? `eventEvidence 指向不存在的 event: ${link.eventId}` : `eventEvidence references a non-existent event: ${link.eventId}`);
    if (!evidenceIds.has(link.evidenceId)) errors.push(lang === 'zh' ? `eventEvidence 指向不存在的 evidence: ${link.evidenceId}` : `eventEvidence references a non-existent evidence: ${link.evidenceId}`);
  }
  for (const link of data.cognitionEvidence) {
    if (!cognitionIds.has(link.cognitionId)) errors.push(lang === 'zh' ? `cognitionEvidence 指向不存在的 cognition: ${link.cognitionId}` : `cognitionEvidence references a non-existent cognition: ${link.cognitionId}`);
    if (!evidenceIds.has(link.evidenceId)) errors.push(lang === 'zh' ? `cognitionEvidence 指向不存在的 evidence: ${link.evidenceId}` : `cognitionEvidence references a non-existent evidence: ${link.evidenceId}`);
  }

  // 软告警：subject 混入（包声明是 A，却夹了 B 的行）。
  for (const e of data.evidence) {
    if (e.subjectId !== b.subjectId) warnings.push(lang === 'zh' ? `evidence ${e.id} 的 subjectId(${e.subjectId}) 与包(${b.subjectId})不一致` : `evidence ${e.id} subjectId(${e.subjectId}) does not match the bundle(${b.subjectId})`);
  }
  for (const e of data.events) {
    if (e.subjectId !== b.subjectId) warnings.push(lang === 'zh' ? `event ${e.id} 的 subjectId(${e.subjectId}) 与包不一致` : `event ${e.id} subjectId(${e.subjectId}) does not match the bundle`);
  }
  for (const c of data.cognitions) {
    if (c.subjectId !== b.subjectId) warnings.push(lang === 'zh' ? `cognition ${c.id} 的 subjectId(${c.subjectId}) 与包不一致` : `cognition ${c.id} subjectId(${c.subjectId}) does not match the bundle`);
  }

  // 软告警：correctsEvidenceId 指向包外（非致命——导入后目标库可能已有那条）。
  for (const e of data.evidence) {
    if (e.correctsEvidenceId != null && !evidenceIds.has(e.correctsEvidenceId)) {
      warnings.push(lang === 'zh' ? `evidence ${e.id} 的 correctsEvidenceId(${e.correctsEvidenceId}) 不在包内` : `evidence ${e.id} correctsEvidenceId(${e.correctsEvidenceId}) is not in the bundle`);
    }
  }

  // unconsolidatedEventIds（保真 consolidated 标记）：若存在，须是数组且指向包内事件。
  const unconsIds = data.unconsolidatedEventIds;
  if (unconsIds !== undefined) {
    if (!Array.isArray(unconsIds)) {
      errors.push(lang === 'zh' ? 'data.unconsolidatedEventIds 应为数组' : 'data.unconsolidatedEventIds should be an array');
    } else {
      for (const id of unconsIds) if (!eventIds.has(id)) warnings.push(lang === 'zh' ? `unconsolidatedEventIds 含未知 event: ${id}` : `unconsolidatedEventIds contains an unknown event: ${id}`);
    }
  }

  return { valid: errors.length === 0, errors, warnings };
}
