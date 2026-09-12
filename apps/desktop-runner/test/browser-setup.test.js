import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:http';
import { REFERENCE_FIXTURE_DIGEST, REFERENCE_FIXTURE_VERSION } from '@accessforge/contracts';

import { prepareReferenceApp, projectSetupForNavigator } from '../dist/browser-setup.js';

const fixture = {
  nonce: 'fixture-nonce-12345', variant: 'inaccessible',
  template_digest: REFERENCE_FIXTURE_DIGEST, template_version: REFERENCE_FIXTURE_VERSION,
};
const options = {
  permittedOrigin: 'http://127.0.0.1:8081', setupToken: 'test-setup-identity-not-production',
  variant: 'inaccessible', expectedBuildDigest: 'a'.repeat(64),
  observedBuildDigest: 'a'.repeat(64), expectedFixtureDigest: REFERENCE_FIXTURE_DIGEST,
};

async function serve(t, handler) {
  const server = createServer(handler);
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });
  return `http://127.0.0.1:${server.address().port}`;
}

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
      nonce: 'fixture-nonce-12345',
      variant: 'inaccessible',
      template_digest: REFERENCE_FIXTURE_DIGEST,
      template_version: REFERENCE_FIXTURE_VERSION,
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
    expectedFixtureDigest: REFERENCE_FIXTURE_DIGEST,
    fetch,
    launch,
  });

  assert.deepEqual(calls.map(([kind, url]) => [kind, url]), [
    ['fetch', 'http://127.0.0.1:8081/api/_test/reset'],
    ['fetch', 'http://127.0.0.1:8081/api/_test/fixtures?variant=inaccessible'],
    ['launch', 'http://127.0.0.1:8081/form/fixture-nonce-12345'],
  ]);
  assert.equal(result.evidence.environmentResetSucceeded, true);
  assert.equal(result.fixture.templateDigest, REFERENCE_FIXTURE_DIGEST);
  assert.equal(result.fixture.templateVersion, REFERENCE_FIXTURE_VERSION);
  assert.equal(calls[0][2].redirect, 'error');
  assert.equal(result.evidence.originReachable, true);
  assert.equal(result.evidence.observedOrigin, 'http://127.0.0.1:8081');
  assert.deepEqual(projectSetupForNavigator(result), {
    startUrl: 'http://127.0.0.1:8081/form/fixture-nonce-12345',
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
        expectedFixtureDigest: REFERENCE_FIXTURE_DIGEST,
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
          expectedFixtureDigest: REFERENCE_FIXTURE_DIGEST,
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
          nonce: 'fixture-nonce-12345',
          variant: 'accessible',
          template_digest: REFERENCE_FIXTURE_DIGEST,
          template_version: REFERENCE_FIXTURE_VERSION,
        });
  const result = await prepareReferenceApp({
    permittedOrigin: 'http://127.0.0.1:8081',
    setupToken: 'setup-secret-not-for-navigator',
    variant: 'accessible',
    expectedBuildDigest: 'a'.repeat(64),
    observedBuildDigest: 'a'.repeat(64),
    expectedFixtureDigest: REFERENCE_FIXTURE_DIGEST,
    fetch,
    launch: async () => ({
      observedUrl: 'http://localhost:8081/form/fixture-nonce',
      browserVersion: '26.6',
    }),
  });
  assert.equal(result.evidence.originReachable, true);
  assert.equal(result.evidence.observedOrigin, 'http://localhost:8081');
});

for (const [name, change] of [
  ['digest', { template_digest: '0'.repeat(64) }],
  ['version', { template_version: 'legacy-rendered-html' }],
  ['missing version', { template_version: undefined }],
  ['nonce traversal', { nonce: '..' }],
  ['variant', { variant: 'accessible' }],
]) {
  test(`fixture ${name} drift blocks before browser launch`, async () => {
    let launched = false;
    await assert.rejects(() => prepareReferenceApp({
      ...options,
      fetch: async (url) => url.endsWith('/reset') ? response(204) : response(201, { ...fixture, ...change }),
      launch: async () => { launched = true; return { observedUrl: '', browserVersion: '' }; },
    }), /frozen.*contract/);
    assert.equal(launched, false);
  });
}

test('an unsupported sealed fixture is refused before even resetting the application', async () => {
  let requests = 0;
  await assert.rejects(() => prepareReferenceApp({
    ...options, expectedFixtureDigest: '0'.repeat(64),
    fetch: async () => { requests += 1; return response(204); },
    launch: async () => { throw new Error('must not launch'); },
  }), /sealed fixture definition/);
  assert.equal(requests, 0);
});

test('native HTTP setup checks the versioned fixture and preserves credential projection', async (t) => {
  const origin = await serve(t, (request, response) => {
    assert.equal(request.headers['x-setup-token'], options.setupToken);
    response.writeHead(request.url.endsWith('/reset') ? 204 : 201);
    response.end(request.url.endsWith('/reset') ? undefined : JSON.stringify(fixture));
  });
  const result = await prepareReferenceApp({
    ...options, permittedOrigin: origin,
    launch: async (url) => ({ observedUrl: url, browserVersion: 'test-only' }),
  });
  assert.equal(result.fixture.templateDigest, REFERENCE_FIXTURE_DIGEST);
  assert.deepEqual(projectSetupForNavigator(result), { startUrl: origin + '/form/' + fixture.nonce });
});

test('native setup refuses redirect without forwarding its setup identity', async (t) => {
  let leaked = false;
  const destination = await serve(t, (request, response) => {
    leaked = true;
    response.writeHead(204).end();
  });
  const origin = await serve(t, (request, response) => {
    response.writeHead(307, { location: destination + '/capture' }).end();
  });
  await assert.rejects(() => prepareReferenceApp({
    ...options, permittedOrigin: origin,
    launch: async () => { throw new Error('must not launch'); },
  }), /fetch failed/);
  assert.equal(leaked, false);
});

test('native setup refuses an oversized candidate response before parsing or launch', async (t) => {
  const origin = await serve(t, (request, response) => {
    if (request.url.endsWith('/reset')) { response.writeHead(204).end(); return; }
    response.writeHead(201).end('x'.repeat(20_000));
  });
  await assert.rejects(() => prepareReferenceApp({
    ...options, permittedOrigin: origin,
    launch: async () => { throw new Error('must not launch'); },
  }), /exceeds 16 KiB/);
});

test('native setup aborts a stalled response body before browser launch', { timeout: 15_000 }, async (t) => {
  const origin = await serve(t, (request, response) => {
    if (request.url.endsWith('/reset')) { response.writeHead(204).end(); return; }
    response.writeHead(201, { 'content-type': 'application/json' });
    response.write('{"nonce":');
    // Deliberately never finish the body. Headers alone must not end the setup deadline.
  });
  await assert.rejects(() => prepareReferenceApp({
    ...options, permittedOrigin: origin,
    launch: async () => { throw new Error('must not launch'); },
  }), (error) => error.name === 'TimeoutError' || error.name === 'AbortError');
});
