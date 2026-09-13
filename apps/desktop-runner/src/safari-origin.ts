/** Native Safari origin observation for the trusted desktop supervisor, never the navigator. */
import { execFile } from 'node:child_process';
import { lstat } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AuthenticatedRunner, type AuthenticatedRunnerOptions } from './authenticated-runner.js';

interface ProbeRequest {
  readonly expectedUrl: string;
  readonly expectedBrowserVersion: string;
}

export interface SafariOriginOptions extends ProbeRequest {
  /** Operator-owned compiled helper, not a candidate artifact or navigator-selected executable. */
  readonly helperPath?: string;
}

export type SafariProbeRead = (request: ProbeRequest) => Promise<unknown>;

export const SAFARI_PROBE_FAILURES = [
  'HOST_UNSUPPORTED', 'HELPER_UNAVAILABLE', 'INPUT_UNAVAILABLE', 'SAFARI_NOT_FOREGROUND',
  'SAFARI_SIGNATURE_UNAVAILABLE', 'ACCESSIBILITY_UNAVAILABLE', 'BROWSER_VERSION_DIFFERS',
  'AX_TIMEOUT_UNAVAILABLE', 'FOCUSED_WINDOW_UNAVAILABLE', 'MODAL_WINDOW_OR_UNKNOWN',
  'DOCUMENT_UNAVAILABLE', 'DOCUMENT_DIFFERS', 'BROWSER_CHANGED_DURING_SAMPLE', 'OUTPUT_UNAVAILABLE',
  'PROBE_UNAVAILABLE',
] as const;
export type SafariProbeFailure = (typeof SAFARI_PROBE_FAILURES)[number];

/** Bounded, non-sensitive operator diagnostics. No native stderr or document content is included. */
export class SafariProbeUnavailable extends Error {
  constructor(readonly reason: SafariProbeFailure) {
    super('Safari observation unavailable; execution must remain fenced');
  }
}

async function nativeRead(request: ProbeRequest, helperPath: string): Promise<unknown> {
  if (process.platform !== 'darwin') throw new SafariProbeUnavailable('HOST_UNSUPPORTED');
  if (!isAbsolute(helperPath)) throw new SafariProbeUnavailable('HELPER_UNAVAILABLE');
  const stat = await lstat(helperPath).catch(() => { throw new SafariProbeUnavailable('HELPER_UNAVAILABLE'); });
  if (!stat.isFile() || stat.uid !== process.getuid?.() || (stat.mode & 0o022) !== 0 ||
      (stat.mode & 0o100) === 0) throw new SafariProbeUnavailable('HELPER_UNAVAILABLE');
  return new Promise((resolve, reject) => {
    const child = execFile(helperPath, [], {
      timeout: 1500, killSignal: 'SIGKILL', maxBuffer: 16384, encoding: 'utf8',
      env: { PATH: '/usr/bin:/bin', LANG: 'en_US.UTF-8' },
    }, (error, stdout) => {
      if (error) {
        let reason: SafariProbeFailure = 'PROBE_UNAVAILABLE';
        // Only a normal explicit refusal may supply a whitelisted diagnostic; timeout, signal,
        // oversized output and arbitrary process failures never masquerade as a known observation.
        if (error.code === 78 && !error.killed && !error.signal) {
          try {
            const result = JSON.parse(stdout) as Record<string, unknown>;
            if (result.schemaVersion === 1 && result.status === 'UNKNOWN' &&
                SAFARI_PROBE_FAILURES.includes(result.reason as SafariProbeFailure)) reason = result.reason as SafariProbeFailure;
          } catch { /* Keep the generic code. */ }
        }
        reject(new SafariProbeUnavailable(reason)); return;
      }
      try { resolve(JSON.parse(stdout) as unknown); }
      catch { reject(new Error('native Safari observation malformed')); }
    });
    child.stdin?.on('error', () => { /* The process callback reports the unavailable observation. */ });
    child.stdin?.end(JSON.stringify(request));
  });
}

/**
 * Every call samples the OS again. The first successful observation binds the browser process;
 * any ambiguity or process replacement permanently refuses reuse for this attempt.
 * `read` is a test/embedding port; production uses the fixed native helper above.
 */
export function createSafariOriginProbe(
  options: SafariOriginOptions, read?: SafariProbeRead,
): () => Promise<string> {
  const url = new URL(options.expectedUrl);
  if (url.href !== options.expectedUrl || url.protocol !== 'http:' ||
      !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      url.username !== '' || url.password !== '' || url.search !== '' || url.hash !== '' ||
      !/^\/form\/[A-Za-z0-9_-]{16,64}$/.test(url.pathname) ||
      !/^[0-9]+(?:\.[0-9]+){1,3}$/.test(options.expectedBrowserVersion)) {
    throw new Error('native Safari probe requires an exact sealed reference fixture and browser version');
  }
  const request = Object.freeze({ expectedUrl: url.href, expectedBrowserVersion: options.expectedBrowserVersion });
  const helperPath = options.helperPath ?? fileURLToPath(new URL('./native/safari-origin-probe', import.meta.url));
  const sample = read ?? ((value: ProbeRequest) => nativeRead(value, helperPath));
  let identity: string | undefined;
  let fenced = false;
  let busy = false;
  return async () => {
    if (fenced || busy) { fenced = true; throw new Error('Safari observation fenced'); }
    busy = true;
    try {
      const value = await sample(request);
      if (fenced || typeof value !== 'object' || value === null) throw new Error('unavailable');
      const result = value as Record<string, unknown>;
      if (Object.keys(result).sort().join(',') !== 'browserVersion,bundleId,launchedAt,pid,schemaVersion,status,url' ||
          result.schemaVersion !== 1 || result.status !== 'KNOWN' || result.bundleId !== 'com.apple.Safari' ||
          result.url !== request.expectedUrl || result.browserVersion !== request.expectedBrowserVersion ||
          typeof result.pid !== 'number' || !Number.isSafeInteger(result.pid) || result.pid <= 0 ||
          typeof result.launchedAt !== 'number' || !Number.isFinite(result.launchedAt) || result.launchedAt <= 0) {
        throw new Error('unavailable');
      }
      const observedIdentity = `${result.pid}:${result.launchedAt}`;
      if (identity !== undefined && identity !== observedIdentity) throw new Error('browser replaced');
      identity = observedIdentity;
      return url.origin;
    } catch (error) {
      fenced = true;
      // Neither the intended private nonce nor a different foreground document leaks in diagnostics.
      throw error instanceof SafariProbeUnavailable ? error : new SafariProbeUnavailable('PROBE_UNAVAILABLE');
    } finally { busy = false; }
  };
}

/** Live-origin wiring only. Physical preflight and focus/effect authorization remain mandatory. */
export function createSafariAuthenticatedRunner(
  options: Omit<AuthenticatedRunnerOptions, 'observeOrigin'> & { readonly safari: SafariOriginOptions },
): AuthenticatedRunner {
  const { safari, ...runtime } = options;
  return new AuthenticatedRunner({ ...runtime, observeOrigin: createSafariOriginProbe(safari) });
}
