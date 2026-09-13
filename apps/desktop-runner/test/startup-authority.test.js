import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { NativeExecutionSession } from '../dist/dispatch-receiver.js';
import { READER_STARTUP_EFFECTS_DIGEST, parseReaderStartupConsentScope } from '../dist/reader-startup-consent.js';

// Real local HTTP with synthetic credentials/authority responses, no reader or live control plane.
async function fixture(t, fault, consent = false) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'accessforge-startup-authority-')));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  const ticket = { ticketId: randomUUID(), token: 'T'.repeat(43), expiresAt: new Date(Date.now() + 30000).toISOString() };
  const scope = { consentId: randomUUID(), manifestDigest: '1'.repeat(64), desktopSessionKey: '2'.repeat(64),
    runnerProfileDigest: '3'.repeat(64), effectsDigest: READER_STARTUP_EFFECTS_DIGEST };
  let sessionSecret, calls = 0;
  const server = createServer(async (req, res) => {
    let raw = '';
    for await (const bytes of req) raw += bytes.toString();
    if (req.url.endsWith('/session')) {
      assert.equal(req.headers.authorization, `Bearer ${ticket.token}`);
      sessionSecret = JSON.parse(raw).sessionSecret;
      res.writeHead(201, { 'Content-Type': 'application/json' }).end(JSON.stringify({ ...reference,
        sessionId: ticket.ticketId, expiresAt: ticket.expiresAt, meaning: 'SUPERVISOR_SESSION_OPENED' }));
      return;
    }
    calls++;
    assert.equal(req.url, `/v1/workspaces/${reference.workspaceId}/supervisor-sessions/${ticket.ticketId}/${consent ? 'reader-startup-consent' : 'startup-authority'}`);
    assert.equal(req.headers.authorization, `Bearer ${sessionSecret}`);
    assert.notEqual(sessionSecret, ticket.token);
    assert.deepEqual(JSON.parse(raw), {});
    const body = { sessionId: ticket.ticketId, reference: { ...reference }, expiresAt: ticket.expiresAt,
      meaning: 'EXECUTION_AUTHORITY_RECHECKED_NOT_READER_START_CONSENT' };
    if (consent) Object.assign(body, scope, { meaning: 'OPERATOR_STARTUP_CONSENT_RECHECKED_NOT_PHYSICAL_PROOF' });
    if (fault === 'grant') body.consentId = randomUUID();
    if (fault === 'desktop') body.desktopSessionKey = '4'.repeat(64);
    if (fault === 'effects') body.effectsDigest = '5'.repeat(64);
    if (fault === 'reference') body.reference.epoch++;
    if (fault === 'expired') body.expiresAt = '2000-01-01T00:00:00Z';
    if (fault === 'extended') body.expiresAt = new Date(Date.now() + 60000).toISOString();
    if (fault === 'meaning') body.meaning = 'READER_START_APPROVED';
    res.writeHead(fault === 'revoked' ? 403 : 200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const session = await NativeExecutionSession.open({ apiOrigin: `http://127.0.0.1:${server.address().port}`,
    claimsDirectory: root, localReference: reference, allowLoopbackHttp: true }, { reference, ticket });
  return { session, scope, calls: () => calls };
}

test('startup authority uses a fresh machine-secret read each time and honors pre-cancellation', async (t) => {
  const f = await fixture(t);
  const signal = new AbortController().signal;
  await f.session.checkStartupAuthority(signal);
  await f.session.checkStartupAuthority(signal);
  assert.equal(f.calls(), 2);
  const cancelled = new AbortController(); cancelled.abort();
  await assert.rejects(f.session.checkStartupAuthority(cancelled.signal));
  assert.equal(f.calls(), 2);
});

for (const fault of ['reference', 'expired', 'extended', 'meaning', 'revoked']) {
  test(`unavailable startup authority (${fault}) fences subsequent reads and intents`, async (t) => {
    const f = await fixture(t, fault);
    const signal = new AbortController().signal;
    await assert.rejects(f.session.checkStartupAuthority(signal));
    await assert.rejects(f.session.checkStartupAuthority(signal));
    await assert.rejects(f.session.retainIntent({ action: 'NEXT', sequence: 1, origin: 'http://127.0.0.1' }));
    assert.equal(f.calls(), 1);
  });
}

test('startup consent snapshots exact pinned scope and rechecks with the private machine credential', async (t) => {
  const f = await fixture(t, undefined, true);
  const scope = parseReaderStartupConsentScope(f.scope);
  const signal = new AbortController().signal;
  f.scope.manifestDigest = '9'.repeat(64);
  assert.equal(scope.manifestDigest, '1'.repeat(64));
  f.scope.manifestDigest = scope.manifestDigest;
  assert.ok(Object.isFrozen(scope));
  assert.throws(() => parseReaderStartupConsentScope({ ...scope, effectsDigest: '0'.repeat(64) }));
  assert.throws(() => parseReaderStartupConsentScope({ ...scope, allowTcc: true }));
  await f.session.checkReaderStartupConsent(scope, signal);
  await f.session.checkReaderStartupConsent(scope, signal);
  assert.equal(f.calls(), 2);
  const cancelled = new AbortController(); cancelled.abort();
  await assert.rejects(f.session.checkReaderStartupConsent(scope, cancelled.signal));
  assert.equal(f.calls(), 2);
});

for (const fault of ['grant', 'desktop', 'effects', 'reference', 'expired', 'extended', 'revoked']) {
  test(`startup consent (${fault}) refuses and fences input without a cached-consent fallback`, async (t) => {
    const f = await fixture(t, fault, true);
    const signal = new AbortController().signal;
    await assert.rejects(f.session.checkReaderStartupConsent(f.scope, signal));
    await assert.rejects(f.session.checkReaderStartupConsent(f.scope, signal));
    await assert.rejects(f.session.retainIntent({ action: 'NEXT', sequence: 1, origin: 'http://127.0.0.1' }));
    assert.equal(f.calls(), 1);
  });
}
