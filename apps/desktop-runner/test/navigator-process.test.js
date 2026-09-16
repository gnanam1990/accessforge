import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { chmodSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { runNavigatorProcess, startOwnedNavigatorExecution } from '../dist/navigator-process.js';

// Actual child/pipe lifecycle with an explicitly synthetic executable and native bridge. No AT,
// provider call or database access. Child stdout/stderr deliberately contain a sentinel secret.
function fixture(t, mode = 'good') {
  const root = realpathSync(mkdtempSync('/tmp/af-process-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const executable = join(root, 'synthetic-child');
  writeFileSync(executable, `#!${process.execPath}
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(3, 'utf8'));
if (process.env.GITHUB_TOKEN !== undefined || process.argv.some(v => v.includes('private-token'))) process.exit(5);
if (${JSON.stringify(mode)} === 'codex-env' && (process.env.HOME !== '/operator/home' ||
    process.env.PATH !== '/operator/bin' || process.env.CODEX_HOME !== '/operator/codex' ||
    process.env.OPENAI_API_KEY !== undefined || process.env.AWS_ACCESS_KEY_ID !== undefined)) process.exit(6);
console.log('private-token'); console.error('private-token');
if (${JSON.stringify(mode)} === 'nonzero') process.exit(3);
if (${JSON.stringify(mode)} === 'oversize') { fs.writeSync(4, 'x'.repeat(3000)); process.exit(0); }
const result = {schemaVersion: 1, reference: input.reference, status: 'STOP_ACKNOWLEDGED',
  completedCalls: 1, lastOperationId: '${randomUUID()}'};
if (${JSON.stringify(mode)} === 'foreign') result.reference = {...result.reference, epoch: 99};
if (${JSON.stringify(mode)} === 'not-stopped') result.status = 'NOT_FINISHED';
if (${JSON.stringify(mode)} === 'duplicate') { fs.writeSync(4, JSON.stringify(result).slice(0, -1) + ',"schemaVersion":1}'); process.exit(0); }
fs.writeSync(4, JSON.stringify(result) + '\\n');
`, { mode: 0o700 });
  chmodSync(executable, 0o700);
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  const calls = [];
  const bridge = { privateReference() { calls.push('capability'); return {reference, token: 'private-token'}; },
    async close() { calls.push('close'); },
    async finish() { calls.push('finish'); return {status: 'FINALIZING'}; } };
  const controller = new AbortController();
  const options = { pythonExecutable: executable, environment: {ACCESSFORGE_DATABASE_URL: 'unused'},
    reference, consentId: randomUUID(), modelProfile: {}, allowBillableModelCalls: true,
    deadlineMonotonic: performance.now() + 10000, signal: controller.signal,
    async closeIndependentObserver(signal) { assert.equal(signal.aborted, false); calls.push('observer-closed'); } };
  return {bridge, options, calls, controller};
}

test('private child receipt and clean exit precede independent closure and native finish', {skip: process.platform === 'win32'}, async t => {
  const h = fixture(t);
  assert.deepEqual(await runNavigatorProcess(h.bridge, h.options), {status: 'FINALIZING'});
  assert.deepEqual(h.calls, ['capability', 'observer-closed', 'finish']);
  await assert.rejects(runNavigatorProcess(h.bridge, h.options));
  assert.equal(h.calls.filter(v => v === 'finish').length, 1);
});

for (const mode of ['nonzero', 'oversize', 'foreign', 'not-stopped', 'duplicate']) {
  test(`child ${mode} cannot acknowledge finish or be replayed`, {skip: process.platform === 'win32'}, async t => {
    const h = fixture(t, mode);
    await assert.rejects(runNavigatorProcess(h.bridge, h.options), /unconfirmed/);
    assert.ok(h.calls.includes('close'));
    assert.ok(!h.calls.includes('observer-closed') && !h.calls.includes('finish'));
    await assert.rejects(runNavigatorProcess(h.bridge, h.options));
    assert.equal(h.calls.filter(v => v === 'capability').length, 1);
  });
}

test('billable approval and credential isolation are checked before physical bootstrap', {skip: process.platform === 'win32'}, async t => {
  const h = fixture(t);
  let bootstraps = 0;
  const bootstrap = async () => { bootstraps++; return h.bridge; };
  await assert.rejects(startOwnedNavigatorExecution(bootstrap, {...h.options, allowBillableModelCalls: false}));
  await assert.rejects(startOwnedNavigatorExecution(bootstrap, {...h.options,
    environment: {...h.options.environment, GITHUB_TOKEN: 'not-a-model-credential'}}));
  assert.equal(bootstraps, 0);
});

test('Codex receives only explicit OAuth home and executable paths through the real child environment', {skip: process.platform === 'win32'}, async t => {
  const h = fixture(t, 'codex-env');
  h.options.modelProfile = {provider: 'codex-chatgpt'};
  h.options.environment = {...h.options.environment, HOME: '/operator/home', PATH: '/operator/bin', CODEX_HOME: '/operator/codex'};
  assert.deepEqual(await runNavigatorProcess(h.bridge, h.options), {status: 'FINALIZING'});
});

for (const invalid of [{}, {HOME: '/operator/home'}, {HOME: 'relative', PATH: '/operator/bin'},
  {HOME: '/operator/home', PATH: '/operator/bin:'}, {HOME: '/operator/home', PATH: '.'},
  {HOME: '/operator/home', PATH: '/operator/bin', CODEX_HOME: 'relative'},
  {HOME: '/operator/home', PATH: '/operator/bin', AWS_ACCESS_KEY_ID: 'forbidden'},
  {HOME: '/operator/home', PATH: '/operator/bin', OPENAI_API_KEY: 'forbidden'}]) {
  test('Codex rejects missing paths or foreign credentials before bootstrap: ' + JSON.stringify(Object.keys(invalid)), {skip: process.platform === 'win32'}, async t => {
    const h = fixture(t);
    let bootstraps = 0;
    h.options.modelProfile = {provider: 'codex-chatgpt'};
    h.options.environment = {ACCESSFORGE_DATABASE_URL: 'unused', ...invalid};
    await assert.rejects(startOwnedNavigatorExecution(async () => { bootstraps++; return h.bridge; }, h.options));
    assert.equal(bootstraps, 0);
  });
}

test('abort during observer closure cannot later finish or release', {skip: process.platform === 'win32'}, async t => {
  const h = fixture(t);
  let release;
  h.options.closeIndependentObserver = async () => {
    h.calls.push('observer-pending'); h.controller.abort();
    await new Promise(resolve => { release = resolve; });
  };
  await assert.rejects(runNavigatorProcess(h.bridge, h.options), /unconfirmed/);
  release();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(!h.calls.includes('finish'));
});

test('a late bootstrap after cancellation is closed without launching a child', {skip: process.platform === 'win32'}, async t => {
  const h = fixture(t);
  let release;
  const ready = new Promise(resolve => { release = resolve; });
  let entered;
  const started = new Promise(resolve => { entered = resolve; });
  const running = startOwnedNavigatorExecution(() => { entered(); return ready; }, h.options);
  await started;
  h.controller.abort();
  await assert.rejects(running, /cancelled/);
  release(h.bridge);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(h.calls, ['close']);
});

test('cancellation before queued bootstrap entry prevents startup entirely', {skip: process.platform === 'win32'}, async t => {
  const h = fixture(t);
  let started = 0;
  const running = startOwnedNavigatorExecution(async () => { started++; return h.bridge; }, h.options);
  h.controller.abort();
  await assert.rejects(running, /cancelled/);
  assert.equal(started, 0);
  assert.deepEqual(h.calls, []);
});
