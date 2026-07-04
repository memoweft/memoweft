/**
 * dist 冒烟脚本（T6 步2·Node 20 job 专用）。
 *
 * 为什么单独有它：Node 20 没有原生剥 .ts 类型的能力，`node --test tests/**\/*.ts` 物理上跑不起来
 *   （引 tsx 之类新 dev 依赖被 CONTRIBUTING「默认拒绝新依赖」挡下，未经作者拍板不加）。
 *   所以 Node 20 的验收改走这条：先 `npm run build` 出 dist（纯 JS），再用本脚本走一遍真实开库链，
 *   证明 better-sqlite3 驱动在 Node 20 上把 memoweft 跑通。
 *
 * 走的链：开库（openStores，内部选中 better-sqlite3 驱动 + 自动跑迁移）→ 存证据 + 存认知
 *   → 读回校验 → 查 schema 版本（迁移已把新库盖到最新版）→ 关库。全同步、无 LLM、纯离线。
 *
 * 跑法：`MEMOWEFT_TEST_DRIVER=better-sqlite3 node tests/smoke/dist-smoke.mjs`
 *   （Node 20 上本来也没 node:sqlite，强制只为把意图写死、避免误判走了别的驱动）。
 *   通过 → 退出码 0 并打印 OK；任一步不符 → throw、退出码非 0，CI job 变红。
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';

// 从 build 产物导入（不是 src）——本脚本验的就是"发出去的 dist 在 Node 20 上能跑"。
import { openStores, getSchemaVersion, LATEST_SCHEMA_VERSION, MEMOWEFT_VERSION } from '../../dist/index.js';

const dir = mkdtempSync(join(tmpdir(), 'mw-smoke-'));
const dbPath = join(dir, 'smoke.db');

function main() {
  console.log(`[smoke] memoweft ${MEMOWEFT_VERSION} · Node ${process.versions.node} · 驱动=${process.env.MEMOWEFT_TEST_DRIVER ?? '(自动选择)'}`);

  // 1) 开库：openStores 是同步 API；内部选驱动 + 自动跑迁移（新库直接盖最新版）。
  const s = openStores(dbPath);
  try {
    // 2) 新库迁移已把 schema 盖到最新版。
    assert.equal(getSchemaVersion(s.db), LATEST_SCHEMA_VERSION, 'schema 已升到最新版');

    // 3) 存一条证据 + 一条认知（走真实写路径，命名对象绑定 + 位置绑定都会被触达）。
    const ev = s.evidenceStore.put({
      subjectId: 'owner',
      sourceKind: 'spoken',
      hostId: 'smoke',
      rawContent: '我喜欢喝茶',
    });
    assert.ok(ev.id, '证据已落库、拿到 id');

    const cog = s.cognitionStore.put({
      subjectId: 'owner',
      content: '喜欢喝茶',
      contentType: 'preference',
      formedBy: 'stated',
      confidence: 500,
      credStatus: 'limited',
      evidence: [{ evidenceId: ev.id, relation: 'support' }],
    });
    assert.ok(cog.id, '认知已落库、拿到 id');

    // 4) 读回校验（get 位置绑定 + all 无参绑定）。
    assert.equal(s.evidenceStore.get(ev.id)?.rawContent, '我喜欢喝茶', '证据读回一致');
    assert.equal(s.cognitionStore.all('owner').length, 1, '认知读回一条');
    assert.equal(s.cognitionStore.sourcesOf(cog.id).length, 1, '认知↔证据链读回一条');
  } finally {
    // 5) 关库。
    s.close();
    rmSync(dir, { recursive: true, force: true });
  }

  console.log('[smoke] OK · better-sqlite3 驱动在本 Node 上把开库→写→读→迁移→关库全链跑通');
}

main();
