import assert from 'node:assert/strict';
import { test } from 'node:test';

import { MemoryJournal } from '../dist/journal.js';
import { Supervisor } from '../dist/supervisor.js';
import { createVoiceOverDispatch } from '../dist/voiceover.js';

function clock() {
  let monotonic = 100;
  return {
    monotonic: () => monotonic++,
    utc: () => '2026-09-12T08:00:00Z',
  };
}

function lease() {
  return {
    leaseId: 'lease-voiceover',
    epoch: 4,
    deadlineMonotonic: 10_000,
    maxActions: 10,
    maxWallTimeSeconds: 60,
  };
}

test('the desktop supervisor journals intent before touching VoiceOver and records its observation', async () => {
  const order = [];
  const baseJournal = new MemoryJournal();
  const journal = {
    appendAndFlush: async (entry) => {
      order.push(entry.result === undefined ? 'intent-flushed' : 'result-flushed');
      await baseJournal.appendAndFlush(entry);
    },
    read: () => baseJournal.read(),
  };
  const adapter = {
    perform: async (request, context) => {
      order.push(`reader:${request.action}`);
      return {
        status: 'SUCCEEDED',
        observation: {
          phrase: 'Submit request, button',
          capturedAtUtc: context.capturedAtUtc(),
          actionId: context.actionId,
          actionSequence: context.actionSequence,
        },
      };
    },
  };
  const observations = [];
  const localClock = clock();
  const supervisor = new Supervisor({
    clock: localClock,
    journal,
    dispatch: createVoiceOverDispatch(adapter, {
      utc: localClock.utc,
      recordObservation: async (observation) => {
        order.push('observation-recorded');
        observations.push(observation);
      },
    }),
    actionTimeoutMs: 100,
  });
  supervisor.adoptLease(lease());

  assert.equal((await supervisor.performAction('NEXT')).status, 'SUCCEEDED');
  assert.deepEqual(order, [
    'intent-flushed',
    'reader:NEXT',
    'observation-recorded',
    'result-flushed',
  ]);
  assert.equal(observations[0].phrase, 'Submit request, button');
  assert.equal(observations[0].actionId, 'lease-voiceover:4:1');
});

test('failure to retain an observation fences an action that may already have happened', async () => {
  const adapter = {
    perform: async (_request, context) => ({
      status: 'SUCCEEDED',
      observation: {
        phrase: 'Request created',
        capturedAtUtc: context.capturedAtUtc(),
        actionId: context.actionId,
        actionSequence: context.actionSequence,
      },
    }),
  };
  const supervisor = new Supervisor({
    clock: clock(),
    journal: new MemoryJournal(),
    dispatch: createVoiceOverDispatch(adapter, {
      utc: () => '2026-09-12T08:00:00Z',
      recordObservation: async () => Promise.reject(new Error('evidence sink unavailable')),
    }),
    actionTimeoutMs: 100,
  });
  supervisor.adoptLease(lease());

  const outcome = await supervisor.performAction('ACTIVATE');
  assert.equal(outcome.status, 'AMBIGUOUS');
  assert.equal(outcome.ambiguityReason, 'ACTION_RESULT_NEVER_ARRIVED');
  assert.equal(supervisor.isFenced(), true);
});

test('STOP records no reader observation', async () => {
  const adapter = {
    perform: async () => ({ status: 'SUCCEEDED' }),
  };
  let recorded = false;
  const dispatch = createVoiceOverDispatch(adapter, {
    utc: () => '2026-09-12T08:00:00Z',
    recordObservation: async () => {
      recorded = true;
    },
  });

  assert.equal(
    await dispatch({ actionId: 'lease:1:1', sequence: 1, action: 'STOP' }),
    'SUCCEEDED',
  );
  assert.equal(recorded, false);
});
