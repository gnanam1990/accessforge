import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, readdirSync, realpathSync, rmSync, statSync, chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { createReferenceNativeProvisioner } from '../dist/reference-native-provisioner.js';
import { FileJournal } from '../dist/journal.js';

function setup(t, observeStaleInputSource = async () => undefined) {
  const privateDirectory = realpathSync(mkdtempSync(join(tmpdir(), 'af-provision-')));
  t.after(() => rmSync(privateDirectory, { recursive: true, force: true }));
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 2 };
  const forbidden = async () => { throw new Error('authority must not run during provisioning'); };
  return { privateDirectory, maxActions: 10, maxWallTimeSeconds: 60, observeStaleInputSource,
    bootstrap: {
      receiver: { apiOrigin: 'https://api.example.test', claimsDirectory: privateDirectory,
        localReference: reference }, desktopClaimDirectory: privateDirectory,
      readerStartupConsentPath: join(privateDirectory, 'consent.json'), actionTimeoutMs: 1000,
      readerStartup: { timeoutMs: 1000, authorize: forbidden },
      authorizePhysicalAction: forbidden, recordObservation: forbidden,
      safari: { expectedUrl: 'http://127.0.0.1:8000/form/fixture_nonce_123456', expectedBrowserVersion: '26.6' },
      referencePreparation: { authorize: forbidden, fixture: {
        permittedOrigin: 'http://127.0.0.1:8000', reservedNonce: 'fixture_nonce_123456',
        expectedBuildDigest: 'a'.repeat(64), expectedFixtureDigest: 'b'.repeat(64),
        setupToken: 'synthetic-only', variant: 'accessible',
      } },
    },
    physicalPreflight: { expectedDesktopSessionId: '100025', artifactProbe: {
      expectedBuildDigest: 'a'.repeat(64), reference: { protocol: 'accessforge.artifact-probe.v1',
        socketPath: join(privateDirectory, 'absent.sock'), token: 'c'.repeat(64),
        taskId: 'synthetic-task', candidateId: 'synthetic-candidate', imageId: 'synthetic-image',
        daemonId: 'synthetic-daemon' },
    } },
    navigator: { pythonExecutable: '/nonexistent/synthetic-python', environment: {},
      consentId: randomUUID(), allowBillableModelCalls: false, modelProfile: {},
      independentObserver: { pythonExecutable: '/nonexistent/synthetic-python', environment: {},
        credentialRef: 'synthetic-credential' },
    },
  };
}

test('inert assembly and one private attempt share reference, lease, clock and durable journal', async t => {
  const options = setup(t);
  const provision = createReferenceNativeProvisioner(options);
  assert.deepEqual(readdirSync(options.privateDirectory), []);
  const expected = { ...options.bootstrap.receiver.localReference };
  options.bootstrap.receiver.localReference.epoch = 99;
  options.physicalPreflight.expectedDesktopSessionId = '999';
  options.navigator.environment.SECRET = 'must-not-be-copied-later';
  const config = await provision(new AbortController().signal);
  assert.deepEqual(config.bootstrap.receiver.localReference, expected);
  assert.deepEqual(config.navigator.reference, expected);
  assert.equal(config.bootstrap.lease.leaseId, expected.leaseId);
  assert.equal(config.bootstrap.lease.epoch, 2);
  assert.equal(config.bootstrap.lease.deadlineMonotonic, config.navigator.deadlineMonotonic);
  assert.ok(config.navigator.deadlineMonotonic > config.bootstrap.clock.monotonic());
  assert.equal(config.bootstrap.physicalPreflight.expectedDesktopSessionId, '100025');
  assert.deepEqual(config.navigator.environment, {});
  assert.ok(config.bootstrap.journal instanceof FileJournal);
  assert.equal(config.bootstrap.navigatorBridgeDirectory, config.privateDirectory);
  assert.equal(statSync(config.privateDirectory).mode & 0o077, 0);
  assert.deepEqual(readdirSync(config.privateDirectory), []);
  assert.deepEqual(await config.bootstrap.physicalPreflight.observeRuntimeEvidence(
    new AbortController().signal), {});
  assert.equal(await config.bootstrap.journal.probeWritable(), true);
  assert.deepEqual(readdirSync(config.privateDirectory), ['actions.jsonl']);
  await assert.rejects(provision(new AbortController().signal), /replacement attempt/);
  assert.equal(readdirSync(options.privateDirectory).length, 1);
});

test('cancelled provisioning never allocates or silently retries', async t => {
  const options = setup(t), controller = new AbortController();
  const provision = createReferenceNativeProvisioner(options);
  controller.abort();
  await assert.rejects(provision(controller.signal));
  assert.deepEqual(readdirSync(options.privateDirectory), []);
  await assert.rejects(provision(new AbortController().signal), /replacement attempt/);
});

test('private directory requirement is enforced before attempt allocation', async t => {
  const options = setup(t);
  chmodSync(options.privateDirectory, 0o755);
  await assert.rejects(createReferenceNativeProvisioner(options)(new AbortController().signal), /private/);
  assert.deepEqual(readdirSync(options.privateDirectory), []);
});

for (const value of [true, false, undefined, 'false']) {
  test(`stale-input observation ${String(value)} is measured, not defaulted`, async t => {
    let calls = 0;
    const options = setup(t, async () => { calls++; return value; });
    const config = await createReferenceNativeProvisioner(options)(new AbortController().signal);
    assert.equal(calls, 0);
    const observe = () => config.bootstrap.physicalPreflight.observeRuntimeEvidence(new AbortController().signal);
    if (typeof value === 'string') await assert.rejects(observe, /malformed/);
    else assert.deepEqual(await observe(), value === undefined ? {} : { staleInputSourceDetected: value });
    assert.equal(calls, 1);
  });
}

test('lifetime cancellation prevents a late successful runtime observation', async t => {
  let complete;
  const options = setup(t, () => new Promise(resolve => { complete = resolve; }));
  const controller = new AbortController();
  const config = await createReferenceNativeProvisioner(options)(controller.signal);
  const observation = config.bootstrap.physicalPreflight.observeRuntimeEvidence(new AbortController().signal);
  controller.abort(); complete(false);
  await assert.rejects(observation);
});

test('invalid bounds, missing authority and mismatched fixture bindings fail before allocation', t => {
  for (const mutate of [
    options => { options.maxActions = 0; },
    options => { options.maxWallTimeSeconds = 1801; },
    options => { options.observeStaleInputSource = undefined; },
    options => { options.bootstrap.authorizePhysicalAction = undefined; },
    options => { options.physicalPreflight.artifactProbe.expectedBuildDigest = 'd'.repeat(64); },
    options => { options.bootstrap.safari.expectedUrl += '/elsewhere'; },
  ]) {
    const options = setup(t); mutate(options);
    assert.throws(() => createReferenceNativeProvisioner(options));
    assert.deepEqual(readdirSync(options.privateDirectory), []);
  }
});
