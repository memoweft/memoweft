import { isAbsolute, resolve } from 'node:path';

const DEFAULT_PORT = 7888;
const MEMORY_AUTHORITIES = new Set(['legacy', 'next']);

/** The public-facing runtime label must describe the actual long-term authority. */
export function memoryAuthorityLabel(memoryAuthority) {
  return memoryAuthority === 'next' ? 'next-accepted-world' : 'legacy-cognition';
}

/**
 * Process-start configuration only.  None of these values are accepted from
 * HTTP, so an in-browser request cannot redirect the testbench database, logs,
 * or loopback listener.  Absolute override paths make isolated dogfood runs
 * explicit and leave the 1.x defaults byte-for-byte in place when unset.
 */
export function readTestbenchRuntimeConfig({ env = process.env, dirname }) {
  const rawPort = env.MEMOWEFT_TESTBENCH_PORT;
  const port = rawPort === undefined || rawPort === '' ? DEFAULT_PORT : Number(rawPort);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('MEMOWEFT_TESTBENCH_PORT 必须是 1 到 65535 的整数。');
  }

  function explicitAbsolutePath(name, fallback) {
    const raw = env[name];
    if (raw === undefined || raw === '') return fallback;
    if (!isAbsolute(raw)) throw new Error(`${name} 必须是绝对路径。`);
    return resolve(raw);
  }

  const rawProfileEveryTurn = env.MEMOWEFT_TESTBENCH_PROFILE_EVERY_TURN;
  let profileEveryTurn = false;
  if (rawProfileEveryTurn !== undefined) {
    if (rawProfileEveryTurn !== 'on' && rawProfileEveryTurn !== 'off') {
      throw new Error('MEMOWEFT_TESTBENCH_PROFILE_EVERY_TURN 只能是 on 或 off。');
    }
    profileEveryTurn = rawProfileEveryTurn === 'on';
  }

  const memoryAuthority = env.MEMOWEFT_TESTBENCH_MEMORY_AUTHORITY ?? 'legacy';
  if (!MEMORY_AUTHORITIES.has(memoryAuthority)) {
    throw new Error('MEMOWEFT_TESTBENCH_MEMORY_AUTHORITY 只能是 legacy 或 next。');
  }

  return {
    port,
    dbPath: explicitAbsolutePath(
      'MEMOWEFT_TESTBENCH_DB_PATH',
      resolve(dirname, 'testbench-evidence.db'),
    ),
    logDir: explicitAbsolutePath('MEMOWEFT_TESTBENCH_LOG_DIR', resolve(dirname, '..', 'logs')),
    // Isolated workbench mode only: explicitly schedule a non-blocking profile update after every turn.
    profileEveryTurn,
    // `legacy` preserves the published 1.x cognition authority.  `next` keeps
    // 1.x Evidence but delegates durable memory authority to accepted 2.0 world records.
    memoryAuthority,
    memoryAuthorityLabel: memoryAuthorityLabel(memoryAuthority),
  };
}
