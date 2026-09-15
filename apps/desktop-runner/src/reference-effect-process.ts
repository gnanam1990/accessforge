/** Own the independent collector child; no source credentials or effects reach the planner. */
import { spawn, type ChildProcess } from 'node:child_process';
import { realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, resolve } from 'node:path';
import { Duplex } from 'node:stream';
import { parseReference, type DispatchReference } from './dispatch-receiver.js';
import type { ObserverProcessOptions } from './observer-process.js';

export interface ReferenceEffectProcessOptions extends ObserverProcessOptions {
  readonly applicationRole: string;
  readonly installationId: string;
}

const protocol = 'accessforge.reference-effect-observer.v1';
const uuid = (value: unknown): value is string => typeof value === 'string' &&
  /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
const keys = new Set(['ACCESSFORGE_DATABASE_URL', 'ACCESSFORGE_OBSERVER_DATABASE_URL', 'SSL_CERT_FILE']);

/** Inert validation. start() is called after machine-session opening, before reader/planner input. */
export function createReferenceEffectProcess(options: ReferenceEffectProcessOptions,
  reference: DispatchReference, deadlineMonotonic: number) {
  const bound = parseReference(reference);
  const executable = options.pythonExecutable;
  const credential = options.credentialRef, role = options.applicationRole;
  const installation = options.installationId;
  const environment = Object.freeze({ ...options.environment });
  if (process.platform === 'win32' || !isAbsolute(executable) || resolve(executable) !== executable ||
      realpathSync(dirname(executable)) !== dirname(executable) || !statSync(executable).isFile() ||
      typeof credential !== 'string' || !credential.trim() || credential.length > 256 ||
      typeof role !== 'string' || !role.trim() || role.length > 63 ||
      /[\x00-\x1f\x7f]/.test(credential + role) || !uuid(installation) ||
      !Number.isFinite(deadlineMonotonic) || deadlineMonotonic <= performance.now() ||
      deadlineMonotonic - performance.now() > 1800000 ||
      !environment.ACCESSFORGE_DATABASE_URL || !environment.ACCESSFORGE_OBSERVER_DATABASE_URL ||
      Object.entries(environment).some(([key, value]) => !keys.has(key) || typeof value !== 'string' ||
        !value || value.includes('\0'))) throw new Error('independent effect observer configuration unavailable');

  let state: 'NEW' | 'STARTING' | 'ACTIVE' | 'CLOSING' | 'CLOSED' | 'FAILED' = 'NEW';
  let child: ChildProcess | undefined, input: Duplex | undefined;
  let signal: AbortSignal | undefined;
  let stageTimer: ReturnType<typeof setTimeout> | undefined, expiry: ReturnType<typeof setTimeout> | undefined;
  let killTimer: ReturnType<typeof setTimeout> | undefined;
  let readyId: string | undefined, closedReceipt = false, ended = false;
  let buffer = Buffer.alloc(0), size = 0;
  let resolveReady!: () => void, rejectReady!: (error: Error) => void;
  let resolveClosed!: () => void, rejectClosed!: (error: Error) => void;
  let rejectFailure!: (error: Error) => void;
  const ready = new Promise<void>((yes, no) => { resolveReady = yes; rejectReady = no; });
  const closed = new Promise<void>((yes, no) => { resolveClosed = yes; rejectClosed = no; });
  const failure = new Promise<never>((_yes, no) => { rejectFailure = no; });
  void ready.catch(() => {}); void closed.catch(() => {}); void failure.catch(() => {});
  const cleanup = () => {
    if (stageTimer !== undefined) clearTimeout(stageTimer);
    if (expiry !== undefined) clearTimeout(expiry);
    signal?.removeEventListener('abort', fail);
  };
  function fail(): void {
    if (state === 'CLOSED' || state === 'FAILED') return;
    state = 'FAILED'; cleanup();
    if (child !== undefined && child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      killTimer = setTimeout(() => {
        if (child?.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
      }, 2000);
    }
    const error = new Error('independent effect observer unconfirmed; reconcile original records');
    rejectReady(error); rejectClosed(error); rejectFailure(error);
  }
  const active = () => {
    if (state !== 'ACTIVE' || signal?.aborted || performance.now() >= deadlineMonotonic ||
        child === undefined || child.exitCode !== null || child.signalCode !== null) {
      fail(); throw new Error('independent effect observer is not active');
    }
  };
  const consume = (line: Buffer) => {
    const raw = new TextDecoder('utf-8', { fatal: true }).decode(line);
    const row: unknown = JSON.parse(raw);
    if (row === null || typeof row !== 'object' || Array.isArray(row) || JSON.stringify(row) !== raw) throw new Error();
    const r = row as Record<string, unknown>;
    const expected = ['protocol', 'status', 'workspaceId', 'runId', 'attemptId', 'eventId'];
    if (Object.keys(r).length !== expected.length || expected.some(k => !Object.hasOwn(r, k)) ||
        r.protocol !== protocol || r.workspaceId !== bound.workspaceId || r.runId !== bound.runId ||
        r.attemptId !== bound.attemptId || !uuid(r.eventId)) throw new Error();
    if (state === 'STARTING' && r.status === 'READY_RETAINED') {
      readyId = r.eventId; state = 'ACTIVE';
      if (stageTimer !== undefined) clearTimeout(stageTimer);
      resolveReady();
    } else if (state === 'CLOSING' && r.status === 'CLOSED_RETAINED' && !closedReceipt && r.eventId !== readyId) {
      closedReceipt = true;
    } else throw new Error();
  };
  return {
    failure,
    assertActive: active,
    abort: fail,
    async start(parentSignal: AbortSignal): Promise<void> {
      if (state !== 'NEW' || parentSignal.aborted || performance.now() >= deadlineMonotonic) {
        fail(); throw new Error('effect observer cannot start or replay');
      }
      state = 'STARTING'; signal = parentSignal;
      signal.addEventListener('abort', fail, { once: true });
      const remaining = deadlineMonotonic - performance.now();
      expiry = setTimeout(fail, Math.max(1, remaining));
      stageTimer = setTimeout(fail, Math.max(1, Math.min(30000, remaining)));
      try {
        child = spawn(executable, ['-I', '-m', 'accessforge_orchestrator.reference_effect_worker', '--private-host'],
          { env: environment, cwd: dirname(executable), shell: false,
            stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
        const source = child.stdio[4], destination = child.stdio[3];
        child.once('error', fail);
        child.once('exit', (code, termination) => {
          if (code !== 0 || termination !== null || state !== 'CLOSING') fail();
        });
        child.once('close', (code, termination) => {
          if (killTimer !== undefined) clearTimeout(killTimer);
          if (state === 'FAILED') return;
          if (code !== 0 || termination !== null || state !== 'CLOSING' || !closedReceipt ||
              !ended || buffer.length !== 0 || signal?.aborted || performance.now() >= deadlineMonotonic) {
            fail(); return;
          }
          state = 'CLOSED'; cleanup(); resolveClosed();
        });
        if (!(source instanceof Duplex) || !(destination instanceof Duplex)) throw new Error();
        input = destination;
        input.on('error', fail); source.on('error', fail);
        source.on('end', () => {
          ended = true;
          if (state !== 'CLOSING' || !closedReceipt || buffer.length !== 0) fail();
        });
        source.on('data', (chunk: Buffer) => {
          if (state === 'FAILED' || state === 'CLOSED') return;
          try {
            size += chunk.length;
            if (size > 2048) throw new Error();
            buffer = Buffer.concat([buffer, chunk]);
            let newline: number;
            while ((newline = buffer.indexOf(10)) !== -1) {
              const line = buffer.subarray(0, newline); buffer = buffer.subarray(newline + 1);
              consume(line);
            }
          } catch { fail(); }
        });
        input.write(JSON.stringify({ protocol, workspaceId: bound.workspaceId, runId: bound.runId,
          attemptId: bound.attemptId, observerCredentialRef: credential, applicationRole: role,
          installationId: installation, maxWallSeconds: Math.max(1, Math.min(1800, Math.ceil(remaining / 1000))) }) + '\n');
        if (signal.aborted) fail();
      } catch { fail(); }
      await ready; active();
    },
    async finish(): Promise<void> {
      active();
      state = 'CLOSING';
      stageTimer = setTimeout(fail, Math.max(1, Math.min(30000, deadlineMonotonic - performance.now())));
      try { input!.end(JSON.stringify({ protocol, command: 'FINISH', readyEventId: readyId }) + '\n'); }
      catch { fail(); }
      await closed;
    },
  };
}
