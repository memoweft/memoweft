#!/usr/bin/env node
// 抽取 docs 与根 README 中【可运行】的 ```ts 围栏,逐个用 Node 冒烟跑(无 key、内存库)。
// 紧邻围栏上方有 <!-- snippet:skip ... --> 的跳过(需模型 / 长驻服务 / 需完整写路径等)。
// 前置:先 `npm run build` —— 片段以 `import 'memoweft'` 解析到 dist(包 self-reference)。
// 用法:node scripts/doc-snippets.mjs   (cwd = 仓库根)
import { readFileSync, readdirSync, statSync, writeFileSync, mkdirSync, rmSync, existsSync } from 'node:fs';
import { join, relative } from 'node:path';
import { execFileSync } from 'node:child_process';

const ROOT = process.cwd();
const TMP = join(ROOT, '.doc-snippets-tmp');

if (!existsSync(join(ROOT, 'dist', 'index.js'))) {
  console.error('dist/ 缺失 —— 先跑 `npm run build`(片段 import "memoweft" 需解析到 dist)。');
  process.exit(1);
}

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '.git') continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, acc);
    else if (name.endsWith('.md')) acc.push(p);
  }
  return acc;
}
const files = walk(join(ROOT, 'docs'));
for (const extra of ['README.md', 'README.zh-CN.md']) {
  const p = join(ROOT, extra);
  if (existsSync(p)) files.push(p);
}

// 抽 ```ts 围栏;向上跳过空行看最近非空行是否 snippet:skip。
function extractRunnable(file) {
  const lines = readFileSync(file, 'utf8').split(/\r?\n/);
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    if (!/^```ts\s*$/.test(lines[i])) continue;
    let j = i - 1;
    while (j >= 0 && lines[j].trim() === '') j--;
    const skip = j >= 0 && /<!--\s*snippet:skip/.test(lines[j]);
    const body = [];
    let k = i + 1;
    for (; k < lines.length; k++) { if (/^```\s*$/.test(lines[k])) break; body.push(lines[k]); }
    if (!skip) out.push({ file, line: i + 1, code: body.join('\n') });
    i = k;
  }
  return out;
}

const snippets = files.flatMap(extractRunnable);
mkdirSync(TMP, { recursive: true });
let fail = 0;
snippets.forEach((s, idx) => {
  const f = join(TMP, `snippet-${idx}.ts`);
  writeFileSync(f, s.code + '\n');
  const label = `${relative(ROOT, s.file).replace(/\\/g, '/')}:${s.line}`;
  try {
    execFileSync(process.execPath, [f], { stdio: 'pipe', timeout: 30000 });
    console.log(`ok    ${label}`);
  } catch (e) {
    const msg = (e.stderr || e.stdout || e.message || '').toString().trim();
    console.log(`FAIL  ${label}\n${msg.split('\n').map((l) => '        ' + l).join('\n')}`);
    fail++;
  }
});
rmSync(TMP, { recursive: true, force: true });
console.log(fail === 0 ? `\nAll ${snippets.length} runnable snippets passed.` : `\n${fail}/${snippets.length} runnable snippets FAILED.`);
process.exit(fail === 0 ? 0 : 1);
