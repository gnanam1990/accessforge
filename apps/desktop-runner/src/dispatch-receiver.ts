/** Native, one-shot control-plane reception. This module has NO reader/OS-action capability. */
import {
  closeSync, constants, fsyncSync, fstatSync, openSync, readSync, realpathSync,
  statSync, writeFileSync,
} from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';

export interface DispatchReference {
  readonly workspaceId: string;
  readonly runId: string;
  readonly attemptId: string;
  readonly runnerId: string;
  readonly leaseId: string;
  readonly epoch: number;
}

export interface ReceiverConfig {
  readonly apiOrigin: string;
  readonly claimsDirectory: string;
  readonly localReference: DispatchReference;
  readonly allowLoopbackHttp?: boolean;
  readonly timeoutMs?: number;
}

export class ReceiverRefused extends Error {}
export class ReceptionUnknown extends Error {}

const referenceKeys = ['workspaceId', 'runId', 'attemptId', 'runnerId', 'leaseId', 'epoch'];

function exactObject(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new ReceiverRefused('invalid dispatch shape');
  }
  const object = value as Record<string, unknown>;
  if (Object.keys(object).length !== keys.length || keys.some((key) => !Object.hasOwn(object, key))) {
    throw new ReceiverRefused('invalid dispatch fields');
  }
  return object;
}

function uuid(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)) {
    throw new ReceiverRefused('invalid dispatch identity');
  }
  return value.toLowerCase();
}

export function parseReference(value: unknown): DispatchReference {
  const row = exactObject(value, referenceKeys);
  if (typeof row.epoch !== 'number' || !Number.isSafeInteger(row.epoch) || row.epoch < 1) {
    throw new ReceiverRefused('invalid dispatch epoch');
  }
  return {
    workspaceId: uuid(row.workspaceId), runId: uuid(row.runId), attemptId: uuid(row.attemptId),
    runnerId: uuid(row.runnerId), leaseId: uuid(row.leaseId), epoch: row.epoch,
  };
}

function privateOwner(mode: number, uid: number): void {
  // Windows needs an ACL/handle implementation, not an assumption that POSIX mode bits work there.
  if (typeof process.getuid !== 'function' || uid !== process.getuid() || (mode & 0o077) !== 0) {
    throw new ReceiverRefused('receiver storage must be private and owned by this process user');
  }
}

