/** Authenticated control-plane -> durable local supervisor -> reader adapter -> result bridge.
 * No default adapter or preflight bypass. Construct only in trusted desktop infrastructure.
 */
import type { PreflightReport, RawObservation, UnknownObservation } from '@accessforge/at-voiceover';
import { PREFLIGHT_CHECKS } from '@accessforge/at-voiceover';
import { parseReference } from './dispatch-receiver.js';
import { Supervisor, type ActionCommand, type Clock, type DispatchOutcome, type Journal, type LeaseState } from './supervisor.js';
import { createVoiceOverDispatch, type VoiceOverRuntime } from './voiceover.js';

export interface ExecutionSessionPort {
  readonly receipt: Readonly<Record<string, unknown>>;
  retainIntent(command: unknown): Promise<Readonly<Record<string, unknown>>>;
  commitDispatch(actionId: string, origin: string): Promise<ActionCommand>;
  completeAction(actionId: string, status: 'SUCCEEDED' | 'FAILED' | 'AMBIGUOUS'): Promise<void>;
  retainObservation(command: ActionCommand, observation: RawObservation | UnknownObservation, capturedAtUtc: string): Promise<void>;
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
  readonly recordObservation: (value: RawObservation | UnknownObservation) => Promise<void>;
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
  #supervisor: Supervisor;
  #sequence = 0;
  #busy = false;
  #fenced = false;
  #stopping = false;
  #executing = false;
  #started = false;
  #serverCommand: ActionCommand | undefined;
  #origin = '';

  constructor(private readonly options: AuthenticatedRunnerOptions) {
    const reference = parseReference(options.session.receipt.reference);
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
        await this.#checkPhysical();
        if (command === undefined) throw new Error('physical action identity unavailable');
        await bounded(options.authorizePhysicalAction(command), this.#remaining());
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

  async #checkPhysical(): Promise<void> {
    const report = await bounded(this.options.preflight(), this.#remaining());
    // Require the complete vocabulary, not an empty caller-supplied object passing every().
    const checks = report.checks as Record<string, { condition: string }>;
    if (PREFLIGHT_CHECKS.some((key) => checks[key]?.condition !== 'TRUE')) throw new Error('physical preflight unavailable');
  }

  requestCancellation(): void {
    this.#stopping = true;
    this.#executing = false;
    this.#supervisor.requestCancellation();
  }

  async perform(request: { action: ActionCommand['action']; keyChord?: string; textValueRef?: string }): Promise<DispatchOutcome> {
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
      return outcome;
    } catch {
      this.#executing = false;
      this.#fenced = true;
      if (actionId !== undefined) {
        try { await this.options.session.completeAction(actionId, 'AMBIGUOUS'); } catch { /* Retain local fencing; recovery owns the unresolved server state. */ }
      }
      return { status: actionId === undefined ? 'REFUSED' : 'AMBIGUOUS', detail: 'execution fenced; no retry or automatic reset' };
    } finally { this.#executing = false; this.#busy = false; }
  }
}
