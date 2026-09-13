/** Trusted controller embedding: claim -> one-shot machine session -> reader initialization. */
import type { ExecutionSessionPort } from './authenticated-runner.js';
import { NativeExecutionSession, parseReference, parseDispatchEnvelope, type ReceiverConfig } from './dispatch-receiver.js';
import { createGuidepupPhysicalSafariRunner } from './physical-preflight.js';
import type { ExclusiveDesktopRunner } from './desktop-claim.js';
import { parseReaderStartupConsentScope, type ReaderStartupConsentScope } from './reader-startup-consent.js';
import { readStartupConsentReference } from './startup-provisioning.js';

export type ExecutionBootstrapOptions = Omit<Parameters<typeof createGuidepupPhysicalSafariRunner>[0], 'session'> & {
  /** Independently provisioned private host configuration, never navigator fields. */
  readonly receiver: ReceiverConfig;
  /** Controller-delivered one-shot envelope. Neither it nor session credentials are returned. */
  readonly dispatchEnvelope: unknown;
  /** Exact grant and sealed identities from private operator provisioning, never navigator input. */
  readonly readerStartupConsent: ReaderStartupConsentScope;
};

/** Operator CLI export -> private host reference -> existing exact authenticated startup path.
 * Loading the file neither creates consent nor bypasses the production profile/physical gates.
 */
export function createProvisionedExecutionBootstrap(options: Omit<ExecutionBootstrapOptions, 'readerStartupConsent'> & {
  readonly readerStartupConsentPath: string;
}): ExclusiveDesktopRunner {
  const { readerStartupConsentPath, ...runtime } = options;
  const readerStartupConsent = readStartupConsentReference(readerStartupConsentPath, runtime.receiver.localReference);
  return createExecutionBootstrap({ ...runtime, readerStartupConsent });
}

/**
 * Construction claims the desktop and validates inert runner configuration. initialize() then
 * checks profile eligibility, opens the machine session exactly once under the claim, and performs
 * authorized reader startup with fresh physical probes. Failure retains both local claim types;
 * recovery must reconcile, never replay this envelope. No default authority/evidence callbacks.
 */
export function createExecutionBootstrap(options: ExecutionBootstrapOptions): ExclusiveDesktopRunner {
  const { receiver, dispatchEnvelope, readerStartup, readerStartupConsent, ...runtime } = options;
  const consent = parseReaderStartupConsentScope(readerStartupConsent);
  const reference = Object.freeze(parseReference(receiver.localReference));
  const config = Object.freeze({ ...receiver, localReference: reference });
  const envelope = parseDispatchEnvelope(dispatchEnvelope);
  if (JSON.stringify(envelope.reference) !== JSON.stringify(reference)) throw new Error('bootstrap reference differs');
  if (!Number.isFinite(runtime.lease.deadlineMonotonic)) throw new Error('bootstrap lease deadline unavailable');
  let machine: NativeExecutionSession | undefined;
  let opening = false;
  let deadline: number | undefined;
  let lastClock: number | undefined;
  const requireMachine = (): NativeExecutionSession => {
    if (machine === undefined) throw new Error('execution session has not been initialized');
    return machine;
  };
  const session: ExecutionSessionPort = {
    receipt: Object.freeze({ reference }),
    retainIntent: (command) => requireMachine().retainIntent(command),
    commitDispatch: (actionId, origin) => requireMachine().commitDispatch(actionId, origin),
    completeAction: (actionId, status) => requireMachine().completeAction(actionId, status),
    retainObservation: (command, observation, capturedAt) => requireMachine().retainObservation(command, observation, capturedAt),
    retainRuntimePreflight: (command, report, capturedAt) => requireMachine().retainRuntimePreflight(command, report, capturedAt),
    authorizeCandidateFormEffect: (command) => requireMachine().authorizeCandidateFormEffect(command),
    finish: () => requireMachine().finish(),
  };
  return createGuidepupPhysicalSafariRunner({ ...runtime, session, readerStartup: {
    timeoutMs: readerStartup.timeoutMs,
    async authorize(signal) {
      if (signal.aborted) throw new Error('execution startup cancelled');
      if (machine === undefined) {
        if (opening) throw new Error('execution session cannot be replayed');
        opening = true;
        const before = runtime.clock.monotonic();
        const wall = Date.now();
        if (!Number.isFinite(before) || before < 0) throw new Error('startup clock unavailable');
        lastClock = before;
        machine = await NativeExecutionSession.open(config, envelope);
        // Count the entire handshake against the returned expiry, rather than granting a new
        // session lifetime when a slow response eventually arrives. Actions retain server gates.
        const remaining = Date.parse(String(machine.receipt.expiresAt)) - wall;
        if (!Number.isFinite(remaining) || remaining <= 0) throw new Error('startup session expiry unavailable');
        deadline = Math.min(runtime.lease.deadlineMonotonic, before + Math.min(1800000, remaining));
      }
      const check = () => {
        const now = runtime.clock.monotonic();
        if (signal.aborted || deadline === undefined || !Number.isFinite(now) || now < 0 ||
            lastClock === undefined || now < lastClock || now >= deadline) {
          throw new Error('execution startup authority expired');
        }
        lastClock = now;
      };
      check();
      await readerStartup.authorize(signal);
      check();
      // The server authenticates live run/lease authority and the separate immutable operator
      // grant together. Recheck last so a slow host callback cannot cache an earlier decision.
      await requireMachine().checkReaderStartupConsent(consent, signal);
      check();
    },
  } });
}
