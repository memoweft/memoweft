/**
 * 校验便携记忆包。纯函数，不触库。
 *
 * 分级：
 *  - errors（致命，valid=false，绝不导入）：格式/版本/必需字段不对；溯源引用悬空。
 *  - warnings（软提示，可导入）：subject 混入；correctsEvidenceId 指向包外；旧 schemaVersion。
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

const CONTENT_TYPE_SET = new Set<string>(CONTENT_TYPES);
const FORMED_BY_SET = new Set<string>(FORMED_BY_VALUES);
const CRED_STATUS_SET = new Set<string>(CRED_STATUSES);

export function validateBundle(bundle: unknown): ValidateResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  const lang = resolveLang();

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
  if (typeof b.schemaVersion !== 'number') {
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
  const badId = (x: { id?: unknown }) => typeof x.id !== 'string' || x.id === '';
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
    if (typeof l.eventId !== 'string' || typeof l.evidenceId !== 'string') {
      errors.push(
        lang === 'zh'
          ? 'data.eventEvidence 存在非法端点'
          : 'data.eventEvidence has an invalid endpoint',
      );
      break;
    }
  }
  for (const l of data.cognitionEvidence) {
    if (typeof l.cognitionId !== 'string' || typeof l.evidenceId !== 'string') {
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

  // 软告警：subject 混入（包声明是 A，却夹了 B 的行）。
  for (const e of data.evidence) {
    if (e.subjectId !== b.subjectId)
      warnings.push(
        lang === 'zh'
          ? `evidence ${e.id} 的 subjectId(${e.subjectId}) 与包(${b.subjectId})不一致`
          : `evidence ${e.id} subjectId(${e.subjectId}) does not match the bundle(${b.subjectId})`,
      );
  }
  for (const e of data.events) {
    if (e.subjectId !== b.subjectId)
      warnings.push(
        lang === 'zh'
          ? `event ${e.id} 的 subjectId(${e.subjectId}) 与包不一致`
          : `event ${e.id} subjectId(${e.subjectId}) does not match the bundle`,
      );
  }
  for (const c of data.cognitions) {
    if (c.subjectId !== b.subjectId)
      warnings.push(
        lang === 'zh'
          ? `cognition ${c.id} 的 subjectId(${c.subjectId}) 与包不一致`
          : `cognition ${c.id} subjectId(${c.subjectId}) does not match the bundle`,
      );
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
    } else if (
      arr.some(
        (x) => typeof (x as { id?: unknown }).id !== 'string' || (x as { id?: unknown }).id === '',
      )
    ) {
      errors.push(
        lang === 'zh'
          ? `data.${name} 存在缺 id 的元素`
          : `data.${name} has an element with a missing id`,
      );
    }
  }

  return { valid: errors.length === 0, errors, warnings };
}
