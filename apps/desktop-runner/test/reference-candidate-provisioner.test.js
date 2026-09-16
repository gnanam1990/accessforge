import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { test } from 'node:test';
import { createReferenceCandidateProvisioner } from '../dist/reference-candidate-provisioner.js';

function options(observe = async () => undefined) {
  const forbidden = async () => { throw Error('no authority call during assembly'); };
  return {
    desktop: { directory: '/nonexistent/private-test-root', desktopSessionId: '100025', reference: {
      workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
      runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1,
    } },
    physicalPreflight: { expectedDesktopSessionId: '100025', artifactProbe: {
      expectedBuildDigest: 'a'.repeat(64), reference: { protocol: 'accessforge.artifact-probe.v1',
        socketPath: '/nonexistent/probe.sock', token: 'b'.repeat(64), taskId: 'synthetic-task',
        candidateId: 'synthetic-candidate', imageId: 'synthetic-image', daemonId: 'synthetic-daemon' },
    } },
    safari: { expectedUrl: 'http://127.0.0.1:8000/form/fixture_nonce_123456', expectedBrowserVersion: '26.6' },
    referencePreparation: { authorize: forbidden, fixture: {
      permittedOrigin: 'http://127.0.0.1:8000', reservedNonce: 'fixture_nonce_123456',
      expectedBuildDigest: 'a'.repeat(64), expectedFixtureDigest: 'c'.repeat(64),
      setupToken: 'synthetic-only', variant: 'accessible',
    } },
    actions: [{ action: 'TYPE_TEXT', text: 'approved-fixture' }], approvedTextValues: ['approved-fixture'],
    maxDurationSeconds: 60, actionTimeoutMs: 1000,
    authorizeStartup: forbidden, authorizeAction: forbidden, observeStaleInputSource: observe,
  };
}

test('candidate factory is inert, snapshots bindings and permits only one configuration', async () => {
  const input = options(), expected = structuredClone(input.desktop);
  const provision = createReferenceCandidateProvisioner(input);
  input.desktop.reference.epoch = 90;
  input.actions[0].text = 'not-approved';
  input.physicalPreflight.expectedDesktopSessionId = '99';
  const config = await provision(new AbortController().signal);
  assert.deepEqual(config.desktop, expected);
  assert.equal(config.actions[0].text, 'approved-fixture');
  assert.equal(config.physicalPreflight.expectedDesktopSessionId, '100025');
  assert.equal(config.authorizeStartup, input.authorizeStartup);
  assert.equal(config.referencePreparation.authorize, input.referencePreparation.authorize);
  assert.deepEqual(await config.physicalPreflight.observeRuntimeEvidence(new AbortController().signal), {});
  await assert.rejects(provision(new AbortController().signal), /replacement/);
});

test('invalid binding, unapproved typing and absent authority refuse before execution', () => {
  for (const change of [
    x => { x.desktop.desktopSessionId = '2'; },
    x => { x.referencePreparation.fixture.expectedBuildDigest = 'd'.repeat(64); },
    x => { x.safari.expectedUrl = 'http://127.0.0.1:8000/form/other'; },
    x => { x.approvedTextValues = []; },
    x => { x.authorizeStartup = undefined; },
    x => { x.observeStaleInputSource = undefined; },
    x => { x.actions = [{ action: 'ARBITRARY_SCRIPT' }]; },
    x => { x.maxDurationSeconds = 0; },
  ]) {
    const input = options(); change(input);
    assert.throws(() => createReferenceCandidateProvisioner(input));
  }
});

for (const value of [true, false, undefined, 'false']) {
  test(`candidate stale-input observation preserves ${String(value)} without inventing a pass`, async () => {
    let calls = 0;
    const config = await createReferenceCandidateProvisioner(options(async () => { calls++; return value; }))(
      new AbortController().signal);
    assert.equal(calls, 0);
    const observe = () => config.physicalPreflight.observeRuntimeEvidence(new AbortController().signal);
    if (typeof value === 'string') await assert.rejects(observe, /malformed/);
    else assert.deepEqual(await observe(), value === undefined ? {} : { staleInputSourceDetected: value });
    assert.equal(calls, 1);
  });
}

test('cancelled provisioning is consumed and late observations are fenced', async () => {
  const lifetime = new AbortController(); lifetime.abort();
  const provision = createReferenceCandidateProvisioner(options());
  await assert.rejects(provision(lifetime.signal));
  await assert.rejects(provision(new AbortController().signal), /replacement/);
  const active = new AbortController();
  const config = await createReferenceCandidateProvisioner(options(async signal => {
    active.abort(); assert.equal(signal.aborted, true); return false;
  }))(active.signal);
  await assert.rejects(config.physicalPreflight.observeRuntimeEvidence(new AbortController().signal));
});
