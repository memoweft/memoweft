/**
 * 黄金检索集结构校验。
 *
 * 这份测试**不测召回质量**（由独立评估工具负责），只锁死 golden.json 的
 * 结构不变量——让它作为一份可复现、可信赖的评测基准，任何后续手改都不会悄悄破坏它。
 *
 * golden.json 是完全合成的虚构人物数据；期望 id 独立于被测检索器输出生成。
 *
 * 逐条断言对应检验点：
 *   ① 所有 case.expect 的 id 都存在于 cognitions
 *   ② cognitions id 唯一、case id 唯一
 *   ③ 每条 cognition 至少被一条 case 覆盖
 *   ④ kind 配比 4:4:2 近似：direct∈[35%,50%]、paraphrase∈[30%,45%]、multihop∈[15%,30%]；
 *      且 direct/paraphrase ≥ multihop、三类都非空
 *   ⑤ 中文 case（query 含 CJK/汉字）≥10
 *   ⑥ case 数 ∈[50,100]、cognition 数 ∈[30,45]
 *   ⑦ contentType 覆盖 ≥6 种
 * 附加结构守卫（与公开 JSON schema 一致）：id 命名规范、kind/contentType 取值合法。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// ---- 类型形状（仅用于本测试的可读性，运行时被 Node 类型擦除）----
type ContentType =
  'fact' | 'preference' | 'goal' | 'project' | 'state' | 'trait' | 'hypothesis' | 'trend';
type Kind = 'direct' | 'paraphrase' | 'multihop';

interface Cognition {
  id: string;
  content: string;
  contentType: ContentType;
}
interface Case {
  id: string;
  query: string;
  expect: string[];
  kind: Kind;
}
interface Golden {
  cognitions: Cognition[];
  cases: Case[];
}

// 与 src/cognition/model.ts 的 ContentType 联合类型逐字一致（自包含，不 import src）。
const CONTENT_TYPES = new Set<string>([
  'fact',
  'preference',
  'goal',
  'project',
  'state',
  'trait',
  'hypothesis',
  'trend',
]);
const KINDS = new Set<string>(['direct', 'paraphrase', 'multihop']);

// 汉字/CJK 判定：只要 query 里出现一个汉字就算中文 case。
const HAN = /\p{Script=Han}/u;

// 直接读文件 + JSON.parse（不用 import assertion，跨 Node 版本最稳）。
const golden = JSON.parse(
  readFileSync(new URL('./golden.json', import.meta.url), 'utf8'),
) as Golden;
const cognitions = golden.cognitions;
const cases = golden.cases;

test('顶层结构：cognitions 与 cases 均为非空数组', () => {
  assert.ok(Array.isArray(cognitions), 'cognitions 必须是数组');
  assert.ok(Array.isArray(cases), 'cases 必须是数组');
  assert.ok(cognitions.length > 0, 'cognitions 不能为空');
  assert.ok(cases.length > 0, 'cases 不能为空');
});

test('② id 唯一：cognitions id 无重复', () => {
  const ids = cognitions.map((c) => c.id);
  assert.equal(new Set(ids).size, ids.length, `cognition id 有重复：${ids.join(',')}`);
});

test('② id 唯一：case id 无重复', () => {
  const ids = cases.map((c) => c.id);
  assert.equal(new Set(ids).size, ids.length, `case id 有重复：${ids.join(',')}`);
});

test('结构守卫：cognition 字段完整、id 形如 cog-NNN、contentType 合法', () => {
  const idPat = /^cog-\d{3}$/;
  for (const c of cognitions) {
    assert.ok(idPat.test(c.id), `cognition id 不符合 cog-NNN：${c.id}`);
    assert.equal(typeof c.content, 'string');
    assert.ok(c.content.trim().length > 0, `cognition ${c.id} content 不能为空`);
    assert.ok(
      CONTENT_TYPES.has(c.contentType),
      `cognition ${c.id} contentType 非法：${c.contentType}`,
    );
  }
});

test('结构守卫：case 字段完整、id 形如 G-NNN、kind 合法、expect 非空', () => {
  const idPat = /^G-\d{3}$/;
  for (const c of cases) {
    assert.ok(idPat.test(c.id), `case id 不符合 G-NNN：${c.id}`);
    assert.equal(typeof c.query, 'string');
    assert.ok(c.query.trim().length > 0, `case ${c.id} query 不能为空`);
    assert.ok(KINDS.has(c.kind), `case ${c.id} kind 非法：${c.kind}`);
    assert.ok(Array.isArray(c.expect) && c.expect.length > 0, `case ${c.id} expect 不能为空`);
    // 单条 case 内 expect 不应自我重复
    assert.equal(
      new Set(c.expect).size,
      c.expect.length,
      `case ${c.id} expect 有重复：${c.expect.join(',')}`,
    );
  }
});

test('① 所有 case.expect 的 id 都存在于 cognitions', () => {
  const known = new Set(cognitions.map((c) => c.id));
  const dangling: string[] = [];
  for (const c of cases) {
    for (const id of c.expect) {
      if (!known.has(id)) dangling.push(`${c.id}→${id}`);
    }
  }
  assert.equal(dangling.length, 0, `expect 指向不存在的 cognition：${dangling.join(', ')}`);
});

test('③ 每条 cognition 至少被一条 case 覆盖', () => {
  const covered = new Set<string>();
  for (const c of cases) for (const id of c.expect) covered.add(id);
  const orphans = cognitions.map((c) => c.id).filter((id) => !covered.has(id));
  assert.equal(orphans.length, 0, `以下 cognition 没有任何 case 覆盖：${orphans.join(', ')}`);
});

test('⑥ 规模：case 数 ∈[50,100]、cognition 数 ∈[30,45]', () => {
  assert.ok(
    cases.length >= 50 && cases.length <= 100,
    `case 数应在 [50,100]，实为 ${cases.length}`,
  );
  assert.ok(
    cognitions.length >= 30 && cognitions.length <= 45,
    `cognition 数应在 [30,45]，实为 ${cognitions.length}`,
  );
});

test('④ kind 配比：三类非空、direct/paraphrase ≥ multihop、各自落在 4:4:2 近似区间', () => {
  const n = cases.length;
  const count = { direct: 0, paraphrase: 0, multihop: 0 };
  for (const c of cases) count[c.kind]++;

  assert.ok(count.direct > 0, 'direct 不能为 0');
  assert.ok(count.paraphrase > 0, 'paraphrase 不能为 0');
  assert.ok(count.multihop > 0, 'multihop 不能为 0');

  assert.ok(
    count.direct >= count.multihop,
    `direct(${count.direct}) 应 ≥ multihop(${count.multihop})`,
  );
  assert.ok(
    count.paraphrase >= count.multihop,
    `paraphrase(${count.paraphrase}) 应 ≥ multihop(${count.multihop})`,
  );

  const pDirect = count.direct / n;
  const pPara = count.paraphrase / n;
  const pMulti = count.multihop / n;
  assert.ok(
    pDirect >= 0.35 && pDirect <= 0.5,
    `direct 占比应 ∈[35%,50%]，实为 ${(pDirect * 100).toFixed(1)}%`,
  );
  assert.ok(
    pPara >= 0.3 && pPara <= 0.45,
    `paraphrase 占比应 ∈[30%,45%]，实为 ${(pPara * 100).toFixed(1)}%`,
  );
  assert.ok(
    pMulti >= 0.15 && pMulti <= 0.3,
    `multihop 占比应 ∈[15%,30%]，实为 ${(pMulti * 100).toFixed(1)}%`,
  );
});

test('⑤ 中文 case（query 含汉字）≥10', () => {
  const zh = cases.filter((c) => HAN.test(c.query));
  assert.ok(zh.length >= 10, `中文 case 应 ≥10，实为 ${zh.length}`);
});

test('⑦ contentType 覆盖 ≥6 种', () => {
  const types = new Set(cognitions.map((c) => c.contentType));
  assert.ok(
    types.size >= 6,
    `contentType 覆盖应 ≥6 种，实为 ${types.size}：${[...types].join(',')}`,
  );
});
