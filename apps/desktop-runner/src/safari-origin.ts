/** Native Safari origin observation for the trusted desktop supervisor, never the navigator. */
import { execFile } from 'node:child_process';
import { lstat } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AuthenticatedRunner, type AuthenticatedRunnerOptions } from './authenticated-runner.js';

interface ProbeRequest {
  readonly expectedUrl: string;
  readonly expectedBrowserVersion: string;
  readonly includeKeyboardFocus?: true;
}

export interface SafariOriginOptions extends Omit<ProbeRequest, 'includeKeyboardFocus'> {
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
  'KEYBOARD_FOCUS_UNAVAILABLE', 'KEYBOARD_FOCUS_CHANGED',
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
  const sample = createSafariMeasurement(options, false, read);
  return async () => (await sample()).origin;
}

export interface SafariKeyboardFocus {
  readonly measurementKind: 'AX_KEYBOARD_FOCUS';
  readonly role: string;
  /** Hash of the native accessibility identifier, never a page value, title or selector. */
  readonly identifierDigest: string;
}

/** Private read-only collector, not an action-bound receipt or an evaluator condition. */
export function createSafariKeyboardFocusProbe(options: SafariOriginOptions, read?: SafariProbeRead):
  () => Promise<Readonly<{ origin: string; keyboardFocus: SafariKeyboardFocus }>> {
  const sample = createSafariMeasurement(options, true, read);
  return async () => {
    const result = await sample();
    if (result.keyboardFocus === undefined) throw new SafariProbeUnavailable('KEYBOARD_FOCUS_UNAVAILABLE');
    return Object.freeze({ origin: result.origin, keyboardFocus: result.keyboardFocus });
  };
}

function createSafariMeasurement(options: SafariOriginOptions, includeKeyboardFocus: boolean, read?: SafariProbeRead):
  () => Promise<Readonly<{ origin: string; keyboardFocus?: SafariKeyboardFocus }>> {
  const url = new URL(options.expectedUrl);
  if (url.href !== options.expectedUrl || url.protocol !== 'http:' ||
      !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      url.username !== '' || url.password !== '' || url.search !== '' || url.hash !== '' ||
      !/^\/form\/[A-Za-z0-9_-]{16,64}$/.test(url.pathname) ||
      !/^[0-9]+(?:\.[0-9]+){1,3}$/.test(options.expectedBrowserVersion)) {
    throw new Error('native Safari probe requires an exact sealed reference fixture and browser version');
  }
  const request = Object.freeze({ expectedUrl: url.href, expectedBrowserVersion: options.expectedBrowserVersion,
    ...(includeKeyboardFocus ? { includeKeyboardFocus: true as const } : {}) });
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
      const keys = includeKeyboardFocus ? 'browserVersion,bundleId,keyboardFocus,launchedAt,pid,schemaVersion,status,url'
        : 'browserVersion,bundleId,launchedAt,pid,schemaVersion,status,url';
      if (Object.keys(result).sort().join(',') !== keys ||
          result.schemaVersion !== 1 || result.status !== 'KNOWN' || result.bundleId !== 'com.apple.Safari' ||
          result.url !== request.expectedUrl || result.browserVersion !== request.expectedBrowserVersion ||
          typeof result.pid !== 'number' || !Number.isSafeInteger(result.pid) || result.pid <= 0 ||
          typeof result.launchedAt !== 'number' || !Number.isFinite(result.launchedAt) || result.launchedAt <= 0) {
        throw new Error('unavailable');
      }
      const observedIdentity = `${result.pid}:${result.launchedAt}`;
      if (identity !== undefined && identity !== observedIdentity) throw new Error('browser replaced');
      identity = observedIdentity;
      let keyboardFocus: SafariKeyboardFocus | undefined;
      if (includeKeyboardFocus) {
        const raw = result.keyboardFocus;
        if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('focus unavailable');
        const focus = raw as Record<string, unknown>;
        if (Object.keys(focus).sort().join(',') !== 'identifierDigest,measurementKind,role' ||
            focus.measurementKind !== 'AX_KEYBOARD_FOCUS' || typeof focus.role !== 'string' ||
            !['AXTextField', 'AXTextArea', 'AXButton', 'AXCheckBox', 'AXRadioButton', 'AXPopUpButton', 'AXComboBox', 'AXLink'].includes(focus.role) ||
            typeof focus.identifierDigest !== 'string' || !/^[a-f0-9]{64}$/.test(focus.identifierDigest)) throw new Error('focus unavailable');
        keyboardFocus = Object.freeze({ measurementKind: 'AX_KEYBOARD_FOCUS', role: focus.role, identifierDigest: focus.identifierDigest });
      }
      return Object.freeze({ origin: url.origin, ...(keyboardFocus === undefined ? {} : { keyboardFocus }) });
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
