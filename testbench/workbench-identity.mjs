export const WORKBENCH_IDENTITY_KIND = 'memoweft-workbench';

/**
 * This token is an ownership nonce, not a general authentication mechanism.
 * It is exposed only through the loopback workbench route and must be supplied
 * by the managed launcher; ordinary `npm run testbench` intentionally has no
 * lifecycle identity.
 */
export function getWorkbenchIdentity(environment = process.env) {
  const token = environment?.MEMOWEFT_WORKBENCH_INSTANCE_TOKEN;
  if (typeof token !== 'string' || !/^[a-f0-9]{32}$/.test(token)) return null;
  return Object.freeze({ kind: WORKBENCH_IDENTITY_KIND, instanceToken: token });
}

/** A small pure route contract keeps identity behavior testable without booting the data store. */
export function getWorkbenchIdentityRoute({
  method,
  pathname,
  environment = process.env,
  identity,
} = {}) {
  if (method !== 'GET' || pathname !== '/api/workbench-identity') return null;
  const resolvedIdentity = identity ?? getWorkbenchIdentity(environment);
  if (!resolvedIdentity)
    return { statusCode: 404, body: { error: 'workbench identity unavailable' } };
  return { statusCode: 200, body: resolvedIdentity };
}
