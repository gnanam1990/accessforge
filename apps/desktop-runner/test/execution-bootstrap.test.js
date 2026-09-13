import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, readdirSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { RealReaderUnavailable } from '@accessforge/at-voiceover';
import { createExecutionBootstrap } from '../dist/execution-bootstrap.js';
import { parseDispatchEnvelope } from '../dist/dispatch-receiver.js';
import { MemoryJournal } from '../dist/journal.js';
import { READER_STARTUP_EFFECTS_DIGEST } from '../dist/reader-startup-consent.js';

function input() {
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  return { reference, ticket: { ticketId: randomUUID(), token: 'A'.repeat(43),
    expiresAt: new Date(Date.now() + 60000).toISOString() } };
}

test('dispatch snapshot is bounded, canonical and independent of later controller mutation', () => {
  const envelope = input(), retained = parseDispatchEnvelope(envelope);
  const oldRun = envelope.reference.runId, oldToken = envelope.ticket.token;
  envelope.reference.runId = randomUUID(); envelope.ticket.token = 'B'.repeat(43);
  assert.equal(retained.reference.runId, oldRun);
  assert.equal(retained.ticket.token, oldToken);
  assert.ok(Object.isFrozen(retained.reference)); assert.ok(Object.isFrozen(retained.ticket));
  assert.throws(() => parseDispatchEnvelope({ ...input(), navigatorCommand: 'NEXT' }));
  assert.throws(() => parseDispatchEnvelope({ ...input(), ticket: { ...input().ticket, token: 'A'.repeat(4097) } }));
});

test('unproven production profile refuses before claim creation, network, callbacks or SDK import', (t) => {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'accessforge-bootstrap-')));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const envelope = input();
  let calls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { calls++; throw new Error('network must not be reached'); };
  t.after(() => { globalThis.fetch = originalFetch; });
  assert.throws(() => createExecutionBootstrap({
    receiver: { apiOrigin: 'https://api.example.test', claimsDirectory: directory, localReference: envelope.reference },
    dispatchEnvelope: envelope, desktopClaimDirectory: directory,
    readerStartupConsent: { consentId: randomUUID(), manifestDigest: '1'.repeat(64),
      desktopSessionKey: '2'.repeat(64), runnerProfileDigest: '3'.repeat(64), effectsDigest: READER_STARTUP_EFFECTS_DIGEST },
    lease: { leaseId: envelope.reference.leaseId, epoch: 1, deadlineMonotonic: performance.now() + 10000,
      maxActions: 5, maxWallTimeSeconds: 10 },
    journal: new MemoryJournal(), clock: { monotonic: () => performance.now(), utc: () => new Date().toISOString() },
    actionTimeoutMs: 1000,
    safari: { expectedUrl: `http://127.0.0.1:8000/form/${'a'.repeat(16)}`, expectedBrowserVersion: '26.6' },
    physicalPreflight: { expectedDesktopSessionId: '100025', observeRuntimeEvidence: async () => { calls++; return {}; } },
    readerStartup: { timeoutMs: 1000, authorize: async () => { calls++; } },
    authorizePhysicalAction: async () => { calls++; }, recordObservation: async () => { calls++; },
  }), RealReaderUnavailable);
  assert.equal(calls, 0);
  assert.deepEqual(readdirSync(directory), []);
});
