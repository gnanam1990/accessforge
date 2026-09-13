import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { NativeExecutionSession } from '../dist/dispatch-receiver.js';

// Real local HTTP with synthetic credentials/authority responses, no reader or live control plane.
async function fixture(t, fault) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'accessforge-startup-authority-')));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  const ticket = { ticketId: randomUUID(), token: 'T'.repeat(43), expiresAt: new Date(Date.now() + 30000).toISOString() };
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
    assert.equal(req.url, `/v1/workspaces/${reference.workspaceId}/supervisor-sessions/${ticket.ticketId}/startup-authority`);
    assert.equal(req.headers.authorization, `Bearer ${sessionSecret}`);
    assert.notEqual(sessionSecret, ticket.token);
    assert.deepEqual(JSON.parse(raw), {});
    const body = { sessionId: ticket.ticketId, reference: { ...reference }, expiresAt: ticket.expiresAt,
      meaning: 'EXECUTION_AUTHORITY_RECHECKED_NOT_READER_START_CONSENT' };
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
  return { session, calls: () => calls };
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
