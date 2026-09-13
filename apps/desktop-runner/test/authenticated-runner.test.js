import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { test } from 'node:test';
import { PREFLIGHT_CHECKS } from '@accessforge/at-voiceover';
import { AuthenticatedRunner } from '../dist/authenticated-runner.js';
import { MemoryJournal } from '../dist/journal.js';

// Explicitly synthetic physical adapter/preflight. These test ordering, not actual VoiceOver.
function harness(overrides = {}) {
  const calls = [], journal = new MemoryJournal();
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  let current;
  const session = {
    receipt: { reference },
    async retainIntent(command) { calls.push('server-intent'); current = { ...command, actionId: randomUUID() }; return current; },
    async commitDispatch(id) { calls.push('server-commit'); assert.equal(id, current.actionId); return current; },
    async completeAction(id, status) { calls.push(`server-result:${status}`); assert.equal(id, current.actionId); },
    async retainObservation(command) { calls.push('server-observation'); assert.equal(command.actionId, current.actionId); },
  };
  const options = {
    session, journal: {
      read: () => journal.read(),
      async appendAndFlush(entry) { calls.push(entry.result ? 'local-result' : 'local-intent'); await journal.appendAndFlush(entry); },
    },
    lease: { leaseId: reference.leaseId, epoch: 1, deadlineMonotonic: performance.now() + 10000,
      maxActions: 5, maxWallTimeSeconds: 10 },
    clock: { monotonic: () => performance.now(), utc: () => new Date().toISOString() },
    actionTimeoutMs: 100,
    preflight: async () => ({ checks: Object.fromEntries(PREFLIGHT_CHECKS.map((key) => [key, { condition: 'TRUE' }])) }),
    observeOrigin: async () => 'https://app.example.test',
    authorizePhysicalAction: async () => { calls.push('effect-check'); },
    adapter: { async perform(request, context) {
      calls.push('adapter'); assert.equal(context.actionId, current.actionId);
      assert.equal(journal.entries.at(-1).serverActionId, current.actionId);
      return { status: 'SUCCEEDED', observation: { phrase: request.action, actionId: context.actionId,
        actionSequence: context.actionSequence, capturedAtUtc: context.capturedAtUtc() } };
    } },
    recordObservation: async () => { calls.push('observation'); },
    ...overrides,
  };
  return { runner: new AuthenticatedRunner(options), calls, journal, session, options };
}

test('server claim -> fsynced local intent -> physical checks -> adapter -> observation/result -> server result', async () => {
  const h = harness();
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'SUCCEEDED');
  assert.deepEqual(h.calls, ['server-intent', 'server-commit', 'local-intent', 'effect-check',
    'adapter', 'observation', 'server-observation', 'local-result', 'server-result:SUCCEEDED']);
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'SUCCEEDED');
  assert.equal(h.journal.entries.length, 4);
});

test('incomplete preflight cannot pass vacuously or create a remote intent', async () => {
  const h = harness({ preflight: async () => ({ checks: {} }) });
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'REFUSED');
  assert.deepEqual(h.calls, []);
});

test('lost reader evidence acknowledgement fences input and never reports known action success', async () => {
  const h = harness();
  h.session.retainObservation = async () => { throw new Error('lost reader receipt'); };
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  assert.equal(h.calls.filter((item) => item === 'adapter').length, 1);
  assert.equal(h.calls.includes('server-result:SUCCEEDED'), false);
});

test('journal flush failure cannot invoke a physical adapter', async () => {
  const h = harness({ journal: { read: async () => [], appendAndFlush: async () => { throw new Error('disk'); } } });
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal(h.calls.includes('adapter'), false);
  assert.ok(h.calls.includes('server-result:AMBIGUOUS'));
});

test('fresh effect refusal blocks the adapter and fences subsequent input', async () => {
  const h = harness({ authorizePhysicalAction: async () => { throw new Error('effect not approved'); } });
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal(h.calls.includes('adapter'), false);
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
});

test('cancellation during local flush cannot cause late physical input', async () => {
  const h = harness();
  const append = h.options.journal.appendAndFlush;
  h.options.journal.appendAndFlush = async (entry) => { await append(entry); if (!entry.result) h.runner.requestCancellation(); };
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal(h.calls.includes('adapter'), false);
});

test('a timed-out physical check cannot dispatch when its promise resolves later', async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const h = harness({ actionTimeoutMs: 20, authorizePhysicalAction: () => held });
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  release();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls.includes('adapter'), false);
});

test('lost server result acknowledgement cannot permit another local action', async () => {
  const h = harness();
  h.session.completeAction = async () => { throw new Error('reply lost'); };
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  assert.equal(h.calls.filter((call) => call === 'adapter').length, 1);
});
