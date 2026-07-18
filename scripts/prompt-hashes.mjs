/**
 * Governed prompt hash snapshot generator.
 *
 * 为什么存在：8 条受治理提示词收敛到 src/prompts/ 后，需要一道「内容改了就必须 bump version」的闸门。
 * 本脚本从 registry 现算每条 zh/en 的 sha256，写成一份【按 id 字母序、每行一条】的快照——
 * diff 时能一眼看出「哪条内容变了、版本却没动」。真正的红/绿闸门是 tests/prompts/registry.test.ts
 * （由 npm test 执行回归检查），本脚本只负责生成 / 校对那份快照。
 *
 * 用法（仿 scripts/api-snapshot.mjs）：
 *   node scripts/prompt-hashes.mjs           # 刷新快照（= npm run prompts:update）
 *   node scripts/prompt-hashes.mjs --check    # 只比对不写，不一致 exit 1
 *
 * 快照格式（每行一条）：
 *   <id>@<version>  zh=sha256:<hex>  en=sha256:<hex>
 */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { PROMPT_REGISTRY } from '../src/prompts/registry.ts';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const SNAPSHOT_PATH = join(ROOT, 'tests', 'prompts', 'prompt-hashes.snapshot');

const sha256 = (s) => createHash('sha256').update(s, 'utf8').digest('hex');

/** 生成快照文本（按 id 字母序，确定性）。 */
export function generateSnapshot() {
  const header =
    '// MemoWeft governed prompt hash snapshot.\n' +
    '// 禁止手改；改提示词内容必须 bump 其 version 再 `npm run prompts:update` 重生成。\n\n';
  const lines = [...PROMPT_REGISTRY]
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .map(
      (p) => `${p.id}@${p.version}  zh=sha256:${sha256(p.text.zh)}  en=sha256:${sha256(p.text.en)}`,
    );
  return header + lines.join('\n') + '\n';
}

const WARN =
  '\n⚠️  Prompt hash snapshot refreshed.\n' +
  '    Bump the prompt version, run the full consolidation benchmark, and include before/after scores.\n' +
  '    Preserve the evidence, conflict, and uncertainty constraints unless the change explicitly reviews them.\n';

// CLI 入口
if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const snapshot = generateSnapshot();
  if (process.argv.includes('--check')) {
    let current = '';
    try {
      current = readFileSync(SNAPSHOT_PATH, 'utf8');
    } catch {
      /* 首次无快照 */
    }
    if (current !== snapshot) {
      console.error(
        '提示词哈希快照与当前 registry 不一致。先更新版本，再运行 `npm run prompts:update` 刷新快照。',
      );
      process.exit(1);
    }
    console.log('提示词哈希快照一致。');
  } else {
    mkdirSync(dirname(SNAPSHOT_PATH), { recursive: true });
    writeFileSync(SNAPSHOT_PATH, snapshot, 'utf8');
    console.log(`已写入 ${SNAPSHOT_PATH}（${PROMPT_REGISTRY.length} 条）`);
    console.log(WARN);
  }
}
