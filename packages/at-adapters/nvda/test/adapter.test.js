import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ALLOWED_ACTIONS,
  ALLOWED_CHORDS,
  ActionRefused,
  BLOCKED_REASON,
  CROSS_READER_COMPARISON_RULE,
  EXPLAINED_REFUSALS,
  MODULE_25_MAY_COUNT_NVDA_BENCHMARK,
  PROFILE_STATUS,
  RealReaderUnavailable,
  UNUSABLE_DESKTOP_EXPLANATIONS,
  UNUSABLE_DESKTOP_STATES,
  VERIFIED_MATRICES,
  assertActionPermitted,
  assertRealReaderProven,
  desktopIsUsable,
  dispatch,
} from '../dist/index.js';

// --- blocked, and honestly so -------------------------------------------------------------------

test('the profile is BLOCKED and the verified list is empty', () => {
  assert.equal(PROFILE_STATUS, 'BLOCKED');
  assert.equal(VERIFIED_MATRICES.length, 0);
});

test('the status is derived from the verified list so the two cannot be edited apart', () => {
  assert.equal(PROFILE_STATUS, VERIFIED_MATRICES.length > 0 ? 'VERIFIED' : 'BLOCKED');
});

test('the blocked reason says there is no Windows host at all', () => {
  // Stronger than module 08's blocker, and the difference matters to anyone planning: module 08
  // needs settings changed on a machine that exists.
  assert.match(BLOCKED_REASON, /no Windows machine at all/);
  assert.match(BLOCKED_REASON, /not a service session/);
});

test('module 25 may not count the NVDA benchmark as executed', () => {
  // Asked for by name in the prompt. A benchmark table with an unrun NVDA row outlives the caveat
  // printed beside it.
  assert.equal(MODULE_25_MAY_COUNT_NVDA_BENCHMARK, false);
});

test('every real-reader path is gated', async () => {
  assert.throws(() => assertRealReaderProven(), RealReaderUnavailable);
  await assert.rejects(() => dispatch({ action: 'NEXT' }), RealReaderUnavailable);
});

test('a forbidden chord is refused as a policy breach before the missing reader', async () => {
  await assert.rejects(
    () => dispatch({ action: 'KEY_CHORD', keyChord: 'F12' }),
    (e) => e instanceof ActionRefused && e.code === 'KEY_CHORD_NOT_ALLOWED',
  );
});

test('this package does not depend on a virtual screen reader', async () => {
  // The most tempting shortcut available: a virtual reader runs on this host and would make every
  // test here green while proving nothing about a reader anybody uses. The prompt forbids it by name.
  const { readFileSync } = await import('node:fs');
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  const declared = { ...manifest.dependencies, ...manifest.devDependencies };
  for (const name of Object.keys(declared)) {
    assert.equal(name.includes('virtual-screen-reader'), false, `${name} is a virtual reader`);
  }
});

// --- Windows policy, not a copy of macOS policy -------------------------------------------------

test('the chord allowlist is the Windows one, not VoiceOver browse keys', () => {
  // CTRL+OPT is VoiceOver's navigation and means nothing to NVDA. A copied list would be both too
  // permissive and useless.
  assert.ok(ALLOWED_CHORDS.includes('DOWN'));
  assert.ok(ALLOWED_CHORDS.includes('NVDA+DOWN'));
  for (const chord of ALLOWED_CHORDS) {
    assert.equal(chord.includes('OPT'), false, `${chord} is a macOS chord`);
    assert.equal(chord.includes('CMD'), false, `${chord} is a macOS chord`);
  }
});

test('the refusal list is the Windows one', () => {
  // F12 and WIN+R have no macOS meaning; CMD+L has no Windows meaning.
  assert.ok('F12' in EXPLAINED_REFUSALS);
  assert.ok('WIN+R' in EXPLAINED_REFUSALS);
  assert.equal('CMD+L' in EXPLAINED_REFUSALS, false);
});

test('the list records that CTRL+ALT+DEL cannot be sent at all', () => {
  // Worth stating because it is the clearest argument for an allowlist: a denylist cannot block what
  // never reaches the application.
  assert.match(EXPLAINED_REFUSALS['CTRL+ALT+DEL'], /intercepted by Windows itself/);
});

test('no refused chord is also allowed', () => {
  for (const chord of Object.keys(EXPLAINED_REFUSALS)) {
    assert.equal(ALLOWED_CHORDS.includes(chord), false, `${chord} is both allowed and refused`);
  }
});

test('an unanticipated chord is refused', () => {
  assert.throws(
    () => assertActionPermitted({ action: 'KEY_CHORD', keyChord: 'CTRL+SHIFT+F19' }),
    (e) => e instanceof ActionRefused && /rather than denying dangerous ones/.test(e.message),
  );
});

test('the action vocabulary is shared with module 07 rather than reinvented', () => {
  assert.deepEqual(
    [...ALLOWED_ACTIONS].sort(),
    [
      'ACTIVATE',
      'KEY_CHORD',
      'NEXT',
      'PREVIOUS',
      'READ_CURRENT',
      'STOP',
      'TYPE_TEXT',
      'WAIT_FOR_READER_IDLE',
    ],
  );
});

test('every allowlisted action validates', () => {
  for (const action of ALLOWED_ACTIONS) {
    assertActionPermitted({
      action,
      keyChord: action === 'KEY_CHORD' ? 'TAB' : undefined,
      text: action === 'TYPE_TEXT' ? 'Test Person' : undefined,
    });
  }
});

// --- the unusable desktop -----------------------------------------------------------------------

test('a disconnected RDP session is recognised as unusable', () => {
  // The state with no macOS equivalent, and the reason this list exists separately. The session keeps
  // running, automation returns success, and nothing reaches a screen.
  const { usable, reasons } = desktopIsUsable(['RDP_SESSION_DISCONNECTED']);
  assert.equal(usable, false);
  assert.match(reasons[0], /no console attached/);
  assert.match(reasons[0], /empty trace/);
});

test('a service session is not a usable desktop however healthy it reports itself', () => {
  const { usable, reasons } = desktopIsUsable(['SESSION_IS_A_SERVICE_SESSION']);
  assert.equal(usable, false);
  assert.match(reasons[0], /session 0/);
});

test('an unobserved desktop fails closed', () => {
  // The call a caller makes when its probes have not run. Knowing nothing is not knowing it is fine,
  // and every state in the list is invisible from outside the session it describes.
  const { usable, reasons } = desktopIsUsable(undefined);
  assert.equal(usable, false);
  assert.match(reasons[0], /An unobserved desktop is not a usable one/);
});

test('a desktop with no observed problems is usable', () => {
  // Allowed-path control, so the checks above are not simply a function that refuses everything.
  assert.deepEqual(desktopIsUsable([]), { usable: true, reasons: [] });
});

test('every unusable state has an explanation an operator can act on', () => {
  for (const state of UNUSABLE_DESKTOP_STATES) {
    const explanation = UNUSABLE_DESKTOP_EXPLANATIONS[state];
    assert.ok(explanation && explanation.length > 30, `${state} has no usable explanation`);
  }
});

// --- cross-reader comparison --------------------------------------------------------------------

test('cross-reader comparison is by assertion, never by transcript text', () => {
  // Two readers announce the same correct page differently. Requiring the strings to match would fail
  // every correct Windows page, and would push someone to rewrite NVDA's wording into VoiceOver's --
  // which is manufacturing an observation.
  assert.match(CROSS_READER_COMPARISON_RULE, /frozen assertion set/);
  assert.match(CROSS_READER_COMPARISON_RULE, /never a difference in transcript text/);
});
