import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createSafariKeyboardFocusProbe, createSafariOriginProbe, createSafariObservationProbes } from '../dist/safari-origin.js';

// Synthetic helper output: these tests do not operate Safari, AX or a screen reader.
const options = { expectedUrl: 'http://127.0.0.1:3000/form/fixture_nonce_123456', expectedBrowserVersion: '26.6' };
const focus = { measurementKind: 'AX_KEYBOARD_FOCUS', role: 'AXTextField', identifierDigest: 'a'.repeat(64) };
const known = { schemaVersion: 1, status: 'KNOWN', bundleId: 'com.apple.Safari',
  pid: 312, launchedAt: 1700000000.5, browserVersion: '26.6', url: options.expectedUrl, keyboardFocus: focus };

test('paired origin and focus share original process identity and refusal state', async () => {
  const { keyboardFocus, ...origin } = known;
  const probes = createSafariObservationProbes(options, async request => request.includeKeyboardFocus
    ? { ...known, pid: 999 } : origin);
  assert.equal(await probes.observeOrigin(), 'http://127.0.0.1:3000');
  await assert.rejects(probes.observeKeyboardFocus);
  await assert.rejects(probes.observeOrigin);
});

test('overlapping origin/focus sampling fences both channels, including late origin', async () => {
  const { keyboardFocus, ...origin } = known;
  let resolve;
  const probes = createSafariObservationProbes(options, () => new Promise(done => { resolve = done; }));
  const first = probes.observeOrigin();
  await assert.rejects(probes.observeKeyboardFocus);
  resolve(origin);
  await assert.rejects(first);
  await assert.rejects(probes.observeOrigin);
});

test('explicit private focus collection returns only bounded metadata and resamples changes', async () => {
  let calls = 0;
  const probe = createSafariKeyboardFocusProbe(options, async request => {
    assert.deepEqual(request, { ...options, includeKeyboardFocus: true });
    assert.ok(Object.isFrozen(request));
    return { ...known, keyboardFocus: { ...focus, identifierDigest: (++calls === 1 ? 'a' : 'b').repeat(64) } };
  });
  const first = await probe();
  assert.deepEqual(first, { origin: 'http://127.0.0.1:3000', keyboardFocus: focus });
  assert.ok(Object.isFrozen(first) && Object.isFrozen(first.keyboardFocus));
  assert.equal((await probe()).keyboardFocus.identifierDigest, 'b'.repeat(64));
  assert.equal(calls, 2); // Focus may move within the same qualified browser process.
});

for (const [name, keyboardFocus] of Object.entries({
  absent: undefined, null: null, legacyPhrase: { phrase: 'Email edit text' },
  wrongKind: { ...focus, measurementKind: 'SPOKEN_PHRASE' },
  toolbar: { ...focus, role: 'AXToolbar' }, malformedDigest: { ...focus, identifierDigest: 'guess' },
  extraContent: { ...focus, value: 'private input' }, rawIdentifier: { ...focus, identifier: 'secret-selector' },
})) {
  test(`${name} is not a focus measurement and permanently fences the collector`, async () => {
    let calls = 0;
    const probe = createSafariKeyboardFocusProbe(options, async () => {
      calls++;
      return calls === 1 ? { ...known, keyboardFocus } : known;
    });
    await assert.rejects(probe, error => !error.message.includes('secret') && !error.message.includes('fixture_nonce'));
    await assert.rejects(probe);
    assert.equal(calls, 1);
  });
}

test('focus collection retains browser replacement fencing', async () => {
  let calls = 0;
  const probe = createSafariKeyboardFocusProbe(options, async () => ({ ...known, launchedAt: known.launchedAt + calls++ }));
  await probe();
  await assert.rejects(probe);
});

test('focus collection fences concurrent and late observations', async () => {
  let resolve;
  const probe = createSafariKeyboardFocusProbe(options, () => new Promise(done => { resolve = done; }));
  const first = probe();
  await assert.rejects(probe);
  resolve(known);
  await assert.rejects(first);
  await assert.rejects(probe);
});

test('origin-only callers never silently accept focus payloads', async () => {
  const probe = createSafariOriginProbe(options, async request => {
    assert.deepEqual(request, options);
    return known;
  });
  await assert.rejects(probe);
});
