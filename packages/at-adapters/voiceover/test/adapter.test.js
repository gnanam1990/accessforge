import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ALLOWED_ACTIONS,
  ALLOWED_CHORDS,
  ActionRefused,
  BLOCKED_REASON,
  EXPLAINED_REFUSALS,
  FORBIDDEN_ON_NAVIGATOR_CHANNEL,
  GUIDEPUP_MAPPING,
  MAX_PHRASE_LENGTH,
  PREFLIGHT_CHECKS,
  PROFILE_STATUS,
  RealReaderUnavailable,
  TARGET_MATRIX,
  VERIFIED_MATRICES,
  assertActionPermitted,
  assertRealReaderProven,
  captureTimedOut,
  dispatch,
  probePermission,
  probeBrowserVersion,
  probeReaderActive,
  probeReaderVersion,
  probeScreenUnlocked,
  projectForNavigator,
  runPreflight,
  voiceOverPreferencePaths,
} from '../dist/index.js';

/**
 * A probe environment with nothing configured. Every test starts from "this machine has no
 * VoiceOver", which is the actual state of the host this was written on.
 */
function bareEnvironment(overrides = {}) {
  return {
    pathExists: () => false,
    readPreference: () => undefined,
    processRunning: () => false,
    auditSessionId: () => undefined,
    screenLocked: () => undefined,
    hasPermission: () => undefined,
    ...overrides,
  };
}

// --- the blocked gate ---------------------------------------------------------------------------

test('the profile reports BLOCKED and the verified list is empty', () => {
  // These two must agree. A VERIFIED status with no verified matrix would be a claim with nothing
  // behind it, which is the exact shape of dishonesty this project is built to avoid.
  assert.equal(PROFILE_STATUS, 'BLOCKED');
  assert.equal(VERIFIED_MATRICES.length, 0);
});

test('the status is derived from the verified list rather than written independently', () => {
  // Structural: if these could disagree, one of them would eventually be edited alone.
  const derived = VERIFIED_MATRICES.length > 0 ? 'VERIFIED' : 'BLOCKED';
  assert.equal(PROFILE_STATUS, derived);
});

test('every real-reader path is gated', () => {
  assert.throws(() => assertRealReaderProven(), RealReaderUnavailable);
});

