import assert from 'node:assert/strict';
import { test } from 'node:test';

import { runPreflight, PREFLIGHT_CHECKS } from '@accessforge/at-voiceover';

import { CandidateProofRunner } from '../dist/candidate-proof.js';
import { MemoryCandidateTraceSink, CandidateTraceWriter } from '../dist/candidate-trace.js';
import { MemoryJournal } from '../dist/journal.js';

function readyEnvironment(overrides = {}) {
  return {
    pathExists: () => true,
    readPreference: (domain, key) => {
      if (key === 'SCREnableAppleScript') return '1';
      if (key === 'ProductVersion') return '26.6';
      return undefined;
    },
    processRunning: () => true,
    auditSessionId: () => '100025',
    processAuditSessionId: () => '100025',
    screenLocked: () => false,
    hasPermission: () => true,
    browserVersion: () => '26.6',
    ...overrides,
  };
}

function readyPreflight(env = readyEnvironment()) {
  return runPreflight(env, {
    expectedDesktopSessionId: '100025',
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
  });
}

function harness(preflight = readyPreflight(), onRetain) {
  const calls = [];
  const observations = [];
  const adapter = {
    start: async () => calls.push('start'),
    stop: async () => calls.push('stop'),
    perform: async (request, context) => {
      calls.push(request.action);
      if (request.action === 'STOP') return { status: 'SUCCEEDED' };
      return {
        status: 'SUCCEEDED',
        observation: {
          phrase: request.action,
          capturedAtUtc: context.capturedAtUtc(),
          actionId: context.actionId,
          actionSequence: context.actionSequence,
        },
      };
    },
  };
  const sink = new MemoryCandidateTraceSink();
  if (onRetain) {
    const append = sink.appendAndFlush.bind(sink);
    sink.appendAndFlush = async (line) => { onRetain(line); await append(line); };
  }
  let id = 0;
  const trace = new CandidateTraceWriter({
    producerId: 'runner:voiceover:local',
    sink,
    sourceRecordId: () => `proof-${++id}`,
    utc: () => '2026-09-12T12:00:00.000Z',
  });
  let now = 1;
  const runner = new CandidateProofRunner({
    adapter,
    preflight,
    trace,
    journal: new MemoryJournal(),
    clock: {
      monotonic: () => now++,
      utc: () => '2026-09-12T12:00:00.000Z',
    },
    lease: {
      leaseId: 'candidate-proof',
      epoch: 1,
      deadlineMonotonic: 1_000,
      maxActions: 10,
      maxWallTimeSeconds: 60,
    },
    actionTimeoutMs: 100,
    approvedTextValues: new Set(['Test Person']),
    onObservation: async (observation) => observations.push(observation),
  });
  return { calls, observations, runner, sink, adapter };
}

test('the candidate proof runs only through the supervisor and closes its local source stream', async () => {
  const { calls, observations, runner, sink } = harness();
  const result = await runner.run([
    { action: 'NEXT' },
    { action: 'TYPE_TEXT', text: 'Test Person' },
    { action: 'STOP' },
  ]);

  assert.equal(result.status, 'CANDIDATE_COMPLETE');
  assert.deepEqual(calls, ['start', 'NEXT', 'TYPE_TEXT', 'STOP']);
  assert.equal(observations.length, 2);
  const kinds = sink.lines.map((line) =>
    line.kind === 'CANDIDATE_SOURCE_RECORD' ? line.sourceRecord.type : line.kind,
  );
  assert.deepEqual(kinds, [
    'PREFLIGHT_RESULT',
    'ACTION_INTENT',
    'READER_OBSERVATION',
    'ACTION_RESULT',
    'ACTION_INTENT',
    'READER_OBSERVATION',
    'ACTION_RESULT',
    'ACTION_INTENT',
    'ACTION_RESULT',
    'RUN_FINISHED',
    'CANDIDATE_CLOSING_WATERMARK',
  ]);
});

