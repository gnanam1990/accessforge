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
    async retainRuntimePreflight(command) { calls.push('server-preflight'); assert.equal(command.actionId, current.actionId); },
    async authorizeCandidateFormEffect(command) { calls.push('form-permit'); assert.equal(command.actionId, current.actionId); },
    async finish() { calls.push('server-finish'); return { status: 'FINALIZING' }; },
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
  const result = await h.runner.perform({ action: 'READ_CURRENT' });
  assert.equal(result.status, 'SUCCEEDED');
  assert.equal(result.serverActionId, h.journal.entries[0].serverActionId);
  assert.notEqual(result.serverActionId, h.journal.entries[0].actionId);
  assert.deepEqual(h.calls, ['server-intent', 'server-commit', 'local-intent', 'server-preflight', 'effect-check',
    'adapter', 'observation', 'server-observation', 'local-result', 'server-result:SUCCEEDED']);
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'SUCCEEDED');
  assert.equal(h.journal.entries.length, 4);
});

test('configured focus is sampled after the adapter and retained privately before action success', async () => {
  const h = harness();
  const focus = { measurementKind: 'AX_KEYBOARD_FOCUS', status: 'KNOWN', role: 'AXTextField',
    identifierDigest: 'a'.repeat(64), capturedAtUtc: new Date().toISOString() };
  h.options.observeKeyboardFocus = async () => { h.calls.push('focus'); return focus; };
  h.options.recordObservation = async observation => {
    h.calls.push('observation');
    assert.equal('keyboardFocus' in observation, false);
  };
  h.session.retainObservation = async (command, observation, capturedAt, privateFocus) => {
    h.calls.push('server-observation');
    assert.equal(command.actionId, observation.actionId);
    assert.equal(command.sequence, observation.actionSequence);
    assert.deepEqual(privateFocus, focus);
    assert.ok(Object.isFrozen(privateFocus));
  };
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'SUCCEEDED');
  assert.ok(h.calls.indexOf('adapter') < h.calls.indexOf('focus'));
  assert.ok(h.calls.indexOf('focus') < h.calls.indexOf('observation'));
  assert.ok(h.calls.indexOf('server-observation') < h.calls.indexOf('server-result:SUCCEEDED'));
});

test('configured missing or late focus cannot produce known success or publish late evidence', async () => {
  for (const late of [false, true]) {
    const h = harness({ actionTimeoutMs: 10 });
    h.options.observeKeyboardFocus = async () => {
      if (!late) throw new Error('private focus unavailable');
      await new Promise(resolve => setTimeout(resolve, 40));
      return { measurementKind: 'AX_KEYBOARD_FOCUS', status: 'KNOWN', role: 'AXTextField',
        identifierDigest: 'a'.repeat(64), capturedAtUtc: new Date().toISOString() };
    };
    assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
    await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(h.calls.includes('server-observation'), false);
    assert.equal(h.calls.includes('server-result:SUCCEEDED'), false);
    assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  }
});

test('incomplete preflight cannot pass vacuously or create a remote intent', async () => {
  const h = harness({ preflight: async () => ({ checks: {} }) });
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'REFUSED');
  assert.deepEqual(h.calls, []);
});

test('successful STOP fences new input and only a complete local journal can close', async () => {
  const h = harness();
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'SUCCEEDED');
  assert.equal((await h.runner.perform({ action: 'STOP' })).status, 'SUCCEEDED');
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  assert.equal((await h.runner.finish()).status, 'FINALIZING');
  assert.equal(h.calls.filter((item) => item === 'server-finish').length, 1);
  await assert.rejects(h.runner.finish());
});

test('corrupted local journal and lost closing acknowledgement never permit resumed input', async () => {
  for (const fault of ['journal', 'ack']) {
    const h = harness();
    assert.equal((await h.runner.perform({ action: 'STOP' })).status, 'SUCCEEDED');
    if (fault === 'journal') h.journal.entries[0].serverActionId = randomUUID();
    else h.session.finish = async () => { throw new Error('lost close acknowledgement'); };
    await assert.rejects(h.runner.finish());
    assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
    if (fault === 'journal') assert.equal(h.calls.includes('server-finish'), false);
  }
});

