import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { createObserverProcessClosure } from '../dist/observer-process.js';
import { runProvisionedNavigatorExecution } from '../dist/execution-bootstrap.js';

// Real process/pipe checks with a synthetic executable. No database, reader or model proof.
function fixture(t, mode = 'known') {
  const directory = realpathSync(mkdtempSync('/tmp/af-observer-process-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const executable = join(directory, 'synthetic-observer');
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  writeFileSync(executable, `#!${process.execPath}
const args = process.argv.slice(2);
if (args[0] !== '-I' || args[1] !== '-m' || args[2] !== 'accessforge_orchestrator.completion_observer' ||
    args[args.indexOf('--workspace-id') + 1] !== '${reference.workspaceId}' ||
    args[args.indexOf('--run-id') + 1] !== '${reference.runId}' || !args.includes('--final') ||
    process.env.ACCESSFORGE_OBSERVER_DATABASE_URL !== 'private-observer-dsn' ||
    process.env.AWS_ACCESS_KEY_ID || process.env.PYTHONPATH || process.env.GITHUB_TOKEN ||
    args.some(v => v.includes('private-observer-dsn'))) process.exit(9);
console.error('secret-must-not-be-returned');
const mode = ${JSON.stringify(mode)};
if (mode === 'hang') { process.on('SIGTERM', () => {}); setInterval(() => {}, 1000); }
else if (mode === 'oversize') { console.log('secret'.repeat(1000)); }
else if (mode === 'bad') { console.log('secret-must-not-be-returned'); }
else if (mode === 'mismatch') { console.log('observer measurement retained: KNOWN'); process.exitCode = 3; }
else { console.log('observer measurement retained: ' + (mode === 'known' ? 'KNOWN' : 'UNKNOWN'));
process.exitCode = mode === 'known' ? 0 : 3; }
`, { mode: 0o700 });
  const options = { pythonExecutable: executable, credentialRef: 'observer-fixture',
    environment: {ACCESSFORGE_DATABASE_URL: 'private-product-dsn', ACCESSFORGE_OBSERVER_DATABASE_URL: 'private-observer-dsn'} };
  return { options, reference };
}

for (const mode of ['known', 'unknown']) {
  test(`closed ${mode} observer stream is acknowledged once`, async t => {
    const h = fixture(t, mode);
    const close = createObserverProcessClosure(h.options, h.reference, performance.now() + 10000);
    // Mutation after construction must not substitute credentials in the admitted closure.
    h.options.environment.ACCESSFORGE_OBSERVER_DATABASE_URL = 'changed';
    await close(new AbortController().signal);
    await assert.rejects(close(new AbortController().signal), /reconcile/);
  });
}

for (const mode of ['bad', 'mismatch', 'oversize']) {
  test(`observer ${mode} output cannot acknowledge closure or leak diagnostics`, async t => {
    const h = fixture(t, mode);
    const close = createObserverProcessClosure(h.options, h.reference, performance.now() + 10000);
    await assert.rejects(close(new AbortController().signal), error =>
      !error.message.includes('secret') && error.message.includes('unconfirmed'));
    await assert.rejects(close(new AbortController().signal), /reconcile/);
  });
}

test('cancelled and expired observer calls cannot launch or acknowledge a stream', async t => {
  const h = fixture(t, 'hang');
  const controller = new AbortController();
  controller.abort();
  const close = createObserverProcessClosure(h.options, h.reference, performance.now() + 10000);
  await assert.rejects(close(controller.signal), /unavailable/);
  const expires = createObserverProcessClosure(h.options, h.reference, performance.now() + 100);
  await assert.rejects(expires(new AbortController().signal), /unconfirmed|unavailable/);
  await assert.rejects(expires(new AbortController().signal), /reconcile/);
});

test('foreign credentials and ambiguous callback ownership refuse before native startup', async t => {
  const h = fixture(t);
  assert.throws(() => createObserverProcessClosure({...h.options,
    environment: {...h.options.environment, AWS_ACCESS_KEY_ID: 'forbidden'}}, h.reference,
  performance.now() + 10000), /configuration/);
  const deadline = performance.now() + 10000;
  await assert.rejects(runProvisionedNavigatorExecution({receiver: {localReference: h.reference},
    lease: {deadlineMonotonic: deadline}}, {reference: h.reference, deadlineMonotonic: deadline,
    independentObserver: h.options, closeIndependentObserver: async () => {}}), /ambiguous/);
});
