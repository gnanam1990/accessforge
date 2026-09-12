import assert from 'node:assert/strict';
import { test } from 'node:test';

import { prepareReferenceApp, projectSetupForNavigator } from '../dist/browser-setup.js';

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

test('the supervisor resets, seeds and launches only the exact sealed local origin', async () => {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push(['fetch', url, init]);
    if (url.endsWith('/api/_test/reset')) return response(204, undefined);
    return response(201, {
      nonce: 'fixture-nonce',
      variant: 'inaccessible',
      template_digest: 'b'.repeat(64),
    });
  };
  const launch = async (url) => {
    calls.push(['launch', url]);
    return { observedUrl: url, browserVersion: '26.6' };
  };

  const result = await prepareReferenceApp({
    permittedOrigin: 'http://127.0.0.1:8081',
    setupToken: 'setup-secret-not-for-navigator',
    variant: 'inaccessible',
    expectedBuildDigest: 'a'.repeat(64),
    observedBuildDigest: 'a'.repeat(64),
    fetch,
    launch,
  });

  assert.deepEqual(calls.map(([kind, url]) => [kind, url]), [
    ['fetch', 'http://127.0.0.1:8081/api/_test/reset'],
    ['fetch', 'http://127.0.0.1:8081/api/_test/fixtures?variant=inaccessible'],
    ['launch', 'http://127.0.0.1:8081/form/fixture-nonce'],
  ]);
  assert.equal(result.evidence.environmentResetSucceeded, true);
  assert.equal(result.evidence.originReachable, true);
  assert.equal(result.evidence.observedOrigin, 'http://127.0.0.1:8081');
  assert.deepEqual(projectSetupForNavigator(result), {
    startUrl: 'http://127.0.0.1:8081/form/fixture-nonce',
  });
  assert.equal(JSON.stringify(projectSetupForNavigator(result)).includes('setup-secret'), false);
});

test('a failed reset stops before fixture creation or browser launch', async () => {
  let launches = 0;
  await assert.rejects(
    () =>
      prepareReferenceApp({
        permittedOrigin: 'http://127.0.0.1:8081',
        setupToken: 'setup-secret-not-for-navigator',
        variant: 'accessible',
        expectedBuildDigest: 'a'.repeat(64),
        observedBuildDigest: 'a'.repeat(64),
        fetch: async () => response(403, {}),
        launch: async () => {
          launches += 1;
          return { observedUrl: '', browserVersion: '' };
        },
      }),
    /reset returned HTTP 403/,
  );
  assert.equal(launches, 0);
});

for (const origin of [
  'https://example.com',
  'http://127.0.0.1.evil.example:8081',
  'file:///tmp/reference.html',
]) {
  test(`setup refuses non-loopback origin ${origin}`, async () => {
    await assert.rejects(
      () =>
        prepareReferenceApp({
          permittedOrigin: origin,
          setupToken: 'setup-secret-not-for-navigator',
          variant: 'accessible',
          expectedBuildDigest: 'a'.repeat(64),
          observedBuildDigest: 'a'.repeat(64),
          fetch: async () => response(204, {}),
          launch: async () => ({ observedUrl: origin, browserVersion: '26.6' }),
        }),
      /loopback HTTP origin/,
    );
  });
}

test('a browser landing on a different origin is evidence of failure, never success', async () => {
  const fetch = async (url) =>
    url.endsWith('/reset')
      ? response(204, undefined)
      : response(201, {
          nonce: 'fixture-nonce',
          variant: 'accessible',
          template_digest: 'b'.repeat(64),
        });
  const result = await prepareReferenceApp({
    permittedOrigin: 'http://127.0.0.1:8081',
    setupToken: 'setup-secret-not-for-navigator',
    variant: 'accessible',
    expectedBuildDigest: 'a'.repeat(64),
    observedBuildDigest: 'a'.repeat(64),
    fetch,
    launch: async () => ({
      observedUrl: 'http://localhost:8081/form/fixture-nonce',
      browserVersion: '26.6',
    }),
  });
  assert.equal(result.evidence.originReachable, true);
  assert.equal(result.evidence.observedOrigin, 'http://localhost:8081');
});
