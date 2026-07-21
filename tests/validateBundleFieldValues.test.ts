/**
 * validateBundle 的字段值校验（导入路径的数据完整性护栏）。
 *
 * 靶心：validateBundle 此前只查【结构 + id + 引用完整性】，不查 cognition 的字段【值】。
 * 于是越界枚举与非法 confidence 能通过校验、被 importBundle 直插进库（importBundle.ts 完全
 * 信任 validateBundle 的 valid=true，见其 `if (!validation.valid) return plan`）。实测后果：
 *   - 越界 formed_by='bogus' → 落库时 NOT NULL 拦不住（是合法字符串）→ **延迟雷**：
 *     下次任何 computeConfidence 重算时 baseByFormedBy['bogus']=undefined → 结果 NaN，
 *     而 NaN 写不进 `confidence INTEGER NOT NULL` → 那次重算整体失败。用户导入时看着成功。
 *   - 越界 content_type='locaton' → 静默落库 → 下游所有按类型分支的逻辑失效
 *     （衰减/过期/召回门控/趋势聚合）。
 *   - confidence 非整数（字符串/NaN/越界）→ 类型污染，读时算术全 NaN。
 *
 * 这些值全部来自【外部文件】，与 LLM 无关。导入路径没有 consolidate 那层「非法值兜底成 fact」
 * 的保护（那是写路径独有的），故必须在这道唯一的守门 validateBundle 上拦住。
 *
 * 全离线，不碰 store、不碰 LLM。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateBundle } from '../src/portable/validateBundle.ts';
import type { MemoryBundle } from '../src/portable/model.ts';

/** 造一个最小的合法 bundle（一条 evidence + 一条 cognition + 溯源链）。 */
function goodBundle(): MemoryBundle {
  const t = '2026-01-01T00:00:00.000Z';
  return {
    format: 'memoweft-bundle',
    schemaVersion: 2,
    exportedAt: t,
    memoWeftVersion: '0.6.0',
    subjectId: 'u',
    source: 'test',
    data: {
      evidence: [
        {
          id: 'ev-1',
          subjectId: 'u',
          sourceKind: 'spoken',
          hostId: 'h',
          originId: null,
          occurredAt: t,
          recordedAt: t,
          rawContent: '我喜欢咖啡',
          summary: '',
          allowLocalRead: true,
          allowCloudRead: true,
          allowInference: true,
          correctsEvidenceId: null,
        },
      ],
      events: [],
      eventEvidence: [],
      cognitions: [
        {
          id: 'cog-1',
          subjectId: 'u',
          content: '用户喜欢咖啡',
          contentType: 'preference',
          formedBy: 'stated',
          confidence: 600,
          credStatus: 'limited',
          scope: null,
          validAt: null,
          invalidAt: null,
          askedAt: null,
          archivedAt: null,
          mutedAt: null,
          createdAt: t,
          updatedAt: t,
        },
      ],
      cognitionEvidence: [{ cognitionId: 'cog-1', evidenceId: 'ev-1', relation: 'support' }],
    },
  } as unknown as MemoryBundle;
}

/** 改坏第一条 cognition 的某个字段。 */
function withCognition(mut: (c: Record<string, unknown>) => void): MemoryBundle {
  const b = goodBundle();
  mut(b.data.cognitions[0] as unknown as Record<string, unknown>);
  return b;
}

test('基线：干净 bundle 校验通过', () => {
  const r = validateBundle(goodBundle());
  assert.equal(r.valid, true, r.errors.join(' | '));
});

test('越界 content_type → 拒绝（否则静默污染下游按类型分支的逻辑）', () => {
  const r = validateBundle(withCognition((c) => (c.contentType = 'locaton')));
  assert.equal(r.valid, false);
  assert.ok(
    r.errors.some((e) => e.includes('locaton') || e.toLowerCase().includes('content')),
    '应报出非法 content_type：' + r.errors.join(' | '),
  );
});

test('越界 formed_by → 拒绝（否则是延迟雷：下次重算 NaN、那次重算失败）', () => {
  const r = validateBundle(withCognition((c) => (c.formedBy = 'bogus')));
  assert.equal(r.valid, false);
  assert.ok(
    r.errors.some((e) => e.includes('bogus') || e.toLowerCase().includes('formed')),
    '应报出非法 formed_by：' + r.errors.join(' | '),
  );
});

test('hypothesis / trend 是合法 content_type（导入的认知可能含它们，不能误杀）', () => {
  for (const ct of ['hypothesis', 'trend'] as const) {
    const r = validateBundle(withCognition((c) => (c.contentType = ct)));
    assert.equal(r.valid, true, `${ct} 应被接受，却报：` + r.errors.join(' | '));
  }
});

test('越界 cred_status → 拒绝', () => {
  const r = validateBundle(withCognition((c) => (c.credStatus = 'superb')));
  assert.equal(r.valid, false);
  assert.ok(r.errors.some((e) => e.toLowerCase().includes('cred') || e.includes('superb')));
});

test('confidence 是字符串 → 拒绝（否则 "abc" 落进 INTEGER 列，读时算术全 NaN）', () => {
  const r = validateBundle(withCognition((c) => (c.confidence = 'abc')));
  assert.equal(r.valid, false);
  assert.ok(r.errors.some((e) => e.toLowerCase().includes('confidence')));
});

test('confidence 是 NaN → 拒绝', () => {
  const r = validateBundle(withCognition((c) => (c.confidence = Number.NaN)));
  assert.equal(r.valid, false);
  assert.ok(r.errors.some((e) => e.toLowerCase().includes('confidence')));
});

test('confidence 非整数（小数）→ 拒绝', () => {
  const r = validateBundle(withCognition((c) => (c.confidence = 600.5)));
  assert.equal(r.valid, false);
  assert.ok(r.errors.some((e) => e.toLowerCase().includes('confidence')));
});

test('confidence 越界（<0 或 >1000）→ 拒绝', () => {
  for (const v of [-1, 1001]) {
    const r = validateBundle(withCognition((c) => (c.confidence = v)));
    assert.equal(r.valid, false, `confidence=${v} 应被拒`);
  }
});

test('confidence 边界值 0 和 1000 → 接受', () => {
  for (const v of [0, 1000]) {
    const r = validateBundle(withCognition((c) => (c.confidence = v)));
    assert.equal(r.valid, true, `confidence=${v} 应被接受：` + r.errors.join(' | '));
  }
});

test('多个字段同时非法 → 每个都报（不因短路只报第一个）', () => {
  const r = validateBundle(
    withCognition((c) => {
      c.contentType = 'locaton';
      c.formedBy = 'bogus';
      c.confidence = 'abc';
    }),
  );
  assert.equal(r.valid, false);
  assert.ok(r.errors.length >= 3, '三个非法字段应各报一条：' + r.errors.join(' | '));
});
