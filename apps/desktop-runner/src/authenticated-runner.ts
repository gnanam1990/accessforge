/** Authenticated control-plane -> durable local supervisor -> reader adapter -> result bridge.
 * No default adapter or preflight bypass. Construct only in trusted desktop infrastructure.
 */
import type { PreflightReport, RawObservation, UnknownObservation } from '@accessforge/at-voiceover';
import { PREFLIGHT_CHECKS } from '@accessforge/at-voiceover';
import { parseReference, type DispatchReference } from './dispatch-receiver.js';
import { Supervisor, type ActionCommand, type Clock, type DispatchOutcome, type Journal, type LeaseState } from './supervisor.js';
import { createVoiceOverDispatch, type VoiceOverRuntime } from './voiceover.js';

export interface ExecutionSessionPort {
  readonly receipt: Readonly<Record<string, unknown>>;
  retainIntent(command: unknown): Promise<Readonly<Record<string, unknown>>>;
  commitDispatch(actionId: string, origin: string): Promise<ActionCommand>;
  completeAction(actionId: string, status: 'SUCCEEDED' | 'FAILED' | 'AMBIGUOUS'): Promise<void>;
  retainObservation(command: ActionCommand, observation: RawObservation | UnknownObservation, capturedAtUtc: string): Promise<void>;
  retainRuntimePreflight(command: ActionCommand, report: PreflightReport, capturedAtUtc: string): Promise<void>;
  authorizeCandidateFormEffect(command: ActionCommand): Promise<void>;
  finish(): Promise<Readonly<Record<string, unknown>>>;
}

export interface AuthenticatedRunnerOptions {
  readonly session: ExecutionSessionPort;
  readonly lease: LeaseState;
  readonly clock: Clock;
  readonly journal: Journal;
  readonly adapter: VoiceOverRuntime;
  readonly actionTimeoutMs: number;
  /** Trusted live probe, not navigator input. Missing/unknown checks refuse before OS input. */
  readonly preflight: () => Promise<PreflightReport>;
  /** Trusted actual browser-origin observation, not the journey's intended URL. */
  readonly observeOrigin: () => Promise<string>;
  /** Fresh focus/effect authorization against the sealed environment; throws on unknown/refused. */
  readonly authorizePhysicalAction: (command: ActionCommand) => Promise<void>;
  /** Explicit trusted candidate setup only; permission does not itself perform a POST. */
  readonly candidateFormEffects?: boolean;
  readonly recordObservation: (value: RawObservation | UnknownObservation) => Promise<void>;
}

export interface AuthenticatedActionOutcome extends DispatchOutcome {
  /** Control-plane identity, not the local journal's lease:epoch:sequence identifier. */
  readonly serverActionId?: string;
}

async function bounded<T>(promise: Promise<T>, milliseconds: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    if (milliseconds <= 0) throw new Error('local deadline elapsed');
    return await Promise.race([promise, new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error('local action timed out')), milliseconds);
    })]);
  } finally { if (timer !== undefined) clearTimeout(timer); }
}

export class AuthenticatedRunner {
  readonly reference: Readonly<DispatchReference>;
  #supervisor: Supervisor;
  #sequence = 0;
  #busy = false;
  #fenced = false;
  #stopping = false;
  #executing = false;
  #started = false;
  #stopSucceeded = false;
  #commands: ActionCommand[] = [];
  #serverCommand: ActionCommand | undefined;
  #origin = '';

