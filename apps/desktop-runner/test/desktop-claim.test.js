import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { chmodSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { PREFLIGHT_CHECKS } from '@accessforge/at-voiceover';
import { createExclusiveDesktopRunner } from '../dist/desktop-claim.js';
import { AuthenticatedRunner } from '../dist/authenticated-runner.js';
import { MemoryJournal } from '../dist/journal.js';

// Synthetic physical/session ports. These cases exercise local exclusion, not real VoiceOver.
function fixture(t) {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'accessforge-desktop-claim-')));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const path = join(directory, 'desktop-100025.json');
  function create(overrides = {}) {
    const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
      runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
    const journal = new MemoryJournal(), calls = [];
    let current;
    const session = {
      receipt: { reference },
      async retainIntent(command) { current = { ...command, actionId: randomUUID() }; return current; },
      async commitDispatch() { return current; },
      async completeAction() {},
      async retainObservation() {},
      async finish() { calls.push('finish'); return { status: 'FINALIZING' }; },
      ...overrides.session,
    };
    const runner = createExclusiveDesktopRunner({ directory, desktopSessionId: '100025', reference },
      (guard) => new AuthenticatedRunner({ session, journal,
        lease: { leaseId: reference.leaseId, epoch: 1, deadlineMonotonic: performance.now() + 10000,
          maxActions: 5, maxWallTimeSeconds: 10 },
        clock: { monotonic: () => performance.now(), utc: () => new Date().toISOString() },
        actionTimeoutMs: 1000,
        preflight: async () => ({ checks: Object.fromEntries(PREFLIGHT_CHECKS.map((key) => [key, { condition: 'TRUE' }])) }),
        observeOrigin: async () => 'https://app.example.test',
        authorizePhysicalAction: overrides.authorize ?? (async () => {}),
        recordObservation: async () => {},
        adapter: { async perform() {
          guard(); calls.push('adapter');
          if (overrides.adapterThrows) throw new Error('unknown physical state');
          return { status: 'SUCCEEDED' };
        } },
      }), overrides.initialization);
    return { runner, calls, journal, reference };
  }
  return { directory, path, create };
}

test('different run/runner registrations compete for the same desktop, not separate run locks', async (t) => {
  const f = fixture(t), h = f.create();
  assert.deepEqual(JSON.parse(readFileSync(f.path)).reference, h.reference);
  assert.throws(() => f.create(), /already held/);
  assert.equal((await h.runner.perform({ action: 'STOP' })).status, 'SUCCEEDED');
  assert.ok(existsSync(f.path), 'STOP alone cannot release before server finish ACK');
  assert.equal((await h.runner.finish()).status, 'FINALIZING');
  assert.equal(existsSync(f.path), false);
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  await assert.rejects(h.runner.finish());
  f.create().runner.requestCancellation(); // A clean completed attempt permits a new owner.
});

test('cancellation, ambiguous input, incomplete journal and lost ACK all retain the claim', async (t) => {
  for (const fault of ['cancel', 'adapter', 'journal', 'ack']) {
    const f = fixture(t);
    const h = f.create({ adapterThrows: fault === 'adapter',
      ...(fault === 'ack' ? { session: { finish: async () => { throw new Error('lost ACK'); } } } : {}),
    });
    if (fault === 'cancel') h.runner.requestCancellation();
    else {
      const result = await h.runner.perform({ action: 'STOP' });
      assert.equal(result.status, fault === 'adapter' ? 'AMBIGUOUS' : 'SUCCEEDED');
      if (fault === 'journal') h.journal.entries[0].serverActionId = randomUUID();
    }
    await assert.rejects(h.runner.finish());
    assert.ok(existsSync(f.path));
    assert.throws(() => f.create(), /already held/);
  }
});

test('a crash/partial claim is not deleted or expired by another runner', (t) => {
  const f = fixture(t);
  writeFileSync(f.path, '{partial', { mode: 0o600 });
  assert.throws(() => f.create(), /already held/);
  assert.equal(readFileSync(f.path, 'utf8'), '{partial');
});

