import assert from 'node:assert/strict';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { createConnection } from 'node:net';
import { test } from 'node:test';
import { startNavigatorActionBridge } from '../dist/navigator-action-bridge.js';

// Actual local sockets with a synthetic runner; not an actual-reader or provider demonstration.
async function fixture(t, perform) {
  const directory = realpathSync(mkdtempSync('/tmp/afn-'));
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  const calls = [];
  const runner = { reference, async initialize() { calls.push('initialize'); },
    async perform(command) { calls.push(command); return perform?.(command) ?? { status: 'SUCCEEDED', serverActionId: randomUUID() }; },
    requestCancellation() { calls.push('cancel'); },
    async finish() { calls.push('finish'); return { status: 'FINALIZING' }; } };
  const bridge = await startNavigatorActionBridge({ runner, reference, privateDirectory: directory,
    deadlineMonotonic: performance.now() + 10000, maxActions: 5 });
  const capability = bridge.privateReference();
  t.after(async () => { await bridge.close(); rmSync(directory, { recursive: true, force: true }); });
  return { bridge, capability, calls };
}

function send(capability, sequence, command, override = {}) {
  const requestId = randomBytes(16).toString('hex');
  return new Promise((resolve, reject) => {
    const socket = createConnection(capability.socketPath);
    let data = '';
    const timer = setTimeout(() => { socket.destroy(); reject(new Error('fixture timeout')); }, 3000);
    socket.on('data', (chunk) => { data += chunk; });
    socket.on('error', reject);
    socket.once('connect', () => socket.write(JSON.stringify({ protocol: capability.protocol,
      token: capability.token, reference: capability.reference, requestId, sequence, command, ...override }) + '\n'));
    socket.once('close', () => { clearTimeout(timer);
      if (!data) { resolve(null); return; }
      try { const result = JSON.parse(data); assert.equal(result.requestId, requestId); resolve(result); }
      catch (error) { reject(error); }
    });
  });
}

test('native bridge admits references, preserves known failures and requires explicit STOP/finish', { skip: process.platform === 'win32' }, async (t) => {
  const h = await fixture(t, (command) => ({ status: command.action === 'NEXT' ? 'FAILED' : 'SUCCEEDED', serverActionId: randomUUID() }));
  assert.equal((await send(h.capability, 1, { action: 'TYPE_TEXT', textValueRef: 'fullName' })).status, 'SUCCEEDED');
  assert.deepEqual(h.calls[1], { action: 'TYPE_TEXT', textValueRef: 'fullName' });
  assert.equal((await send(h.capability, 2, { action: 'NEXT' })).status, 'FAILED');
  await assert.rejects(h.bridge.finish());
  assert.equal((await send(h.capability, 3, { action: 'STOP' })).status, 'SUCCEEDED');
  assert.equal(await send(h.capability, 4, { action: 'NEXT' }), null);
  assert.equal((await h.bridge.finish()).status, 'FINALIZING');
});

for (const fault of ['raw-text', 'foreign', 'replay', 'unknown']) {
  test(`native bridge fences ${fault} without a second action`, { skip: process.platform === 'win32' }, async (t) => {
    const h = await fixture(t, fault === 'unknown' ? () => ({ status: 'AMBIGUOUS', serverActionId: randomUUID() }) : undefined);
    const command = fault === 'raw-text' ? { action: 'TYPE_TEXT', text: 'not authorized' } : { action: 'NEXT' };
    if (fault === 'replay') assert.equal((await send(h.capability, 1, command)).status, 'SUCCEEDED');
    const result = await send(h.capability, 1, command, fault === 'foreign'
      ? { reference: { ...h.capability.reference, runId: randomUUID() } } : {});
    assert.equal(result?.status ?? null, fault === 'unknown' ? 'AMBIGUOUS' : null);
    assert.equal(await send(h.capability, 2, { action: 'NEXT' }), null);
    assert.equal(h.calls.filter((item) => typeof item === 'object').length, ['replay', 'unknown'].includes(fault) ? 1 : 0);
    await assert.rejects(h.bridge.finish());
  });
}

test('finish drains the STOP reply before server-side socket closure', { skip: process.platform === 'win32' }, async (t) => {
  const h = await fixture(t);
  const socket = createConnection({ path: h.capability.socketPath, allowHalfOpen: true });
  t.after(() => socket.destroy());
  const finished = new Promise((resolve, reject) => {
    let data = '';
    socket.on('error', reject);
    socket.once('connect', () => socket.write(JSON.stringify({ ...h.capability,
      socketPath: undefined, requestId: randomBytes(16).toString('hex'), sequence: 1,
      command: { action: 'STOP' } }) + '\n'));
    socket.on('data', (chunk) => {
      data += chunk;
      if (!data.endsWith('\n')) return;
      try { assert.equal(JSON.parse(data).status, 'SUCCEEDED'); }
      catch (error) { reject(error); socket.destroy(); return; }
      h.bridge.finish().then(resolve, reject);
      socket.end();
    });
  });
  assert.equal((await finished).status, 'FINALIZING');
  assert.equal(h.calls.filter((item) => item === 'finish').length, 1);
});

test('disconnect during an entered action fences its late result and all later input', { skip: process.platform === 'win32' }, async (t) => {
  let release, entered;
  const started = new Promise((resolve) => { entered = resolve; });
  const pending = new Promise((resolve) => { release = resolve; });
  const h = await fixture(t, () => { entered(); return pending; });
  const socket = createConnection(h.capability.socketPath);
  socket.on('error', () => {});
  t.after(() => socket.destroy());
  socket.once('connect', () => socket.write(JSON.stringify({ protocol: h.capability.protocol,
    token: h.capability.token, reference: h.capability.reference,
    requestId: randomBytes(16).toString('hex'), sequence: 1, command: { action: 'NEXT' } }) + '\n'));
  await started;
  const disconnected = new Promise((resolve) => socket.once('close', resolve));
  socket.destroy();
  await disconnected;
  // The server must observe the disconnect even while perform is still unresolved.
  // A concurrent authenticated request also fences, so neither ordering can allow a second input.
  assert.equal(await send(h.capability, 2, { action: 'NEXT' }), null);
  release({ status: 'SUCCEEDED', serverActionId: randomUUID() });
  assert.equal(await send(h.capability, 3, { action: 'STOP' }), null);
  assert.equal(h.calls.filter((item) => typeof item === 'object').length, 1);
  await assert.rejects(h.bridge.finish());
});
