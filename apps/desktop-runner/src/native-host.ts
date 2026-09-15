/** Explicit operator-owned host module. Never load navigator-supplied code or configuration. */
import { closeSync, constants, fstatSync, fsyncSync, linkSync, lstatSync, openSync, realpathSync, unlinkSync, writeSync } from 'node:fs';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { assertRealReaderProven } from '@accessforge/at-voiceover';
import { startNativeDispatchListener } from './native-start-listener.js';
import type { NavigatorProcessOptions } from './navigator-process.js';
import type { ObserverProcessOptions } from './observer-process.js';
import type { ReferenceEffectProcessOptions } from './reference-effect-process.js';

export interface NativeHostConfiguration {
  readonly privateDirectory: string;
  readonly bootstrap: Parameters<typeof startNativeDispatchListener>[1];
  readonly navigator: Omit<NavigatorProcessOptions, 'signal' | 'closeIndependentObserver'> & {
    readonly independentObserver: ObserverProcessOptions;
    readonly independentEffectObserver?: ReferenceEffectProcessOptions;
  };
}

function privateParent(path: string): void {
  if (!isAbsolute(path) || realpathSync(dirname(path)) !== resolve(dirname(path))) throw new Error('path');
  const parent = lstatSync(dirname(path));
  if (!parent.isDirectory() || parent.uid !== process.getuid?.() || (parent.mode & 0o077) !== 0) throw new Error('owner');
}

/** New private file only. Never print a bearer token or overwrite an older handoff to retry. */
export function publishNativeHandoff(path: string, reference: Readonly<Record<string, unknown>>): void {
  privateParent(path);
  const bytes = Buffer.from(JSON.stringify(reference) + '\n');
  if (bytes.length > 4096) throw new Error('handoff too large');
  const temporary = join(dirname(path), `.native-handoff-${randomUUID()}`);
  const fd = openSync(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  try {
    let offset = 0;
    while (offset < bytes.length) {
      const n = writeSync(fd, bytes, offset, bytes.length - offset);
      if (n <= 0) throw new Error('handoff write incomplete');
      offset += n;
    }
    fsyncSync(fd);
    linkSync(temporary, path); // Atomic publication; unlike rename, refuses an existing target.
  } finally {
    closeSync(fd);
    unlinkSync(temporary); // Only the exclusive temporary file created by this invocation.
  }
  const directory = openSync(dirname(path), 'r');
  try { fsyncSync(directory); } finally { closeSync(directory); }
}

/** Loading an .mjs file executes trusted operator code with the host user's permissions.
 * This is not a sandbox or a JSON configuration importer. Qualification runs before import.
 * The provisioning module must supply real runtime/effect probes and explicit model consent;
 * no default TRUE probes or capabilities are manufactured by this launcher.
 */
export async function runNativeHost(modulePath: string, handoffPath: string, signal: AbortSignal): Promise<void> {
  assertRealReaderProven();
  if (signal.aborted) throw new Error('cancelled');
  privateParent(handoffPath);
  const module = await loadPrivateOperatorModule(modulePath);
  if (signal.aborted || module === null || typeof module !== 'object' ||
      !('provisionNativeHost' in module) || typeof module.provisionNativeHost !== 'function') throw new Error('host provisioner unavailable');
  const config = await module.provisionNativeHost(signal) as NativeHostConfiguration;
  if (signal.aborted) throw new Error('cancelled');
  const host = await startNativeDispatchListener(config.privateDirectory, config.bootstrap,
    {...config.navigator, signal});
  try {
    publishNativeHandoff(handoffPath, host.privateReference);
    await host.completion;
  } finally {
    host.close();
    // Retain the handoff and native claims for reconciliation. No auto replay or cleanup.
  }
}

/** Trusted executable operator configuration, not a sandbox or navigator import port. */
export async function loadPrivateOperatorModule(modulePath: string): Promise<unknown> {
  privateParent(modulePath);
  if (!modulePath.endsWith('.mjs') || realpathSync(modulePath) !== resolve(modulePath)) throw new Error('host module path');
  const fd = openSync(modulePath, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid?.() || info.nlink !== 1 ||
        (info.mode & 0o077) !== 0 || info.size > 65536) throw new Error('host module unavailable');
  } finally { closeSync(fd); }
  return await import(pathToFileURL(modulePath).href) as unknown;
}
