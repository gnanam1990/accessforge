/** Native, one-shot control-plane reception. This module has NO reader/OS-action capability. */
import {
  closeSync, constants, fsyncSync, fstatSync, openSync, readSync, realpathSync,
  statSync, writeFileSync,
} from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { randomBytes } from 'node:crypto';
import { digest } from '@accessforge/contracts';
import { PREFLIGHT_CHECKS, type PreflightReport, type RawObservation, type UnknownObservation } from '@accessforge/at-voiceover';
import type { ActionCommand } from './supervisor.js';
import { parseReaderStartupConsentScope, type ReaderStartupConsentScope } from './reader-startup-consent.js';

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
  return receive(config, input);
}

async function boundedJson(response: Awaited<ReturnType<typeof fetch>>): Promise<unknown> {
  if (response.body === null ||
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
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

/** Validate and snapshot only the bounded wire fields; never clone arbitrary controller objects. */
export function parseDispatchEnvelope(input: unknown): Readonly<{
  reference: DispatchReference;
  ticket: Readonly<{ ticketId: string; token: string; expiresAt: string }>;
}> {
  const envelope = exactObject(input, ['reference', 'ticket']);
  const reference = Object.freeze(parseReference(envelope.reference));
  const ticket = exactObject(envelope.ticket, ['ticketId', 'token', 'expiresAt']);
  const ticketId = uuid(ticket.ticketId);
  if (typeof ticket.token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(ticket.token) ||
      typeof ticket.expiresAt !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(ticket.expiresAt)) {
    throw new ReceiverRefused('invalid dispatch ticket');
  }
  return Object.freeze({ reference, ticket: Object.freeze({ ticketId, token: ticket.token, expiresAt: ticket.expiresAt }) });
}

async function receive(config: ReceiverConfig, input: unknown, sessionSecret?: string): Promise<Record<string, unknown>> {
  config = parseConfig(config);
  const { reference, ticket } = parseDispatchEnvelope(input);
  const local = parseReference(config.localReference);
  if (JSON.stringify(reference) !== JSON.stringify(local)) {
    throw new ReceiverRefused('dispatch does not match this receiver lease');
  }
  const ticketId = ticket.ticketId;
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
  const mode = sessionSecret === undefined ? 'accept' : 'session';
  const url = new URL(`/v1/workspaces/${reference.workspaceId}/supervisor-dispatches/${ticketId}/${mode}`, origin);
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
      body: sessionSecret === undefined ? '{}' : JSON.stringify({ sessionSecret }),
      redirect: 'error', credentials: 'omit', signal: abort.signal,
    });
    if (response.status !== (sessionSecret === undefined ? 200 : 201)) throw new Error('response');
    const result = exactObject(await boundedJson(response),
      [...referenceKeys, ...(sessionSecret === undefined ? ['ticketId', 'meaning'] : ['sessionId', 'expiresAt', 'meaning'])]);
    const received = parseReference(Object.fromEntries(referenceKeys.map((key) => [key, result[key]])));
    const meaning = sessionSecret === undefined ? 'DISPATCH_REFERENCE_ACCEPTED' : 'SUPERVISOR_SESSION_OPENED';
    if (uuid(result[sessionSecret === undefined ? 'ticketId' : 'sessionId']) !== ticketId || result.meaning !== meaning ||
        JSON.stringify(received) !== JSON.stringify(reference) ||
        performance.now() - budgetStarted >= Math.min(timeout, remaining)) throw new Error('identity or deadline');
    if (sessionSecret !== undefined) {
      if (typeof result.expiresAt !== 'string' || !Number.isFinite(Date.parse(result.expiresAt)) ||
          Date.parse(result.expiresAt) <= Date.now()) throw new Error('session deadline');
      return { sessionId: ticketId, reference: received, expiresAt: result.expiresAt, meaning };
    }
    return { ticketId, reference: received, meaning: 'DISPATCH_REFERENCE_ACCEPTED' };
  } catch {
    throw new ReceptionUnknown('dispatch reception is unknown; retain claim and reconcile, never retry');
  } finally {
    clearTimeout(timer);
    abort.abort();
  }
}

