/**
 * 校验便携记忆包。纯函数，不触库。
 *
 * 分级：
 *  - errors（致命，valid=false，绝不导入）：格式/版本/必需字段不对；溯源引用悬空。
 *  - warnings（软提示，可导入）：correctsEvidenceId 指向包外；旧 schemaVersion。
 *
 * 结构 + 引用完整性校验，外加 cognition 的字段【值】校验（枚举 + confidence 范围）。
 *   —— 后者是导入路径的数据完整性护栏：importBundle 完全信任本函数的 valid=true 直接落库，
 *      而 cognition 表的 content_type/formed_by 列【无 CHECK 约束】、confidence 列靠 SQLite
 *      类型亲和性也拦不住字符串。越界值不在这里拦，就会静默落库并埋成延迟雷
 *      （越界 formed_by → 下次 computeConfidence 重算得 NaN → 那次重算整体失败）。
 *      这些值来自外部文件、与 LLM 无关，且导入路径没有 consolidate 那层「非法值兜底成 fact」的保护。
 */
import {
  BUNDLE_FORMAT,
  BUNDLE_SCHEMA_VERSION,
  type MemoryBundle,
  type ValidateResult,
} from './model.ts';
import {
  CONTENT_TYPES,
  FORMED_BY_VALUES,
  CRED_STATUSES,
  type ContentType,
  type FormedBy,
  type CredStatus,
} from '../cognition/model.ts';
import { resolveLang } from '../config.ts';
import { hashContext } from '../interaction/contextHash.ts';
import type { VisibleTurn } from '../interaction/model.ts';

const CONTENT_TYPE_SET = new Set<string>(CONTENT_TYPES);
const FORMED_BY_SET = new Set<string>(FORMED_BY_VALUES);
const CRED_STATUS_SET = new Set<string>(CRED_STATUSES);
const SOURCE_KIND_SET = new Set(['spoken', 'inferred', 'observed', 'tool']);
const EVIDENCE_RELATION_SET = new Set(['support', 'contradict']);
const VISIBLE_TURN_ROLE_SET = new Set(['user', 'assistant', 'tool']);
const RESPONSE_ACT_SET = new Set([
  'affirm',
  'negate',
  'select',
  'elaborate',
  'ask',
  'none',
  'other',
]);
const PROMPT_ACT_SET = new Set(['propose', 'ask', 'state', 'none', 'other']);
const PROPOSITION_ORIGIN_SET = new Set(['user_stated', 'assistant_proposed']);
const ASSERTION_STRENGTH_SET = new Set(['explicit', 'weak', 'none']);

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === 'string' && value !== '';
const isNullableString = (value: unknown): boolean => value === null || typeof value === 'string';
const ISO_DATE_TIME =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

/** 跨语言 strict ISO-8601-with-time-and-zone：不依赖 Date.parse 的宽松/RFC 兼容或溢出归一化。 */
export const isStrictIsoDateTime = (value: unknown): value is string => {
  if (typeof value !== 'string') return false;
  const match = ISO_DATE_TIME.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, zone] = match;
  if (
    yearText === undefined ||
    monthText === undefined ||
    dayText === undefined ||
    hourText === undefined ||
    minuteText === undefined ||
    zone === undefined
  )
    return false;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = secondText === undefined ? 0 : Number(secondText);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]!;
  if (day < 1 || day > daysInMonth) return false;
  if (zone !== 'Z') {
    const [offsetHour, offsetMinute] = zone.slice(1).split(':').map(Number);
    if (offsetHour! > 23 || offsetMinute! > 59) return false;
  }
  return true;
};
const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object';

