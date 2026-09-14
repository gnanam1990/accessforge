/** Explicit trusted browser setup effect. Construction is inert; no reader is started. */
import { execFile } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import type { BrowserLauncher } from './browser-setup.js';
import { createSafariOriginProbe, SafariProbeUnavailable, type SafariOriginOptions, type SafariProbeRead } from './safari-origin.js';

export interface SafariLaunchOptions extends SafariOriginOptions {
  /** Fresh controller approval for opening this exact fixture; never a navigator callback. */
  readonly authorize: (signal: AbortSignal) => Promise<void>;
  /** Synchronous assertion of the controller's existing exclusive desktop claim. */
  readonly assertDesktopHeld: () => void;
  readonly signal: AbortSignal;
}

/** Trusted host ports for deterministic tests, not operator JSON or navigator-supplied hooks. */
export interface SafariLaunchPorts {
  readonly open?: (url: string, signal: AbortSignal) => Promise<void>;
  readonly read?: SafariProbeRead;
}

async function openSafari(url: string, signal: AbortSignal): Promise<void> {
  if (process.platform !== 'darwin') throw new Error('Safari launch requires macOS');
  await new Promise<void>((resolve, reject) => {
    execFile('/usr/bin/open', ['-b', 'com.apple.Safari', url], {
      signal, timeout: 2000, killSignal: 'SIGKILL', maxBuffer: 4096,
      env: { PATH: '/usr/bin:/bin', LANG: 'en_US.UTF-8' },
    }, (error) => error ? reject(new Error('Safari launch outcome unavailable')) : resolve());
  });
}

/**
 * One authorized open, then a native exact-document observation. A successful `open` command is
 * never a browser receipt. Failure/timeout/cancellation is uncertain and cannot replay the open.
 * This does not grant OS permissions, reserve a desktop, start AT or attest a complete environment.
 */
export function createSafariReferenceLauncher(
  options: SafariLaunchOptions, ports: SafariLaunchPorts = {},
): BrowserLauncher {
  // Validate and capture the private target before any possible effect; mutation cannot retarget it.
  const { expectedUrl, expectedBrowserVersion, helperPath, authorize, assertDesktopHeld, signal } = options;
  const probeOptions = { expectedUrl, expectedBrowserVersion,
    ...(helperPath === undefined ? {} : { helperPath }) };
  const read = ports.read;
  let probe = createSafariOriginProbe(probeOptions, read);
  const open = ports.open ?? openSafari;
  let used = false;
  let fenced = false;
  return async (url) => {
    if (used) { fenced = true; throw new Error('Safari launch cannot be replayed'); }
    used = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancel: () => void = () => {};
    const guard = () => {
      if (fenced || signal.aborted || controller.signal.aborted) throw new Error('Safari launch cancelled');
      assertDesktopHeld();
    };
    try {
      if (url !== expectedUrl) throw new Error('Safari launch target differs');
      const cancelled = new Promise<never>((_resolve, reject) => {
        cancel = () => { controller.abort(); reject(new Error('Safari launch cancelled')); };
        signal.addEventListener('abort', cancel, { once: true });
        timer = setTimeout(cancel, 8000);
      });
      return await Promise.race([
        (async () => {
          guard();
          await authorize(controller.signal);
          guard();
          await open(expectedUrl, controller.signal);
          guard();
          // The native probe independently checks signed foreground Safari, version and exact URL.
          // Opening is asynchronous. Only native not-ready refusals may be sampled again within
          // the same overall deadline; never replay open, focus the UI, or forgive malformed data.
          for (;;) {
            try { await probe(); break; } catch (error) {
              guard();
              if (!(error instanceof SafariProbeUnavailable) ||
                  !['SAFARI_NOT_FOREGROUND', 'FOCUSED_WINDOW_UNAVAILABLE',
                    'DOCUMENT_UNAVAILABLE', 'DOCUMENT_DIFFERS'].includes(error.reason)) throw error;
              await delay(100, undefined, { signal: controller.signal });
              guard();
              probe = createSafariOriginProbe(probeOptions, read);
            }
          }
          guard();
          await authorize(controller.signal);
          guard();
          // Reauthorization can take time; recheck the same observed process and document last.
          await probe();
          guard();
          return { observedUrl: expectedUrl, browserVersion: expectedBrowserVersion };
        })(),
        cancelled,
      ]);
    } catch {
      fenced = true;
      // Do not expose the private URL, subprocess stderr or approval diagnostics.
      throw new Error('Safari launch unconfirmed; reconcile the desktop before another attempt');
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
      controller.abort();
    }
  };
}
