/** Session-bound physical preflight assembly; this neither grants permissions nor starts a reader. */
import { createHostEnvironment, runPreflight, type PreflightReport, type ProbeEnvironment,
  type RuntimeProbeEvidence, type ProbeResult } from '@accessforge/at-voiceover';
import type { Clock } from './supervisor.js';
import { createSafariAuthenticatedRunner } from './safari-origin.js';

export interface PhysicalPreflightOptions {
  /** The assigned dedicated audit session, provisioned independently of observed current state. */
  readonly expectedDesktopSessionId: string;
  /** Same clock instance as the action supervisor. */
  readonly clock: Pick<Clock, 'monotonic'>;
  /** Read owned setup/capture/deployment evidence. Must not reset, type or start a reader. */
  readonly observeRuntimeEvidence: (signal: AbortSignal) => Promise<Omit<RuntimeProbeEvidence,
    'expectedDesktopSessionId' | 'monotonicClockHealthy'>>;
  readonly maxProbeDurationMs?: number;
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
        options.observeRuntimeEvidence(controller.signal),
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
  },
): ReturnType<typeof createSafariAuthenticatedRunner> {
  const { physicalPreflight, ...runtime } = options;
  return createSafariAuthenticatedRunner({ ...runtime,
    preflight: createPhysicalPreflight({ ...physicalPreflight, clock: runtime.clock, environment: createHostEnvironment() }),
  });
}
