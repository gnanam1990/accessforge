/** Execute a labelled actual-VoiceOver capability proof through the module-07 supervisor. */

import type {
  ActionRequest,
  AllowedAction,
  PreflightReport,
  RawObservation,
  UnknownObservation,
} from '@accessforge/at-voiceover';
import { assertActionPermitted, PREFLIGHT_CHECKS } from '@accessforge/at-voiceover';

import type { CandidateTraceWriter } from './candidate-trace.js';
import { createVoiceOverDispatch, type VoiceOverRuntime } from './voiceover.js';
import {
  Supervisor,
  type Clock,
  type DispatchOutcome,
  type Journal,
  type JournalEntry,
  type LeaseState,
} from './supervisor.js';

class TracingJournal implements Journal {
  constructor(
    private readonly journal: Journal,
    private readonly trace: CandidateTraceWriter,
  ) {}

  async appendAndFlush(entry: JournalEntry): Promise<void> {
    // The independent local recovery journal is durable first. The candidate source record is then
    // durable before this method resolves, so ACTION_INTENT still precedes any OS dispatch.
    await this.journal.appendAndFlush(entry);
    await this.trace.record(entry.result === undefined ? 'ACTION_INTENT' : 'ACTION_RESULT', entry);
  }

  async read(): Promise<readonly JournalEntry[]> {
    return this.journal.read();
  }
}

export interface CandidateProofOptions {
  readonly adapter: VoiceOverRuntime & { start(): Promise<void>; stop(): Promise<void> };
  /** Fresh physical measurements, not a saved setup report. Never starts/enables the reader. */
  readonly preflight: () => Promise<PreflightReport>;
  /** Trusted operator consent and reader-control configuration check; no default authorization. */
  readonly authorizeReaderStartup: (signal: AbortSignal) => Promise<void>;
  /** Trusted host focus/effect gate; resolved before the same late-dispatch fence as preflight. */
  readonly authorizePhysicalAction?: (request: ActionRequest, signal: AbortSignal) => Promise<void>;
  readonly trace: CandidateTraceWriter;
  readonly journal: Journal;
  readonly clock: Clock;
  readonly lease: LeaseState;
  readonly actionTimeoutMs: number;
  /** Bounds asynchronous SDK start/cleanup observation, not proof that the SDK was cancelled. */
  readonly lifecycleTimeoutMs?: number;
  readonly approvedTextValues: ReadonlySet<string>;
  readonly onObservation?: (
    observation: RawObservation | UnknownObservation,
  ) => Promise<void>;
}

export interface CandidateProofResult {
  readonly status: 'BLOCKED' | 'CANDIDATE_COMPLETE' | 'INTERRUPTED';
  readonly actions: readonly DispatchOutcome[];
  readonly detail: string;
}

function failedChecks(report: PreflightReport, beforeStartup = false): readonly string[] {
  const missing = PREFLIGHT_CHECKS
    .filter((name) => {
      const condition = report.checks?.[name]?.condition;
      // Only reader activation/capture can be unavailable before an authorized startup. A
      // missing check is still a malformed report, not evidence that the reader is inactive.
      if (beforeStartup && (name === 'READER_ACTIVE' || name === 'SPEECH_CAPTURE_WORKING') &&
          (condition === 'FALSE' || condition === 'UNKNOWN')) return false;
      return condition !== 'TRUE';
    })
    .map((name) => `${name}=${report.checks?.[name]?.condition ?? 'UNKNOWN'}`);
  const additional = Object.entries(report.checks ?? {})
    .filter(([name, result]) => !PREFLIGHT_CHECKS.some((required) => required === name) &&
      result?.condition !== 'TRUE')
    .map(([name, result]) => `${name}=${result?.condition ?? 'UNKNOWN'}`);
  return [...missing, ...additional];
}

export class CandidateProofRunner {
  private consumed = false;