/** In-memory machine credential. No secret in JSON/inspection output and no restart/resume path.
 * This client carries action commitments/results. It imports no reader and grants no physical OS capability.
 */
export class NativeExecutionSession {
  #secret: string;
  #config: ReceiverConfig;
  #sessionId: string;
  #deadline: number;
  #intentPending = false;
  #current: { id: string; sequence: number; command: Record<string, unknown> } | undefined;
  #committed = false;
  #busy = false;
  #fenced = false;
  #observationSequence = 0;
  #observationPending = false;
  #runtimePreflightPending = false;
  #stopActionId: string | undefined;
  #finishStarted = false;
  readonly receipt: Readonly<Record<string, unknown>>;

  private constructor(config: ReceiverConfig, secret: string, receipt: Record<string, unknown>) {
    this.#secret = secret;
    this.#config = config;
    this.#sessionId = uuid(receipt.sessionId);
    this.#deadline = performance.now() + Math.min(1800000, Date.parse(String(receipt.expiresAt)) - Date.now());
    this.receipt = Object.freeze(receipt);
  }

  static async open(config: ReceiverConfig, envelope: unknown): Promise<NativeExecutionSession> {
    const local = parseConfig(config);
    const secret = randomBytes(32).toString('base64url');
    const receipt = await receive(local, envelope, secret);
    return new NativeExecutionSession(local, secret, receipt);
  }

