/** Session-bound physical preflight assembly; this neither grants permissions nor starts a reader. */
import { createHostEnvironment, runPreflight, type PreflightReport, type ProbeEnvironment,
  type RuntimeProbeEvidence, type ProbeResult, PREFLIGHT_CHECKS, probeReaderControlConfigured,
  assertRealReaderProven, createGuidepupVoiceOverAdapter, type VoiceOverAdapter } from '@accessforge/at-voiceover';
import type { Clock } from './supervisor.js';
import { createSafariAuthenticatedRunner, createSafariOriginProbe } from './safari-origin.js';
import { createExclusiveDesktopRunner, type ExclusiveDesktopRunner } from './desktop-claim.js';
import { parseReference } from './dispatch-receiver.js';
import { createArtifactProbe, type ArtifactProbeOptions } from './artifact-probe.js';
import { createVoiceOverCaptureProbe } from './capture-probe.js';
import { createReferencePreparation, type ReferencePreparationOptions } from './reference-preparation.js';

export interface PhysicalPreflightOptions {
  /** The assigned dedicated audit session, provisioned independently of observed current state. */
  readonly expectedDesktopSessionId: string;
  /** Same clock instance as the action supervisor. */
  readonly clock: Pick<Clock, 'monotonic'>;
  /** Read owned setup/capture/deployment evidence. Must not reset, type or start a reader. */
  readonly observeRuntimeEvidence: (signal: AbortSignal) => Promise<Omit<RuntimeProbeEvidence,
    'expectedDesktopSessionId' | 'monotonicClockHealthy'>>;
  readonly maxProbeDurationMs?: number;
  /** Optional concrete live candidate measurement. Configured failures never use callback digests. */
  readonly artifactProbe?: ArtifactProbeOptions;
  /** Read-only host port; production defaults to real macOS probes. */
  readonly environment?: ProbeEnvironment;
}

const refused = (condition: 'FALSE' | 'UNKNOWN', detail: string): ProbeResult =>
  ({ condition, detail, requiresOperator: false });

export function createPhysicalPreflight(options: PhysicalPreflightOptions): () => Promise<PreflightReport> {
  const assigned = options.expectedDesktopSessionId;
  const limit = options.maxProbeDurationMs ?? 30000;
  if (!/^[1-9][0-9]*$/.test(assigned) || Number(assigned) >= 4294967295 ||
      !Number.isFinite(limit) || limit <= 0 || limit > 30000) throw new Error('physical preflight configuration unavailable');
  const environment = options.environment ?? createHostEnvironment();
  const artifactProbe = options.artifactProbe === undefined ? undefined : createArtifactProbe(options.artifactProbe);
  let last: number | undefined;
  let busy = false;
  let fenced = false;
  return async () => {
    if (busy || fenced) { fenced = true; throw new Error('physical preflight fenced'); }
    busy = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const start = options.clock.monotonic();
      const startSession = environment.auditSessionId();
      const runtime = await Promise.race([
        (async () => {
          const runtime = await options.observeRuntimeEvidence(controller.signal);
          if (controller.signal.aborted) throw new Error('physical evidence cancelled');
          // The concrete measurement wins over any historical setup digest in the runtime port.
          return artifactProbe === undefined ? runtime : { ...runtime, ...await artifactProbe(controller.signal) };
        })(),
        new Promise<never>((_resolve, reject) => {
          timer = setTimeout(() => { controller.abort(); reject(new Error('physical evidence deadline elapsed')); }, limit);
        }),
      ]);
      if (fenced) throw new Error('physical preflight fenced');
      const report = runPreflight(environment, {
        ...runtime, expectedDesktopSessionId: assigned, monotonicClockHealthy: false,
      });
      const endSession = environment.auditSessionId();
      const end = options.clock.monotonic();
      const healthy = Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start &&
        (last === undefined || start >= last);
      if (healthy && end - start > limit) throw new Error('physical probe became stale before completion');
      last = end;
      const checks = { ...report.checks,
        MONOTONIC_CLOCK_HEALTHY: healthy
          ? { condition: 'TRUE' as const, detail: '', requiresOperator: false }
          : refused('FALSE', 'the supervisor clock did not advance consistently across the physical probe'),
      };
      if (startSession === undefined || endSession === undefined) {
        checks.DESKTOP_SESSION_OWNED = refused('UNKNOWN', 'the assigned desktop could not be observed across the physical probe');
      } else if (startSession !== assigned || endSession !== assigned) {
        checks.DESKTOP_SESSION_OWNED = refused('FALSE', 'the active desktop changed or differs from the assigned session');
      }
      if (!healthy || checks.DESKTOP_SESSION_OWNED.condition !== 'TRUE') fenced = true;
      return { ...report, checks };
    } catch {
      fenced = true;
      throw new Error('physical preflight unavailable; no automatic reset or permission grant');
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      controller.abort();
      busy = false;
    }
  };
}

