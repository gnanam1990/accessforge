import assert from 'node:assert/strict';
import { test } from 'node:test';

import { runPreflight } from '@accessforge/at-voiceover';

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
    screenLocked: () => false,
    hasPermission: () => true,
    browserVersion: () => '26.6',
    ...overrides,
  };
}

function readyPreflight(env = readyEnvironment()) {
  return runPreflight(env, {
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

function harness(preflight = readyPreflight()) {
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
  return { calls, observations, runner, sink };
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
