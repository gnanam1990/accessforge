/** Private one-run handoff listener. Receipt is delivery, never reader completion. */
import { chmodSync, lstatSync, mkdtempSync, realpathSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer, type Socket } from 'node:net';
import { assertRealReaderProven } from '@accessforge/at-voiceover';
import { parseDispatchEnvelope, parseReference } from './dispatch-receiver.js';
import { runProvisionedNavigatorExecution } from './execution-bootstrap.js';

const protocol = 'accessforge.native-start.v1';
type Bootstrap = Omit<Parameters<typeof runProvisionedNavigatorExecution>[0], 'dispatchEnvelope'>;
type Navigator = Parameters<typeof runProvisionedNavigatorExecution>[1];

/** Host-only provisioning: callbacks/credentials come from the operator, never the socket.
 * The first-profile candidate proof remains separate. No unavailable profile is advertised.
 * The returned completion promise must be observed; close requests cancellation, not STOP proof.
 */
export async function startNativeDispatchListener(
  privateDirectory: string, bootstrap: Bootstrap, navigator: Navigator,
): Promise<{
  privateReference: Readonly<Record<string, unknown>>;
  completion: Promise<Readonly<Record<string, unknown>>>;
  close(): void;
}> {
  const reference = parseReference(bootstrap.receiver.localReference);
  const deadline = Math.min(bootstrap.lease.deadlineMonotonic, navigator.deadlineMonotonic);
  if (JSON.stringify(reference) !== JSON.stringify(parseReference(navigator.reference)) ||
      !Number.isFinite(deadline) || deadline <= performance.now() ||
      deadline - performance.now() > 1800000 || navigator.signal.aborted ||
      navigator.allowBillableModelCalls !== true) throw new Error('native handoff authority unavailable');
  assertRealReaderProven();
  if (!isAbsolute(privateDirectory) || realpathSync(privateDirectory) !== resolve(privateDirectory)) {
    throw new Error('private native handoff directory required');
  }
  const owner = lstatSync(privateDirectory);
  if (!owner.isDirectory() || typeof process.getuid !== 'function' || owner.uid !== process.getuid() ||
      (owner.mode & 0o077) !== 0) throw new Error('private native handoff directory required');
  const directory = mkdtempSync(join(privateDirectory, 'start-'));
  const path = join(directory, 's.sock');
  if (Buffer.byteLength(path) > 100) throw new Error('native handoff socket path too long');
  const token = randomBytes(32);
  const controller = new AbortController();
  let consumed = false, closed = false;
  let resolveCompletion!: (value: Readonly<Record<string, unknown>>) => void;
  let rejectCompletion!: (reason: Error) => void;
  const completion = new Promise<Readonly<Record<string, unknown>>>((yes, no) => {
    resolveCompletion = yes; rejectCompletion = no;
  });
  void completion.catch(() => {}); // Preserve rejection for the owner, without an unhandled gap.
  const sockets = new Set<Socket>();
  const server = createServer(socket => {
    if (closed || consumed || sockets.size >= 2) { socket.destroy(); return; }
    sockets.add(socket);
    socket.on('error', () => {});
    const timer = setTimeout(() => socket.destroy(), 3000);
    socket.once('close', () => { clearTimeout(timer); sockets.delete(socket); });
    let data = Buffer.alloc(0), handled = false;
    socket.on('data', (chunk: Buffer) => {
      if (handled) { socket.destroy(); return; }
      data = Buffer.concat([data, chunk]);
      if (data.length > 4096) { socket.destroy(); return; }
      if (!data.includes(10)) return;
      handled = true;
      try {
        if (closed || consumed || controller.signal.aborted || performance.now() >= deadline ||
            data.at(-1) !== 10 || data.subarray(0, -1).includes(10)) throw new Error('framing');
        const packet: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(data));
        if (packet === null || typeof packet !== 'object' || Array.isArray(packet)) throw new Error('shape');
        const row = packet as Record<string, unknown>;
        if (Object.keys(row).length !== 3 || row.protocol !== protocol || typeof row.token !== 'string' ||
            !/^[a-f0-9]{64}$/.test(row.token) || !timingSafeEqual(Buffer.from(row.token, 'hex'), token)) throw new Error('authority');
        const envelope = parseDispatchEnvelope(row.envelope);
        if (JSON.stringify(envelope.reference) !== JSON.stringify(reference) ||
            !Number.isFinite(Date.parse(envelope.ticket.expiresAt)) ||
            Date.parse(envelope.ticket.expiresAt) <= Date.now()) throw new Error('identity');
        consumed = true; // No second handoff even if receipt or execution response is lost.
        socket.end(JSON.stringify({protocol, reference, status: 'HANDOFF_ACCEPTED'}) + '\n');
        void runProvisionedNavigatorExecution({...bootstrap, dispatchEnvelope: envelope},
          {...navigator, signal: controller.signal}).then(resolveCompletion, () => {
          rejectCompletion(new Error('native execution unconfirmed; reconcile original attempt'));
        }).finally(close);
      } catch { socket.destroy(); }
    });
  });
  const onAbort = () => close();
  const expiry = setTimeout(() => close(), Math.max(1, deadline - performance.now()));
  function close(): void {
    if (closed) return;
    closed = true; controller.abort(); clearTimeout(expiry);
    navigator.signal.removeEventListener('abort', onAbort);
    server.close();
    for (const socket of sockets) socket.destroy();
    if (!consumed) rejectCompletion(new Error('native handoff closed before delivery'));
    // Keep the private directory for reconciliation; no recursive cleanup or claim deletion.
  }
  navigator.signal.addEventListener('abort', onAbort, {once: true});
  try {
    await new Promise<void>((yes, no) => { server.once('error', no); server.listen(path, yes); });
    chmodSync(path, 0o600);
    if (navigator.signal.aborted || closed) throw new Error('cancelled');
    server.on('error', close);
  } catch {
    close(); throw new Error('native listener unavailable; no handoff advertised');
  }
  return {privateReference: Object.freeze({protocol, socketPath: path, token: token.toString('hex'), reference}),
    completion, close};
}
