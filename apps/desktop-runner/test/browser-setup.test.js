import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:http';
import { REFERENCE_FIXTURE_DIGEST, REFERENCE_FIXTURE_VERSION } from '@accessforge/contracts';
import { prepareReferenceApp, projectSetupForNavigator } from '../dist/browser-setup.js';

// Synthetic reservation and browser observation. Not real reader proof.
const fixture = { nonce: 'fixture-nonce-12345', variant: 'inaccessible',
  template_digest: REFERENCE_FIXTURE_DIGEST, template_version: REFERENCE_FIXTURE_VERSION };
const options = { permittedOrigin: 'http://127.0.0.1:8081', setupToken: 'synthetic-setup-token',
  reservedNonce: fixture.nonce, variant: 'inaccessible', expectedBuildDigest: 'a'.repeat(64),
  observedBuildDigest: 'a'.repeat(64), expectedFixtureDigest: REFERENCE_FIXTURE_DIGEST };
const reply = (status, body = fixture) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const launch = async (url) => ({ observedUrl: url, browserVersion: '26.6' });
const neverLaunch = async () => { assert.fail('must not launch'); };

async function serve(t, handler) {
  const server = createServer(handler);
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  t.after(async () => { server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); });
  return 'http://127.0.0.1:' + server.address().port;
}

test('reconciles the reserved nonce without a global reset and projects no credentials', async () => {
  const calls = [];
  const result = await prepareReferenceApp({ ...options,
    fetch: async (url, init) => { calls.push([url, init]); return reply(200); }, launch });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], options.permittedOrigin + '/api/_test/fixtures?variant=inaccessible&nonce=' + fixture.nonce);
  assert.equal(calls[0][1].redirect, 'error');
  assert.equal(result.evidence.environmentResetSucceeded, true);
  assert.equal(result.evidence.observedOrigin, options.permittedOrigin);
  assert.deepEqual(projectSetupForNavigator(result), { startUrl: options.permittedOrigin + '/form/' + fixture.nonce });
  assert.equal(JSON.stringify(projectSetupForNavigator(result)).includes(options.setupToken), false);
});

test('missing-label scenario reconciles only its approved variant and reveals no oracle metadata', async () => {
  const variant = 'missing-label-v1';
  const result = await prepareReferenceApp({ ...options, variant, launch,
    fetch: async (url) => {
      assert.equal(new URL(url).searchParams.get('variant'), variant);
      return reply(200, { ...fixture, variant });
    } });
  assert.equal(result.fixture.variant, variant);
  assert.deepEqual(projectSetupForNavigator(result), { startUrl: options.permittedOrigin + '/form/' + fixture.nonce });
  await assert.rejects(prepareReferenceApp({ ...options, variant, launch: neverLaunch,
    fetch: async () => reply(200, fixture) }), /fixture response does not match/);
});

test('caller cancellation aborts a pending HTTP response without launching or retrying', async (t) => {
  const controller = new AbortController(); let requests = 0, received;
  const ready = new Promise(resolve => { received = resolve; });
  const permittedOrigin = await serve(t, (_request, response) => {
    requests++;
    response.writeHead(200, {'content-type': 'application/json'});
    response.write('{'); // Leave the actual fetch body pending.
    received();
  });
  const pending = prepareReferenceApp({...options, permittedOrigin, signal: controller.signal, launch: neverLaunch});
  const refused = assert.rejects(pending);
  await ready;
  controller.abort();
  await refused;
  assert.equal(requests, 1);
});

test('a custom fetch cannot turn cancellation into a launch', async () => {
  const controller = new AbortController();
  await assert.rejects(prepareReferenceApp({...options, signal: controller.signal,
    fetch: async (_url, init) => {
      assert.equal(init.signal, controller.signal);
      controller.abort();
      return reply(200);
    }, launch: neverLaunch}));
});

