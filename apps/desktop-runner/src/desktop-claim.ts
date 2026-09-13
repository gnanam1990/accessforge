/** Cooperative, crash-persistent exclusion for one operator-owned POSIX desktop session. */
import {
  closeSync, constants, fsyncSync, fstatSync, lstatSync, openSync, readSync,
  realpathSync, unlinkSync, writeFileSync, type Stats,
} from 'node:fs';
import { randomBytes } from 'node:crypto';
import { isAbsolute, join, resolve } from 'node:path';
import { parseReference, type DispatchReference } from './dispatch-receiver.js';
import type { AuthenticatedRunner } from './authenticated-runner.js';

export interface DesktopClaimOptions {
  /** All runner registrations on this host MUST share this independently provisioned private root. */
  readonly directory: string;
  readonly desktopSessionId: string;
  readonly reference: DispatchReference;
}

function owned(info: Stats): void {
  if (typeof process.getuid !== 'function' || info.uid !== process.getuid() ||
      (info.mode & 0o077) !== 0) throw new Error('private owned storage required');
}

function sameFile(left: Stats, right: Stats): boolean {
  return left.dev === right.dev && left.ino === right.ino;
}

/** No stale-age/PID expiry: unknown state requires independent operator reconciliation. */
function claimDesktop(options: DesktopClaimOptions): { assertHeld(): void; release(): void } {
  const { directory, desktopSessionId } = options;
  if (typeof desktopSessionId !== 'string' || !/^[1-9][0-9]*$/.test(desktopSessionId) ||
      Number(desktopSessionId) >= 4294967295) throw new Error('assigned desktop session unavailable');
  const reference = parseReference(options.reference);
  const path = join(directory, `desktop-${desktopSessionId}.json`);
  const payload = Buffer.from(JSON.stringify({ schemaVersion: 1, desktopSessionId, reference,
    claimNonce: randomBytes(32).toString('hex') }) + '\n');
  let root: Stats | undefined;
  let file: Stats | undefined;
  let closed = false;

  function openRoot(): number {
    if (!isAbsolute(directory) || realpathSync(directory) !== resolve(directory)) throw new Error('claim root path');
    const info = lstatSync(directory);
    owned(info);
    if (!info.isDirectory() || (root !== undefined && !sameFile(root, info))) throw new Error('claim root changed');
    const fd = openSync(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try {
      const opened = fstatSync(fd);
      owned(opened);
      if (!sameFile(info, opened)) throw new Error('claim root changed');
      root ??= opened;
      return fd;
    } catch (error) { closeSync(fd); throw error; }
  }

  let parent: number | undefined;
  let created: number | undefined;
  try {
    parent = openRoot();
    created = openSync(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    writeFileSync(created, payload);
    fsyncSync(created);
    file = fstatSync(created);
    owned(file);
    if (!file.isFile() || file.nlink !== 1 || file.size !== payload.length) throw new Error('claim file unavailable');
    fsyncSync(parent);
  } catch {
    // Even a partial write is an exclusion marker. Never delete it as constructor cleanup.
    throw new Error('desktop claim unavailable or already held; reconcile before another attempt');
  } finally {
    if (created !== undefined) closeSync(created);
    if (parent !== undefined) closeSync(parent);
  }

  function assertHeld(): void {
    let parentFd: number | undefined;
    let fd: number | undefined;
    try {
      if (closed || file === undefined) throw new Error('claim closed');
      parentFd = openRoot();
      fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
      const info = fstatSync(fd);
      owned(info);
      if (!info.isFile() || info.nlink !== 1 || !sameFile(file, info) || info.size !== payload.length) {
        throw new Error('claim identity changed');
      }
      const bytes = Buffer.alloc(payload.length + 1);
      let size = 0;
      while (size < bytes.length) {
        const n = readSync(fd, bytes, size, bytes.length - size, null);
        if (n === 0) break;
        size += n;
      }
      if (size !== payload.length || !bytes.subarray(0, size).equals(payload)) throw new Error('claim content changed');
    } catch {
      closed = true;
      throw new Error('desktop claim lost or uncertain; execution fenced');
    } finally {
      if (fd !== undefined) closeSync(fd);
      if (parentFd !== undefined) closeSync(parentFd);
    }
  }

  return {
    assertHeld,
    release() {
      let parentFd: number | undefined;
      try {
        assertHeld();
        closed = true; // No retry after an ambiguous unlink/fsync result.
        parentFd = openRoot();
        unlinkSync(path);
        fsyncSync(parentFd);
      } catch {
        closed = true;
        throw new Error('desktop claim release unconfirmed; reconcile before reuse');
      } finally { if (parentFd !== undefined) closeSync(parentFd); }
    },
  };
}

export type ExclusiveDesktopRunner = Pick<AuthenticatedRunner, 'perform' | 'finish' | 'requestCancellation'>;

/**
 * Trusted construction hook only: build must perform no OS work and must gate its final adapter
 * call with assertHeld. The concrete physical factory supplies that wiring. No inner runner or
 * release capability is returned. Acquire before reader startup, not after it has begun.
 */
export function createExclusiveDesktopRunner(
  options: DesktopClaimOptions,
  build: (assertHeld: () => void) => AuthenticatedRunner,
): ExclusiveDesktopRunner {
  let claim: ReturnType<typeof claimDesktop> | undefined;
  let state: 'ACTIVE' | 'FINISHING' | 'CLOSED' = 'ACTIVE';
  let busy = false;
  let runner: AuthenticatedRunner | undefined;
  const cancel = () => { state = 'CLOSED'; runner?.requestCancellation(); };
  const guard = () => {
    try {
      if (state !== 'ACTIVE' || claim === undefined) throw new Error('desktop not claimed');
      claim.assertHeld();
    } catch (error) { cancel(); throw error; }
  };
  runner = build(guard); // Validate the inert runtime before leaving a durable claim.
  claim = claimDesktop(options);
  const inner = runner;
  return Object.freeze({
    requestCancellation: cancel,
    async perform(request: Parameters<AuthenticatedRunner['perform']>[0]) {
      if (state !== 'ACTIVE' || busy) return { status: 'REFUSED' as const, detail: 'desktop runner fenced or busy' };
      busy = true;
      try {
        guard();
        const result = await inner.perform(request);
        if (result.status === 'AMBIGUOUS') cancel();
        return result;
      } catch {
        cancel();
        return { status: 'REFUSED' as const, detail: 'desktop claim unavailable; no automatic retry' };
      } finally { busy = false; }
    },
    async finish() {
      if (state !== 'ACTIVE' || busy) throw new Error('desktop runner cannot finish');
      guard();
      state = 'FINISHING';
      try {
        // Inner finish checks successful STOP and exact durable journal before server ACK.
        const receipt = await inner.finish();
        // Cancellation racing the remote ACK must retain the claim for reconciliation.
        if ((state as string) !== 'FINISHING') throw new Error('desktop finish cancelled');
        claim.assertHeld();
        claim.release();
        return receipt;
      } finally { cancel(); }
    },
  });
}
