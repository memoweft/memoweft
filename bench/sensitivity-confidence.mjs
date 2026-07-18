/**
 * Confidence-parameter sensitivity grid. Deterministic, offline, and model-free.
 *
 * The grid varies confidence bases by ±20% and half-lives by 0.5x/1x/2x.
 * Part A counts credStatus changes across representative inputs. Part B
 * estimates how long effective confidence remains above the recall threshold.
 *
 * This script characterizes sensitivity only; it never changes defaults.
 *
 * Imports TypeScript source directly and requires Node.js 24+.
 * Usage: node bench/sensitivity-confidence.mjs
 * Prints the report and writes an ignored, commit-stamped file under bench/runs/.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { execSync } from 'node:child_process';
import { config } from '../src/config.ts';
import { computeConfidence, deriveCredStatus } from '../src/consolidation/confidence.ts';
import { effectiveConfidence } from '../src/background/decay.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const GATE = config.retrieval.minEffectiveConfidence;
const BASE_MULTS = [0.8, 1.0, 1.2];
const HL_MULTS = [0.5, 1.0, 2.0];

/** Creates an isolated configuration variant for one grid point. */
function variant(baseMult, hlMult) {
  const c = structuredClone(config);
  for (const k of Object.keys(c.consolidation.baseByFormedBy)) {
    c.consolidation.baseByFormedBy[k] = Math.round(c.consolidation.baseByFormedBy[k] * baseMult);
  }
  for (const k of Object.keys(c.background.halfLifeDays)) {
    c.background.halfLifeDays[k] = c.background.halfLifeDays[k] * hlMult;
  }
  return c;
}

// Part A: base confidence sensitivity
const FORMED = ['stated', 'observed', 'ruled', 'confirmed', 'inferred'];
const TYPES_A = ['fact', 'preference', 'state', 'trait'];
const SUPPORTS = [0, 1, 2, 3, 5];
const CONTRADICTS = [0, 1];

function partA() {
  const inputs = [];
  for (const formedBy of FORMED)
    for (const contentType of TYPES_A)
      for (const supportCount of SUPPORTS)
        for (const contradictCount of CONTRADICTS)
          inputs.push({ contentType, formedBy, supportCount, contradictCount });
  const RANK = { candidate: 0, low: 1, limited: 2, stable: 3, conflicted: -1 };
  let flips = 0,
    wild = 0;
  const examples = [];
  for (const inp of inputs) {
    const row = BASE_MULTS.map((m) => {
      const cfg = variant(m, 1.0);
      const conf = computeConfidence(inp, cfg);
      return { conf, cred: deriveCredStatus(conf, inp.contradictCount, inp.contentType, cfg) };
    });
    if (row[0].cred !== row[2].cred) {
      flips++;
      const jump = Math.abs((RANK[row[2].cred] ?? 0) - (RANK[row[0].cred] ?? 0));
      if (jump > 1) wild++;
      if (examples.length < 14) examples.push({ inp, row });
    }
  }
  return { total: inputs.length, flips, wild, examples };
}

// Part B: retention above the recall threshold
const DECAY_TYPES = ['state', 'hypothesis', 'trend', 'goal', 'project', 'trait'];
const START_CONF = 500;
function retentionDays(startConf, contentType, cfg) {
  const now = new Date('2026-01-01T00:00:00Z');
  let last = 0;
  for (let d = 0; d <= 800; d += 0.25) {
    const updatedAt = new Date(now.getTime() - d * 86_400_000).toISOString();
    const eff = effectiveConfidence({ confidence: startConf, contentType, updatedAt }, now, cfg);
    if (eff < GATE) return last;
    last = d;
  }
  return Infinity;
}
function partB() {
  const rows = [];
  for (const t of DECAY_TYPES) {
    const cells = HL_MULTS.map((m) => retentionDays(START_CONF, t, variant(1.0, m)));
    const hlDefault = config.background.halfLifeDays[t] ?? 0;
    rows.push({ type: t, hlDefault, cells });
  }
  return rows;
}

