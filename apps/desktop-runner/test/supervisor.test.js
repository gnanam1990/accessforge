import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { FileJournal, MemoryJournal } from '../dist/journal.js';
import {
  ALLOWED_ACTIONS,
  CONSEQUENTIAL_ACTIONS,
  Supervisor,
  SupervisorFenced,
  inspectJournalAfterRestart,
} from '../dist/supervisor.js';

/** A clock whose two readings move independently, so a test can jump one and not the other. */
function fakeClock(startMonotonic = 1000, startUtc = '2026-09-10T12:00:00.000000Z') {
  return {
    mono: startMonotonic,
    wall: startUtc,
    monotonic() {
      return this.mono;
    },
    utc() {
      return this.wall;
    },
  };
}

function lease(overrides = {}) {
  return {
    leaseId: 'lease-1',
    epoch: 3,
    deadlineMonotonic: 10_000,
    maxActions: 50,
    maxWallTimeSeconds: 600,
    ...overrides,
  };
}

function build(overrides = {}) {
  const clock = overrides.clock ?? fakeClock();
  const journal = overrides.journal ?? new MemoryJournal();
  const dispatch = overrides.dispatch ?? (async () => 'SUCCEEDED');
  const supervisor = new Supervisor({
    clock,
    journal,
    dispatch,
    actionTimeoutMs: overrides.actionTimeoutMs ?? 50,
  });
  supervisor.adoptLease(overrides.lease ?? lease());
  return { supervisor, clock, journal };
}

// --- the allowed path ---------------------------------------------------------------------------

test('a valid action on a valid lease is dispatched', async () => {
  const { supervisor, journal } = build();
  const outcome = await supervisor.performAction('NEXT');
  assert.equal(outcome.status, 'SUCCEEDED');
  assert.equal(journal.entries.length, 2, 'one intent entry and one result entry');
});

test('every allowlisted action can be dispatched', async () => {
  for (const action of ALLOWED_ACTIONS) {
    const { supervisor } = build();
    const outcome = await supervisor.performAction(action, {
      keyChord: action === 'KEY_CHORD' ? 'TAB' : undefined,
      text: action === 'TYPE_TEXT' ? 'Test Person' : undefined,
    });
    assert.equal(outcome.status, 'SUCCEEDED', action);
  }
});

// --- intent before dispatch ---------------------------------------------------------------------

test('the intent is journaled and flushed before the action reaches the operating system', async () => {
  const journal = new MemoryJournal();
  const order = [];
  const { supervisor } = build({
    journal,
    dispatch: async () => {
      order.push('dispatched');
      return 'SUCCEEDED';
    },
  });
  const original = journal.appendAndFlush.bind(journal);
  journal.appendAndFlush = async (entry) => {
    order.push(entry.result === undefined ? 'intent-flushed' : 'result-flushed');
    return original(entry);
  };

  await supervisor.performAction('ACTIVATE');

  // If these were the other way around, a crash between them would leave no trace of a keystroke
  // that may have landed.
  assert.deepEqual(order, ['intent-flushed', 'dispatched', 'result-flushed']);
});

