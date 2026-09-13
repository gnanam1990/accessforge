/** Own one explicit Python navigator child without exposing private host capability in argv/files. */
import { spawn, type ChildProcess } from 'node:child_process';
import { realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, resolve } from 'node:path';
import { Duplex } from 'node:stream';
import { parseReference, type DispatchReference } from './dispatch-receiver.js';
import type { startNavigatorActionBridge } from './navigator-action-bridge.js';

type Bridge = Awaited<ReturnType<typeof startNavigatorActionBridge>>;
const owned = new WeakSet<Bridge>();
const environmentKeys = new Set(['ACCESSFORGE_DATABASE_URL', 'AWS_ACCESS_KEY_ID',
  'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_PROFILE', 'AWS_SHARED_CREDENTIALS_FILE',
  'AWS_CONFIG_FILE', 'SSL_CERT_FILE']);

export interface NavigatorProcessOptions {
  readonly pythonExecutable: string;
  /** Explicit provider/database-only environment. Parent environment is NEVER inherited. */
  readonly environment: Readonly<Record<string, string>>;
  readonly reference: DispatchReference;
  readonly consentId: string;
  readonly modelProfile: Readonly<Record<string, unknown>>;
  readonly allowBillableModelCalls: boolean;
  readonly deadlineMonotonic: number;
  readonly signal: AbortSignal;
  /** Independent observer producer closure, not model output. Server finish verifies it again. */
  readonly closeIndependentObserver: (signal: AbortSignal) => Promise<void>;
}

function validate(options: NavigatorProcessOptions): void {
  if (process.platform === 'win32' || options.allowBillableModelCalls !== true || options.signal.aborted ||
      !isAbsolute(options.pythonExecutable) || resolve(options.pythonExecutable) !== options.pythonExecutable ||
      realpathSync(dirname(options.pythonExecutable)) !== dirname(options.pythonExecutable) ||
      !statSync(options.pythonExecutable).isFile() ||
      !Number.isFinite(options.deadlineMonotonic) || options.deadlineMonotonic <= performance.now() ||
      options.deadlineMonotonic - performance.now() > 1800000 ||
      !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(options.consentId) ||
      !options.environment.ACCESSFORGE_DATABASE_URL ||
      Object.entries(options.environment).some(([key, value]) => !environmentKeys.has(key) ||
        typeof value !== 'string' || !value || value.includes('\0'))) {
    throw new Error('navigator process requires explicit private provider configuration and live consent');
  }
  parseReference(options.reference);
}