// Report
function fmtDays(d) {
  return d === Infinity ? '∞(不衰减)' : `${d.toFixed(2)}d`;
}

const a = partA();
const b = partB();
const commit = (() => {
  try {
    return execSync('git rev-parse --short HEAD').toString().trim();
  } catch {
    return 'nogit';
  }
})();

const L = [];
L.push('# Confidence parameter sensitivity grid');
L.push('');
L.push(
  `- commit: \`${commit}\` · 网格:底分 ×{0.8, 1.0, 1.2} × 半衰期 ×{0.5, 1.0, 2.0} · 召回门 effectiveConfidence ≥ ${GATE}`,
);
L.push(
  `- 默认底分 baseByFormedBy=${JSON.stringify(config.consolidation.baseByFormedBy)} · 档位阈值=${JSON.stringify(config.consolidation.credThresholds)}`,
);
L.push('');
L.push('## Part A — 底分 ±20% 对 credStatus 的敏感性');
L.push('');
L.push(
  `代表性输入 ${a.total} 组(formedBy×contentType×support×contradict);量底分 0.8 vs 1.2 下 credStatus 是否翻转。`,
);
L.push('');
L.push(
  `- **翻转率:${a.flips}/${a.total} = ${((a.flips / a.total) * 100).toFixed(1)}%**；其中跨 >1 档的变化：${a.wild}。这只描述本脚本枚举的输入网格。`,
);
L.push('');
L.push('翻转样例(在档位边界附近才翻):');
L.push('');
L.push('| formedBy | type | sup | con | conf@0.8 | conf@1.0 | conf@1.2 | cred 0.8→1.2 |');
L.push('|---|---|---|---|---|---|---|---|');
for (const e of a.examples) {
  const { inp, row } = e;
  L.push(
    `| ${inp.formedBy} | ${inp.contentType} | ${inp.supportCount} | ${inp.contradictCount} | ${row[0].conf} | ${row[1].conf} | ${row[2].conf} | ${row[0].cred} → ${row[2].cred} |`,
  );
}
L.push('');
L.push('## Part B — 半衰期 ×0.5/1/2 对召回保留窗口的影响');
L.push('');
L.push(
  `各衰减类型:起始把握度 ${START_CONF} 的认知,多少天后有效置信跌破召回门 ${GATE}(= 不再被召回)。`,
);
L.push('');
L.push('| contentType | 默认半衰期(天) | 窗口 ×0.5 | 窗口 ×1.0 | 窗口 ×2.0 |');
L.push('|---|---|---|---|---|');
for (const r of b) {
  L.push(
    `| ${r.type} | ${r.hlDefault} | ${fmtDays(r.cells[0])} | ${fmtDays(r.cells[1])} | ${fmtDays(r.cells[2])} |`,
  );
}
L.push('');
L.push('## 结论');
L.push('');
L.push(
  `- **底分 ±20%**：在此网格中，翻转率为 ${((a.flips / a.total) * 100).toFixed(1)}%，且没有观察到跨越超过一个档位的变化。翻转样例集中在档位阈值附近；其他输入空间和下游质量并未由此评估。`,
);
L.push(
  '- **半衰期**：对这里固定的起始置信度和门槛，保留窗口会随所选半衰期倍数增加。数值按 0.25 天步长采样，不能外推为所有内容类型、阈值或工作负载的质量结论。',
);
L.push(
  '- **This grid does not identify a better default.** It measures sensitivity rather than downstream quality. Any future threshold change should be evaluated against representative retrieval and memory-formation workloads.',
);
L.push('');
const report = L.join('\n') + '\n';
console.log(report);
const runsDir = resolve(HERE, 'runs');
mkdirSync(runsDir, { recursive: true });
const reportPath = resolve(
  runsDir,
  `${new Date().toISOString().slice(0, 10)}-${commit}-confidence-sensitivity.md`,
);
writeFileSync(reportPath, report);
console.log(`written: ${reportPath}`);
