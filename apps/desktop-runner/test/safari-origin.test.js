import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { PREFLIGHT_CHECKS } from '@accessforge/at-voiceover';
import { createSafariOriginProbe } from '../dist/safari-origin.js';
import { AuthenticatedRunner } from '../dist/authenticated-runner.js';
import { MemoryJournal } from '../dist/journal.js';

const options = { expectedUrl: 'http://127.0.0.1:3000/form/fixture_nonce_123456', expectedBrowserVersion: '26.6' };
const known = { schemaVersion: 1, status: 'KNOWN', bundleId: 'com.apple.Safari',
  pid: 312, launchedAt: 1700000000.5, browserVersion: '26.6', url: options.expectedUrl };

test('fresh private native measurements, not requested URL echo or cached approval', async () => {
  let calls = 0;
  const mutable = { ...options };
  const probe = createSafariOriginProbe(mutable, async (request) => {
    calls++;
    assert.deepEqual(request, options);
    assert.equal(Object.isFrozen(request), true);
    return { ...known };
  });
  mutable.expectedUrl = 'http://elsewhere.test';
  assert.equal(await probe(), 'http://127.0.0.1:3000');
  assert.equal(await probe(), 'http://127.0.0.1:3000');
  assert.equal(calls, 2);
});

for (const [label, change] of Object.entries({
  unknown: { status: 'UNKNOWN' }, version: { browserVersion: '26.7' },
  app: { bundleId: 'com.apple.Terminal' }, malformed: { pid: '312' },
  fractionalPid: { pid: 1.1 }, invalidLaunch: { launchedAt: NaN },
  otherFixture: { url: options.expectedUrl.replace('123456', '654321') },
  sameOriginQuery: { url: options.expectedUrl + '?token=secret' },
  sameOriginHash: { url: options.expectedUrl + '#elsewhere' },
  extraContent: { title: 'must not reach navigator' },
})) {
  test(`${label} observation refuses and permanently fences reuse`, async () => {
    let calls = 0;
    const probe = createSafariOriginProbe(options, async () => {
      calls++;
      return calls === 1 ? { ...known, ...change } : known;
    });
    await assert.rejects(probe, (error) => !error.message.includes('fixture_nonce'));
    await assert.rejects(probe);
    assert.equal(calls, 1);
  });
}

test('same process ID reused after browser restart is not the original instance', async () => {
  let calls = 0;
  const probe = createSafariOriginProbe(options, async () => ({ ...known, launchedAt: known.launchedAt + calls++ }));
  await probe();
  await assert.rejects(probe);
});

test('concurrent sampling fences both the late result and all subsequent calls', async () => {
  let release;
  const probe = createSafariOriginProbe(options, () => new Promise((resolve) => { release = resolve; }));
  const first = probe();
  await assert.rejects(probe);
  release(known);
  await assert.rejects(first);
  await assert.rejects(probe);
});

test('invalid target never invokes a native probe', () => {
  for (const url of ['https://app.example/form/fixture_nonce_123456', 'http://localhost:3000/',
    options.expectedUrl + '?x=1', options.expectedUrl + '#x',
    options.expectedUrl.replace('127.0.0.1', 'user:secret@127.0.0.1')]) {
    assert.throws(() => createSafariOriginProbe({ ...options, expectedUrl: url }));
  }
});

test('missing native executable refuses without simulated readiness', async () => {
  const probe = createSafariOriginProbe({ ...options, helperPath: '/nonexistent-accessforge-native-probe' });
  await assert.rejects(probe, /Safari observation unavailable/);
  await assert.rejects(probe, /fenced/);
});

for (const [label, input] of [
  ['malformed private input', 'private-fixture-secret-invalid-json'],
  ['extra executable configuration', JSON.stringify({ ...options, helperPath: '/untrusted/helper' })],
  ['oversized input', 'private-fixture-secret'.repeat(500)],
]) {
  test(`operator command refuses ${label} without echoing input`, async () => {
    const result = await new Promise((resolve) => {
      const child = execFile(process.execPath, [fileURLToPath(new URL('../dist/probe-safari.js', import.meta.url))],
        { timeout: 3000, maxBuffer: 4096 }, (error, stdout, stderr) => resolve({ error, stdout, stderr }));
      child.stdin.on('error', () => {});
      child.stdin.end(input);
    });
    assert.equal(result.error?.code, 78);
    assert.deepEqual(JSON.parse(result.stdout), { status: 'UNKNOWN', reason: 'INPUT_OR_PROBE_UNAVAILABLE' });
    assert.equal(result.stderr, '');
    assert.equal(result.stdout.includes('private-fixture-secret'), false);
  });
}

test('URL drift between intent and physical dispatch prevents real-adapter invocation', async () => {
  let samples = 0, effects = 0, adapterCalls = 0;
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  let command;
  const results = [];
  const journal = new MemoryJournal();
  const runner = new AuthenticatedRunner({
    session: {
      receipt: { reference },
      async retainIntent(value) { command = { ...value, actionId: randomUUID() }; return command; },
      async commitDispatch() { return command; },
      async completeAction(_id, status) { results.push(status); },
      async retainObservation() { throw new Error('no reader observation should exist'); },
      async finish() { throw new Error('no STOP'); },
    },
    journal, clock: { monotonic: () => performance.now(), utc: () => new Date().toISOString() },
    lease: { leaseId: reference.leaseId, epoch: 1, deadlineMonotonic: performance.now() + 10000,
      maxActions: 5, maxWallTimeSeconds: 10 },
    actionTimeoutMs: 2000,
    preflight: async () => ({ checks: Object.fromEntries(PREFLIGHT_CHECKS.map((key) => [key, { condition: 'TRUE' }])) }),
    observeOrigin: createSafariOriginProbe(options, async () => ++samples === 1 ? known : { ...known, url: 'http://elsewhere.test' }),
    authorizePhysicalAction: async () => { effects++; },
    adapter: { async perform() { adapterCalls++; return { status: 'SUCCEEDED' }; } },
    recordObservation: async () => {},
  });
  // Only the control plane, journal and native-probe port are exercised: no actual OS input.
  assert.equal((await runner.perform({ action: 'NEXT' })).status, 'AMBIGUOUS');
  assert.equal(adapterCalls, 0);
  assert.equal(effects, 1);
  assert.equal(samples, 2);
  assert.deepEqual(results, ['AMBIGUOUS']);
  assert.equal(journal.entries.length, 2);
  assert.equal((await runner.perform({ action: 'NEXT' })).status, 'REFUSED');
});
