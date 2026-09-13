/** Private controller transport into the existing authenticated desktop runner, never raw OS input. */
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { chmodSync, lstatSync, mkdtempSync, realpathSync, rmdirSync, type Stats } from 'node:fs';
import { createServer, type Socket } from 'node:net';
import { isAbsolute, join, resolve } from 'node:path';
import type { ExclusiveDesktopRunner } from './desktop-claim.js';
import { parseReference, type DispatchReference } from './dispatch-receiver.js';
import { ALLOWED_ACTIONS, type AllowedAction } from './supervisor.js';

export const NAVIGATOR_BRIDGE_PROTOCOL = 'accessforge.navigator-action.v1';
const owners = new WeakSet<ExclusiveDesktopRunner>();
const hex = (value: unknown, length: number): value is string =>
  typeof value === 'string' && value.length === length && /^[a-f0-9]+$/.test(value);
const uuid = (value: unknown): value is string => typeof value === 'string' &&
  /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('shape');
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: readonly string[]): void {
  if (Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))) throw new Error('shape');
}
function privateDirectory(path: string): Stats {
  if (typeof process.getuid !== 'function' || !isAbsolute(path) || realpathSync(path) !== resolve(path)) throw new Error('path');
  const info = lstatSync(path);
  if (!info.isDirectory() || info.uid !== process.getuid() || (info.mode & 0o077) !== 0) throw new Error('private directory');
  return info;
}

export interface NavigatorActionBridgeOptions {
  readonly privateDirectory: string;
  /** Fresh, not previously initialized or used, from createExecutionBootstrap. */
  readonly runner: ExclusiveDesktopRunner;
  readonly reference: DispatchReference;
  /** Trusted host bound on the same monotonic clock as the runner's lease. */
  readonly deadlineMonotonic: number;
  readonly maxActions: number;
}