test('the blocked reason names each thing an operator must do', () => {
  for (const required of [
    'AppleScript',
    'Accessibility',
    'Automation',
    'dedicated signed-in desktop session',
  ]) {
    assert.match(BLOCKED_REASON, new RegExp(required.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
});

test('the blocked reason says why the software cannot fix it itself', () => {
  assert.match(BLOCKED_REASON, /must be granted by the person at the machine/);
});

test('dispatch refuses rather than returning a result', async () => {
  await assert.rejects(() => dispatch({ action: 'NEXT' }), RealReaderUnavailable);
});

test('a forbidden chord is refused as a policy breach, not as a missing reader', async () => {
  // The ordering matters for diagnosis. A navigator reaching for developer tools is a finding about
  // the navigator; "no reader available" is a finding about the host. Reporting the first as the
  // second would send a reviewer to the wrong conclusion entirely.
  await assert.rejects(
    () => dispatch({ action: 'KEY_CHORD', keyChord: 'CMD+OPT+I' }),
    (error) => error instanceof ActionRefused && error.code === 'KEY_CHORD_NOT_ALLOWED',
  );
});

// --- the action and chord policy ----------------------------------------------------------------

test('every allowlisted action passes validation', () => {
  for (const action of ALLOWED_ACTIONS) {
    assertActionPermitted({
      action,
      keyChord: action === 'KEY_CHORD' ? 'TAB' : undefined,
      text: action === 'TYPE_TEXT' ? 'Test Person' : undefined,
    });
  }
});

test('the action vocabulary is exactly the contract vocabulary', () => {
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

test('every action has a documented reader mapping', () => {
  // A missing entry would mean an action that validates and then has no defined behaviour.
  for (const action of ALLOWED_ACTIONS) {
    assert.ok(GUIDEPUP_MAPPING[action], `${action} has no mapping`);
  }
});

test('STOP maps to no reader call at all', () => {
  // Stopping is the supervisor tearing down its own session. Sending a keystroke to a screen reader
  // to stop would be one more action admitted after the decision to stop admitting actions.
  assert.match(GUIDEPUP_MAPPING.STOP, /no reader call/);
});

for (const action of ['EXECUTE_SHELL', 'CLICK_SELECTOR', 'NAVIGATE', 'SCREENSHOT', 'next']) {
  test(`an action outside the policy is refused: ${action}`, () => {
    assert.throws(
      () => assertActionPermitted({ action }),
      (e) => e instanceof ActionRefused && e.code === 'ACTION_NOT_ALLOWED',
    );
  });
}

for (const chord of Object.keys(EXPLAINED_REFUSALS)) {
  test(`a dangerous chord is refused with its reason: ${chord}`, () => {
    assert.throws(
      () => assertActionPermitted({ action: 'KEY_CHORD', keyChord: chord }),
      (e) => e instanceof ActionRefused && new RegExp(EXPLAINED_REFUSALS[chord]).test(e.message),
    );
  });
}

test('an unanticipated chord is refused even though nobody listed it', () => {
  // The point of an allowlist. A denylist would have to anticipate every dangerous shortcut in an
  // operating system nobody controls, and would fail open on the one nobody thought of.
  assert.throws(
    () => assertActionPermitted({ action: 'KEY_CHORD', keyChord: 'CMD+SHIFT+OPT+F17' }),
    (e) => e instanceof ActionRefused && /not a denylist/.test(e.message),
  );
});

test('no explained refusal is accidentally also allowed', () => {
  for (const chord of Object.keys(EXPLAINED_REFUSALS)) {
    assert.equal(ALLOWED_CHORDS.includes(chord), false, `${chord} is both allowed and refused`);
  }
});

test('the chord allowlist contains no chord reaching the operating system', () => {
  // A property over the whole list rather than a check of the entries someone remembered. CMD is the
  // macOS application and system modifier; reader navigation uses CTRL+OPT.
  for (const chord of ALLOWED_CHORDS) {
    assert.equal(chord.includes('CMD'), false, `${chord} carries the system modifier`);
  }
});

test('KEY_CHORD without a chord and TYPE_TEXT without text are malformed', () => {
  assert.throws(
    () => assertActionPermitted({ action: 'KEY_CHORD' }),
    (e) => e.code === 'MALFORMED',
  );
  assert.throws(
    () => assertActionPermitted({ action: 'TYPE_TEXT' }),
    (e) => e.code === 'MALFORMED',
  );
});

// --- preflight ----------------------------------------------------------------------------------

test('both VoiceOver preference paths are checked', () => {
  // An earlier version of this check looked only at the legacy path and concluded "configured" on a
  // machine where nothing was.
  const paths = voiceOverPreferencePaths('/Users/example');
  assert.equal(paths.length, 2);
  assert.ok(paths.some((p) => p.includes('Group Containers')));
  assert.ok(paths.some((p) => !p.includes('Group Containers')));
});

test('an unconfigured VoiceOver is a FALSE that names the operator action', () => {
  const result = probeReaderActive(bareEnvironment());
  assert.equal(result.condition, 'FALSE');
  assert.equal(result.requiresOperator, true);
  assert.match(result.detail, /never been run on this machine/);
});

test('a configured VoiceOver without AppleScript control is a different FALSE', () => {
  // Different remedy, different message. Collapsing the two sends someone to the wrong pane.
  const result = probeReaderActive(bareEnvironment({ pathExists: () => true }));
  assert.equal(result.condition, 'FALSE');
  assert.match(result.detail, /AppleScript control is not enabled/);
});

test('a configured and controllable VoiceOver that is not running is a plain FALSE', () => {
  const result = probeReaderActive(
    bareEnvironment({ pathExists: () => true, readPreference: () => '1' }),
  );
  assert.equal(result.condition, 'FALSE');
  assert.equal(result.requiresOperator, false, 'starting it is not a permission grant');
  assert.match(result.detail, /is not running/);
});

test('a fully configured and running VoiceOver is TRUE', () => {
  // The allowed path. Without it every assertion above could pass against a probe that always fails.
  const result = probeReaderActive(
    bareEnvironment({
      pathExists: () => true,
      readPreference: () => '1',
      processRunning: () => true,
    }),
  );
  assert.equal(result.condition, 'TRUE');
});

test('an undeterminable screen lock is UNKNOWN, never TRUE', () => {
  // A locked screen swallows every keystroke, so the run would produce an empty trace rather than an
  // error. "Cannot tell" reported as "unlocked" is how that happens.
  assert.equal(probeScreenUnlocked(bareEnvironment()).condition, 'UNKNOWN');
  assert.equal(probeScreenUnlocked(bareEnvironment({ screenLocked: () => true })).condition, 'FALSE');
  assert.equal(probeScreenUnlocked(bareEnvironment({ screenLocked: () => false })).condition, 'TRUE');
});

test('a missing permission is FALSE with an instruction, and is never self-granted', () => {
  const result = probePermission(bareEnvironment({ hasPermission: () => false }), 'Accessibility');
  assert.equal(result.condition, 'FALSE');
  assert.equal(result.requiresOperator, true);
  assert.match(result.detail, /System Settings/);
  assert.match(result.detail, /This software does not grant it/);
});

test('an undeterminable permission is UNKNOWN', () => {
  assert.equal(probePermission(bareEnvironment(), 'Automation').condition, 'UNKNOWN');
});

test('preflight reports every check module 07 requires', () => {
  // An omitted check fails readiness in module 07. Reporting all fourteen means the failure an
  // operator sees is the real one rather than "you did not tell us about this".
  const report = runPreflight(bareEnvironment());
  assert.deepEqual(Object.keys(report.checks).sort(), [...PREFLIGHT_CHECKS].sort());
});

test('preflight never reports itself available', () => {
  const report = runPreflight(bareEnvironment());
  assert.equal(report.realReaderAvailable, false);
  assert.equal(report.blockedReason, BLOCKED_REASON);
});

test('preflight surfaces the operator actions separately from the failures', () => {
  const report = runPreflight(bareEnvironment({ hasPermission: () => false }));
  assert.ok(report.operatorActions.length >= 3);
  for (const action of report.operatorActions) {
    assert.match(action, /^[A-Z_]+: /, 'each action names the check it belongs to');
  }
});

test('no check is silently reported TRUE by default', () => {
  // The failure mode this guards: a probe that is not implemented yet returning the passing value
  // because that was the easiest placeholder.
  const report = runPreflight(bareEnvironment());
  const trues = Object.entries(report.checks).filter(([, r]) => r.condition === 'TRUE');
  assert.deepEqual(
    trues.map(([name]) => name),
    ['MONOTONIC_CLOCK_HEALTHY'],
    'only the one check that genuinely needs no reader may be TRUE on a bare host',
  );
});

test('browser version is read from the host and must match the pinned profile exactly', () => {
  assert.equal(
    probeBrowserVersion(bareEnvironment({ browserVersion: () => '26.6' })).condition,
    'TRUE',
  );
  const drifted = probeBrowserVersion(bareEnvironment({ browserVersion: () => '26.6.1' }));
  assert.equal(drifted.condition, 'FALSE');
  assert.match(drifted.detail, /26\.6\.1/);
  assert.equal(probeBrowserVersion(bareEnvironment()).condition, 'UNKNOWN');
});

test('runtime setup evidence fills the seven non-host checks without weakening exact matches', () => {
  const report = runPreflight(
    bareEnvironment({ browserVersion: () => '26.6' }),
    {
      speechCaptureWorking: true,
      permittedOrigin: 'http://127.0.0.1:8081',
      observedOrigin: 'http://127.0.0.1:8081',
      originReachable: true,
      environmentResetSucceeded: true,
      expectedBuildDigest: 'a'.repeat(64),
      observedBuildDigest: 'a'.repeat(64),
      staleInputSourceDetected: false,
      journalWritable: true,
      monotonicClockHealthy: true,
    },
  );

  for (const name of [
    'BROWSER_VERSION_MATCHES_PROFILE',
    'SPEECH_CAPTURE_WORKING',
    'PERMITTED_ORIGIN_REACHABLE',
    'ENVIRONMENT_RESET_SUCCEEDED',
    'BUILD_IDENTITY_MATCHES_MANIFEST',
    'NO_STALE_INPUT_SOURCE',
    'LOCAL_JOURNAL_WRITABLE',
    'MONOTONIC_CLOCK_HEALTHY',
  ]) {
    assert.equal(report.checks[name].condition, 'TRUE', `${name} should be proven`);
  }

  const wrongOrigin = runPreflight(bareEnvironment(), {
    permittedOrigin: 'http://127.0.0.1:8081',
    observedOrigin: 'http://localhost:8081',
    originReachable: true,
  });
  assert.equal(wrongOrigin.checks.PERMITTED_ORIGIN_REACHABLE.condition, 'FALSE');
  assert.match(wrongOrigin.checks.PERMITTED_ORIGIN_REACHABLE.detail, /does not equal/);
});

// --- the navigator channel ----------------------------------------------------------------------

test('the navigator receives the phrase, its provenance, and nothing else', () => {
  const projected = projectForNavigator({
    phrase: 'Email, invalid entry',
    capturedAtUtc: '2026-09-10T12:00:00.000000Z',
    actionId: 'a1',
    actionSequence: 4,
    accessibilityTree: { role: 'textbox' },
    domSnapshot: '<input id="email" aria-invalid="true">',
    screenshotPath: '/tmp/shot.png',
    selector: '#email-error',
    observerConfig: { expected_request_count: '1' },
    fixtureAnswers: { email: 'test.person@example.test' },
  });
  assert.deepEqual(Object.keys(projected).sort(), ['phrase', 'provenance', 'truncated']);
  assert.equal(projected.provenance, 'ACTUAL_READER');
});

test('no forbidden field survives projection, checked over the whole list', () => {
  // Over the exported list rather than the fields the author happened to remember.
  const projected = projectForNavigator({
    phrase: 'x',
    capturedAtUtc: 'now',
    actionId: 'a1',
    actionSequence: 1,
    domSnapshot: '<html>',
    selector: '#x',
    observerConfig: { k: 'v' },
    fixtureAnswers: { k: 'v' },
    screenshotPath: '/tmp/x',
    accessibilityTree: {},
  });
  const serialized = JSON.stringify(projected);
  for (const field of FORBIDDEN_ON_NAVIGATOR_CHANNEL) {
    assert.equal(field in projected, false, `${field} crossed the boundary`);
  }
  for (const leaked of ['<html>', '#x', 'test.person', 'expected_request_count', '/tmp/x']) {
    assert.equal(serialized.includes(leaked), false, `${leaked} appears in the navigator view`);
  }
});

test('projection builds a new object rather than deleting fields from the old one', () => {
  // The structural difference that matters: a field added to RawObservation tomorrow is excluded by
  // default. A filter would include it by default and rely on someone remembering this file.
  const raw = { phrase: 'ok', capturedAtUtc: 'now', actionId: 'a', actionSequence: 1 };
  const withNewField = { ...raw, somethingAddedLater: 'sensitive' };
  assert.equal('somethingAddedLater' in projectForNavigator(withNewField), false);
});

test('an over-long announcement is truncated and says so', () => {
  const projected = projectForNavigator({
    phrase: 'x'.repeat(MAX_PHRASE_LENGTH + 500),
    capturedAtUtc: 'now',
    actionId: 'a',
    actionSequence: 1,
  });
  assert.equal(projected.phrase.length, MAX_PHRASE_LENGTH);
  assert.equal(projected.truncated, true);
});

test('an announcement at the limit is not marked truncated', () => {
  const projected = projectForNavigator({
    phrase: 'x'.repeat(MAX_PHRASE_LENGTH),
    capturedAtUtc: 'now',
    actionId: 'a',
    actionSequence: 1,
  });
  assert.equal(projected.truncated, false);
});

test('a capture timeout is unknown evidence, not an empty phrase', () => {
  // The distinction module 11 depends on. An unlabelled control genuinely announcing nothing and a
  // capture that failed are different observations; reporting the second as the first would evaluate
  // to FALSE and turn an infrastructure gap into a reported accessibility defect.
  const unknown = captureTimedOut(5000);
  assert.equal(unknown.provenance, 'CAPTURE_UNKNOWN');
  assert.equal('phrase' in unknown, false);
  assert.match(unknown.reason, /unknown evidence, not silence/);
});

test('the pinned matrix records what was targeted, not what was proved', () => {
  assert.equal(TARGET_MATRIX.macos, '26.6 (build 25G72)');
  assert.match(TARGET_MATRIX.voiceOver, /bundled with macOS/);
  // Every field must be populated: an empty one is a dimension of the profile nobody pinned.
  for (const [key, value] of Object.entries(TARGET_MATRIX)) {
    assert.ok(String(value).trim().length > 0, `${key} is empty`);
  }
});

// --- from a hand review of green CI --------------------------------------------------------------

test('the macOS version comparison is exact, not a prefix match', async () => {
  // `'26.6 (build 25G72)'.startsWith('26')` is true, so the prefix version of this check accepted
  // macOS 26 as macOS 26.6 -- a whole release apart, with different VoiceOver announcements,
  // reported as the pinned profile.
  const { probeReaderVersion } = await import('../dist/index.js');
  const at = (version) =>
    probeReaderVersion(bareEnvironment({ readPreference: () => version })).condition;

  assert.equal(at('26.6'), 'TRUE');
  assert.equal(at('26'), 'FALSE', 'a major version is not the pinned point release');
  assert.equal(at('26.61'), 'FALSE', 'a longer version that shares a prefix is a different release');
  assert.equal(at('27.0'), 'FALSE');
});

test('an unreadable macOS version is UNKNOWN rather than a mismatch', () => {
  // "I could not read the version" and "the version is wrong" send an operator to different places.
  assert.equal(probeReaderVersion(bareEnvironment()).condition, 'UNKNOWN');
});