test('lost reader evidence acknowledgement fences input and never reports known action success', async () => {
  const h = harness();
  h.session.retainObservation = async () => { throw new Error('lost reader receipt'); };
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  assert.equal(h.calls.filter((item) => item === 'adapter').length, 1);
  assert.equal(h.calls.includes('server-result:SUCCEEDED'), false);
});

test('lost runtime preflight acknowledgement prevents adapter entry and fences the action', async () => {
  const h = harness();
  h.session.retainRuntimePreflight = async () => { throw new Error('lost runtime receipt'); };
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal(h.calls.includes('adapter'), false);
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
});

test('candidate form permission follows physical checks and precedes the adapter', async () => {
  const h = harness({ candidateFormEffects: true });
  assert.equal((await h.runner.perform({ action: 'ACTIVATE' })).status, 'SUCCEEDED');
  assert.ok(h.calls.indexOf('form-permit') > h.calls.indexOf('effect-check'));
  assert.ok(h.calls.indexOf('form-permit') > h.calls.indexOf('server-preflight'));
  assert.ok(h.calls.indexOf('form-permit') < h.calls.indexOf('adapter'));
});

test('lost candidate form permission acknowledgement cannot enter or replay the adapter', async () => {
  const h = harness({ candidateFormEffects: true });
  h.session.authorizeCandidateFormEffect = async () => { throw new Error('lost permission ack'); };
  assert.equal((await h.runner.perform({ action: 'ACTIVATE' })).status, 'AMBIGUOUS');
  assert.equal(h.calls.includes('adapter'), false);
  assert.equal((await h.runner.perform({ action: 'ACTIVATE' })).status, 'REFUSED');
});

test('a newly UNKNOWN physical check is retained but never dispatched to the adapter', async () => {
  let probes = 0;
  const h = harness({ preflight: async () => ({ checks: Object.fromEntries(PREFLIGHT_CHECKS.map((key) =>
    [key, { condition: ++probes > PREFLIGHT_CHECKS.length && key === 'SCREEN_UNLOCKED' ? 'UNKNOWN' : 'TRUE' }])) }) });
  let observed;
  h.session.retainRuntimePreflight = async (_command, report) => { observed = report.checks.SCREEN_UNLOCKED.condition; };
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal(observed, 'UNKNOWN');
  assert.equal(h.calls.includes('adapter'), false);
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
  let release, authority;
  const held = new Promise((resolve) => { release = resolve; });
  const h = harness({ actionTimeoutMs: 20, authorizePhysicalAction: (_command, signal) => {
    authority = signal; return held;
  } });
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal(authority.aborted, true);
  release();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls.includes('adapter'), false);
});

test('each successful baseline action closes its own fresh approval signal', async () => {
  const signals = [];
  const h = harness({ authorizePhysicalAction: async (_command, signal) => {
    assert.equal(signal.aborted, false); signals.push(signal);
  } });
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'SUCCEEDED');
  assert.equal(signals[0].aborted, true);
  assert.equal((await h.runner.perform({ action: 'PREVIOUS' })).status, 'SUCCEEDED');
  assert.equal(signals[1].aborted, true);
  assert.notEqual(signals[0], signals[1]);
});

test('late retained preflight never opens a stale approval callback', async () => {
  const h = harness({ actionTimeoutMs: 20 });
  let release;
  h.session.retainRuntimePreflight = () => new Promise(resolve => { release = resolve; });
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'AMBIGUOUS');
  release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.includes('effect-check'), false);
  assert.equal(h.calls.includes('adapter'), false);
});

test('explicit cancellation reaches the in-flight baseline approval', async () => {
  let authority;
  const h = harness({ authorizePhysicalAction: async (_command, signal) => {
    authority = signal; h.runner.requestCancellation();
    signal.throwIfAborted();
  } });
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'AMBIGUOUS');
  assert.equal(authority.aborted, true);
  assert.equal(h.calls.includes('adapter'), false);
});

test('lost server result acknowledgement cannot permit another local action', async () => {
  const h = harness();
  h.session.completeAction = async () => { throw new Error('reply lost'); };
  assert.equal((await h.runner.perform({ action: 'READ_CURRENT' })).status, 'AMBIGUOUS');
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  assert.equal(h.calls.filter((call) => call === 'adapter').length, 1);
});
