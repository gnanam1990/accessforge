import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync,
  symlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { readReceiverConfig, receiveDispatch, ReceiverRefused, ReceptionUnknown } from '../dist/dispatch-receiver.js';

const command = fileURLToPath(new URL('../dist/receive-dispatch.js', import.meta.url));

async function fixture(t, handler) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'accessforge-receiver-')));
  const claims = join(root, 'claims');
  mkdirSync(claims, { mode: 0o700 });
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 7 };
  const ticket = { ticketId: randomUUID(), token: 't'.repeat(43), expiresAt: new Date(Date.now() + 30000).toISOString() };
  const envelope = { reference, ticket };
  const receipt = { ...reference, ticketId: ticket.ticketId, meaning: 'DISPATCH_REFERENCE_ACCEPTED' };
  const state = { root, claims, reference, ticket, envelope, receipt, calls: 0, claim: join(claims, `${reference.runId}.json`) };
  const server = createServer((req, res) => {
    state.calls++;
    assert.ok(existsSync(state.claim), 'durable local intent must precede the HTTP request');
    assert.equal(readFileSync(state.claim, 'utf8').includes(ticket.token), false);
    assert.equal(req.url, `/v1/workspaces/${reference.workspaceId}/supervisor-dispatches/${ticket.ticketId}/accept`);
    assert.equal(req.method, 'POST');
    assert.equal(req.headers.authorization, `Bearer ${ticket.token}`);
    if (handler) handler(req, res, state);
    else res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(receipt));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  state.config = { apiOrigin: `http://127.0.0.1:${server.address().port}`, claimsDirectory: claims,
    localReference: reference, allowLoopbackHttp: true, timeoutMs: 2000 };
  state.configPath = join(root, 'receiver.json');
  writeFileSync(state.configPath, JSON.stringify(state.config), { mode: 0o600 });
  return state;
}

