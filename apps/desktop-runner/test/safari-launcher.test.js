import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createSafariReferenceLauncher, prepareSafariReferenceApp } from '../dist/safari-launcher.js';
import { SafariProbeUnavailable } from '../dist/safari-origin.js';
import { REFERENCE_FIXTURE_DIGEST, REFERENCE_FIXTURE_VERSION } from '@accessforge/contracts';

const expectedUrl = 'http://127.0.0.1:3000/form/fixture_nonce_123456';
const known = { schemaVersion: 1, status: 'KNOWN', bundleId: 'com.apple.Safari',
  pid: 312, launchedAt: 1700000000.5, browserVersion: '26.6', url: expectedUrl };
function fixture(changes = {}, ports = {}) {
  const events = [];
  const controller = new AbortController();
  const options = { expectedUrl, expectedBrowserVersion: '26.6', signal: controller.signal,
    authorize: async () => { events.push('authorize'); },
    assertDesktopHeld: () => { events.push('held'); }, ...changes };
  const launch = createSafariReferenceLauncher(options, {
    open: async (url) => { assert.equal(url, expectedUrl); events.push('open'); },
    read: async () => { events.push('observe'); return known; }, ...ports,
  });
  return { launch, events, controller, options };
}

test('inert construction, exact native receipt after open and fresh authority, no replay', async () => {
  const { launch, events, options } = fixture();
  assert.deepEqual(events, []);
  options.expectedUrl = 'http://elsewhere.test';
  assert.deepEqual(await launch(expectedUrl), { observedUrl: expectedUrl, browserVersion: '26.6' });
  assert.deepEqual(events, ['held', 'authorize', 'held', 'open', 'held', 'observe', 'held', 'authorize', 'held', 'observe', 'held']);
  await assert.rejects(() => launch(expectedUrl));
  assert.equal(events.filter((event) => event === 'open').length, 1);
});

test('wrong destination, cancelled authority and absent desktop claim never open Safari', async () => {
  const wrong = fixture();
  await assert.rejects(() => wrong.launch(expectedUrl + '?other=1'));
  const aborted = fixture();
  aborted.controller.abort();
  await assert.rejects(() => aborted.launch(expectedUrl));
  const unowned = fixture({ assertDesktopHeld: () => { throw new Error('not held'); } });
  await assert.rejects(() => unowned.launch(expectedUrl));
  for (const item of [wrong, aborted, unowned]) assert.equal(item.events.includes('open'), false);
});

test('late authorization after cancellation cannot enter native open', async () => {
  let release;
  const item = fixture({ authorize: () => new Promise((resolve) => { release = resolve; }) });
  const result = item.launch(expectedUrl);
  item.controller.abort();
  await assert.rejects(result);
  release();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(item.events.includes('open'), false);
});

test('successful OS command is not an observation; mismatched document refuses and stays one-shot', async () => {
  const item = fixture({}, { read: async () => ({ ...known, url: expectedUrl + '#wrong' }) });
  await assert.rejects(() => item.launch(expectedUrl), (error) => !error.message.includes('fixture_nonce'));
  await assert.rejects(() => item.launch(expectedUrl));
  assert.equal(item.events.filter((event) => event === 'open').length, 1);
});

test('authority revoked after native observation prevents a successful setup receipt', async () => {
  let authorizations = 0;
  const item = fixture({ authorize: async () => {
    if (++authorizations === 2) throw new Error('private approval diagnostic');
  } });
  await assert.rejects(() => item.launch(expectedUrl), (error) => !error.message.includes('private'));
  assert.equal(item.events.includes('observe'), true);
});

test('concurrent invocation fences the already pending invocation before it can open', async () => {
  let release;
  const item = fixture({ authorize: () => new Promise((resolve) => { release = resolve; }) });
  const first = item.launch(expectedUrl);
  await assert.rejects(() => item.launch(expectedUrl));
  release();
  await assert.rejects(first);
  assert.equal(item.events.includes('open'), false);
});

test('native document readiness may be sampled again without reopening the browser', async () => {
  let reads = 0;
  const item = fixture({}, { read: async () => {
    if (++reads === 1) throw new SafariProbeUnavailable('DOCUMENT_UNAVAILABLE');
    return known;
  } });
  await item.launch(expectedUrl);
  assert.equal(reads, 3);
  assert.equal(item.events.filter((event) => event === 'open').length, 1);
});

test('browser replacement during reauthorization refuses the final receipt', async () => {
  let reads = 0;
  const item = fixture({}, { read: async () => ({ ...known, launchedAt: known.launchedAt + reads++ }) });
  await assert.rejects(() => item.launch(expectedUrl));
  assert.equal(reads, 2);
});

test('baseline composition reconciles original reservation before native launch of that same fixture', async () => {
  const events = [];
  const nonce = 'fixture_nonce_123456';
  const result = await prepareSafariReferenceApp({
    permittedOrigin: 'http://127.0.0.1:3000', reservedNonce: nonce,
    setupToken: 'synthetic-private-token', variant: 'inaccessible',
    expectedFixtureDigest: REFERENCE_FIXTURE_DIGEST,
    expectedBuildDigest: 'a'.repeat(64), observedBuildDigest: 'a'.repeat(64),
    fetch: async (url) => {
      assert.equal(new URL(url).searchParams.get('nonce'), nonce);
      events.push('reconcile');
      return { ok: true, status: 200, json: async () => ({ nonce, variant: 'inaccessible',
        template_digest: REFERENCE_FIXTURE_DIGEST, template_version: REFERENCE_FIXTURE_VERSION }) };
    },
  }, { expectedBrowserVersion: '26.6', signal: new AbortController().signal,
    authorize: async () => { events.push('authorize'); }, assertDesktopHeld: () => {},
  }, { open: async (url) => { assert.equal(url, expectedUrl); events.push('open'); },
    read: async () => known,
  });
  assert.deepEqual(events, ['reconcile', 'authorize', 'open', 'authorize']);
  assert.equal(result.startUrl, expectedUrl);
  assert.equal(JSON.stringify(result).includes('synthetic-private-token'), false);
});