for (const status of [201, 403, 409, 500]) {
  test('HTTP ' + status + ' cannot confirm the original existing empty fixture', async () => {
    await assert.rejects(() => prepareReferenceApp({ ...options, fetch: async () => reply(status), launch: neverLaunch }),
      /fixture reconciliation returned HTTP/);
  });
}
for (const change of [
  { reservedNonce: undefined }, { reservedNonce: '..' }, { reservedNonce: 'bad?nonce' },
  { permittedOrigin: 'https://example.com' }, { permittedOrigin: 'http://127.0.0.1.evil.example:8081' },
  { permittedOrigin: 'file:///tmp/reference.html' }, { expectedFixtureDigest: '0'.repeat(64) },
  { observedBuildDigest: 'b'.repeat(64) }, { observedBuildDigest: undefined },
  { expectedBuildDigest: '', observedBuildDigest: '' },
]) {
  test('invalid controller configuration is inert: ' + JSON.stringify(change), async () => {
    let requests = 0;
    await assert.rejects(() => prepareReferenceApp({ ...options, ...change,
      fetch: async () => { requests++; return reply(200); }, launch: neverLaunch }));
    assert.equal(requests, 0);
  });
}
for (const observedUrl of [
  'http://localhost:8081/form/fixture-nonce-12345',
  'http://127.0.0.1:8081/form/another-nonce-12345',
  'http://127.0.0.1:8081/form/fixture-nonce-12345?redirected=1', '',
]) {
  test('wrong browser destination cannot be projected: ' + observedUrl, async () => {
    await assert.rejects(() => prepareReferenceApp({ ...options, fetch: async () => reply(200),
      launch: async () => ({ observedUrl, browserVersion: '26.6' }) }), /exact reserved fixture URL/);
  });
}
for (const [name, change] of [
  ['digest', { template_digest: '0'.repeat(64) }], ['version', { template_version: 'legacy-rendered-html' }],
  ['missing version', { template_version: undefined }], ['nonce traversal', { nonce: '..' }],
  ['different valid nonce', { nonce: 'another-nonce-12345' }], ['variant', { variant: 'accessible' }],
]) {
  test('fixture ' + name + ' drift blocks launch', async () => {
    await assert.rejects(() => prepareReferenceApp({ ...options,
      fetch: async () => reply(200, { ...fixture, ...change }), launch: neverLaunch }), /frozen.*contract/);
  });
}
test('native HTTP reconciles the exact nonce with no reset request', async (t) => {
  const requests = [];
  const origin = await serve(t, (request, response) => {
    requests.push(request.url);
    assert.equal(request.headers['x-setup-token'], options.setupToken);
    response.writeHead(200).end(JSON.stringify(fixture));
  });
  const result = await prepareReferenceApp({ ...options, permittedOrigin: origin, launch });
  assert.deepEqual(requests, ['/api/_test/fixtures?variant=inaccessible&nonce=' + fixture.nonce]);
  assert.deepEqual(projectSetupForNavigator(result), { startUrl: origin + '/form/' + fixture.nonce });
});
test('native HTTP refuses redirects without leaking setup identity', async (t) => {
  let leaked = false;
  const destination = await serve(t, (_request, response) => { leaked = true; response.writeHead(204).end(); });
  const origin = await serve(t, (_request, response) => { response.writeHead(307, { location: destination }).end(); });
  await assert.rejects(() => prepareReferenceApp({ ...options, permittedOrigin: origin, launch: neverLaunch }), /fetch failed/);
  assert.equal(leaked, false);
});
test('native HTTP refuses oversized body before parsing or launch', async (t) => {
  const origin = await serve(t, (_request, response) => { response.writeHead(200).end('x'.repeat(20000)); });
  await assert.rejects(() => prepareReferenceApp({ ...options, permittedOrigin: origin, launch: neverLaunch }), /exceeds 16 KiB/);
});
test('native HTTP bounds a stalled body', { timeout: 15000 }, async (t) => {
  const origin = await serve(t, (_request, response) => {
    response.writeHead(200, { 'content-type': 'application/json' }); response.write('{"nonce":');
  });
  await assert.rejects(() => prepareReferenceApp({ ...options, permittedOrigin: origin, launch: neverLaunch }),
    (error) => error.name === 'TimeoutError' || error.name === 'AbortError');
});