test('empty or incomplete qualification preflight never starts the reader', async () => {
  for (const missing of [null, ...PREFLIGHT_CHECKS]) {
    const report = readyPreflight();
    if (missing === null) report.checks = {};
    else delete report.checks[missing];
    const { runner, calls } = harness(report);
    assert.equal((await runner.run([{ action: 'NEXT' }])).status, 'BLOCKED');
    assert.deepEqual(calls, []);
  }
});

test('async trace retention cannot upgrade an UNKNOWN qualification check into startup', async () => {
  const report = readyPreflight();
  report.checks.DESKTOP_SESSION_OWNED = { ...report.checks.DESKTOP_SESSION_OWNED, condition: 'UNKNOWN' };
  const { runner, calls, sink } = harness(report, () => {
    report.checks.DESKTOP_SESSION_OWNED.condition = 'TRUE';
  });
  assert.equal((await runner.run([{ action: 'NEXT' }])).status, 'BLOCKED');
  assert.deepEqual(calls, []);
  assert.equal(sink.lines[0].sourceRecord.payload.checks.DESKTOP_SESSION_OWNED.condition, 'UNKNOWN');
});

test('any failed or unknown preflight check blocks before VoiceOver starts', async () => {
  const { calls, runner, sink } = harness(
    readyPreflight(readyEnvironment({ screenLocked: () => undefined })),
  );
  const result = await runner.run([{ action: 'NEXT' }]);
  assert.equal(result.status, 'BLOCKED');
  assert.deepEqual(calls, []);
  assert.equal(sink.lines.at(-1).kind, 'CANDIDATE_CLOSING_WATERMARK');
});

test('TYPE_TEXT must match an approved synthetic fixture value before the reader starts', async () => {
  const { calls, runner } = harness();
  await assert.rejects(
    () => runner.run([{ action: 'TYPE_TEXT', text: 'not-approved' }]),
    /approved synthetic fixture value/,
  );
  assert.deepEqual(calls, []);
});

test('an empty action list cannot become a completed candidate proof', async () => {
  const { calls, runner, sink } = harness();
  await assert.rejects(() => runner.run([]), /requires at least one action/);
  assert.deepEqual(calls, []);
  assert.deepEqual(sink.lines, []);
});

test('partial reader startup failure still attempts cleanup before closing the trace', async () => {
  const { runner, adapter, calls, sink } = harness();
  adapter.start = async () => { calls.push('partial-start'); throw new Error('startup failed'); };
  adapter.stop = async () => {
    assert.equal(sink.lines.some(line => line.kind === 'CANDIDATE_CLOSING_WATERMARK'), false);
    calls.push('stop');
  };
  assert.equal((await runner.run([{ action: 'NEXT' }])).status, 'INTERRUPTED');
  assert.deepEqual(calls, ['partial-start', 'stop']);
  assert.equal(sink.lines.at(-1).kind, 'CANDIDATE_CLOSING_WATERMARK');
});

test('cleanup failure cannot produce a completed candidate trace', async () => {
  const { runner, adapter, calls, sink } = harness();
  adapter.stop = async () => { calls.push('stop'); throw new Error('cleanup failed'); };
  const result = await runner.run([{ action: 'NEXT' }]);
  assert.equal(result.status, 'INTERRUPTED');
  assert.match(result.detail, /cleanup unconfirmed/);
  assert.deepEqual(calls, ['start', 'NEXT', 'stop']);
  assert.equal(sink.lines.at(-2).sourceRecord.payload.status, 'INTERRUPTED');
});

test('successful implicit cleanup precedes the completed trace', async () => {
  const { runner, adapter, calls, sink } = harness();
  adapter.stop = async () => {
    assert.equal(sink.lines.some(line => line.sourceRecord?.type === 'RUN_FINISHED'), false);
    calls.push('stop');
  };
  assert.equal((await runner.run([{ action: 'NEXT' }])).status, 'CANDIDATE_COMPLETE');
  assert.deepEqual(calls, ['start', 'NEXT', 'stop']);
});