function child(f, input = JSON.stringify(f.envelope)) {
  const process = spawn(globalThis.process.execPath, [command, f.configPath], { stdio: ['pipe', 'pipe', 'pipe'] });
  const result = new Promise((resolve, reject) => {
    let stdout = '', stderr = '';
    process.stdout.on('data', (data) => { stdout += data; });
    process.stderr.on('data', (data) => { stderr += data; });
    process.on('error', reject);
    process.on('close', (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
  process.stdin.on('error', () => {}); // A refusing child may close before consuming input.
  process.stdin.end(input);
  return { process, result };
}

test('one exact bootstrap receipt, no token persisted, and concurrent/case/ticket replay fenced', async (t) => {
  const f = await fixture(t);
  const results = await Promise.allSettled([receiveDispatch(f.config, f.envelope), receiveDispatch(f.config, f.envelope)]);
  assert.deepEqual(results.map((r) => r.status).sort(), ['fulfilled', 'rejected']);
  assert.deepEqual(results.find((r) => r.status === 'fulfilled').value,
    { reference: f.reference, ticketId: f.ticket.ticketId, meaning: 'DISPATCH_REFERENCE_ACCEPTED' });
  const alternate = { reference: Object.fromEntries(Object.entries(f.reference).map(([key, value]) =>
    [key, typeof value === 'string' ? value.toUpperCase() : value])), ticket: { ...f.ticket, ticketId: randomUUID() } };
  await assert.rejects(receiveDispatch(f.config, alternate), ReceiverRefused);
  const restarted = await child(f).result;
  assert.equal(restarted.code, 78);
  assert.equal(f.calls, 1);
  assert.equal(JSON.stringify(restarted).includes(f.ticket.token), false);
  assert.equal(JSON.parse(readFileSync(f.claim, 'utf8')).phase, 'RECEPTION_INTENT');
});

for (const field of ['workspaceId', 'runId', 'attemptId', 'runnerId', 'leaseId', 'epoch']) {
  test(`wrong local ${field} refuses before claiming or network`, async (t) => {
    const f = await fixture(t);
    const wrong = { ...f.config, localReference: { ...f.reference, [field]: field === 'epoch' ? 8 : randomUUID() } };
    await assert.rejects(receiveDispatch(wrong, f.envelope), ReceiverRefused);
    assert.equal(f.calls, 0);
    assert.equal(existsSync(f.claim), false);
  });
}

for (const fault of ['expired', 'token', 'ticket-field', 'reference-field', 'config-field', 'epoch',
  'http', 'remote-http', 'origin-path', 'origin-secret', 'origin-invalid', 'timeout']) {
  test(`invalid ${fault} refuses before network`, async (t) => {
    const f = await fixture(t);
    const config = structuredClone(f.config), envelope = structuredClone(f.envelope);
    if (fault === 'expired') envelope.ticket.expiresAt = '2000-01-01T00:00:00Z';
    if (fault === 'token') envelope.ticket.token += 'x';
    if (fault === 'ticket-field') envelope.ticket.command = 'start';
    if (fault === 'reference-field') envelope.reference.command = 'start';
    if (fault === 'config-field') config.command = 'start';
    if (fault === 'epoch') envelope.reference.epoch = Number.MAX_SAFE_INTEGER + 1;
    if (fault === 'http') config.allowLoopbackHttp = false;
    if (fault === 'remote-http') config.apiOrigin = 'http://example.test';
    if (fault === 'origin-path') config.apiOrigin += '/other';
    if (fault === 'origin-secret') config.apiOrigin = 'https://user:password@example.test';
    if (fault === 'origin-invalid') config.apiOrigin = 'not-an-origin';
    if (fault === 'timeout') config.timeoutMs = Infinity;
    await assert.rejects(receiveDispatch(config, envelope), ReceiverRefused);
    assert.equal(f.calls, 0);
    assert.equal(existsSync(f.claim), false);
  });
}

for (const fault of ['workspaceId', 'runId', 'attemptId', 'runnerId', 'leaseId', 'epoch', 'ticketId',
  'meaning', 'old-shape', 'extra', 'malformed', 'oversize', 'redirect', '401', 'drop', 'timeout']) {
  test(`response ${fault} is UNKNOWN with permanent replay fence`, async (t) => {
    const f = await fixture(t, (req, res, state) => {
      const result = { ...state.receipt };
      if (['workspaceId', 'runId', 'attemptId', 'runnerId', 'leaseId', 'ticketId'].includes(fault)) result[fault] = randomUUID();
      if (fault === 'epoch') result.epoch++;
      if (fault === 'meaning') result.meaning = 'PASS';
      if (fault === 'old-shape') delete result.attemptId;
      if (fault === 'extra') result.token = state.ticket.token;
      if (fault === 'redirect') { res.writeHead(307, { Location: '/redirected' }).end(); return; }
      if (fault === '401') { res.writeHead(401).end(state.ticket.token); return; }
      if (fault === 'drop') { req.socket.destroy(); return; }
      if (fault === 'timeout') return;
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(fault === 'malformed' ? state.ticket.token :
        fault === 'oversize' ? ' '.repeat(4097) : JSON.stringify(result));
    });
    f.config.timeoutMs = 100;
    await assert.rejects(receiveDispatch(f.config, f.envelope), (error) =>
      error instanceof ReceptionUnknown && !error.message.includes(f.ticket.token));
    await assert.rejects(receiveDispatch(f.config, f.envelope), ReceiverRefused);
    assert.equal(f.calls, 1);
    assert.ok(existsSync(f.claim));
  });
}

for (const fault of ['config-mode', 'config-symlink', 'config-size', 'claims-mode', 'claims-symlink', 'partial-claim']) {
  test(`unsafe storage ${fault} fails closed without touching the API`, async (t) => {
    const f = await fixture(t);
    if (fault === 'config-mode') chmodSync(f.configPath, 0o644);
    if (fault === 'config-symlink') { const alias = join(f.root, 'alias'); symlinkSync(f.configPath, alias); f.configPath = alias; }
    if (fault === 'config-size') writeFileSync(f.configPath, ' '.repeat(8193));
    if (fault === 'claims-mode') chmodSync(f.claims, 0o755);
    if (fault === 'claims-symlink') { const alias = join(f.root, 'alias'); symlinkSync(f.claims, alias); f.config.claimsDirectory = alias; }
    if (fault === 'partial-claim') writeFileSync(f.claim, '{', { mode: 0o600 });
    if (fault.startsWith('config-')) assert.throws(() => readReceiverConfig(f.configPath), ReceiverRefused);
    else await assert.rejects(receiveDispatch(f.config, f.envelope), ReceiverRefused);
    assert.equal(f.calls, 0);
    if (fault === 'partial-claim') assert.equal(readFileSync(f.claim, 'utf8'), '{');
  });
}

test('real CLI receives its secret only on stdin and emits only exact bootstrap receipt', async (t) => {
  const f = await fixture(t);
  const result = await child(f).result;
  assert.equal(result.code, 0);
  assert.equal(result.stderr, '');
  assert.deepEqual(JSON.parse(result.stdout), { ticketId: f.ticket.ticketId, reference: f.reference, meaning: 'DISPATCH_REFERENCE_ACCEPTED' });
  assert.equal(JSON.stringify(result).includes(f.ticket.token), false);
  assert.equal(f.calls, 1);
});

test('process killed after request cannot replay after restart', async (t) => {
  let sent;
  const requested = new Promise((resolve) => { sent = resolve; });
  const f = await fixture(t, () => sent());
  const first = child(f);
  await requested;
  first.process.kill('SIGKILL');
  assert.equal((await first.result).signal, 'SIGKILL');
  const second = await child(f).result;
  assert.equal(second.code, 78);
  assert.equal(f.calls, 1);
  assert.ok(existsSync(f.claim));
});

test('malformed and oversized stdin never log secret payloads or send requests', async (t) => {
  const f = await fixture(t);
  for (const input of [f.ticket.token, f.ticket.token.repeat(100)]) {
    const result = await child(f, input).result;
    assert.equal(result.code, 78);
    assert.equal(result.stdout, '');
    assert.equal(result.stderr.includes(f.ticket.token), false);
  }
  assert.equal(f.calls, 0);
});

test('a FIFO config is refused without blocking on a writer', async (t) => {
  const f = await fixture(t);
  const fifo = join(f.root, 'config-fifo');
  execFileSync('mkfifo', ['-m', '600', fifo]);
  assert.throws(() => readReceiverConfig(fifo), ReceiverRefused);
  assert.equal(f.calls, 0);
});

test('durable-storage latency consumes the reception budget before the first HTTP request', async (t) => {
  const f = await fixture(t);
  let clockCalls = 0;
  t.mock.method(performance, 'now', () => clockCalls++ === 0 ? 0 : 10001);
  await assert.rejects(receiveDispatch(f.config, f.envelope), ReceptionUnknown);
  assert.equal(f.calls, 0);
  assert.ok(existsSync(f.claim));
});
