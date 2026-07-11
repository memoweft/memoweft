#!/usr/bin/env node
// docs 死链检查:扫 docs/ 与根 README / CONTRIBUTING 的相对内链。
// 硬查:目标文件路径存在(缺失 → 退出码 1,挡在 CI 外)。
// 软查:#锚点是否命中某标题 slug —— 只警告不失败(锚点 slug 跨渲染器不完全一致,尤其中文)。
// 用法:node scripts/doc-links.mjs   (cwd = 仓库根)
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, dirname, resolve, relative } from 'node:path';

const ROOT = process.cwd();

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
for (const extra of ['README.md', 'README.zh-CN.md', 'CONTRIBUTING.md', 'CONTRIBUTING.zh-CN.md']) {
  const p = join(ROOT, extra);
  if (existsSync(p)) files.push(p);
}

// 半角 + 全角标点都剥掉,近似 GitHub 中文锚点 slug。
const PUNCT = /[`*_~()[\]{}.,:;!?'"/\\<>|？！，。；：（）【】「」“”‘’—…·]/g;
const slug = (h) => h.trim().toLowerCase().replace(PUNCT, '').replace(/\s+/g, '-');

const hcache = new Map();
function headingsOf(file) {
  if (hcache.has(file)) return hcache.get(file);
  const out = new Set();
  let inFence = false;
  for (const line of readFileSync(file, 'utf8').split(/\r?\n/)) {
    if (/^(```|~~~)/.test(line)) { inFence = !inFence; continue; }
    if (inFence) continue;
    const m = line.match(/^#{1,6}\s+(.*)$/);
    if (m) out.add(slug(m[1]));
  }
  hcache.set(file, out);
  return out;
}

const linkRe = /\[[^\]]*\]\(([^)]+)\)/g;
let deadPath = 0, deadAnchor = 0, checked = 0;
for (const file of files) {
  const rel = relative(ROOT, file).replace(/\\/g, '/');
  let inFence = false;
  for (const raw of readFileSync(file, 'utf8').split(/\r?\n/)) {
    if (/^(```|~~~)/.test(raw)) { inFence = !inFence; continue; }
    if (inFence) continue;
    let m; linkRe.lastIndex = 0;
    while ((m = linkRe.exec(raw))) {
      const target = m[1].trim().split(/\s+/)[0];
      if (/^(https?:|mailto:|tel:)/.test(target)) continue;
      if (target.startsWith('#')) {
        checked++;
        if (!headingsOf(file).has(target.slice(1).toLowerCase())) { console.log(`ANCHOR?  ${rel} -> ${target}`); deadAnchor++; }
        continue;
      }
      const [pathPart, anchor] = target.split('#');
      const abs = resolve(dirname(file), pathPart);
      checked++;
      if (!existsSync(abs)) { console.log(`DEAD     ${rel} -> ${target}`); deadPath++; continue; }
      if (anchor && abs.endsWith('.md') && statSync(abs).isFile()) {
        if (!headingsOf(abs).has(anchor.toLowerCase())) { console.log(`ANCHOR?  ${rel} -> ${target}`); deadAnchor++; }
      }
    }
  }
}
console.log(`\nfiles=${files.length} links=${checked} deadPath=${deadPath} deadAnchor(warn)=${deadAnchor}`);
if (deadPath > 0) { console.error(`\n${deadPath} dead link(s) — see DEAD lines above.`); process.exit(1); }
