/** Execute a labelled actual-VoiceOver capability proof through the module-07 supervisor. */

import type {
  ActionRequest,
  AllowedAction,
  PreflightReport,
  RawObservation,
  UnknownObservation,
} from '@accessforge/at-voiceover';
import { assertActionPermitted } from '@accessforge/at-voiceover';

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
  readonly preflight: PreflightReport;
  readonly trace: CandidateTraceWriter;
  readonly journal: Journal;
  readonly clock: Clock;
  readonly lease: LeaseState;
  readonly actionTimeoutMs: number;
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

function failedChecks(report: PreflightReport): readonly string[] {
  return Object.entries(report.checks)
    .filter(([, result]) => result.condition !== 'TRUE')
    .map(([name, result]) => `${name}=${result.condition}`);
}

export class CandidateProofRunner {
  constructor(private readonly options: CandidateProofOptions) {}

  async run(actions: readonly ActionRequest[]): Promise<CandidateProofResult> {
    if (actions.length === 0) {
      throw new Error('candidate proof requires at least one action');
    }

    // Reject unbound typing before VoiceOver starts. The allowlist constrains commands, but the
    // fixture binding constrains what those commands are permitted to type.
    for (const action of actions) {
      assertActionPermitted(action);
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

    await this.options.trace.record('PREFLIGHT_RESULT', this.options.preflight);
    const blocked = failedChecks(this.options.preflight);
    if (blocked.length > 0) {
      const detail = `candidate proof blocked by preflight: ${blocked.join(', ')}`;
      await this.options.trace.record('RUN_FINISHED', { status: 'BLOCKED', detail });
      await this.options.trace.close();
      return { status: 'BLOCKED', actions: [], detail };
    }

    const outcomes: DispatchOutcome[] = [];
    let readerStarted = false;
    let readerStoppedByAction = false;
    try {
      await this.options.adapter.start();
      readerStarted = true;
      const journal = new TracingJournal(this.options.journal, this.options.trace);
      const dispatch = createVoiceOverDispatch(this.options.adapter, {
        utc: this.options.clock.utc,
        recordObservation: async (observation) => {
          await this.options.trace.record('READER_OBSERVATION', observation);
          await this.options.onObservation?.(observation);
        },
      });
      const supervisor = new Supervisor({
        clock: this.options.clock,
        journal,
        dispatch,
        actionTimeoutMs: this.options.actionTimeoutMs,
      });
      supervisor.adoptLease(this.options.lease);

      for (const request of actions) {
        const outcome = await supervisor.performAction(request.action as AllowedAction, {
          ...(request.keyChord !== undefined ? { keyChord: request.keyChord } : {}),
          ...(request.text !== undefined ? { text: request.text } : {}),
        });
        outcomes.push(outcome);
        if (request.action === 'STOP' && outcome.status === 'SUCCEEDED') {
          readerStoppedByAction = true;
        }
        if (outcome.status !== 'SUCCEEDED') {
          const detail = `candidate proof interrupted: ${outcome.status}`;
          await this.options.trace.record('INTERRUPTION', { outcome, detail });
          await this.options.trace.record('RUN_FINISHED', { status: 'INTERRUPTED', detail });
          await this.options.trace.close();
          return { status: 'INTERRUPTED', actions: outcomes, detail };
        }
      }

      const detail =
        'actual-reader actions completed as local CANDIDATE_PROOF; no canonical outcome or ' +
        'verified finding is claimed';
      await this.options.trace.record('RUN_FINISHED', {
        status: 'CANDIDATE_COMPLETE',
        detail,
      });
      await this.options.trace.close();
      return { status: 'CANDIDATE_COMPLETE', actions: outcomes, detail };
    } catch (error) {
      const detail = `candidate proof interrupted by ${
        error instanceof Error ? error.message : 'an unknown runtime error'
      }`;
      await this.options.trace.record('INTERRUPTION', { detail });
      await this.options.trace.record('RUN_FINISHED', { status: 'INTERRUPTED', detail });
      await this.options.trace.close();
      return { status: 'INTERRUPTED', actions: outcomes, detail };
    } finally {
      if (readerStarted && !readerStoppedByAction) {
        try {
          await this.options.adapter.stop();
        } catch {
          // The trace already says whether the run completed or was interrupted. A teardown failure
          // must be surfaced by the caller's diagnostics and next preflight; it must not rewrite the
          // already-fsynced action history.
        }
      }
    }
  }
}