  constructor(private readonly options: CandidateProofOptions) {}

  private async checkPhysical(phase: 'BEFORE_STARTUP' | 'AFTER_STARTUP' | 'BEFORE_ACTION'): Promise<readonly string[]> {
    const preflight = structuredClone(await this.options.preflight());
    const blocked = failedChecks(preflight, phase === 'BEFORE_STARTUP');
    // Snapshot both the decision and retained measurement before an async sink sees it.
    await this.options.trace.record('PREFLIGHT_RESULT', { ...preflight, phase });
    return blocked;
  }

  async run(actions: readonly ActionRequest[]): Promise<CandidateProofResult> {
    // Claim synchronously before any validation, probe or trace await. An uncertain/failed
    // attempt must not overlap or replay against this original journal, trace and reader.
    if (this.consumed) throw new Error('candidate proof already consumed; reconcile the original attempt');
    this.consumed = true;
    const lifecycleTimeout = this.options.lifecycleTimeoutMs ?? 30000;
    if (!Number.isFinite(lifecycleTimeout) || lifecycleTimeout <= 0 || lifecycleTimeout > 30000) {
      throw new Error('candidate reader lifecycle deadline must be bounded');
    }
    const bounded = async (operation: (signal: AbortSignal) => Promise<void>): Promise<void> => {
      const authority = new AbortController();
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([Promise.resolve().then(() => operation(authority.signal)), new Promise<never>((_resolve, reject) => {
          timer = setTimeout(() => reject(new Error('reader lifecycle outcome unconfirmed')), lifecycleTimeout);
        })]);
      } finally {
        if (timer !== undefined) clearTimeout(timer);
        authority.abort(); // End pending operator work, including after timeout or refusal.
      }
    };
    // One private input snapshot across async retention/startup. Readonly types do not stop a
    // caller from mutating the original array or text after validation but before dispatch.
    const approvedActions = structuredClone(actions);
    if (approvedActions.length === 0) {
      throw new Error('candidate proof requires at least one action');
    }

    // Reject unbound typing before VoiceOver starts. The allowlist constrains commands, but the
    // fixture binding constrains what those commands are permitted to type.
    for (const [index, action] of approvedActions.entries()) {
      assertActionPermitted(action);
      if (action.action === 'STOP' && index !== approvedActions.length - 1) {
        throw new Error('STOP must be the final action in a candidate proof');
      }
      if (
        action.action === 'TYPE_TEXT' &&
        (action.text === undefined || !this.options.approvedTextValues.has(action.text))
      ) {
        throw new Error(
          'TYPE_TEXT must exactly match an approved synthetic fixture value; arbitrary text is ' +
            'not authorized for the candidate proof',
        );
      }
    }

    const blocked = await this.checkPhysical('BEFORE_STARTUP');
    if (blocked.length > 0) {
      const detail = `candidate proof blocked by preflight: ${blocked.join(', ')}`;
      await this.options.trace.record('RUN_FINISHED', { status: 'BLOCKED', detail });
      await this.options.trace.close();
      return { status: 'BLOCKED', actions: [], detail };
    }

