import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createEffectStartupAuthorization } from '../dist/effect-startup-authority.js';

function fixture(authorize = async () => {}) {
  const lifetime = new AbortController(), operation = new AbortController(), calls = [];
  const effect = {
    start: async signal => { assert.equal(signal, lifetime.signal); calls.push('start'); },
    assertActive: () => calls.push('active'),
    abort: () => calls.push('abort'),
  };
  return { lifetime, operation, calls, effect,
    run: createEffectStartupAuthorization(authorize, effect, lifetime.signal) };
}

test('late startup approval cannot launch a collector after cancellation', async () => {
  let release;
  const h = fixture(() => new Promise(resolve => { release = resolve; }));
  const pending = h.run(h.operation.signal);
  h.operation.abort();
  release();
  await assert.rejects(() => pending);
  assert.deepEqual(h.calls, []);
});

test('cancellation while collector starts aborts it and cannot admit readiness', async () => {
  const h = fixture();
  let release, started;
  const reached = new Promise(resolve => { started = resolve; });
  h.effect.start = async () => {
    h.calls.push('start'); started();
    await new Promise(resolve => { release = resolve; });
  };
  const pending = h.run(h.operation.signal);
  await reached;
  h.operation.abort();
  release();
  await assert.rejects(() => pending);
  assert.deepEqual(h.calls, ['start', 'abort']);
});

test('successful startup releases operation cancellation but never starts twice', async () => {
  const h = fixture();
  await h.run(h.operation.signal);
  h.operation.abort(); // Supervisor closes a completed operation scope.
  await h.run(new AbortController().signal);
  assert.deepEqual(h.calls, ['start', 'active', 'active']);
});

test('run cancellation and rejected approval refuse startup', async () => {
  const h = fixture(); h.lifetime.abort();
  await assert.rejects(() => h.run(h.operation.signal));
  assert.deepEqual(h.calls, []);
  const refused = fixture(async () => { throw new Error('not approved'); });
  await assert.rejects(() => refused.run(refused.operation.signal));
  assert.deepEqual(refused.calls, []);
});
