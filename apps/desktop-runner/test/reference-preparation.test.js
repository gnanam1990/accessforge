import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createReferencePreparation } from '../dist/reference-preparation.js';

// Synthetic ports prove orchestration only; no browser, reader, provider or fixture POST runs.
function fixture(overrides = {}) {
  const calls = [], controller = new AbortController();
  const hash = 'a'.repeat(64), nonce = 'n'.repeat(16), origin = 'http://127.0.0.1:3000';
  const options = { fixture: { permittedOrigin: origin, reservedNonce: nonce, setupToken: 'private',
    variant: 'accessible', expectedBuildDigest: hash, expectedFixtureDigest: 'b'.repeat(64) },
    async authorize() { calls.push('authorize'); } };
  const safari = {expectedUrl: `${origin}/form/${nonce}`, expectedBrowserVersion: '26.6'};
  const ports = {
    async measure() { calls.push('measure'); return {expectedBuildDigest: hash, observedBuildDigest: hash}; },
    async prepare(setup, host) {
      calls.push('prepare'); host.assertDesktopHeld();
      assert.equal(setup.observedBuildDigest, hash);
      assert.equal(host.signal, controller.signal);
      assert.equal('fetch' in setup, false);
      return {startUrl: safari.expectedUrl, browserVersion: safari.expectedBrowserVersion,
        evidence: {permittedOrigin: origin, environmentResetSucceeded: true}};
    }, ...overrides,
  };
  return {calls, controller, options, safari, ports, hash,
    make() { return createReferencePreparation(options, safari, {expectedBuildDigest: hash, reference: {}}, ports); }};
}

test('measure and reauthorize before preparation using the original startup guard', async () => {
  const f = fixture(), run = f.make(); let guards = 0;
  const evidence = await run(() => { guards++; }, f.controller.signal);
  assert.deepEqual(f.calls, ['authorize', 'measure', 'authorize', 'prepare']);
  assert.ok(guards >= 6);
  assert.equal(evidence.environmentResetSucceeded, true);
  assert.equal(evidence.staleInputSourceDetected, undefined);
  assert.equal(evidence.speechCaptureWorking, undefined);
  assert.ok(Object.isFrozen(evidence));
  await assert.rejects(run(() => {}, f.controller.signal), /replayed/);
});

test('cancelled measurement cannot reach fixture reconciliation or restart', async () => {
  const f = fixture();
  f.ports.measure = async () => { f.controller.abort(); return {expectedBuildDigest: f.hash, observedBuildDigest: f.hash}; };
  const run = f.make();
  await assert.rejects(run(() => {}, f.controller.signal), /cancelled/);
  assert.deepEqual(f.calls, ['authorize']);
  await assert.rejects(run(() => {}, new AbortController().signal), /replayed/);
});

test('lost ownership, failed reauthorization and mismatched build prevent preparation', async () => {
  for (const mode of ['ownership', 'authorization', 'build']) {
    const f = fixture();
    let approved = 0;
    f.options.authorize = async () => { approved++; if (mode === 'authorization' && approved === 2) throw new Error('revoked'); };
    if (mode === 'build') f.ports.measure = async () => ({expectedBuildDigest: f.hash, observedBuildDigest: 'c'.repeat(64)});
    const run = f.make();
    await assert.rejects(run(() => { if (mode === 'ownership' && approved) throw new Error('lost'); }, f.controller.signal));
    assert.equal(f.calls.includes('prepare'), false);
  }
});

test('missing live probe or a different sealed target is refused at construction', () => {
  const f = fixture();
  assert.throws(() => createReferencePreparation(f.options, f.safari, undefined, f.ports));
  assert.throws(() => createReferencePreparation(f.options, {...f.safari, expectedUrl: f.safari.expectedUrl + 'other'},
    {expectedBuildDigest: f.hash, reference: {}}, f.ports));
});

test('a concurrent second call fences the original pending preparation', async () => {
  const f = fixture(); let release;
  f.ports.measure = () => new Promise(resolve => { release = resolve; });
  const run = f.make(), first = run(() => {}, f.controller.signal);
  await Promise.resolve();
  await assert.rejects(run(() => {}, f.controller.signal), /replayed/);
  release({expectedBuildDigest: f.hash, observedBuildDigest: f.hash});
  await assert.rejects(first, /cancelled/);
  assert.equal(f.calls.includes('prepare'), false);
});
