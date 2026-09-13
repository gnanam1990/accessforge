import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { PREFLIGHT_CHECKS } from '@accessforge/at-voiceover';
import { NativeExecutionSession } from '../dist/dispatch-receiver.js';

// Actual HTTP, synthetic authority and transport state. No reader or candidate is invoked.
for (const terminal of ['RESPONSE_RETAINED', 'CLOSED_UNUSED', 'FOREIGN']) {
  test(`form window waits read-only before action result: ${terminal}`, async (t) => {
    const root = realpathSync(mkdtempSync(join(tmpdir(), 'af-effect-')));
    t.after(() => rmSync(root, { recursive: true, force: true }));
    const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
      runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
    const ticket = { ticketId: randomUUID(), token: 'T'.repeat(43), expiresAt: new Date(Date.now() + 30000).toISOString() };
    const actionId = randomUUID(), permitId = randomUUID();
    let secret, polls = 0, grants = 0, results = 0;
    const server = createServer(async (req, res) => {
      let raw = '';
      for await (const part of req) raw += part.toString();
      const send = (status, body) => res.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(body));
      if (req.url.endsWith('/session')) {
        secret = JSON.parse(raw).sessionSecret;
        send(201, { ...reference, sessionId: ticket.ticketId, expiresAt: ticket.expiresAt, meaning: 'SUPERVISOR_SESSION_OPENED' }); return;
      }
      assert.equal(req.headers.authorization, `Bearer ${secret}`);
      const base = { sessionId: ticket.ticketId, actionId };
      if (req.url.endsWith('/action-intents')) send(201, { ...base, sequence: 1, meaning: 'ACTION_INTENT_RETAINED' });
      else if (req.url.endsWith('/dispatch')) send(200, { sessionId: ticket.ticketId, command: { actionId, sequence: 1, action: 'ACTIVATE' }, meaning: 'ACTION_DISPATCH_COMMITTED' });
      else if (req.url.endsWith('/preflight')) send(200, { ...base, eventId: randomUUID(), sourceRecordDigest: JSON.parse(raw).sourceRecordDigest, meaning: 'RUNTIME_PREFLIGHT_RETAINED_NOT_IDENTITY_ATTESTATION' });
      else if (req.url.endsWith('/form-effect-permit') && req.method === 'POST') {
        grants++;
        send(200, { ...base, permitId, expiresAt: new Date(Date.now() + 5000).toISOString(), meaning: 'ACTION_FORM_PERMISSION_NOT_EFFECT_PROOF' });
      } else if (req.url.endsWith('/form-effect-permit') && req.method === 'GET') {
        assert.equal(raw, '');
        polls++;
        send(200, { ...base, permitId: terminal === 'FOREIGN' ? randomUUID() : permitId,
          phase: polls === 1 ? 'OPEN' : terminal, meaning: 'FORM_TRANSPORT_STATE_NOT_EFFECT_PROOF' });
      } else if (req.url.endsWith('/result')) {
        results++;
        assert.equal(polls, 2);
        send(200, { ...base, status: JSON.parse(raw).status, meaning: 'ACTION_RESULT_RETAINED' });
      } else send(404, {});
    });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    t.after(() => { server.closeAllConnections(); server.close(); });
    const session = await NativeExecutionSession.open({ apiOrigin: `http://127.0.0.1:${server.address().port}`,
      claimsDirectory: root, localReference: reference, allowLoopbackHttp: true }, { reference, ticket });
    await session.retainIntent({ action: 'ACTIVATE', sequence: 1, origin: 'http://127.0.0.1' });
    const command = await session.commitDispatch(actionId, 'http://127.0.0.1');
    await session.retainRuntimePreflight(command, { checks: Object.fromEntries(PREFLIGHT_CHECKS.map((key) => [key, { condition: 'TRUE' }])) }, new Date().toISOString());
    await session.authorizeCandidateFormEffect(command);
    if (terminal === 'FOREIGN') {
      await assert.rejects(session.completeAction(actionId, 'SUCCEEDED'));
      assert.equal(results, 0);
      await assert.rejects(session.retainIntent({ action: 'NEXT', sequence: 2, origin: 'http://127.0.0.1' }));
    } else {
      await session.completeAction(actionId, 'SUCCEEDED');
      assert.equal(results, 1);
    }
    assert.equal(grants, 1);
  });
}
