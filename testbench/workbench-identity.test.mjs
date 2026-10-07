import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WORKBENCH_IDENTITY_KIND, getWorkbenchIdentityRoute } from './workbench-identity.mjs';

test('workbench identity route returns the exact managed token', () => {
  const response = getWorkbenchIdentityRoute({
    method: 'GET',
    pathname: '/api/workbench-identity',
    environment: { MEMOWEFT_WORKBENCH_INSTANCE_TOKEN: '0123456789abcdef0123456789abcdef' },
  });
  assert.deepEqual(response, {
    statusCode: 200,
    body: {
      kind: WORKBENCH_IDENTITY_KIND,
      instanceToken: '0123456789abcdef0123456789abcdef',
    },
  });
});

test('workbench identity route is explicitly unavailable without a managed token', () => {
  assert.deepEqual(
    getWorkbenchIdentityRoute({
      method: 'GET',
      pathname: '/api/workbench-identity',
      environment: {},
    }),
    { statusCode: 404, body: { error: 'workbench identity unavailable' } },
  );
  assert.equal(
    getWorkbenchIdentityRoute({
      method: 'GET',
      pathname: '/api/workbench-identity',
      environment: { MEMOWEFT_WORKBENCH_INSTANCE_TOKEN: 'not-a-random-token' },
    }).statusCode,
    404,
  );
});

test('workbench identity route can hold the startup identity after later environment changes', () => {
  const captured = {
    kind: WORKBENCH_IDENTITY_KIND,
    instanceToken: '0123456789abcdef0123456789abcdef',
  };
  assert.deepEqual(
    getWorkbenchIdentityRoute({
      method: 'GET',
      pathname: '/api/workbench-identity',
      environment: { MEMOWEFT_WORKBENCH_INSTANCE_TOKEN: 'fedcba9876543210fedcba9876543210' },
      identity: captured,
    }).body,
    captured,
  );
});

test('workbench identity handler does not claim unrelated routes', () => {
  assert.equal(
    getWorkbenchIdentityRoute({
      method: 'POST',
      pathname: '/api/workbench-identity',
      environment: { MEMOWEFT_WORKBENCH_INSTANCE_TOKEN: '0123456789abcdef0123456789abcdef' },
    }),
    null,
  );
});