test('claim mutation during physical authorization blocks the final adapter and never deletes the changed file', async (t) => {
  const f = fixture(t);
  const h = f.create({ authorize: async () => writeFileSync(f.path, 'changed') });
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'AMBIGUOUS');
  assert.equal(h.calls.includes('adapter'), false);
  await assert.rejects(h.runner.finish());
  assert.equal(readFileSync(f.path, 'utf8'), 'changed');
});

test('unsafe root is refused before creation, and later permission drift fences input', async (t) => {
  const f = fixture(t);
  chmodSync(f.directory, 0o755);
  assert.throws(() => f.create(), /claim unavailable/);
  assert.equal(existsSync(f.path), false);
  chmodSync(f.directory, 0o700);
  const h = f.create();
  chmodSync(f.directory, 0o750);
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  assert.deepEqual(h.calls, []);
  assert.ok(existsSync(f.path));
});

test('cancellation racing successful server finish keeps the claim and fences late work', async (t) => {
  const f = fixture(t);
  let acknowledge, entered;
  const waiting = new Promise((resolve) => { entered = resolve; });
  const h = f.create({ session: { finish: () => { entered(); return new Promise((resolve) => { acknowledge = resolve; }); } } });
  assert.equal((await h.runner.perform({ action: 'STOP' })).status, 'SUCCEEDED');
  const finished = h.runner.finish();
  const rejected = assert.rejects(finished, /cancelled/);
  await waiting;
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  h.runner.requestCancellation();
  acknowledge({ status: 'FINALIZING' });
  await rejected;
  assert.ok(existsSync(f.path));
  assert.throws(() => f.create(), /already held/);
});

test('initialization holds the claim before startup and admits actions only after readiness', async (t) => {
  const f = fixture(t);
  let release, entered;
  const started = new Promise((resolve) => { entered = resolve; });
  const h = f.create({ initialization: { timeoutMs: 1000, run: async (guard) => {
    guard(); assert.ok(existsSync(f.path));
    assert.throws(() => f.create(), /already held/);
    entered();
    await new Promise((resolve) => { release = resolve; });
    guard();
  } } });
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  const ready = h.runner.initialize();
  await started;
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  await assert.rejects(h.runner.initialize(), /not repeatable/);
  release(); await ready;
  await assert.rejects(h.runner.initialize(), /not repeatable/);
  assert.equal((await h.runner.perform({ action: 'STOP' })).status, 'SUCCEEDED');
  await h.runner.finish();
  assert.equal(existsSync(f.path), false);
});

test('cancelled startup settles promptly and a late initializer cannot send subsequent input', async (t) => {
  const f = fixture(t);
  let release, entered, lateInput = false;
  const started = new Promise((resolve) => { entered = resolve; });
  const h = f.create({ initialization: { timeoutMs: 1000, run: async (guard) => {
    entered(); await new Promise((resolve) => { release = resolve; });
    guard(); lateInput = true;
  } } });
  const rejected = assert.rejects(h.runner.initialize(), /unconfirmed/);
  await started;
  h.runner.requestCancellation();
  await rejected;
  release();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(lateInput, false);
  assert.equal((await h.runner.perform({ action: 'NEXT' })).status, 'REFUSED');
  assert.ok(existsSync(f.path));
});

test('startup failure retains exclusion and cannot become a clean STOP or a retry', async (t) => {
  const f = fixture(t);
  const h = f.create({ initialization: { timeoutMs: 1000, run: async () => { throw new Error('readiness missing'); } } });
  await assert.rejects(h.runner.initialize(), /unconfirmed/);
  await assert.rejects(h.runner.initialize(), /not repeatable/);
  await assert.rejects(h.runner.finish());
  assert.ok(existsSync(f.path));
  assert.throws(() => f.create(), /already held/);
});
