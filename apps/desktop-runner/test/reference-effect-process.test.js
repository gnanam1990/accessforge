import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { createReferenceEffectProcess } from '../dist/reference-effect-process.js';
import { runProvisionedNavigatorExecution } from '../dist/execution-bootstrap.js';

// Actual child/pipe behavior with synthetic receipts. No source DB, reader or provider calls.
function fixture(t, mode = 'complete') {
  const directory = realpathSync(mkdtempSync('/tmp/af-effect-process-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const executable = join(directory, 'synthetic-worker'), marker = join(directory, 'started');
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  writeFileSync(executable, `#!${process.execPath}
const fs = require('node:fs');
fs.writeFileSync(${JSON.stringify(marker)}, 'synthetic');
const args = process.argv.slice(2);
if (args.join(' ') !== '-I -m accessforge_orchestrator.reference_effect_worker --private-host' ||
    process.env.ACCESSFORGE_OBSERVER_DATABASE_URL !== 'private-source' ||
    process.env.AWS_ACCESS_KEY_ID || process.env.PYTHONPATH || process.env.GITHUB_TOKEN ||
    args.some(v => v.includes('private-source'))) process.exit(9);
console.error('private-diagnostic-must-not-escape');
const mode = ${JSON.stringify(mode)};
let data = '', config, readyId = '${randomUUID()}';
const input = fs.createReadStream('', { fd: 3 });
function receipt(status) {
  return { protocol: 'accessforge.reference-effect-observer.v1', status,
    workspaceId: config.workspaceId, runId: config.runId, attemptId: config.attemptId,
    eventId: status === 'READY_RETAINED' ? readyId : '${randomUUID()}' };
}
input.on('data', chunk => {
  data += chunk.toString('utf8');
  if (!config && data.includes('\\n')) {
    const end = data.indexOf('\\n'); config = JSON.parse(data.slice(0, end)); data = data.slice(end + 1);
    if (config.runId !== '${reference.runId}' || config.attemptId !== '${reference.attemptId}' ||
        config.observerCredentialRef !== 'observer-fixture' || config.applicationRole !== 'application-fixture') process.exit(9);
    if (mode === 'hang') { process.on('SIGTERM', () => {}); setInterval(() => {}, 1000); return; }
    const ready = receipt('READY_RETAINED');
    if (mode === 'foreign-ready') ready.attemptId = '${randomUUID()}';
    if (mode === 'oversize') fs.writeSync(4, 'x'.repeat(3000));
    else fs.writeSync(4, JSON.stringify(ready) + '\\n');
    if (mode === 'duplicate-ready') fs.writeSync(4, JSON.stringify(ready) + '\\n');
    if (mode === 'closed-pipe') fs.closeSync(4);
    if (mode === 'early-exit') process.exit(0);
  }
});
input.on('end', () => {
  if (mode === 'hang') return;
  const command = JSON.parse(data);
  if (command.command !== 'FINISH' || command.readyEventId !== readyId) process.exit(9);
  if (mode !== 'missing-close') {
    const closed = receipt('CLOSED_RETAINED');
    if (mode === 'same-event') closed.eventId = readyId;
    fs.writeSync(4, JSON.stringify(closed) + '\\n');
    if (mode === 'trailing') fs.writeSync(4, 'private-trailing');
  }
  process.exitCode = mode === 'bad-exit' ? 3 : 0;
});
`, { mode: 0o700 });
  const options = { pythonExecutable: executable, credentialRef: 'observer-fixture',
    applicationRole: 'application-fixture', installationId: randomUUID(),
    environment: { ACCESSFORGE_DATABASE_URL: 'private-product', ACCESSFORGE_OBSERVER_DATABASE_URL: 'private-source' } };
  return { options, reference, marker };
}

test('collector has one private startup and requires matching closure plus clean child exit', async t => {
  const h = fixture(t);
  const worker = createReferenceEffectProcess(h.options, h.reference, performance.now() + 10000);
  t.after(() => worker.abort());
  assert.equal(existsSync(h.marker), false);
  h.options.environment.ACCESSFORGE_OBSERVER_DATABASE_URL = 'changed-after-construction';
  await worker.start(new AbortController().signal);
  worker.assertActive();
  await worker.finish();
  await assert.rejects(worker.start(new AbortController().signal));
  await assert.rejects(worker.finish());
});

for (const mode of ['foreign-ready', 'oversize', 'early-exit', 'duplicate-ready', 'closed-pipe']) {
  test(`${mode} cannot leave an apparently active collector`, async t => {
    const h = fixture(t, mode);
    const worker = createReferenceEffectProcess(h.options, h.reference, performance.now() + 10000);
    t.after(() => worker.abort());
    await worker.start(new AbortController().signal).catch(() => {});
    await assert.rejects(worker.failure, error => !error.message.includes('private'));
    assert.throws(worker.assertActive);
  });
}

for (const mode of ['missing-close', 'same-event', 'bad-exit', 'trailing']) {
  test(`${mode} cannot acknowledge successful collector closure`, async t => {
    const h = fixture(t, mode);
    const worker = createReferenceEffectProcess(h.options, h.reference, performance.now() + 10000);
    t.after(() => worker.abort());
    await worker.start(new AbortController().signal);
    await assert.rejects(worker.finish(), error => !error.message.includes('private'));
    await assert.rejects(worker.failure);
  });
}

test('pre-cancelled startup does not spawn and hung startup expires without replay', async t => {
  const h = fixture(t, 'hang');
  const cancelled = new AbortController(); cancelled.abort();
  const unused = createReferenceEffectProcess(h.options, h.reference, performance.now() + 10000);
  await assert.rejects(unused.start(cancelled.signal));
  assert.equal(existsSync(h.marker), false);
  const worker = createReferenceEffectProcess(h.options, h.reference, performance.now() + 150);
  t.after(() => worker.abort());
  await assert.rejects(worker.start(new AbortController().signal));
  await assert.rejects(worker.start(new AbortController().signal));
});

test('cancellation after READY fences an active collector', async t => {
  const h = fixture(t);
  const signal = new AbortController();
  const worker = createReferenceEffectProcess(h.options, h.reference, performance.now() + 10000);
  t.after(() => worker.abort());
  await worker.start(signal.signal);
  signal.abort();
  await assert.rejects(worker.failure);
  assert.throws(worker.assertActive);
});

test('configured collector does not bypass navigator launch consent', async t => {
  const h = fixture(t);
  const deadline = performance.now() + 10000;
  await assert.rejects(runProvisionedNavigatorExecution({
    receiver: { localReference: h.reference }, lease: { deadlineMonotonic: deadline },
    readerStartup: { timeoutMs: 1000, async authorize() { throw new Error('must not enter'); } },
    async authorizePhysicalAction() { throw new Error('must not enter'); },
  }, {
    pythonExecutable: h.options.pythonExecutable, environment: { ACCESSFORGE_DATABASE_URL: 'private-product' },
    reference: h.reference, consentId: randomUUID(), modelProfile: {}, allowBillableModelCalls: false,
    deadlineMonotonic: deadline, signal: new AbortController().signal,
    independentObserver: h.options, independentEffectObserver: h.options,
  }));
  assert.equal(existsSync(h.marker), false);
});