export function readReceiverConfig(path: string): ReceiverConfig {
  let fd: number | undefined;
  try {
    if (!isAbsolute(path) || realpathSync(path) !== resolve(path)) throw new Error('path');
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const info = fstatSync(fd);
    privateOwner(info.mode, info.uid);
    if (!info.isFile() || info.size > 8192) throw new Error('size');
    const bytes = Buffer.alloc(8193);
    let size = 0;
    while (size < bytes.length) {
      const n = readSync(fd, bytes, size, bytes.length - size, null);
      if (n === 0) break;
      size += n;
    }
    if (size > 8192) throw new Error('size');
    return parseConfig(JSON.parse(bytes.subarray(0, size).toString('utf8')));
  } catch {
    throw new ReceiverRefused('private receiver configuration unavailable');
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

function parseConfig(value: unknown): ReceiverConfig {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new ReceiverRefused('invalid receiver configuration');
  }
  const row = value as Record<string, unknown>;
  const keys = ['apiOrigin', 'claimsDirectory', 'localReference', 'allowLoopbackHttp', 'timeoutMs'];
  if (Object.keys(row).some((key) => !keys.includes(key)) ||
      typeof row.apiOrigin !== 'string' || typeof row.claimsDirectory !== 'string' ||
      (row.allowLoopbackHttp !== undefined && typeof row.allowLoopbackHttp !== 'boolean') ||
      (row.timeoutMs !== undefined && typeof row.timeoutMs !== 'number')) {
    throw new ReceiverRefused('invalid receiver configuration');
  }
  return { apiOrigin: row.apiOrigin, claimsDirectory: row.claimsDirectory,
    localReference: parseReference(row.localReference),
    ...(row.allowLoopbackHttp === undefined ? {} : { allowLoopbackHttp: row.allowLoopbackHttp }),
    ...(row.timeoutMs === undefined ? {} : { timeoutMs: row.timeoutMs }) };
}

function reserve(directory: string, reference: DispatchReference, ticketId: string): void {
  let fd: number | undefined;
  let parent: number | undefined;
  try {
    if (!isAbsolute(directory) || realpathSync(directory) !== resolve(directory)) throw new Error('path');
    const info = statSync(directory);
    privateOwner(info.mode, info.uid);
    if (!info.isDirectory()) throw new Error('directory');
    parent = openSync(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    const opened = fstatSync(parent);
    privateOwner(opened.mode, opened.uid);
    if (opened.dev !== info.dev || opened.ino !== info.ino) throw new Error('directory changed');
    // The operator-owned storage tree must not be concurrently replaced or restored. This is not
    // a sandbox against a malicious process running as the same OS user (or root).
    // One run can be received only once, even if a different ticket/UUID casing is presented later.
    fd = openSync(join(directory, `${reference.runId}.json`),
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    writeFileSync(fd, JSON.stringify({ phase: 'RECEPTION_INTENT', ticketId, reference }) + '\n');
    fsyncSync(fd);
    fsyncSync(parent);
  } catch {
    // Never unlink an uncertain/partial claim. Its existence prevents a restart from sending again.
    throw new ReceiverRefused('local dispatch claim unavailable or already exists; do not replay');
  } finally {
    if (fd !== undefined) closeSync(fd);
    if (parent !== undefined) closeSync(parent);
  }
}

export async function receiveDispatch(config: ReceiverConfig, input: unknown): Promise<object> {
  config = parseConfig(config);
  const envelope = exactObject(input, ['reference', 'ticket']);
  const reference = parseReference(envelope.reference);
  const local = parseReference(config.localReference);
  if (JSON.stringify(reference) !== JSON.stringify(local)) {
    throw new ReceiverRefused('dispatch does not match this receiver lease');
  }
  const ticket = exactObject(envelope.ticket, ['ticketId', 'token', 'expiresAt']);
  const ticketId = uuid(ticket.ticketId);
  if (typeof ticket.token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(ticket.token) ||
      typeof ticket.expiresAt !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(ticket.expiresAt)) {
    throw new ReceiverRefused('invalid dispatch ticket');
  }
  const timeout = config.timeoutMs ?? 5000;
  const remaining = Date.parse(ticket.expiresAt) - Date.now();
  const budgetStarted = performance.now();
  if (!Number.isFinite(timeout) || timeout <= 0 || timeout > 10000 ||
      !Number.isFinite(remaining) || remaining <= 0) {
    throw new ReceiverRefused('expired ticket or invalid receiver deadline');
  }
  let origin: URL;
  try { origin = new URL(config.apiOrigin); }
  catch { throw new ReceiverRefused('invalid receiver origin'); }
  const loopback = ['127.0.0.1', '[::1]'].includes(origin.hostname);
  if (origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/' ||
      (origin.protocol !== 'https:' && !(config.allowLoopbackHttp === true && loopback &&
        origin.protocol === 'http:'))) {
    throw new ReceiverRefused('receiver requires an explicit HTTPS origin or approved loopback HTTP');
  }
  const url = new URL(`/v1/workspaces/${reference.workspaceId}/supervisor-dispatches/${ticketId}/accept`, origin);
  reserve(config.claimsDirectory, reference, ticketId);
  // Durable storage latency consumes the same budget; it must not extend ticket reception.
  const budget = Math.min(timeout, remaining) - (performance.now() - budgetStarted);
  if (budget <= 0) {
    throw new ReceptionUnknown('dispatch reception deadline elapsed; retain claim and reconcile');
  }
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), Math.max(1, Math.floor(budget)));
  try {
    const response = await fetch(url, {
      method: 'POST', headers: { Authorization: `Bearer ${ticket.token}`, 'Content-Type': 'application/json',
        'Cache-Control': 'no-store' },
      body: '{}', redirect: 'error', credentials: 'omit', signal: abort.signal,
    });
    if (response.status !== 200 || response.body === null ||
        !/^application\/json(?:;|$)/i.test(response.headers.get('content-type') ?? '')) throw new Error('response');
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > 4096) throw new Error('response bound');
      chunks.push(chunk.value);
    }
    const result = exactObject(JSON.parse(Buffer.concat(chunks).toString('utf8')),
      [...referenceKeys, 'ticketId', 'meaning']);
    const received = parseReference(Object.fromEntries(referenceKeys.map((key) => [key, result[key]])));
    if (uuid(result.ticketId) !== ticketId || result.meaning !== 'DISPATCH_REFERENCE_ACCEPTED' ||
        JSON.stringify(received) !== JSON.stringify(reference) ||
        performance.now() - budgetStarted >= Math.min(timeout, remaining)) throw new Error('identity or deadline');
    return { ticketId, reference: received, meaning: 'DISPATCH_REFERENCE_ACCEPTED' };
  } catch {
    throw new ReceptionUnknown('dispatch reception is unknown; retain claim and reconcile, never retry');
  } finally {
    clearTimeout(timer);
    abort.abort();
  }
}