export function validateBundle(bundle: unknown): ValidateResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  const lang = resolveLang();
  const invalidField = (entity: string, id: unknown, field: string) =>
    errors.push(
      lang === 'zh'
        ? `${entity} ${String(id)} 的 ${field} 非法或缺失`
        : `${entity} ${String(id)} has an invalid or missing ${field}`,
    );

  if (bundle == null || typeof bundle !== 'object') {
    return {
      valid: false,
      errors: [lang === 'zh' ? 'bundle 不是对象' : 'bundle is not an object'],
      warnings,
    };
  }
  const b = bundle as Partial<MemoryBundle>;

  if (b.format !== BUNDLE_FORMAT) {
    errors.push(
      lang === 'zh'
        ? `format 应为 "${BUNDLE_FORMAT}"，实为 ${JSON.stringify(b.format)}`
        : `format should be "${BUNDLE_FORMAT}", but got ${JSON.stringify(b.format)}`,
    );
  }
  if (
    typeof b.schemaVersion !== 'number' ||
    !Number.isFinite(b.schemaVersion) ||
    !Number.isInteger(b.schemaVersion)
  ) {
    errors.push(
      lang === 'zh' ? 'schemaVersion 缺失或非数字' : 'schemaVersion is missing or not a number',
    );
  } else if (b.schemaVersion > BUNDLE_SCHEMA_VERSION) {
    errors.push(
      lang === 'zh'
        ? `schemaVersion=${b.schemaVersion} 高于当前 MemoWeft 支持的 ${BUNDLE_SCHEMA_VERSION}（请升级 MemoWeft 再导入）`
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
  if (!isStrictIsoDateTime(b.exportedAt)) {
    errors.push(lang === 'zh' ? 'exportedAt 非法或缺失' : 'exportedAt is invalid or missing');
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
    if (!Array.isArray(arr))
      errors.push(lang === 'zh' ? `data.${name} 应为数组` : `data.${name} should be an array`);
  }
  if (errors.length > 0) return { valid: false, errors, warnings };

  // 每个元素必须有非空字符串 id / 端点：防 undefined 混进 Set 掩盖引用检查，也防 undefined 落库。
  const badId = (x: unknown) => !isRecord(x) || !isNonEmptyString(x.id);
  if (data.evidence.some(badId))
    errors.push(
      lang === 'zh'
        ? 'data.evidence 存在缺 id 的元素'
        : 'data.evidence has an element with a missing id',
    );
  if (data.events.some(badId))
    errors.push(
      lang === 'zh'
        ? 'data.events 存在缺 id 的元素'
        : 'data.events has an element with a missing id',
    );
  if (data.cognitions.some(badId))
    errors.push(
      lang === 'zh'
        ? 'data.cognitions 存在缺 id 的元素'
        : 'data.cognitions has an element with a missing id',
    );
  for (const l of data.eventEvidence) {
    if (!isRecord(l) || typeof l.eventId !== 'string' || typeof l.evidenceId !== 'string') {
      errors.push(
        lang === 'zh'
          ? 'data.eventEvidence 存在非法端点'
          : 'data.eventEvidence has an invalid endpoint',
      );
      break;
    }
  }
  for (const l of data.cognitionEvidence) {
    if (!isRecord(l) || typeof l.cognitionId !== 'string' || typeof l.evidenceId !== 'string') {
      errors.push(
        lang === 'zh'
          ? 'data.cognitionEvidence 存在非法端点'
          : 'data.cognitionEvidence has an invalid endpoint',
      );
      break;
    }
  }
  if (errors.length > 0) return { valid: false, errors, warnings };

  // 到这里五个数组都在、元素 id 都是非空字符串。
  const evidenceIds = new Set(data.evidence.map((e) => e.id));
  const eventIds = new Set(data.events.map((e) => e.id));
  const cognitionIds = new Set(data.cognitions.map((c) => c.id));

  // 包内 id 必须唯一（否则 merge 阶段会撞 PRIMARY KEY 抛错）。
  if (evidenceIds.size !== data.evidence.length)
    errors.push(lang === 'zh' ? 'data.evidence 存在重复 id' : 'data.evidence has duplicate ids');
  if (eventIds.size !== data.events.length)
    errors.push(lang === 'zh' ? 'data.events 存在重复 id' : 'data.events has duplicate ids');
  if (cognitionIds.size !== data.cognitions.length)
    errors.push(
      lang === 'zh' ? 'data.cognitions 存在重复 id' : 'data.cognitions has duplicate ids',
    );

  // 引用完整性（致命）：join 行指向的两端都必须在包内。
  for (const link of data.eventEvidence) {
    if (!eventIds.has(link.eventId))
      errors.push(
        lang === 'zh'
          ? `eventEvidence 指向不存在的 event: ${link.eventId}`
          : `eventEvidence references a non-existent event: ${link.eventId}`,
      );
    if (!evidenceIds.has(link.evidenceId))
      errors.push(
        lang === 'zh'
          ? `eventEvidence 指向不存在的 evidence: ${link.evidenceId}`
          : `eventEvidence references a non-existent evidence: ${link.evidenceId}`,
      );
  }
  for (const link of data.cognitionEvidence) {
    if (!cognitionIds.has(link.cognitionId))
      errors.push(
        lang === 'zh'
          ? `cognitionEvidence 指向不存在的 cognition: ${link.cognitionId}`
          : `cognitionEvidence references a non-existent cognition: ${link.cognitionId}`,
      );
    if (!evidenceIds.has(link.evidenceId))
      errors.push(
        lang === 'zh'
          ? `cognitionEvidence 指向不存在的 evidence: ${link.evidenceId}`
          : `cognitionEvidence references a non-existent evidence: ${link.evidenceId}`,
      );
  }

  // join 表的复合键必须在 bundle 内唯一。重复 support 会膨胀 supportCount、confidence 与模型输入；
  // SQLite 未必有联合唯一约束，故在唯一的外部导入守门处 fail-closed。
  const eventEvidenceTuples = new Set<string>();
  for (const link of data.eventEvidence) {
    const tuple = `${link.eventId}\u0000${link.evidenceId}`;
    if (eventEvidenceTuples.has(tuple))
      errors.push(
        lang === 'zh'
          ? `data.eventEvidence 存在重复 link: ${link.eventId}/${link.evidenceId}`
          : `data.eventEvidence has duplicate link: ${link.eventId}/${link.evidenceId}`,
      );
    eventEvidenceTuples.add(tuple);
  }
  const cognitionEvidenceTuples = new Set<string>();
  for (const link of data.cognitionEvidence) {
    const tuple = `${link.cognitionId}\u0000${link.evidenceId}\u0000${link.relation}`;
    if (cognitionEvidenceTuples.has(tuple))
      errors.push(
        lang === 'zh'
          ? `data.cognitionEvidence 存在重复 link: ${link.cognitionId}/${link.evidenceId}/${link.relation}`
          : `data.cognitionEvidence has duplicate link: ${link.cognitionId}/${link.evidenceId}/${link.relation}`,
      );
    cognitionEvidenceTuples.add(tuple);
  }

  // Portable bundle 是单 subject 边界，不是混装容器。显式校验每个实体与每条 join 的归属，
  // 防止 A 的 cognition 通过 link 引用 B 的 evidence，把 B 的摘要泄露到 A 的 explain/导出路径。
  const evidenceSubjects = new Map(data.evidence.map((e) => [e.id, e.subjectId]));
  const eventSubjects = new Map(data.events.map((e) => [e.id, e.subjectId]));
  const cognitionSubjects = new Map(data.cognitions.map((c) => [c.id, c.subjectId]));
  for (const evidence of data.evidence) {
    if (evidence.subjectId !== b.subjectId)
      errors.push(
        lang === 'zh'
          ? `evidence ${evidence.id} 的 subjectId(${evidence.subjectId}) 与包(${b.subjectId})不一致`
          : `evidence ${evidence.id} subjectId(${evidence.subjectId}) does not match the bundle(${b.subjectId})`,
      );
  }
  for (const event of data.events) {
    if (event.subjectId !== b.subjectId)
      errors.push(
        lang === 'zh'
          ? `event ${event.id} 的 subjectId(${event.subjectId}) 与包不一致`
          : `event ${event.id} subjectId(${event.subjectId}) does not match the bundle`,
      );
  }
  for (const cognition of data.cognitions) {
    if (cognition.subjectId !== b.subjectId)
      errors.push(
        lang === 'zh'
          ? `cognition ${cognition.id} 的 subjectId(${cognition.subjectId}) 与包不一致`
          : `cognition ${cognition.id} subjectId(${cognition.subjectId}) does not match the bundle`,
      );
  }
  for (const link of data.eventEvidence) {
    const eventSubject = eventSubjects.get(link.eventId);
    const evidenceSubject = evidenceSubjects.get(link.evidenceId);
    if (
      eventSubject !== undefined &&
      evidenceSubject !== undefined &&
      eventSubject !== evidenceSubject
    )
      errors.push(
        lang === 'zh'
          ? `eventEvidence 跨 subject 引用: ${link.eventId}/${link.evidenceId}`
          : `eventEvidence crosses subjects: ${link.eventId}/${link.evidenceId}`,
      );
  }
  for (const link of data.cognitionEvidence) {
    const cognitionSubject = cognitionSubjects.get(link.cognitionId);
    const evidenceSubject = evidenceSubjects.get(link.evidenceId);
    if (
      cognitionSubject !== undefined &&
      evidenceSubject !== undefined &&
      cognitionSubject !== evidenceSubject
    )
      errors.push(
        lang === 'zh'
          ? `cognitionEvidence 跨 subject 引用: ${link.cognitionId}/${link.evidenceId}`
          : `cognitionEvidence crosses subjects: ${link.cognitionId}/${link.evidenceId}`,
      );
  }

  // cognition 字段值校验（致命）：枚举越界 / confidence 非法。
  //   为什么在这道守门拦：cognition 表 content_type/formed_by 列无 CHECK、confidence 列靠
  //   SQLite 类型亲和性也拦不住字符串，importBundle 又完全信任 valid=true 直插（见文件头）。
  //   content_type 认【完整 8 值】(含 hypothesis/trend)——导入的是已落库认知，可能由
  //   attribute/trends 产出这两类，不能只认 consolidate 收的那 6 个（那会误杀合法认知）。
  for (const c of data.cognitions) {
    if (!CONTENT_TYPE_SET.has(c.contentType as ContentType))
      errors.push(
        lang === 'zh'
          ? `cognition ${c.id} 的 content_type 非法: ${JSON.stringify(c.contentType)}`
          : `cognition ${c.id} has an invalid content_type: ${JSON.stringify(c.contentType)}`,
      );
    if (!FORMED_BY_SET.has(c.formedBy as FormedBy))
      errors.push(
        lang === 'zh'
          ? `cognition ${c.id} 的 formed_by 非法: ${JSON.stringify(c.formedBy)}`
          : `cognition ${c.id} has an invalid formed_by: ${JSON.stringify(c.formedBy)}`,
      );
    if (!CRED_STATUS_SET.has(c.credStatus as CredStatus))
      errors.push(
        lang === 'zh'
          ? `cognition ${c.id} 的 cred_status 非法: ${JSON.stringify(c.credStatus)}`
          : `cognition ${c.id} has an invalid cred_status: ${JSON.stringify(c.credStatus)}`,
      );
    // confidence 必须是 0~1000 的整数：非数字/NaN/小数/越界一律拒（否则读时算术全 NaN 或类型污染）。
    if (
      typeof c.confidence !== 'number' ||
      !Number.isInteger(c.confidence) ||
      c.confidence < 0 ||
      c.confidence > 1000
    )
      errors.push(
        lang === 'zh'
          ? `cognition ${c.id} 的 confidence 非法(应为 0~1000 的整数): ${JSON.stringify(c.confidence)}`
          : `cognition ${c.id} has an invalid confidence (must be an integer 0-1000): ${JSON.stringify(c.confidence)}`,
      );
  }

  // 外部 bundle 绕开了各 store 的写路径，因此所有会进入下游分支或 SQLite 的字段也必须在此校验。
  // 日期只要求可被运行时解析，不重写旧 v2 bundle 的原始时间字符串。
  for (const e of data.evidence) {
    if (!isNonEmptyString(e.subjectId)) invalidField('evidence', e.id, 'subjectId');
    if (!SOURCE_KIND_SET.has(e.sourceKind)) invalidField('evidence', e.id, 'sourceKind');
    if (!isNonEmptyString(e.hostId)) invalidField('evidence', e.id, 'hostId');
    if (!isStrictIsoDateTime(e.occurredAt)) invalidField('evidence', e.id, 'occurredAt');
    if (!isStrictIsoDateTime(e.recordedAt)) invalidField('evidence', e.id, 'recordedAt');
    // 稳定写路径允许空消息（例如宿主的占位/空提交）；exportBundle 必须能重新导入自己的产物。
    if (typeof e.rawContent !== 'string') invalidField('evidence', e.id, 'rawContent');
    if (typeof e.summary !== 'string') invalidField('evidence', e.id, 'summary');
    if (typeof e.allowLocalRead !== 'boolean') invalidField('evidence', e.id, 'allowLocalRead');
    if (typeof e.allowCloudRead !== 'boolean') invalidField('evidence', e.id, 'allowCloudRead');
    if (typeof e.allowInference !== 'boolean') invalidField('evidence', e.id, 'allowInference');
    if (!isNullableString(e.originId)) invalidField('evidence', e.id, 'originId');
    if (!isNullableString(e.correctsEvidenceId))
      invalidField('evidence', e.id, 'correctsEvidenceId');
  }
  for (const e of data.events) {
    if (!isNonEmptyString(e.subjectId)) invalidField('event', e.id, 'subjectId');
    if (typeof e.summary !== 'string') invalidField('event', e.id, 'summary');
    if (!isStrictIsoDateTime(e.occurredAt)) invalidField('event', e.id, 'occurredAt');
    if (!isStrictIsoDateTime(e.createdAt)) invalidField('event', e.id, 'createdAt');
  }
  for (const c of data.cognitions) {
    if (!isNonEmptyString(c.subjectId)) invalidField('cognition', c.id, 'subjectId');
    if (!isNonEmptyString(c.content)) invalidField('cognition', c.id, 'content');
    if (!isNullableString(c.scope)) invalidField('cognition', c.id, 'scope');
    for (const [field, value] of [
      ['validAt', c.validAt],
      ['invalidAt', c.invalidAt],
      ['askedAt', c.askedAt],
      ['archivedAt', c.archivedAt],
      ['mutedAt', c.mutedAt],
    ] as const) {
      if (value !== undefined && value !== null && !isStrictIsoDateTime(value))
        invalidField('cognition', c.id, field);
    }
    if (!isStrictIsoDateTime(c.createdAt)) invalidField('cognition', c.id, 'createdAt');
    if (!isStrictIsoDateTime(c.updatedAt)) invalidField('cognition', c.id, 'updatedAt');
  }
  for (const link of data.cognitionEvidence) {
    if (!EVIDENCE_RELATION_SET.has(link.relation))
      invalidField('cognitionEvidence', `${link.cognitionId}/${link.evidenceId}`, 'relation');
  }

  // 软告警：correctsEvidenceId 指向包外（非致命——导入后目标库可能已有那条）。
  for (const e of data.evidence) {
    if (e.correctsEvidenceId != null && !evidenceIds.has(e.correctsEvidenceId)) {
      warnings.push(
        lang === 'zh'
          ? `evidence ${e.id} 的 correctsEvidenceId(${e.correctsEvidenceId}) 不在包内`
          : `evidence ${e.id} correctsEvidenceId(${e.correctsEvidenceId}) is not in the bundle`,
      );
    }
  }

  // unconsolidatedEventIds（保真 consolidated 标记）：若存在，须是数组且指向包内事件。
  const unconsIds = data.unconsolidatedEventIds;
  if (unconsIds !== undefined) {
    if (!Array.isArray(unconsIds)) {
      errors.push(
        lang === 'zh'
          ? 'data.unconsolidatedEventIds 应为数组'
          : 'data.unconsolidatedEventIds should be an array',
      );
    } else {
      for (const id of unconsIds)
        if (!eventIds.has(id))
          warnings.push(
            lang === 'zh'
              ? `unconsolidatedEventIds 含未知 event: ${id}`
              : `unconsolidatedEventIds contains an unknown event: ${id}`,
          );
    }
  }

  // 交互层（v0.6，可选：v2 包带、v1 包无 → 跳过）：若存在须为数组且元素有非空 id。
  for (const [name, arr] of [
    ['interactionContexts', data.interactionContexts],
    ['semanticResolutions', data.semanticResolutions],
  ] as Array<[string, unknown]>) {
    if (arr === undefined) continue;
    if (!Array.isArray(arr)) {
      errors.push(lang === 'zh' ? `data.${name} 应为数组` : `data.${name} should be an array`);
    } else if (arr.some((x) => !isRecord(x) || !isNonEmptyString(x.id))) {
      errors.push(
        lang === 'zh'
          ? `data.${name} 存在缺 id 的元素`
          : `data.${name} has an element with a missing id`,
      );
    }
  }

  // v2 interaction 记录与 semantic resolution 也来自外部文件，不能把未验证字段直插进弱约束表。
  const interactionContexts = data.interactionContexts;
  if (Array.isArray(interactionContexts)) {
    const contextIds = new Set<string>();
    for (const value of interactionContexts as unknown[]) {
      if (!isRecord(value) || !isNonEmptyString(value.id)) continue;
      if (contextIds.has(value.id))
        errors.push(
          lang === 'zh'
            ? 'data.interactionContexts 存在重复 id'
            : 'data.interactionContexts has duplicate ids',
        );
      contextIds.add(value.id);
      for (const field of ['subjectId', 'conversationId', 'episodeId', 'contextHash'] as const)
        if (!isNonEmptyString(value[field])) invalidField('interactionContext', value.id, field);
      if (value.subjectId !== b.subjectId)
        errors.push(
          lang === 'zh'
            ? `interactionContext ${value.id} 的 subjectId(${String(value.subjectId)}) 与包不一致`
            : `interactionContext ${value.id} subjectId(${String(value.subjectId)}) does not match the bundle`,
        );
      if (!isStrictIsoDateTime(value.createdAt))
        invalidField('interactionContext', value.id, 'createdAt');
      if (!Array.isArray(value.context)) {
        invalidField('interactionContext', value.id, 'context');
      } else {
        let contextValid = true;
        for (const turn of value.context) {
          if (!isRecord(turn) || !VISIBLE_TURN_ROLE_SET.has(turn.role as string)) {
            invalidField('interactionContext', value.id, 'context.role');
            contextValid = false;
          }
          if (!isRecord(turn) || typeof turn.content !== 'string') {
            invalidField('interactionContext', value.id, 'context.content');
            contextValid = false;
          }
        }
        if (
          contextValid &&
          isNonEmptyString(value.contextHash) &&
          hashContext(value.context as VisibleTurn[]) !== value.contextHash
        ) {
          errors.push(
            lang === 'zh'
              ? `interactionContext ${value.id} 的 contextHash 与 context 内容不匹配`
              : `interactionContext ${value.id} contextHash does not match its context`,
          );
        }
      }
    }
  }

  const semanticResolutions = data.semanticResolutions;
  if (Array.isArray(semanticResolutions)) {
    const resolutionIds = new Set<string>();
    const evidenceWithResolution = new Set<string>();
    for (const value of semanticResolutions as unknown[]) {
      if (!isRecord(value) || !isNonEmptyString(value.id)) continue;
      if (resolutionIds.has(value.id))
        errors.push(
          lang === 'zh'
            ? 'data.semanticResolutions 存在重复 id'
            : 'data.semanticResolutions has duplicate ids',
        );
      resolutionIds.add(value.id);
      if (!isNonEmptyString(value.evidenceId) || !evidenceIds.has(value.evidenceId))
        errors.push(
          lang === 'zh'
            ? `semanticResolution ${value.id} 指向不存在的 evidence: ${String(value.evidenceId)}`
            : `semanticResolution ${value.id} references a non-existent evidence: ${String(value.evidenceId)}`,
        );
      else if (evidenceWithResolution.has(value.evidenceId))
        errors.push(
          lang === 'zh'
            ? `data.semanticResolutions 的 evidenceId 重复: ${value.evidenceId}`
            : `data.semanticResolutions has duplicate evidenceId: ${value.evidenceId}`,
        );
      else evidenceWithResolution.add(value.evidenceId);
      if (typeof value.resolvedContent !== 'string')
        invalidField('semanticResolution', value.id, 'resolvedContent');
      if (!isNonEmptyString(value.resolverVersion))
        invalidField('semanticResolution', value.id, 'resolverVersion');
      if (!isStrictIsoDateTime(value.createdAt))
        invalidField('semanticResolution', value.id, 'createdAt');
      if (value.responseAct !== null && !RESPONSE_ACT_SET.has(value.responseAct as string))
        invalidField('semanticResolution', value.id, 'responseAct');
      if (value.promptAct !== null && !PROMPT_ACT_SET.has(value.promptAct as string))
        invalidField('semanticResolution', value.id, 'promptAct');
      if (
        value.propositionOrigin !== null &&
        !PROPOSITION_ORIGIN_SET.has(value.propositionOrigin as string)
      )
        invalidField('semanticResolution', value.id, 'propositionOrigin');
      if (
        value.assertionStrength !== null &&
        !ASSERTION_STRENGTH_SET.has(value.assertionStrength as string)
      )
        invalidField('semanticResolution', value.id, 'assertionStrength');
      if (!isNullableString(value.requiredContext))
        invalidField('semanticResolution', value.id, 'requiredContext');
    }
  }

  return { valid: errors.length === 0, errors, warnings };
}