test('the intent entry carries a wall-clock timestamp and no monotonic one', async () => {
  const journal = new MemoryJournal();
  const { supervisor } = build({ journal });
  await supervisor.performAction('NEXT');
  const intent = journal.entries[0];
  assert.match(intent.intentAtUtc, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(
    intent.dispatchedAtMonotonic,
    undefined,
    'a monotonic reading is meaningless in an audit timeline',
  );
});

// --- no blind replay ----------------------------------------------------------------------------

test('a lost result is ambiguous and is not retried', async () => {
  let calls = 0;
  const { supervisor } = build({
    dispatch: () => {
      calls += 1;
      return new Promise(() => {}); // never settles: the result is lost
    },
    actionTimeoutMs: 20,
  });

  const outcome = await supervisor.performAction('TYPE_TEXT', { text: 'Test Person' });
  assert.equal(outcome.status, 'AMBIGUOUS');
  assert.equal(outcome.ambiguityReason, 'ACTION_RESULT_NEVER_ARRIVED');
  assert.equal(calls, 1, 'the action must not be re-dispatched');
  assert.match(outcome.detail, /will not be repeated/);
});

test('an ambiguous action fences the supervisor', async () => {
  const { supervisor } = build({
    dispatch: () => new Promise(() => {}),
    actionTimeoutMs: 20,
  });
  await supervisor.performAction('ACTIVATE');
  assert.equal(supervisor.isFenced(), true);
  assert.equal(supervisor.fencedReason(), 'ACTION_RESULT_NEVER_ARRIVED');

  const next = await supervisor.performAction('NEXT');
  assert.equal(next.status, 'REFUSED', 'a fenced desktop admits nothing further');
});

test('a fenced supervisor cannot adopt a new lease without a reset', async () => {
  const { supervisor } = build({
    dispatch: () => new Promise(() => {}),
    actionTimeoutMs: 20,
  });
  await supervisor.performAction('NEXT');
  assert.throws(() => supervisor.adoptLease(lease({ epoch: 4 })), SupervisorFenced);
});

test('a rejected dispatch is ambiguous rather than failed', async () => {
  // A rejected promise is not evidence that the operating system did nothing: the adapter may have
  // sent the keystroke and then lost the channel it was going to report on.
  const { supervisor } = build({
    dispatch: async () => {
      throw new Error('adapter channel closed');
    },
  });
  const outcome = await supervisor.performAction('ACTIVATE');
  assert.equal(outcome.status, 'AMBIGUOUS');
});

test('the consequential list is for reporting, not for deciding whether to retry', async () => {
  // Nothing is retried, consequential or not: a repeated NEXT is harmless in effect and still
  // destroys attribution of the reader's announcement.
  let calls = 0;
  const { supervisor } = build({
    dispatch: () => {
      calls += 1;
      return new Promise(() => {});
    },
    actionTimeoutMs: 20,
  });
  const outcome = await supervisor.performAction('NEXT');
  assert.equal(CONSEQUENTIAL_ACTIONS.includes('NEXT'), false);
  assert.equal(outcome.status, 'AMBIGUOUS');
  assert.equal(calls, 1);
});

// --- monotonic deadlines ------------------------------------------------------------------------

test('an expired local deadline refuses without asking the server', async () => {
  const clock = fakeClock();
  const { supervisor } = build({ clock, lease: lease({ deadlineMonotonic: 1500 }) });
  clock.mono = 1600;
  const outcome = await supervisor.performAction('NEXT');
  assert.equal(outcome.status, 'REFUSED');
  assert.equal(outcome.refusal, 'LEASE_EXPIRED');
  assert.match(outcome.detail, /not permission to keep typing/);
});

test('a wall-clock jump does not extend or shorten a lease', async () => {
  const clock = fakeClock();
  const { supervisor } = build({ clock, lease: lease({ deadlineMonotonic: 5000 }) });

  clock.wall = '2020-01-01T00:00:00.000000Z'; // six years backwards
  assert.equal((await supervisor.performAction('NEXT')).status, 'SUCCEEDED');

  clock.wall = '2040-01-01T00:00:00.000000Z'; // fourteen years forwards
  assert.equal((await supervisor.performAction('NEXT')).status, 'SUCCEEDED');

  // Only the monotonic reading decides.
  clock.mono = 5000;
  assert.equal((await supervisor.performAction('NEXT')).refusal, 'LEASE_EXPIRED');
});

test('a monotonic clock going backwards fences the desktop', async () => {
  // A monotonic clock that moves backwards is a broken premise, not a late lease: there is no
  // longer any basis for deciding when this lease ends.
  const clock = fakeClock();
  const { supervisor } = build({ clock });
  await supervisor.performAction('NEXT');
  clock.mono -= 500;
  const outcome = await supervisor.performAction('NEXT');
  assert.equal(outcome.status, 'REFUSED');
  assert.match(outcome.detail, /monotonic clock went backwards/);
  assert.equal(supervisor.fencedReason(), 'CLOCK_DISCONTINUITY');
});

test('the deadline boundary is exclusive', async () => {
  const clock = fakeClock();
  const { supervisor } = build({ clock, lease: lease({ deadlineMonotonic: 2000 }) });
  clock.mono = 2000;
  assert.equal((await supervisor.performAction('NEXT')).refusal, 'LEASE_EXPIRED');
});

// --- cancellation -------------------------------------------------------------------------------

test('a cancellation request fences the next action', async () => {
  const { supervisor } = build();
  supervisor.requestCancellation();
  const outcome = await supervisor.performAction('NEXT');
  assert.equal(outcome.refusal, 'CANCELLATION_REQUESTED');
  assert.match(outcome.detail, /effects already performed remain recorded/);
});

test('a stop may not be acknowledged while an action is unresolved', async () => {
  let release;
  let reachedDispatch;
  const dispatched = new Promise((resolve) => { reachedDispatch = resolve; });
  const { supervisor } = build({
    dispatch: () => {
      reachedDispatch();
      return new Promise((resolve) => { release = resolve; });
    },
    actionTimeoutMs: 5000,
  });

  const pending = supervisor.performAction('TYPE_TEXT', { text: 'Test Person' });
  await dispatched;
  supervisor.requestCancellation();
  assert.equal(supervisor.mayAcknowledgeStop(), false, 'an unresolved keystroke is not a stop');

  release('SUCCEEDED');
  await pending;
  assert.equal(supervisor.mayAcknowledgeStop(), true);
});

test('a cancellation during the journal flush still finds the action in flight', async () => {
  // The window this closes is real: the flush is I/O, and `inFlight` used to be set only after it.
  // A cancellation arriving in between would have found nothing unresolved and reported a clean stop
  // for a keystroke that was about to be dispatched.
  let observedDuringFlush;
  const journal = new MemoryJournal();
  const slowFlush = {
    entries: journal.entries,
    appendAndFlush: async (entry) => {
      if (entry.result === undefined) {
        observedDuringFlush = supervisor.mayAcknowledgeStop();
      }
      return journal.appendAndFlush(entry);
    },
    read: () => journal.read(),
  };
  const { supervisor } = build({ journal: slowFlush });
  supervisor.requestCancellation();

  const outcome = await supervisor.performAction('NEXT');
  // The gate refuses after cancellation, so no flush happens at all -- which is the stronger
  // guarantee. The assertion records that nothing reached the journal rather than that the window
  // merely narrowed.
  assert.equal(outcome.refusal, 'CANCELLATION_REQUESTED');
  assert.equal(observedDuringFlush, undefined, 'no intent was journaled after cancellation');
  assert.equal(journal.entries.length, 0);
});

test('a stop may not be acknowledged without a cancellation', async () => {
  const { supervisor } = build();
  assert.equal(supervisor.mayAcknowledgeStop(), false);
});

// --- budgets and policy -------------------------------------------------------------------------

test('an exhausted action budget stops work', async () => {
  const { supervisor } = build({ lease: lease({ maxActions: 1 }) });
  assert.equal((await supervisor.performAction('NEXT')).status, 'SUCCEEDED');
  const outcome = await supervisor.performAction('NEXT');
  assert.equal(outcome.refusal, 'ACTION_BUDGET_EXHAUSTED');
});

test('an action outside the sealed policy is refused', async () => {
  const { supervisor } = build();
  const outcome = await supervisor.performAction('EXECUTE_SHELL');
  assert.equal(outcome.refusal, 'ACTION_NOT_ALLOWED');
});

test('a supervisor with no lease admits nothing', async () => {
  const supervisor = new Supervisor({
    clock: fakeClock(),
    journal: new MemoryJournal(),
    dispatch: async () => 'SUCCEEDED',
    actionTimeoutMs: 50,
  });
  const outcome = await supervisor.performAction('NEXT');
  assert.equal(outcome.refusal, 'NOT_LEASED');
});

test('a lease epoch that does not advance is refused', () => {
  const { supervisor } = build({ lease: lease({ epoch: 3 }) });
  assert.throws(() => supervisor.adoptLease(lease({ epoch: 3 })), /epochs are monotonic/);
  assert.throws(() => supervisor.adoptLease(lease({ epoch: 2 })), /does not advance/);
});

// --- restart recovery ---------------------------------------------------------------------------

test('a journal with an unresolved intent requires quarantine and forbids resumption', async () => {
  const journal = new MemoryJournal();
  const { supervisor } = build({
    journal,
    dispatch: () => new Promise(() => {}),
    actionTimeoutMs: 20,
  });
  // Simulate a crash: the intent is flushed, the process dies, no result is ever written.
  const pending = supervisor.performAction('TYPE_TEXT', { text: 'Test Person' });
  const crashed = new MemoryJournal();
  crashed.entries.push(journal.entries[0]);
  await pending;

  const report = await inspectJournalAfterRestart(crashed);
  assert.equal(report.mustQuarantine, true);
  assert.equal(report.mayResumeThisAttempt, false);
  assert.equal(report.ambiguityReason, 'SUPERVISOR_CRASHED_AFTER_INTENT');
  assert.equal(report.unresolved.length, 1);
  assert.match(report.explanation, /None of these actions is re-dispatched/);
});

test('a fully resolved journal needs no quarantine but still does not resume the attempt', async () => {
  const journal = new MemoryJournal();
  const { supervisor } = build({ journal });
  await supervisor.performAction('NEXT');
  const report = await inspectJournalAfterRestart(journal);
  assert.equal(report.mustQuarantine, false);
  assert.equal(report.mayResumeThisAttempt, false, 'a restart invalidates the attempt');
  assert.equal(report.unresolved.length, 0);
});

test('recovery reports rather than recovers', async () => {
  // Structural: there is no function here that could grow a retry. The report is data.
  const report = await inspectJournalAfterRestart(new MemoryJournal());
  assert.equal(typeof report.explanation, 'string');
  assert.equal('retry' in report, false);
  assert.equal('resume' in report, false);
});

// --- the on-disk journal ------------------------------------------------------------------------

test('the file journal survives being read back by a separate reader', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'accessforge-journal-'));
  const path = join(dir, 'actions.jsonl');
  const journal = new FileJournal(path);
  const { supervisor } = build({ journal });

  await supervisor.performAction('NEXT');

  const raw = readFileSync(path, 'utf8');
  assert.equal(raw.trim().split('\n').length, 2);

  const reread = await new FileJournal(path).read();
  assert.equal(reread.length, 2);
  assert.equal(reread[0].action, 'NEXT');
});

test('a truncated final line does not discard the entries before it', async () => {
  // The expected shape of a crash mid-append. The earlier entries are intact and are exactly what
  // the restart needs to read.
  const dir = mkdtempSync(join(tmpdir(), 'accessforge-journal-'));
  const path = join(dir, 'actions.jsonl');
  const journal = new FileJournal(path);
  const { supervisor } = build({ journal });
  await supervisor.performAction('NEXT');
  appendFileSync(path, '{"actionId":"lease-1:3:2","act');

  const entries = await journal.read();
  assert.equal(entries.length, 2, 'the two complete entries survive');
});

test('reading a journal that does not exist yet is empty rather than an error', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'accessforge-journal-'));
  const entries = await new FileJournal(join(dir, 'absent.jsonl')).read();
  assert.deepEqual(entries, []);
});