/** Startup is explicit and retains all bootstrap/profile/operator-consent gates. */
export async function startNavigatorActionBridge(options: NavigatorActionBridgeOptions): Promise<{
  privateReference(): Readonly<Record<string, unknown>>;
  /** Called by trusted controller only, after its independent final observer closure. */
  finish(): Promise<Readonly<Record<string, unknown>>>;
  /** Fences input; never synthesizes STOP, acknowledges cleanup, or releases the desktop claim. */
  close(): Promise<void>;
}> {
  const reference = Object.freeze(parseReference(options.reference));
  const runner = options.runner;
  const deadline = options.deadlineMonotonic;
  const maxActions = options.maxActions, parentPath = options.privateDirectory;
  if (JSON.stringify(parseReference(runner.reference)) !== JSON.stringify(reference) ||
      !Number.isSafeInteger(maxActions) || maxActions < 1 || maxActions > 500 ||
      !Number.isFinite(deadline) || deadline <= performance.now() || deadline - performance.now() > 1800000 ||
      owners.has(runner)) throw new Error('navigator bridge configuration unavailable');
  const parent = privateDirectory(parentPath);
  owners.add(runner); // An uncertain startup is never reusable with another bridge.
  const directory = mkdtempSync(join(parentPath, 'nav-'));
  const directoryIdentity = privateDirectory(directory);
  const path = join(directory, 'a.sock');
  const token = randomBytes(32).toString('hex');
  let closed = false, stopped = false, busy = false, finishing = false, next = 1, requests = 0;
  let lastClock = performance.now();
  let socketIdentity: Stats | undefined;
  const sockets = new Set<Socket>();
  const fence = () => { closed = true; runner.requestCancellation(); };
  const guard = () => {
    const now = performance.now();
    const current = privateDirectory(parentPath);
    const root = privateDirectory(directory);
    if (closed || stopped || now < lastClock || now >= deadline || current.dev !== parent.dev ||
        current.ino !== parent.ino || root.dev !== directoryIdentity.dev ||
        root.ino !== directoryIdentity.ino) throw new Error('bridge fenced');
    lastClock = now;
    if (socketIdentity !== undefined) {
      const info = lstatSync(path);
      if (!info.isSocket() || info.dev !== socketIdentity.dev || info.ino !== socketIdentity.ino ||
          info.uid !== parent.uid || (info.mode & 0o077) !== 0) throw new Error('socket changed');
    }
  };
  const server = createServer((socket) => {
    sockets.add(socket);
    socket.on('error', () => {});
    let entered = false, replied = false;
    const timeout = setTimeout(() => socket.destroy(), 32000);
    socket.once('close', () => {
      clearTimeout(timeout); sockets.delete(socket);
      if (entered && !replied) fence(); // The operation may have happened; never accept a repeat.
    });
    if (++requests > 1000 || sockets.size > 4 || closed || stopped) { socket.destroy(); return; }
    let data = Buffer.alloc(0), handled = false;
    socket.on('data', (chunk: Buffer) => {
      if (handled) { socket.destroy(); return; }
      data = Buffer.concat([data, chunk]);
      if (data.length > 4096) { socket.destroy(); return; }
      if (!data.includes(10)) return;
      handled = true;
      void (async () => {
        let claimed = false;
        try {
          if (data.at(-1) !== 10 || data.subarray(0, -1).includes(10)) throw new Error('framing');
          const request = object(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(data)));
          if (!hex(request.token, 64) || !timingSafeEqual(Buffer.from(request.token), Buffer.from(token))) throw new Error('capability');
          entered = true;
          guard();
          exact(request, ['protocol', 'token', 'reference', 'requestId', 'sequence', 'command']);
          if (request.protocol !== NAVIGATOR_BRIDGE_PROTOCOL || !hex(request.requestId, 32) ||
              JSON.stringify(parseReference(request.reference)) !== JSON.stringify(reference) ||
              request.sequence !== next || next > maxActions || busy) throw new Error('request binding');
          const command = object(request.command);
          if (typeof command.action !== 'string' || !ALLOWED_ACTIONS.includes(command.action as AllowedAction)) throw new Error('action');
          if (command.action === 'TYPE_TEXT') {
            exact(command, ['action', 'textValueRef']);
            if (typeof command.textValueRef !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(command.textValueRef)) throw new Error('fixture ref');
          } else if (command.action === 'KEY_CHORD') {
            exact(command, ['action', 'keyChord']);
            if (typeof command.keyChord !== 'string' || !command.keyChord || command.keyChord.length > 80) throw new Error('chord');
          } else exact(command, ['action']);
          busy = true; claimed = true; next += 1; // Consume before any awaited dispatch.
          const result = await runner.perform(command as Parameters<ExclusiveDesktopRunner['perform']>[0]);
          guard();
          if (!['SUCCEEDED', 'FAILED', 'AMBIGUOUS', 'REFUSED'].includes(result.status) ||
              (result.status !== 'REFUSED' && !uuid(result.serverActionId))) throw new Error('result identity');
          if (result.status === 'SUCCEEDED' && command.action === 'STOP') stopped = true;
          else if (result.status === 'AMBIGUOUS' || result.status === 'REFUSED' || command.action === 'STOP') fence();
          const response = JSON.stringify({ protocol: NAVIGATOR_BRIDGE_PROTOCOL, requestId: request.requestId,
            sequence: request.sequence, reference, status: result.status, actionId: result.serverActionId ?? null });
          // No raw error/observation/credential data crosses the action port.
          socket.end(response + '\n', (error?: Error | null) => {
            if (error) fence();
            else replied = true;
          });
        } catch { if (entered) fence(); socket.destroy(); }
        finally { if (claimed) busy = false; }
      })();
    });
  });
  let expiry: ReturnType<typeof setTimeout> | undefined;
  const stopListening = async () => {
    if (expiry !== undefined) clearTimeout(expiry);
    for (const socket of sockets) socket.destroy();
    if (server.listening) {
      try {
        const root = privateDirectory(directory), info = lstatSync(path);
        if (root.dev !== directoryIdentity.dev || root.ino !== directoryIdentity.ino ||
            socketIdentity === undefined || info.dev !== socketIdentity.dev || info.ino !== socketIdentity.ino) throw new Error('changed path');
      } catch {
        server.unref(); // Do not let automatic socket unlink target a replacement path.
        throw new Error('navigator bridge cleanup unconfirmed; private path changed');
      }
    }
    if (server.listening) await new Promise<void>((done) => server.close(() => done()));
    // Never recursively delete a replaced directory or an unresolved socket path.
    try {
      const root = privateDirectory(directory);
      if (root.dev === directoryIdentity.dev && root.ino === directoryIdentity.ino) rmdirSync(directory);
    } catch { /* Leave unexpected contents for operator inspection. */ }
  };
  server.on('error', () => fence());
  try {
    if (Buffer.byteLength(path) > 100) throw new Error('socket path too long');
    guard();
    await runner.initialize();
    guard();
    await new Promise<void>((done, reject) => {
      server.once('error', reject);
      server.listen(path, () => { server.removeListener('error', reject); done(); });
    });
    socketIdentity = lstatSync(path); chmodSync(path, 0o600); guard();
    expiry = setTimeout(() => { fence(); void stopListening().catch(() => {}); }, Math.max(1, deadline - performance.now()));
  } catch {
    fence(); await stopListening();
    throw new Error('navigator bridge startup unavailable; reconcile desktop before retry');
  }
  return Object.freeze({
    privateReference() { guard(); return Object.freeze({ protocol: NAVIGATOR_BRIDGE_PROTOCOL, socketPath: path, token, reference }); },
    async finish() {
      if (closed || !stopped || busy || finishing) throw new Error('navigator bridge cannot finish');
      finishing = true;
      try {
        // The peer may have read STOP before Node emits this side's socket close event.
        // Drain actual transport closure (bounded by each connection's timer), not a sleep.
        // The lease timer and lost-reply fence remain active throughout this wait.
        await Promise.all([...sockets].map((socket) => new Promise<void>((done) => socket.once('close', () => done()))));
        if (closed || busy) throw new Error('navigator bridge finish interrupted');
        closed = true;
        return await runner.finish();
      }
      finally { await stopListening(); }
    },
    async close() { fence(); await stopListening(); },
  });
}
