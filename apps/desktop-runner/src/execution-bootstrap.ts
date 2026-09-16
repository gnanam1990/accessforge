/** Trusted controller embedding: claim -> one-shot machine session -> reader initialization. */
import type { ExecutionSessionPort } from './authenticated-runner.js';
import { NativeExecutionSession, parseReference, parseDispatchEnvelope, type ReceiverConfig } from './dispatch-receiver.js';
import { createGuidepupPhysicalSafariRunner } from './physical-preflight.js';
import type { ExclusiveDesktopRunner } from './desktop-claim.js';
import { parseReaderStartupConsentScope, type ReaderStartupConsentScope } from './reader-startup-consent.js';
import { readStartupConsentReference } from './startup-provisioning.js';
import { startNavigatorActionBridge } from './navigator-action-bridge.js';
import { startOwnedNavigatorExecution, type NavigatorProcessOptions } from './navigator-process.js';
import { createObserverProcessClosure, type ObserverProcessOptions } from './observer-process.js';
import { FileJournal } from './journal.js';
import { createReferenceEffectProcess, type ReferenceEffectProcessOptions } from './reference-effect-process.js';
import { createEffectStartupAuthorization } from './effect-startup-authority.js';

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

/** Same private provisioned bootstrap, with one bounded native action port for the planner.
 * Does not invoke a provider or feed unretained observations to the model. The controller still
 * owns independent observer closure and explicit finish; the verified reader matrix is unchanged.
 */
export async function startProvisionedNavigatorExecution(options: Omit<ExecutionBootstrapOptions, 'readerStartupConsent'> & {
  readonly readerStartupConsentPath: string;
  readonly navigatorBridgeDirectory: string;
}): ReturnType<typeof startNavigatorActionBridge> {
  const { navigatorBridgeDirectory, ...runtime } = options;
  const runner = createProvisionedExecutionBootstrap(runtime);
  return startNavigatorActionBridge({ runner, privateDirectory: navigatorBridgeDirectory,
    reference: runtime.receiver.localReference, deadlineMonotonic: runtime.lease.deadlineMonotonic,
    maxActions: runtime.lease.maxActions });
}

/** Explicit trusted host entry: existing physical bootstrap -> private child -> verified finish.
 * The production capability matrix and every reader-startup/effect approval remain unchanged.
 */
export async function runProvisionedNavigatorExecution(
  bootstrap: Parameters<typeof startProvisionedNavigatorExecution>[0],
  navigator: NavigatorProcessOptions | (Omit<NavigatorProcessOptions, 'closeIndependentObserver'> & {
    readonly independentObserver: ObserverProcessOptions;
    readonly independentEffectObserver?: ReferenceEffectProcessOptions;
  }),
): Promise<Readonly<Record<string, unknown>>> {
  if (JSON.stringify(parseReference(bootstrap.receiver.localReference)) !==
      JSON.stringify(parseReference(navigator.reference)) ||
      navigator.deadlineMonotonic > bootstrap.lease.deadlineMonotonic) {
    throw new Error('navigator process does not match its native bootstrap lease');
  }
  if ('independentObserver' in navigator) {
    if ('closeIndependentObserver' in navigator) throw new Error('ambiguous observer ownership');
    const { independentObserver, independentEffectObserver, ...planner } = navigator;
    const closeIndependentObserver = createObserverProcessClosure(independentObserver,
      planner.reference, planner.deadlineMonotonic);
    if (independentEffectObserver !== undefined) {
      if (independentEffectObserver.credentialRef !== independentObserver.credentialRef ||
          independentEffectObserver.environment.ACCESSFORGE_DATABASE_URL !== independentObserver.environment.ACCESSFORGE_DATABASE_URL ||
          independentEffectObserver.environment.ACCESSFORGE_OBSERVER_DATABASE_URL !== independentObserver.environment.ACCESSFORGE_OBSERVER_DATABASE_URL) {
        throw new Error('effect and completion observer configurations differ');
      }
      const lifetime = new AbortController();
      const cancel = () => lifetime.abort();
      const effect = createReferenceEffectProcess(independentEffectObserver, planner.reference, planner.deadlineMonotonic);
      planner.signal.addEventListener('abort', cancel, { once: true });
      if (planner.signal.aborted) cancel();
      // A failed worker fences the navigator/native bridge while the execution is still active.
      void effect.failure.catch(cancel);
      const observedBootstrap = { ...bootstrap,
        readerStartup: { ...bootstrap.readerStartup,
          // Machine session opens first. Fresh consent may repeat; collector startup cannot.
          authorize: createEffectStartupAuthorization(
            signal => bootstrap.readerStartup.authorize(signal), effect, lifetime.signal),
        },
        async authorizePhysicalAction(command: Parameters<typeof bootstrap.authorizePhysicalAction>[0], signal: AbortSignal) {
          signal.throwIfAborted();
          effect.assertActive();
          await bootstrap.authorizePhysicalAction(command, signal);
          signal.throwIfAborted();
          effect.assertActive();
        },
      };
      try {
        return await Promise.race([
          startOwnedNavigatorExecution(() => startProvisionedNavigatorExecution(observedBootstrap), {
            ...planner, signal: lifetime.signal,
            async closeIndependentObserver(signal: AbortSignal) {
              await effect.finish(); // Original STOP is independently checked inside the worker.
              await closeIndependentObserver(signal); // Ordinary final sample/producer closure follows.
            },
          }),
          effect.failure,
        ]);
      } finally {
        planner.signal.removeEventListener('abort', cancel);
        lifetime.abort();
        effect.abort();
      }
    }
    return startOwnedNavigatorExecution(() => startProvisionedNavigatorExecution(bootstrap),
      { ...planner, closeIndependentObserver });
  }
  if ('independentEffectObserver' in navigator) throw new Error('effect observer requires independent completion ownership');
  return startOwnedNavigatorExecution(() => startProvisionedNavigatorExecution(bootstrap), navigator);
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
    retainObservation: (command, observation, capturedAt, keyboardFocus) => requireMachine().retainObservation(command, observation, capturedAt, keyboardFocus),
    retainRuntimePreflight: (command, report, capturedAt) => requireMachine().retainRuntimePreflight(command, report, capturedAt),
    authorizeCandidateFormEffect: (command) => requireMachine().authorizeCandidateFormEffect(command),
    finish: () => requireMachine().finish(),
  };
  return createGuidepupPhysicalSafariRunner({ ...runtime, session,
    physicalPreflight: {
      ...runtime.physicalPreflight,
      async observeRuntimeEvidence(signal) {
        const evidence = await runtime.physicalPreflight.observeRuntimeEvidence(signal);
        if (signal.aborted) throw new Error('runtime evidence cancelled');
        // Production never trusts a configuration boolean for actual journal durability.
        // Test/embedding memory journals cannot establish the physical runtime gate.
        const journalWritable = runtime.journal instanceof FileJournal &&
          await runtime.journal.probeWritable();
        if (signal.aborted) throw new Error('runtime evidence cancelled');
        return { ...evidence, journalWritable };
      },
    }, readerStartup: {
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
