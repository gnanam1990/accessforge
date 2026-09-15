/** Trusted host -> independent observer process. No observer credentials reach the navigator. */
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, resolve } from 'node:path';
import { parseReference, type DispatchReference } from './dispatch-receiver.js';

export interface ObserverProcessOptions {
  readonly pythonExecutable: string;
  readonly credentialRef: string;
  /** Independently provisioned observer-only credentials, not the navigator environment. */
  readonly environment: Readonly<Record<string, string>>;
}

const keys = new Set(['ACCESSFORGE_DATABASE_URL', 'ACCESSFORGE_OBSERVER_DATABASE_URL', 'SSL_CERT_FILE']);

/** Creates a one-shot closure before reader startup; construction never launches a process.
 * Exit 3 with the exact UNKNOWN receipt still means a closed stream, not a known observation.
 * The server independently validates the producer closure before native finish/finalization.
 */
export function createObserverProcessClosure(
  options: ObserverProcessOptions, reference: DispatchReference, deadlineMonotonic: number,
): (signal: AbortSignal) => Promise<void> {
  const bound = parseReference(reference);
  const executable = options.pythonExecutable;
  const credentialRef = options.credentialRef;
  const environment = Object.freeze({ ...options.environment });
  if (process.platform === 'win32' || !isAbsolute(executable) || resolve(executable) !== executable ||
      realpathSync(dirname(executable)) !== dirname(executable) || !statSync(executable).isFile() ||
      typeof credentialRef !== 'string' || !credentialRef.trim() || credentialRef.length > 256 ||
      /[\x00-\x1f\x7f]/.test(credentialRef) ||
      !Number.isFinite(deadlineMonotonic) || deadlineMonotonic <= performance.now() ||
      !environment.ACCESSFORGE_DATABASE_URL || !environment.ACCESSFORGE_OBSERVER_DATABASE_URL ||
      Object.entries(environment).some(([key, value]) => !keys.has(key) || typeof value !== 'string' ||
        !value || value.includes('\0'))) throw new Error('independent observer configuration unavailable');
  const recordId = randomUUID();
  let consumed = false;
  return async signal => {
    if (consumed || signal.aborted || performance.now() >= deadlineMonotonic) {
      throw new Error('observer closure unavailable; reconcile original stream');
    }
    consumed = true;
    await new Promise<void>((resolveClosure, reject) => {
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let killTimer: ReturnType<typeof setTimeout> | undefined;
      const child = spawn(executable, ['-I', '-m', 'accessforge_orchestrator.completion_observer',
        '--workspace-id', bound.workspaceId, '--run-id', bound.runId,
        '--source-record-id', recordId, '--observer-credential-ref', credentialRef, '--final'],
      { env: environment, cwd: dirname(executable), shell: false, stdio: ['ignore', 'pipe', 'ignore'] });
      const chunks: Buffer[] = [];
      let size = 0;
      const cleanup = () => {
        if (timer !== undefined) clearTimeout(timer);
        signal.removeEventListener('abort', abort);
      };
      const fail = () => {
        if (settled) return;
        settled = true;
        cleanup();
        if (child.exitCode === null && child.signalCode === null) {
          child.kill('SIGTERM');
          killTimer = setTimeout(() => {
            if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
          }, 2000);
        }
        // A killed process may have committed. This is UNKNOWN, never permission to retry.
        reject(new Error('independent observer closure unconfirmed; reconcile original stream'));
      };
      const abort = () => fail();
      timer = setTimeout(fail, Math.max(1, Math.min(30000, deadlineMonotonic - performance.now())));
      signal.addEventListener('abort', abort, { once: true });
      child.stdout!.on('data', (chunk: Buffer) => {
        size += chunk.length;
        if (size > 128) fail(); else if (!settled) chunks.push(chunk);
      });
      child.once('error', fail);
      child.stdout!.once('error', fail);
      child.once('close', (code, termination) => {
        if (killTimer !== undefined) clearTimeout(killTimer);
        if (settled) return;
        const output = Buffer.concat(chunks).toString('utf8');
        if (signal.aborted || performance.now() >= deadlineMonotonic || termination !== null ||
            !((code === 0 && output === 'observer measurement retained: KNOWN\n') ||
              (code === 3 && output === 'observer measurement retained: UNKNOWN\n'))) {
          fail(); return;
        }
        settled = true;
        cleanup();
        resolveClosure();
      });
      if (signal.aborted) fail();
    });
  };
}