  constructor(private readonly options: AuthenticatedRunnerOptions) {
    const reference = parseReference(options.session.receipt.reference);
    this.reference = Object.freeze(reference);
    if (reference.leaseId !== options.lease.leaseId || reference.epoch !== options.lease.epoch ||
        !Number.isFinite(options.actionTimeoutMs) || options.actionTimeoutMs <= 0 || options.actionTimeoutMs > 30000) {
      throw new Error('local supervisor does not match the authenticated session');
    }
    const physicalDispatch = createVoiceOverDispatch(options.adapter, {
      utc: options.clock.utc,
      recordObservation: async (observation) => {
        const command = this.#serverCommand;
        if (command === undefined || !this.#executing || this.#fenced || this.#stopping) {
          throw new Error('reader evidence outside active dispatch');
        }
        await options.recordObservation(observation);
        if (!this.#executing || this.#fenced || this.#stopping) throw new Error('reader evidence fenced');
        await options.session.retainObservation(command, observation, options.clock.utc());
      },
    });
    this.#supervisor = new Supervisor({
      clock: options.clock, actionTimeoutMs: options.actionTimeoutMs,
      journal: {
        read: () => options.journal.read(),
        appendAndFlush: async (entry) => {
          const command = this.#serverCommand;
          if (command === undefined || entry.sequence !== command.sequence) throw new Error('journal identity');
          await options.journal.appendAndFlush({ ...entry, serverActionId: command.actionId });
        },
      },
      dispatch: async () => {
        const command = this.#serverCommand;
        const before = options.clock.monotonic();
        if (command === undefined) throw new Error('physical action identity unavailable');
        await this.#checkPhysical(command);
        await bounded(options.authorizePhysicalAction(command), this.#remaining());
        if (options.candidateFormEffects === true && (command.action === 'ACTIVATE' ||
            (command.action === 'KEY_CHORD' && ['ENTER', 'SPACE'].includes(command.keyChord ?? '')))) {
          await bounded(options.session.authorizeCandidateFormEffect(command), this.#remaining());
        }
        const origin = await bounded(options.observeOrigin(), this.#remaining());
        // A timed-out preflight may resolve later. It must not then send an OS action.
        if (!this.#executing || this.#stopping || this.#fenced || command === undefined ||
            options.clock.monotonic() < before || this.#remaining() <= 0 || origin !== this.#origin) {
          throw new Error('physical dispatch fenced or origin changed');
        }
        const remaining = this.#remaining();
        if (remaining <= 0) throw new Error('physical dispatch deadline elapsed');
        return bounded(physicalDispatch(command), remaining);
      },
    });
    this.#supervisor.adoptLease(options.lease);
  }

  #remaining(): number {
    return Math.min(this.options.actionTimeoutMs,
      this.options.lease.deadlineMonotonic - this.options.clock.monotonic());
  }

  async #checkPhysical(command?: ActionCommand): Promise<void> {
    const report = await bounded(this.options.preflight(), this.#remaining());
    // Snapshot the decision before awaiting retention; mutation of a producer-owned report must
    // not turn the submitted UNKNOWN/FALSE observation into permission to enter the adapter.
    const passed = PREFLIGHT_CHECKS.every((key) => report.checks[key]?.condition === 'TRUE');
    if (command !== undefined) {
      if (!this.#executing || this.#stopping || this.#fenced || this.#remaining() <= 0) throw new Error('runtime preflight fenced');
      await bounded(this.options.session.retainRuntimePreflight(command, report, this.options.clock.utc()), this.#remaining());
    }
    // Require the complete vocabulary, not an empty caller-supplied object passing every().
    if (!passed) throw new Error('physical preflight unavailable');
  }

  requestCancellation(): void {
    this.#stopping = true;
    this.#executing = false;
    this.#supervisor.requestCancellation();
  }

  async perform(request: { action: ActionCommand['action']; keyChord?: string; textValueRef?: string }): Promise<AuthenticatedActionOutcome> {
    if (this.#busy || this.#fenced || this.#stopping) return { status: 'REFUSED', detail: 'runner fenced or busy' };
    this.#busy = true;
    let actionId: string | undefined;
    try {
      if (!this.#started) {
        if ((await this.options.journal.read()).length !== 0) throw new Error('a prior journal cannot be resumed');
        this.#started = true;
      }
      await this.#checkPhysical();
      this.#origin = await bounded(this.options.observeOrigin(), this.#remaining());
      if (this.#stopping) throw new Error('cancelled before intent');
      const intent = await this.options.session.retainIntent({ ...request, sequence: ++this.#sequence, origin: this.#origin });
      if (typeof intent.actionId !== 'string') throw new Error('intent identity');
      actionId = intent.actionId;
      const command = await this.options.session.commitDispatch(actionId, this.#origin);
      if (command.actionId !== actionId || command.sequence !== this.#sequence || command.action !== request.action) {
        throw new Error('dispatch identity');
      }
      this.#serverCommand = command;
      this.#commands.push(command);
      this.#executing = true;
      const outcome = await this.#supervisor.performAction(command.action, {
        ...(command.keyChord === undefined ? {} : { keyChord: command.keyChord }),
        ...(command.text === undefined ? {} : { text: command.text }),
      });
      this.#executing = false;
      const status = outcome.status === 'REFUSED' ? 'AMBIGUOUS' : outcome.status;
      if (status === 'AMBIGUOUS') this.#fenced = true;
      // Supervisor result flush and observation retention have completed before this report.
      await this.options.session.completeAction(actionId, status);
      if (command.action === 'STOP') {
        this.#stopping = true;
        this.#supervisor.requestCancellation(); // Local input fence, not a server cancellation.
        this.#stopSucceeded = status === 'SUCCEEDED';
      }
      return { ...outcome, status, serverActionId: actionId };
    } catch {
      this.#executing = false;
      this.#fenced = true;
      if (actionId !== undefined) {
        try { await this.options.session.completeAction(actionId, 'AMBIGUOUS'); } catch { /* Retain local fencing; recovery owns the unresolved server state. */ }
      }
      return { status: actionId === undefined ? 'REFUSED' : 'AMBIGUOUS',
        ...(actionId === undefined ? {} : { serverActionId: actionId }),
        detail: 'execution fenced; no retry or automatic reset' };
    } finally { this.#executing = false; this.#busy = false; }
  }

  /** Call after the independent observer has retained and closed its final sample. */
  async finish(): Promise<Readonly<Record<string, unknown>>> {
    if (this.#busy || this.#fenced || !this.#stopSucceeded || !this.#supervisor.mayAcknowledgeStop()) {
      throw new Error('execution has no clean STOP acknowledgement');
    }
    this.#busy = true;
    this.#fenced = true;
    try {
      const entries = await this.options.journal.read();
      if (entries.length !== this.#commands.length * 2 || entries.at(-1)?.result !== 'SUCCEEDED' ||
          entries.at(-1)?.action !== 'STOP') throw new Error('local journal tail is incomplete');
      for (const [index, command] of this.#commands.entries()) {
        const intent = entries[index * 2], result = entries[index * 2 + 1];
        for (const entry of [intent, result]) {
          if (entry === undefined || entry.serverActionId !== command.actionId ||
              entry.sequence !== command.sequence || entry.action !== command.action ||
              entry.leaseId !== this.options.lease.leaseId || entry.epoch !== this.options.lease.epoch) {
            throw new Error('local journal action identity differs');
          }
        }
        if (intent?.result !== undefined || !['SUCCEEDED', 'FAILED'].includes(result?.result ?? '')) {
          throw new Error('local journal result is unresolved');
        }
      }
      return await this.options.session.finish();
    } finally { this.#busy = false; }
  }
}