  /** Fresh machine authority, not permission to start/change the reader or a readiness proof. */
  async checkStartupAuthority(signal: AbortSignal): Promise<void> {
    if (this.#fenced || this.#busy || this.#intentPending || this.#finishStarted ||
        this.#current !== undefined || signal.aborted) throw new ReceiverRefused('startup authority unavailable');
    this.#busy = true;
    const started = performance.now();
    const wall = Date.now();
    try {
      const result = exactObject(await this.#post('startup-authority', {}, signal),
        ['sessionId', 'reference', 'expiresAt', 'meaning']);
      const reference = parseReference(result.reference);
      if (uuid(result.sessionId) !== this.#sessionId ||
          JSON.stringify(reference) !== JSON.stringify(this.#config.localReference) ||
          result.meaning !== 'EXECUTION_AUTHORITY_RECHECKED_NOT_READER_START_CONSENT' ||
          typeof result.expiresAt !== 'string' ||
          !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(result.expiresAt) ||
          !Number.isFinite(Date.parse(result.expiresAt)) ||
          Date.parse(result.expiresAt) > Date.parse(String(this.receipt.expiresAt))) throw new Error('startup authority identity');
      const remaining = Date.parse(result.expiresAt) - wall;
      this.#deadline = Math.min(this.#deadline, started + remaining);
      if (!Number.isFinite(remaining) || remaining <= 0 || performance.now() >= this.#deadline || signal.aborted) {
        throw new Error('startup authority expired');
      }
    } catch {
      this.#fenced = true;
      throw new ReceptionUnknown('startup authority unavailable; retain desktop claim, never retry initialization');
    } finally { this.#busy = false; }
  }

  /** Fresh separate operator consent plus live execution authority, before any reader startup. */
  async checkReaderStartupConsent(scope: ReaderStartupConsentScope, signal: AbortSignal): Promise<void> {
    if (this.#fenced || this.#busy || this.#intentPending || this.#finishStarted ||
        this.#current !== undefined || signal.aborted) throw new ReceiverRefused('startup consent unavailable');
    this.#busy = true;
    const started = performance.now(), wall = Date.now();
    try {
      const expected = parseReaderStartupConsentScope(scope);
      const result = exactObject(await this.#post('reader-startup-consent', {}, signal),
        ['sessionId', 'reference', 'consentId', 'manifestDigest', 'desktopSessionKey',
          'runnerProfileDigest', 'effectsDigest', 'expiresAt', 'meaning']);
      if (uuid(result.sessionId) !== this.#sessionId ||
          JSON.stringify(parseReference(result.reference)) !== JSON.stringify(this.#config.localReference) ||
          Object.entries(expected).some(([key, value]) => result[key] !== value) ||
          result.meaning !== 'OPERATOR_STARTUP_CONSENT_RECHECKED_NOT_PHYSICAL_PROOF' ||
          typeof result.expiresAt !== 'string' ||
          !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(result.expiresAt) ||
          !Number.isFinite(Date.parse(result.expiresAt)) ||
          Date.parse(result.expiresAt) > Date.parse(String(this.receipt.expiresAt))) throw new Error('startup consent identity');
      const remaining = Date.parse(result.expiresAt) - wall;
      this.#deadline = Math.min(this.#deadline, started + remaining);
      if (remaining <= 0 || performance.now() >= this.#deadline || signal.aborted) throw new Error('startup consent expired');
    } catch {
      this.#fenced = true;
      throw new ReceptionUnknown('startup consent unavailable; retain desktop claim, never retry initialization');
    } finally { this.#busy = false; }
  }

  async retainIntent(command: unknown): Promise<Readonly<Record<string, unknown>>> {
    if (this.#fenced || this.#busy || this.#finishStarted || this.#stopActionId !== undefined ||
        this.#intentPending || performance.now() >= this.#deadline) {
      throw new ReceiverRefused('session expired or has an unresolved intent; never replay');
    }
    if (command === null || typeof command !== 'object' || Array.isArray(command)) {
      throw new ReceiverRefused('invalid action intent');
    }
    const row = command as Record<string, unknown>;
    if (!Number.isSafeInteger(row.sequence) || Number(row.sequence) < 1 ||
        Object.keys(row).some((key) => !['action', 'sequence', 'origin', 'keyChord', 'textValueRef'].includes(key))) {
      throw new ReceiverRefused('invalid action intent');
    }
    const body = JSON.stringify(row);
    if (Buffer.byteLength(body) > 4096) throw new ReceiverRefused('action intent exceeds bound');
    // Reserve before awaiting any request. Lost acknowledgement cannot clear the local fence.
    this.#intentPending = true;
    const retained = JSON.parse(body) as Record<string, unknown>;
    const abort = new AbortController();
    const timeout = Math.min(this.#config.timeoutMs ?? 5000, this.#deadline - performance.now());
    const timer = setTimeout(() => abort.abort(), Math.max(1, Math.floor(timeout)));
    try {
      const url = new URL(`/v1/workspaces/${this.#config.localReference.workspaceId}/supervisor-sessions/${this.#sessionId}/action-intents`, this.#config.apiOrigin);
      const response = await fetch(url, { method: 'POST', body, redirect: 'error', credentials: 'omit',
        headers: { Authorization: `Bearer ${this.#secret}`, 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
        signal: abort.signal });
      if (response.status !== 201) throw new Error('response');
      const result = exactObject(await boundedJson(response), ['actionId', 'sessionId', 'sequence', 'meaning']);
      uuid(result.actionId);
      if (uuid(result.sessionId) !== this.#sessionId || result.sequence !== retained.sequence ||
          result.meaning !== 'ACTION_INTENT_RETAINED' || performance.now() >= this.#deadline) throw new Error('identity');
      this.#current = { id: uuid(result.actionId), sequence: Number(result.sequence), command: retained };
      return Object.freeze(result);
    } catch {
      throw new ReceptionUnknown('action intent reception unknown; retain fencing and reconcile');
    } finally {
      clearTimeout(timer);
      abort.abort();
    }
  }

  async #post(path: string, payload: object, signal?: AbortSignal): Promise<unknown> {
    const remaining = this.#deadline - performance.now();
    if (remaining <= 0) { this.#fenced = true; throw new ReceiverRefused('session expired'); }
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), Math.max(1, Math.floor(Math.min(remaining, this.#config.timeoutMs ?? 5000))));
    try {
      const url = new URL(`/v1/workspaces/${this.#config.localReference.workspaceId}/supervisor-sessions/${this.#sessionId}/${path}`, this.#config.apiOrigin);
      const response = await fetch(url, { method: 'POST', body: JSON.stringify(payload),
        headers: { Authorization: `Bearer ${this.#secret}`, 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
        redirect: 'error', credentials: 'omit', signal: signal === undefined ? abort.signal : AbortSignal.any([abort.signal, signal]) });
      if (response.status !== 200) throw new Error('response');
      const result = await boundedJson(response);
      if (performance.now() >= this.#deadline || signal?.aborted) throw new Error('deadline');
      return result;
    } catch {
      this.#fenced = true;
      throw new ReceptionUnknown('action acknowledgement unknown; no retry or resumed input');
    } finally { clearTimeout(timer); abort.abort(); }
  }

  async commitDispatch(actionId: string, origin: string): Promise<ActionCommand> {
    const current = this.#current;
    if (this.#fenced || this.#busy || this.#committed || current?.id !== actionId) {
      throw new ReceiverRefused('dispatch identity unavailable or already committed');
    }
    this.#committed = true;
    this.#busy = true;
    try {
      const result = exactObject(await this.#post(`actions/${current.id}/dispatch`, { origin }),
        ['sessionId', 'command', 'meaning']);
      const expected = current.command;
      const keys = ['actionId', 'sequence', 'action',
        ...(expected.action === 'KEY_CHORD' ? ['keyChord'] : []),
        ...(expected.action === 'TYPE_TEXT' ? ['text'] : [])];
      const command = exactObject(result.command, keys);
      if (uuid(result.sessionId) !== this.#sessionId || result.meaning !== 'ACTION_DISPATCH_COMMITTED' ||
          uuid(command.actionId) !== current.id || command.sequence !== current.sequence ||
          command.action !== expected.action || command.keyChord !== expected.keyChord ||
          (expected.action === 'TYPE_TEXT' && (typeof command.text !== 'string' || command.text.length > 4096))) {
        throw new Error('command identity');
      }
      return Object.freeze(command) as unknown as ActionCommand;
    } catch {
      this.#fenced = true;
      throw new ReceptionUnknown('dispatch commitment unknown; never dispatch or replay');
    } finally { this.#busy = false; }
  }

  async completeAction(actionId: string, status: 'SUCCEEDED' | 'FAILED' | 'AMBIGUOUS'): Promise<void> {
    if (this.#busy || this.#current?.id !== actionId ||
        (status !== 'AMBIGUOUS' && (this.#fenced || !this.#committed))) {
      throw new ReceiverRefused('action result identity unavailable');
    }
    this.#busy = true;
    try {
      const result = exactObject(await this.#post(`actions/${actionId}/result`, { status }),
        ['sessionId', 'actionId', 'status', 'meaning']);
      if (uuid(result.sessionId) !== this.#sessionId || uuid(result.actionId) !== actionId ||
          result.status !== status || result.meaning !== 'ACTION_RESULT_RETAINED') throw new Error('identity');
      if (status === 'AMBIGUOUS') this.#fenced = true;
      if (this.#current.command.action === 'STOP' && status === 'SUCCEEDED') this.#stopActionId = actionId;
      // Only this exact successful acknowledgement clears the pending gate. A lost reply does not.
      this.#current = undefined;
      this.#intentPending = false;
      this.#committed = false;
      this.#observationPending = false;
      this.#runtimePreflightPending = false;
    } catch {
      this.#fenced = true;
      throw new ReceptionUnknown('action result acknowledgement unknown; retain fencing');
    } finally { this.#busy = false; }
  }

  async retainRuntimePreflight(command: ActionCommand, report: PreflightReport, capturedAtUtc: string): Promise<void> {
    if (this.#busy || this.#fenced || !this.#committed || this.#runtimePreflightPending ||
        this.#current?.id !== command.actionId || this.#current.sequence !== command.sequence) {
      throw new ReceiverRefused('runtime preflight identity unavailable');
    }
    this.#busy = true;
    this.#runtimePreflightPending = true;
    try {
      const checks = Object.fromEntries(PREFLIGHT_CHECKS.map((key) => [key, report.checks[key]?.condition]));
      if (Object.keys(report.checks).length !== PREFLIGHT_CHECKS.length ||
          Object.values(checks).some((value) => !['TRUE', 'FALSE', 'UNKNOWN'].includes(value))) throw new Error('runtime checks incomplete');
      // Only closed conditions leave the host. Probe diagnostics may contain private host paths,
      // account names or source URLs and are deliberately not transmitted.
      const sourceRecord = { actionId: command.actionId, actionSequence: command.sequence, capturedAtUtc, checks };
      const sourceRecordDigest = digest(sourceRecord);
      const result = exactObject(await this.#post(`actions/${command.actionId}/preflight`, { sourceRecord, sourceRecordDigest }),
        ['sessionId', 'actionId', 'eventId', 'sourceRecordDigest', 'meaning']);
      if (uuid(result.sessionId) !== this.#sessionId || uuid(result.actionId) !== command.actionId ||
          result.sourceRecordDigest !== sourceRecordDigest ||
          result.meaning !== 'RUNTIME_PREFLIGHT_RETAINED_NOT_IDENTITY_ATTESTATION') throw new Error('runtime receipt identity');
      uuid(result.eventId);
    } catch {
      this.#fenced = true;
      throw new ReceptionUnknown('runtime preflight acknowledgement unknown; no physical input or replay');
    } finally { this.#busy = false; }
  }

  async retainObservation(command: ActionCommand, observation: RawObservation | UnknownObservation, capturedAtUtc: string): Promise<void> {
    if (this.#busy || this.#fenced || !this.#committed || this.#observationPending ||
        this.#current?.id !== command.actionId || this.#current.sequence !== command.sequence) {
      throw new ReceiverRefused('reader observation identity unavailable');
    }
    this.#busy = true;
    this.#observationPending = true;
    try {
      const unknown = 'provenance' in observation && observation.provenance === 'CAPTURE_UNKNOWN';
      if (!unknown && (!('actionId' in observation) || observation.actionId !== command.actionId ||
          observation.actionSequence !== command.sequence)) throw new Error('reader action identity');
      // Construct a closed source vocabulary. Diagnostic DOM, selectors, paths and answer keys
      // never cross this channel. The server independently redacts exact fixture values.
      const sourceRecord = unknown
        ? { actionId: command.actionId, actionSequence: command.sequence, capturedAtUtc,
            provenance: 'CAPTURE_UNKNOWN', reason: (observation as UnknownObservation).reason }
        : { actionId: command.actionId, actionSequence: command.sequence,
            capturedAtUtc: (observation as RawObservation).capturedAtUtc,
            phrase: (observation as RawObservation).phrase };
      if (Buffer.byteLength(JSON.stringify(sourceRecord)) > 32768) throw new Error('reader source bound');
      const sourceRecordDigest = digest(sourceRecord);
      const producerSequence = ++this.#observationSequence;
      const result = exactObject(await this.#post(`actions/${command.actionId}/observation`,
        { producerSequence, sourceRecordDigest, sourceRecord }),
        ['sessionId', 'actionId', 'eventId', 'producerSequence', 'submittedSourceRecordDigest', 'meaning']);
      if (uuid(result.sessionId) !== this.#sessionId || uuid(result.actionId) !== command.actionId ||
          result.producerSequence !== producerSequence || result.submittedSourceRecordDigest !== sourceRecordDigest ||
          result.meaning !== 'READER_OBSERVATION_RETAINED') throw new Error('reader acknowledgement identity');
      uuid(result.eventId);
    } catch {
      this.#fenced = true;
      throw new ReceptionUnknown('reader evidence acknowledgement unknown; retain fencing');
    } finally { this.#busy = false; }
  }

  async finish(): Promise<Readonly<Record<string, unknown>>> {
    if (this.#fenced || this.#busy || this.#intentPending || this.#finishStarted ||
        this.#stopActionId === undefined) throw new ReceiverRefused('no acknowledged STOP to close');
    this.#finishStarted = true;
    try {
      const result = exactObject(await this.#post('finish', {
        stopActionId: this.#stopActionId, readerSequence: this.#observationSequence,
      }), ['sessionId', 'runId', 'status', 'outcome', 'missingArtifactCount', 'meaning']);
      if (uuid(result.sessionId) !== this.#sessionId ||
          uuid(result.runId) !== this.#config.localReference.runId ||
          result.status !== 'FINALIZING' || result.outcome !== 'NOT_EVALUATED' ||
          !Number.isSafeInteger(result.missingArtifactCount) || Number(result.missingArtifactCount) < 0 ||
          result.meaning !== 'EXECUTION_STOPPED_AWAITING_FINALIZATION') throw new Error('closing identity');
      return Object.freeze(result);
    } catch {
      throw new ReceptionUnknown('execution closure unknown; reconcile without restarting or replay');
    } finally { this.#fenced = true; }
  }
}