/** Combines concrete host/session preflight and native Safari origin while preserving effect gates. */
export function createPhysicalSafariRunner(options:
  Omit<Parameters<typeof createSafariAuthenticatedRunner>[0], 'preflight'> & {
    readonly physicalPreflight: Omit<PhysicalPreflightOptions, 'clock' | 'environment'>;
    /** One shared private host root across ALL runner registrations, not a per-run directory. */
    readonly desktopClaimDirectory: string;
    /** Optional already-reserved reference fixture; prepared under this same startup claim. */
    readonly referencePreparation?: ReferencePreparationOptions;
    readonly adapter: Parameters<typeof createSafariAuthenticatedRunner>[0]['adapter'] & { start(): Promise<void> };
    readonly readerStartup: {
      /** Fresh trusted controller authorization for SDK stop/restart and preference mounting. */
      readonly authorize: (signal: AbortSignal) => Promise<void>;
      readonly timeoutMs: number;
    };
  },
): ExclusiveDesktopRunner {
  // Known unsupported configuration is refused before reserving a desktop or loading the SDK.
  // First-profile proof remains a separate explicitly authorized candidate workflow.
  assertRealReaderProven();
  const { physicalPreflight, desktopClaimDirectory, readerStartup, referencePreparation, ...runtime } = options;
  const environment = createHostEnvironment();
  const prepareReference = referencePreparation === undefined ? undefined
    : createReferencePreparation(referencePreparation, runtime.safari, physicalPreflight.artifactProbe,
      { expectedSessionId: physicalPreflight.expectedDesktopSessionId, environment });
  let prepared: RuntimeProbeEvidence = {};
  const preflight = createPhysicalPreflight({ ...physicalPreflight, clock: runtime.clock, environment,
    async observeRuntimeEvidence(signal) {
      // A fresh negative/unknown runtime observation must never be overwritten by setup history.
      return { ...prepared, ...await physicalPreflight.observeRuntimeEvidence(signal) };
    },
  });
  const startupOrigin = createSafariOriginProbe(runtime.safari);
  return createExclusiveDesktopRunner({ directory: desktopClaimDirectory,
    desktopSessionId: physicalPreflight.expectedDesktopSessionId,
    reference: parseReference(runtime.session.receipt.reference),
  }, (assertHeld) => createSafariAuthenticatedRunner({ ...runtime,
    preflight,
    authorizePhysicalAction: async (command, signal) => {
      signal.throwIfAborted();
      assertHeld();
      await runtime.authorizePhysicalAction(command, signal);
      signal.throwIfAborted();
      assertHeld();
    },
    adapter: { perform(request, context) {
      assertHeld(); // Synchronous final guard immediately before entering the adapter.
      return runtime.adapter.perform(request, context);
    } },
  }), { timeoutMs: readerStartup.timeoutMs, clock: runtime.clock,
    deadlineMonotonic: runtime.lease.deadlineMonotonic, run: async (guard, signal) => {
    guard();
    await readerStartup.authorize(signal);
    guard();
    if (probeReaderControlConfigured(environment).condition !== 'TRUE') throw new Error('reader control not configured');
    if (prepareReference !== undefined) {
      prepared = await prepareReference(guard, signal);
      guard();
    }
    const before = await preflight();
    // Only activation and speech capture may be unavailable before starting the reader. Every
    // ownership, permission, build, reset, journal and input-source gate is still required.
    if (PREFLIGHT_CHECKS.some((key) => key !== 'READER_ACTIVE' && key !== 'SPEECH_CAPTURE_WORKING' &&
        before.checks[key].condition !== 'TRUE')) throw new Error('reader startup preflight unavailable');
    guard();
    await startupOrigin();
    guard();
    // Consent may expire while native probes run. Reauthorize immediately before SDK startup.
    await readerStartup.authorize(signal);
    guard();
    await runtime.adapter.start();
    guard();
    const after = await preflight();
    if (PREFLIGHT_CHECKS.some((key) => after.checks[key].condition !== 'TRUE')) throw new Error('reader readiness unavailable');
    await startupOrigin();
    guard();
    // The SDK and postflight may consume the remaining session/consent lifetime. Do not publish
    // ACTIVE merely because local physical observations passed after that authority expired.
    await readerStartup.authorize(signal);
    guard();
  } });
}

/** Concrete lazy driver wiring. Construction neither imports Guidepup nor touches the reader. */
export function createGuidepupPhysicalSafariRunner(
  options: Omit<Parameters<typeof createPhysicalSafariRunner>[0], 'adapter'>,
): ExclusiveDesktopRunner {
  let adapter: VoiceOverAdapter | undefined;
  const capture = createVoiceOverCaptureProbe();
  return createPhysicalSafariRunner({ ...options,
    physicalPreflight: {...options.physicalPreflight,
      async observeRuntimeEvidence(signal) {
        const evidence = await options.physicalPreflight.observeRuntimeEvidence(signal);
        if (signal.aborted) throw new Error('capture preflight cancelled');
        const speechCaptureWorking = await capture(signal);
        if (signal.aborted) throw new Error('capture preflight cancelled');
        return {...evidence, speechCaptureWorking};
      },
    }, adapter: {
    async start() {
      if (adapter !== undefined) throw new Error('reader initialization cannot be retried');
      adapter = createGuidepupVoiceOverAdapter({ monotonicNow: () => options.clock.monotonic() });
      await adapter.start();
    },
    async perform(request, context) {
      if (adapter === undefined) throw new Error('reader not initialized');
      return adapter.perform(request, context);
    },
  } });
}
