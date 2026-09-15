/** Explicit first-profile qualification. Never enrolls a matrix or emits canonical evidence. */
import { randomUUID } from 'node:crypto';
import { lstatSync, mkdirSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { createGuidepupVoiceOverAdapter, createHostEnvironment, probeReaderControlConfigured,
  type ActionRequest, type VoiceOverAdapter } from '@accessforge/at-voiceover';
import { CandidateProofRunner, type CandidateProofResult } from './candidate-proof.js';
import { CandidateTraceWriter, FileCandidateTraceSink } from './candidate-trace.js';
import { claimDesktop, type DesktopClaimOptions } from './desktop-claim.js';
import { FileJournal } from './journal.js';
import { loadPrivateOperatorModule } from './native-host.js';
import { createPhysicalPreflight, type PhysicalPreflightOptions } from './physical-preflight.js';
import { createSafariOriginProbe, type SafariOriginOptions } from './safari-origin.js';
import { createVoiceOverCaptureProbe } from './capture-probe.js';

export interface CandidateHostConfiguration {
  readonly desktop: DesktopClaimOptions;
  readonly physicalPreflight: Omit<PhysicalPreflightOptions, 'clock' | 'environment'>;
  readonly safari: SafariOriginOptions;
  readonly actions: readonly ActionRequest[];
  readonly approvedTextValues: readonly string[];
  readonly maxDurationSeconds: number;
  readonly actionTimeoutMs: number;
  /** Trusted live approval, not a command-line flag substituted for current authority. */
  readonly authorizeStartup: (signal: AbortSignal) => Promise<void>;
  readonly authorizeAction: (request: ActionRequest, signal: AbortSignal) => Promise<void>;
}

export async function runCandidateHost(modulePath: string, outputDirectory: string,
  signal: AbortSignal): Promise<CandidateProofResult> {
  if (signal.aborted) throw new Error('cancelled');
  // Unlike production --native-host, this explicitly labelled qualification path can operate
  // before matrix enrollment. It still cannot enable an OS permission or grant itself consent.
  const module = await loadPrivateOperatorModule(modulePath);
  if (signal.aborted || module === null || typeof module !== 'object' ||
      !('provisionCandidateProof' in module) || typeof module.provisionCandidateProof !== 'function') {
    throw new Error('candidate provisioner unavailable');
  }
  const config = await module.provisionCandidateProof(signal) as CandidateHostConfiguration;
  if (signal.aborted || !config || typeof config.authorizeStartup !== 'function' ||
      typeof config.authorizeAction !== 'function' || !Array.isArray(config.actions) ||
      config.actions.length < 1 || config.actions.length > 100 ||
      !Array.isArray(config.approvedTextValues) || config.approvedTextValues.some(v => typeof v !== 'string') ||
      !Number.isFinite(config.maxDurationSeconds) || config.maxDurationSeconds <= 0 || config.maxDurationSeconds > 1800 ||
      !Number.isFinite(config.actionTimeoutMs) || config.actionTimeoutMs <= 0 || config.actionTimeoutMs > 30000 ||
      config.desktop.desktopSessionId !== config.physicalPreflight.expectedDesktopSessionId ||
      config.physicalPreflight.artifactProbe === undefined) throw new Error('candidate configuration unavailable');
  const actions = structuredClone(config.actions);
  const approvedTextValues = new Set(config.approvedTextValues);
  const parent = dirname(outputDirectory);
  if (!isAbsolute(outputDirectory) || realpathSync(parent) !== resolve(parent)) throw new Error('private output parent required');
  const info = lstatSync(parent);
  if (!info.isDirectory() || info.uid !== process.getuid?.() || (info.mode & 0o077) !== 0) throw new Error('private output parent required');
  mkdirSync(outputDirectory, { mode: 0o700 }); // Exclusive new attempt directory, never replay.
  const clock = { monotonic: () => performance.now(), utc: () => new Date().toISOString() };
  const deadline = clock.monotonic() + config.maxDurationSeconds * 1000;
  const journal = new FileJournal(join(outputDirectory, 'journal.jsonl'));
  const trace = new CandidateTraceWriter({ producerId: `candidate:${randomUUID()}`,
    sourceRecordId: randomUUID, utc: clock.utc,
    sink: new FileCandidateTraceSink(join(outputDirectory, 'candidate.jsonl')) });
  const environment = createHostEnvironment();
  const origin = createSafariOriginProbe(config.safari);
  const capture = createVoiceOverCaptureProbe();
  const preflight = createPhysicalPreflight({ ...config.physicalPreflight, clock, environment,
    async observeRuntimeEvidence(probeSignal) {
      const evidence = await config.physicalPreflight.observeRuntimeEvidence(probeSignal);
      const observedOrigin = await origin();
      return { ...evidence, observedOrigin, originReachable: true,
        speechCaptureWorking: await capture(probeSignal), journalWritable: await journal.probeWritable() };
    },
  });
  const claim = claimDesktop(config.desktop);
  const guard = () => {
    claim.assertHeld();
    if (signal.aborted || clock.monotonic() >= deadline) throw new Error('candidate cancelled or expired');
  };
  let adapter: VoiceOverAdapter | undefined;
  const runner = new CandidateProofRunner({ trace, journal, clock, approvedTextValues,
    lease: { leaseId: config.desktop.reference.leaseId, epoch: config.desktop.reference.epoch,
      maxActions: actions.length, maxWallTimeSeconds: config.maxDurationSeconds, deadlineMonotonic: deadline },
    actionTimeoutMs: config.actionTimeoutMs,
    preflight: async () => { guard(); const report = await preflight(); guard(); return report; },
    authorizeReaderStartup: async () => {
      guard(); await config.authorizeStartup(signal); guard();
      if (probeReaderControlConfigured(environment).condition !== 'TRUE') throw new Error('reader control unavailable');
      guard();
    },
    authorizePhysicalAction: async request => {
      guard(); await config.authorizeAction(structuredClone(request), signal); guard();
      await origin(); guard();
    },
    adapter: {
      async start() { guard(); adapter = createGuidepupVoiceOverAdapter({ monotonicNow: clock.monotonic }); await adapter.start(); guard(); },
      async stop() { claim.assertHeld(); if (adapter !== undefined) await adapter.stop(); },
      async perform(request, context) {
        guard();
        if (adapter === undefined) throw new Error('reader not started');
        return adapter.perform(request, context);
      },
    },
  });
  const result = await runner.run(actions);
  // Interrupted/unknown teardown retains the same crash-persistent exclusion marker. An explicit
  // STOP/cleanup plus a complete local result permits release, never an automatic retry.
  if (result.status !== 'INTERRUPTED') claim.release();
  return result;
}
