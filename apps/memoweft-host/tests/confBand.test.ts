/**
 * 把握度用户档 credBand（批次5 步3 审查 must-fix）：验档位基于【有效把握度】而非静态 credStatus。
 * 纯函数，不依赖网络/模型/库。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { credBand } from '../src/confBand.ts';

// 与 Core config.consolidation.credThresholds 同款阈值（stable 750 / limited 500 / low 300）。
const TH = { stable: 750, limited: 500, low: 300 };

test('高有效把握度 → stable', () => {
  assert.equal(credBand({ credStatus: 'stable', effectiveConfidence: 760 }, TH), 'stable');
});

test('must-fix 核心：静态 credStatus=stable 但衰减后有效值 190 → 落回 candidate（不再假显"比较确定"）', () => {
  // 一条 goal 认知 confidence=760（→credStatus=stable），28 天未印证后 effectiveConfidence≈190。
  //   旧实现按 credStatus 仍显 stable（绿色·比较确定）；新实现按有效值落回 candidate，如实反映"已变淡"。
  assert.equal(credBand({ credStatus: 'stable', effectiveConfidence: 190 }, TH), 'candidate');
});

test('冲突态优先：conflicted 不被高有效值覆盖', () => {
  assert.equal(credBand({ credStatus: 'conflicted', effectiveConfidence: 900 }, TH), 'conflicted');
});

test('档位边界（>= 阈值取该档）', () => {
  assert.equal(credBand({ credStatus: 'x', effectiveConfidence: 750 }, TH), 'stable');
  assert.equal(credBand({ credStatus: 'x', effectiveConfidence: 500 }, TH), 'limited');
  assert.equal(credBand({ credStatus: 'x', effectiveConfidence: 300 }, TH), 'low');
  assert.equal(credBand({ credStatus: 'x', effectiveConfidence: 299 }, TH), 'candidate');
});