/** No retry. Receipt + clean child exit + actual native STOP + observer closure are all required. */
export async function runNavigatorProcess(bridge: Bridge, options: NavigatorProcessOptions): Promise<Readonly<Record<string, unknown>>> {
  let child: ChildProcess | undefined;
  const controller = new AbortController();
  let expiry: ReturnType<typeof setTimeout> | undefined;
  let killTimer: ReturnType<typeof setTimeout> | undefined;
  let finished = false;
  const cancel = () => controller.abort();
  const kill = () => {
    if (killTimer === undefined && child !== undefined && child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      killTimer = setTimeout(() => {
        if (child?.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
      }, 2000);
    }
  };
  let rejectAbort: ((error: Error) => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  // Attach immediately: no unhandled rejection if cancellation arrives during synchronous setup.
  void aborted.catch(() => {});
  const onAbort = () => { kill(); void bridge.close().catch(() => {});
    rejectAbort?.(new Error('navigator process cancelled or expired')); };
  controller.signal.addEventListener('abort', onAbort, { once: true });
  try {
    validate(options);
    if (owned.has(bridge)) throw new Error('navigator bridge cannot launch another process');
    owned.add(bridge);
    const reference = parseReference(options.reference);
    const capability = bridge.privateReference();
    if (JSON.stringify(parseReference(capability.reference)) !== JSON.stringify(reference)) throw new Error('native reference differs');
    const input = JSON.stringify({ schemaVersion: 1, reference, consentId: options.consentId,
      modelProfile: options.modelProfile, privateReference: capability });
    if (Buffer.byteLength(input) > 16384) throw new Error('navigator host envelope too large');
    options.signal.addEventListener('abort', cancel, { once: true });
    if (options.signal.aborted) throw new Error('navigator process cancelled');
    expiry = setTimeout(cancel, Math.max(1, options.deadlineMonotonic - performance.now()));
    child = spawn(options.pythonExecutable, ['-m', 'accessforge_orchestrator.navigator.operator',
      '--allow-billable-model-call'], { shell: false, env: { ...options.environment,
      AWS_EC2_METADATA_DISABLED: 'true', PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
    const process = child;
    const receipt: Buffer[] = [];
    let bytes = 0, pipeEnded = false;
    const exited = new Promise<void>((resolve, reject) => {
      process.once('error', () => reject(new Error('navigator process startup unavailable')));
      process.once('close', (code, signal) => {
        if (killTimer !== undefined) clearTimeout(killTimer);
        if (code !== 0 || signal !== null || !pipeEnded) reject(new Error('navigator child did not finish cleanly'));
        else resolve();
      });
      const source = process.stdio[4], destination = process.stdio[3];
      if (!(source instanceof Duplex) || !(destination instanceof Duplex)) {
        reject(new Error('private navigator pipes unavailable')); return;
      }
      destination.on('error', () => reject(new Error('navigator host input unconfirmed')));
      source.on('error', () => reject(new Error('navigator receipt unconfirmed')));
      source.on('end', () => { pipeEnded = true; });
      source.on('data', (chunk: Buffer) => {
        bytes += chunk.length;
        if (bytes > 2048) { reject(new Error('navigator receipt exceeded bound')); kill(); }
        else receipt.push(chunk);
      });
      destination.end(input);
    });
    await Promise.race([exited, aborted]);
    const raw = Buffer.concat(receipt).toString('utf8');
    const result: unknown = JSON.parse(raw);
    if (result === null || typeof result !== 'object' || Array.isArray(result)) throw new Error('receipt shape');
    const row = result as Record<string, unknown>;
    // The closed child protocol has one compact JSON line. Reject duplicate keys and trailing
    // records rather than accepting JSON.parse's last-key-wins interpretation.
    if (raw !== JSON.stringify(row) + '\n') throw new Error('receipt framing');
    const keys = ['schemaVersion', 'reference', 'status', 'completedCalls', 'lastOperationId'];
    if (Object.keys(row).length !== keys.length || keys.some((key) => !Object.hasOwn(row, key)) ||
        row.schemaVersion !== 1 || row.status !== 'STOP_ACKNOWLEDGED' ||
        !Number.isSafeInteger(row.completedCalls) || Number(row.completedCalls) < 1 || Number(row.completedCalls) > 500 ||
        typeof row.lastOperationId !== 'string' || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(row.lastOperationId) ||
        JSON.stringify(parseReference(row.reference)) !== JSON.stringify(reference)) throw new Error('receipt binding');
    await Promise.race([options.closeIndependentObserver(controller.signal), aborted]);
    if (controller.signal.aborted) throw new Error('navigator finish expired');
    const acknowledgement = await Promise.race([bridge.finish(), aborted]);
    finished = true;
    return acknowledgement;
  } catch {
    kill();
    throw new Error('navigator execution unconfirmed; inspect original attempt, never relaunch');
  } finally {
    if (expiry !== undefined) clearTimeout(expiry);
    options.signal.removeEventListener('abort', cancel);
    controller.signal.removeEventListener('abort', onAbort);
    if (!finished) await bridge.close();
  }
}

/** Validate launch authority before reader bootstrap, then own the full child/finish lifecycle. */
export async function startOwnedNavigatorExecution(
  bootstrap: () => Promise<Bridge>, options: NavigatorProcessOptions,
): Promise<Readonly<Record<string, unknown>>> {
  validate(options);
  let cancelled = false;
  let rejectAbort: ((error: Error) => void) | undefined;
  const interrupted = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  const cancel = () => { cancelled = true; rejectAbort?.(new Error('navigator bootstrap cancelled')); };
  const expiry = setTimeout(cancel, Math.max(1, options.deadlineMonotonic - performance.now()));
  options.signal.addEventListener('abort', cancel, { once: true });
  try {
    if (options.signal.aborted) cancel();
    const ready = Promise.resolve().then(bootstrap).then(async bridge => {
      if (cancelled || options.signal.aborted || performance.now() >= options.deadlineMonotonic) {
        await bridge.close();
        throw new Error('late navigator bootstrap fenced');
      }
      return bridge;
    });
    const bridge = await Promise.race([ready, interrupted]);
    return await runNavigatorProcess(bridge, options);
  } finally {
    clearTimeout(expiry);
    options.signal.removeEventListener('abort', cancel);
  }
}
