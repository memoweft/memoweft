const DANGEROUS_PATH_SEGMENTS = new Set(['__proto__', 'prototype', 'constructor']);

/** A deliberate client-input failure whose response never exposes parser internals. */
export class ClientInputError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ClientInputError';
    this.statusCode = 400;
  }
}

export function clientInputRejection(error) {
  return error instanceof ClientInputError ? { statusCode: 400, message: error.message } : null;
}

function headerValue(headers, name) {
  const value = headers[name];
  return typeof value === 'string' ? value : undefined;
}

/** The testbench only listens on IPv4 loopback, so accept its two browser authorities. */
export function isTrustedLoopbackAuthority(value, port) {
  if (!value) return false;
  const authority = value.toLowerCase();
  return authority === `127.0.0.1:${port}` || authority === `localhost:${port}`;
}

/**
 * Return a safe client-facing rejection before any request reaches a route.
 * Every response requires the bound loopback Host. POST requests additionally require browser
 * requests to keep the exact same origin as their Host; local scripts without an Origin header
 * remain supported for the diagnostic workflow.
 */
export function requestRejection(headers, method, port) {
  const host = headerValue(headers, 'host');
  if (!isTrustedLoopbackAuthority(host, port)) {
    return { statusCode: 403, message: '拒绝非本机 Host 的请求' };
  }

  if (method !== 'POST') return null;

  const origin = headerValue(headers, 'origin');
  if (!origin) return null;
  try {
    const parsed = new URL(origin);
    const expectedOrigin = `http://${host.toLowerCase()}`;
    if (parsed.origin.toLowerCase() !== expectedOrigin) {
      return { statusCode: 403, message: 'Origin 不可信' };
    }
  } catch {
    return { statusCode: 403, message: 'Origin 不可信' };
  }
  return null;
}

function isObjectRecord(value) {
  return value !== null && typeof value === 'object';
}

/**
 * Update only an existing own property path. Prototype-related property names are forbidden
 * at every depth so a diagnostics request cannot alter Object.prototype or constructor state.
 */
export function setOwnPath(obj, path, value) {
  const segs = String(path).split('.');
  if (segs.length === 0 || segs.some((segment) => segment === '')) return `非法路径：${path}`;
  if (segs.some((segment) => DANGEROUS_PATH_SEGMENTS.has(segment))) return `非法路径：${path}`;

  let current = obj;
  for (let index = 0; index < segs.length - 1; index++) {
    const key = segs[index];
    if (!isObjectRecord(current) || !Object.hasOwn(current, key)) {
      return `路径不存在：${segs.slice(0, index + 1).join('.')}`;
    }
    current = current[key];
  }

  const last = segs[segs.length - 1];
  if (!isObjectRecord(current) || !Object.hasOwn(current, last)) return `路径不存在：${path}`;
  current[last] = value;
  return null;
}

/**
 * Encode one value for Node's .env parser without silently changing it at load time.
 * Always quote: unquoted values such as 'abc' or "abc" lose their literal wrapper when loaded.
 * Single quotes preserve backslashes, newlines and double quotes. Values containing both quote
 * styles cannot be represented losslessly by Node's dotenv grammar and are explicitly rejected.
 */
export function encodeDotenvValue(value) {
  if (!value.includes("'")) return `'${value}'`;
  if (!value.includes('"') && !value.includes('\\')) return `"${value}"`;
  return null;
}

/** Encode a named collection while making unrepresentable entries explicit to the caller. */
export function encodeDotenvEntries(entries) {
  const encoded = {};
  const unrepresentable = [];
  for (const [name, value] of Object.entries(entries)) {
    const encodedValue = encodeDotenvValue(value);
    if (encodedValue === null) unrepresentable.push(name);
    else encoded[name] = encodedValue;
  }
  return { encoded, unrepresentable };
}