    const outcomes: DispatchOutcome[] = [];
    let activeDispatch: symbol | undefined;
    let actionAuthority: AbortController | undefined;
    let startupAttempted = false;
    let startupSettled = false;
    let readerStoppedByAction = false;
    let result: CandidateProofResult = {
      status: 'CANDIDATE_COMPLETE', actions: outcomes,
      detail: 'actual-reader actions completed as local CANDIDATE_PROOF; no canonical outcome or verified finding is claimed',
    };
    try {
      // Consent observation is bounded too. A late result cannot resume this abandoned startup
      // path, and no SDK cleanup is attempted when startup was never dispatched.
      await bounded(signal => this.options.authorizeReaderStartup(signal));
      // Startup can change reader state before rejecting. Its cleanup is still owned here.
      startupAttempted = true;
      await bounded(() => Promise.resolve().then(() => this.options.adapter.start()).finally(() => {
        startupSettled = true;
      }));
      if ((await this.checkPhysical('AFTER_STARTUP')).length > 0) {
        throw new Error('post-start physical readiness unavailable');
      }
      const journal = new TracingJournal(this.options.journal, this.options.trace);
      const readerDispatch = createVoiceOverDispatch(this.options.adapter, {
        utc: this.options.clock.utc,
        recordObservation: async (observation) => {
          await this.options.trace.record('READER_OBSERVATION', observation);
          await this.options.onObservation?.(observation);
        },
      });
      const supervisor = new Supervisor({
        clock: this.options.clock,
        journal,
        dispatch: async (command) => {
          const token = activeDispatch;
          const authority = actionAuthority;
          // Measure after the durable intent, immediately before physical dispatch. A lock,
          // origin/build change or lost permission between actions must not reuse initial TRUE.
          if ((await this.checkPhysical('BEFORE_ACTION')).length > 0) {
            throw new Error('action-time physical readiness unavailable');
          }
          // A late preflight must not even open a stale operator prompt after timeout/cleanup.
          if (token === undefined || activeDispatch !== token || authority === undefined || authority.signal.aborted) {
            throw new Error('late physical preflight fenced');
          }
          await this.options.authorizePhysicalAction?.(structuredClone(command) as ActionRequest, authority.signal);
          if (activeDispatch !== token || authority.signal.aborted) {
            throw new Error('late physical preflight fenced');
          }
          return readerDispatch(command);
        },
        actionTimeoutMs: this.options.actionTimeoutMs,
      });
      supervisor.adoptLease(this.options.lease);

      for (const request of approvedActions) {
        let outcome: DispatchOutcome;
        activeDispatch = Symbol();
        actionAuthority = new AbortController();
        try {
          outcome = await supervisor.performAction(request.action as AllowedAction, {
            ...(request.keyChord !== undefined ? { keyChord: request.keyChord } : {}),
            ...(request.text !== undefined ? { text: request.text } : {}),
          });
        } finally {
          activeDispatch = undefined;
          actionAuthority.abort();
          actionAuthority = undefined;
        }
        outcomes.push(outcome);
        if (request.action === 'STOP' && outcome.status === 'SUCCEEDED') {
          readerStoppedByAction = true;
        }
        if (outcome.status !== 'SUCCEEDED') {
          const detail = `candidate proof interrupted: ${outcome.status}`;
          result = { status: 'INTERRUPTED', actions: outcomes, detail };
          break;
        }
      }

    } catch (error) {
      const detail = `candidate proof interrupted by ${
        error instanceof Error ? error.message : 'an unknown runtime error'
      }`;
      result = { status: 'INTERRUPTED', actions: outcomes, detail };
    } finally {
      if (startupAttempted && !startupSettled) {
        // Do not race STOP against an SDK startup which may still activate the reader later.
        // The host retains the desktop claim for INTERRUPTED; this is not physical STOP proof.
        result = { status: 'INTERRUPTED', actions: outcomes,
          detail: 'reader startup remains unresolved; retain desktop exclusion and reconcile before any retry' };
      } else if (startupAttempted && !readerStoppedByAction) {
        try {
          await bounded(() => this.options.adapter.stop());
        } catch {
          result = {
            status: 'INTERRUPTED', actions: outcomes,
            detail: 'candidate proof reader cleanup unconfirmed; reconcile desktop state before another run',
          };
        }
      }
    }
    // Cleanup is part of the run. Never seal a successful trace while teardown can still fail.
    if (result.status === 'INTERRUPTED') {
      await this.options.trace.record('INTERRUPTION', { detail: result.detail });
    }
    await this.options.trace.record('RUN_FINISHED', { status: result.status, detail: result.detail });
    await this.options.trace.close();
    return result;
  }
}
