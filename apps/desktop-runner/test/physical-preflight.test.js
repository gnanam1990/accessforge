import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createPhysicalPreflight } from '../dist/physical-preflight.js';

// Synthetic host/runtime inputs; these are not observations of the user's physical desktop.
const environment = {
  pathExists: () => true,
  readPreference: (_domain, key) => key === 'SCREnableAppleScript' ? '1' : '26.6',
  processRunning: () => true,
  auditSessionId: () => '100025', processAuditSessionId: () => '100025',
  screenLocked: () => false, hasPermission: () => true, browserVersion: () => '26.6',
};
const evidence = { speechCaptureWorking: true, permittedOrigin: 'http://127.0.0.1:3000',
  observedOrigin: 'http://127.0.0.1:3000', originReachable: true, environmentResetSucceeded: true,
  expectedBuildDigest: 'a'.repeat(64), observedBuildDigest: 'a'.repeat(64),
  staleInputSourceDetected: false, journalWritable: true };
function options(overrides = {}) {
  let tick = 0;
  return { expectedDesktopSessionId: '100025', environment, clock: { monotonic: () => ++tick },
    observeRuntimeEvidence: async () => evidence, ...overrides };
}

test('assigned process/console and actual shared-clock samples assemble complete checks', async () => {
  const probe = createPhysicalPreflight(options());
  const report = await probe();
  assert.equal(Object.values(report.checks).every((check) => check.condition === 'TRUE'), true);
  assert.equal(report.realReaderAvailable, false);
  assert.equal((await probe()).checks.MONOTONIC_CLOCK_HEALTHY.condition, 'TRUE');
});

test('runtime evidence cannot override the assigned desktop or clock measurements', async () => {
  let tick = 10;
  const probe = createPhysicalPreflight(options({ clock: { monotonic: () => --tick },
    observeRuntimeEvidence: async () => ({ ...evidence, monotonicClockHealthy: true, expectedDesktopSessionId: '100026' }) }));
  const report = await probe();
  assert.equal(report.checks.MONOTONIC_CLOCK_HEALTHY.condition, 'FALSE');
  assert.equal(report.checks.DESKTOP_SESSION_OWNED.condition, 'TRUE');
  await assert.rejects(probe, /fenced/);
});

test('desktop drift during the asynchronous evidence read fences the collector', async () => {
  let session = '100025';
  const probe = createPhysicalPreflight(options({ environment: { ...environment, auditSessionId: () => session },
    observeRuntimeEvidence: async () => { session = '100026'; return evidence; } }));
  assert.equal((await probe()).checks.DESKTOP_SESSION_OWNED.condition, 'FALSE');
  await assert.rejects(probe, /fenced/);
});

test('a late concurrent result cannot return readiness', async () => {
  let release;
  const probe = createPhysicalPreflight(options({ observeRuntimeEvidence: () => new Promise((resolve) => { release = resolve; }) }));
  const pending = probe();
  await assert.rejects(probe, /fenced/);
  release(evidence);
  await assert.rejects(pending, /unavailable/);
});

test('an over-budget measurement refuses instead of calling a healthy clock broken', async () => {
  let tick = 0;
  const probe = createPhysicalPreflight(options({ clock: { monotonic: () => tick += 100 }, maxProbeDurationMs: 50 }));
  await assert.rejects(probe, /unavailable/);
  await assert.rejects(probe, /fenced/);
});

test('absent runtime evidence stays unknown and an unbound configuration is refused', async () => {
  const report = await createPhysicalPreflight(options({ observeRuntimeEvidence: async () => ({}) }))();
  assert.equal(report.checks.SPEECH_CAPTURE_WORKING.condition, 'UNKNOWN');
  assert.equal(report.checks.BUILD_IDENTITY_MATCHES_MANIFEST.condition, 'UNKNOWN');
  assert.throws(() => createPhysicalPreflight(options({ expectedDesktopSessionId: '' })));
});
