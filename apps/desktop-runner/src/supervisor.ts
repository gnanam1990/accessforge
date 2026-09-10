/**
 * The supervisor side of the desktop runner protocol.
 *
 * This is the half that runs *on the desktop*, and it exists because the server's view is not
 * sufficient to keep an interactive session safe. TDD section 5: "During network partitions the
 * runner's local expiry stops new input." A supervisor that only stopped when told to would keep
 * typing into a live application for as long as the network stayed down.
 *
 * Three properties are load-bearing and each is enforced structurally rather than by convention.
 *
 * **The deadline is monotonic.** `Date.now()` is a wall clock: NTP correction, daylight saving, an
 * operator changing the date, or a laptop resuming from sleep all move it, in either direction. A
 * lease deadline compared against it moves too — backwards and the lease looks fresh for decades,
 * forwards and it expires in the middle of a keystroke. `performance.now()` is monotonic and is the
 * only clock this module compares deadlines against. The wall clock is carried separately, purely so
 * an operator reading an incident timeline sees a time they recognise.
 *
 * **The intent is journaled and flushed before the action is dispatched.** CONTRACTS section 7:
 * "Store action intent before sending to the OS." The write is `fsync`ed, because a buffered write
 * lost in a crash is exactly the same as no write: the crash that loses it is the crash the record
 * exists to survive.
 *
 * **An unresolved action is never re-dispatched.** INV-09. On restart, a journal entry with an
 * intent and no result means the keystroke may have landed. The supervisor reports ambiguity and
 * stops; it does not try again. `TYPE_TEXT` and `ACTIVATE` are exactly the actions for which a
 * second attempt is a second real effect.
 *
 * Actual screen-reader control — VoiceOver, NVDA — is modules 08 and 09. This module's `dispatch`
 * callback is where those adapters attach, and nothing here claims to have driven a reader.
 */

/** The sealed action policy, from CONTRACTS section 6. Mirrors the Python allowlist. */
export const ALLOWED_ACTIONS = [
  'NEXT',
  'PREVIOUS',
  'ACTIVATE',
  'TYPE_TEXT',
  'KEY_CHORD',
  'READ_CURRENT',
  'WAIT_FOR_READER_IDLE',
  'STOP',
] as const;

export type AllowedAction = (typeof ALLOWED_ACTIONS)[number];

/**
 * Actions whose effect on the world cannot be undone or re-derived, so a lost acknowledgement is
 * permanently ambiguous. The module prompt names two of them: "never resend TYPE_TEXT, ACTIVATE or
 * another potentially consequential command blindly."
 *
 * The list is here for *reporting*, not for gating: nothing is resent, consequential or not. A
 * repeated `NEXT` is harmless in effect but still ruins the evidence, because nobody can attribute
 * the reader's next announcement to one press or two.
 */
export const CONSEQUENTIAL_ACTIONS: readonly AllowedAction[] = ['TYPE_TEXT', 'ACTIVATE'];

export type GateRefusal =
  | 'LEASE_EPOCH_STALE'
  | 'LEASE_EXPIRED'
  | 'CANCELLATION_REQUESTED'
  | 'ACTION_BUDGET_EXHAUSTED'
  | 'WALL_TIME_BUDGET_EXHAUSTED'
  | 'ACTION_NOT_ALLOWED'
  | 'ACTION_IN_FLIGHT'
  | 'NOT_LEASED';

export type AmbiguityReason =
  | 'ACTION_RESULT_NEVER_ARRIVED'
  | 'SUPERVISOR_CRASHED_AFTER_INTENT'
  | 'LEASE_EXPIRED_MID_ACTION'
  | 'CLOCK_DISCONTINUITY';

export interface LeaseState {
  readonly leaseId: string;
  readonly epoch: number;
  /** A `performance.now()` reading, never a `Date.now()` one. */
  readonly deadlineMonotonic: number;
  readonly maxActions: number;
  readonly maxWallTimeSeconds: number;
}

export interface JournalEntry {
  readonly actionId: string;
  readonly leaseId: string;
  readonly epoch: number;
  readonly sequence: number;
  readonly action: AllowedAction;
  readonly keyChord?: string;
  readonly text?: string;
  /** Wall clock, for the audit trail. Never compared against a deadline. */
  readonly intentAtUtc: string;
  readonly dispatchedAtMonotonic?: number;
  readonly result?: 'SUCCEEDED' | 'FAILED' | 'AMBIGUOUS';
  readonly ambiguityReason?: AmbiguityReason;
}

/**
 * The durable local journal.
 *
 * `appendAndFlush` must not resolve until the bytes are on the device. An implementation that
 * resolved on a buffered write would satisfy the type and lose the guarantee, which is why the
 * method name says `Flush` rather than `Write`.
 */
export interface Journal {
  appendAndFlush(entry: JournalEntry): Promise<void>;
  /** Entries in the order they were appended. Used on restart to find unresolved intents. */
  read(): Promise<readonly JournalEntry[]>;
}

export interface Clock {
  /** Monotonic, for deadlines. */
  monotonic(): number;
  /** Wall clock UTC in RFC3339, for audit records. */
  utc(): string;
}

export interface Decision {
  readonly admitted: boolean;
  readonly refusal?: GateRefusal;
  readonly detail?: string;
}

export interface ActionCommand {
  readonly actionId: string;
  readonly sequence: number;
  readonly action: AllowedAction;
  readonly keyChord?: string;
  readonly text?: string;
}

export interface SupervisorOptions {
  readonly clock: Clock;
  readonly journal: Journal;
  /** Where modules 08 and 09 attach. Resolves with the result, or rejects / never settles. */
  readonly dispatch: (command: ActionCommand) => Promise<'SUCCEEDED' | 'FAILED'>;
  /** How long to wait for a dispatch before declaring the action's outcome unknown. */
  readonly actionTimeoutMs: number;
}

export interface DispatchOutcome {
  readonly status: 'SUCCEEDED' | 'FAILED' | 'AMBIGUOUS' | 'REFUSED';
  readonly refusal?: GateRefusal;
  readonly ambiguityReason?: AmbiguityReason;
  readonly detail?: string;
}

/** Thrown when a caller tries to use a supervisor whose desktop has been fenced. */
export class SupervisorFenced extends Error {}

export class Supervisor {
  private lease: LeaseState | undefined;
  private cancelRequested = false;
  private actionsUsed = 0;
  private wallTimeUsedSeconds = 0;
  private inFlight: string | undefined;
  private sequence = 0;
  private fenced: AmbiguityReason | undefined;
  private lastMonotonic: number;
  private readonly startedMonotonic: number;

  constructor(private readonly options: SupervisorOptions) {
    this.startedMonotonic = options.clock.monotonic();
    this.lastMonotonic = this.startedMonotonic;
  }

  /** Adopt a lease granted by the server. A supervisor with no lease admits nothing. */
  adoptLease(lease: LeaseState): void {
    if (this.fenced !== undefined) {
      throw new SupervisorFenced(
        `this desktop is fenced (${this.fenced}) and must be reset before it can hold a lease again`,
      );
    }
    if (this.lease !== undefined && lease.epoch <= this.lease.epoch) {
      // A server handing back an epoch we already hold is a server we cannot trust about
      // exclusivity, so the safe reading is to refuse rather than to accept the lower number.
      throw new Error(
        `lease epoch ${lease.epoch} does not advance on the held epoch ${this.lease.epoch}; ` +
          'epochs are monotonic and a reused one would revive a superseded supervisor',
      );
    }
    this.lease = lease;
    this.sequence = 0;
  }

  /** The server has requested cancellation. Fences every subsequent action immediately (INV-13). */
  requestCancellation(): void {
    this.cancelRequested = true;
  }

  /**
   * Whether a stop may be reported as a clean acknowledgement.
   *
   * False while an action is unresolved. Acknowledging then would say the supervisor stopped
   * *asking*, which is not the same as saying the keystroke did not land.
   */
  mayAcknowledgeStop(): boolean {
    return this.cancelRequested && this.inFlight === undefined && this.fenced === undefined;
  }

  isFenced(): boolean {
    return this.fenced !== undefined;
  }

  fencedReason(): AmbiguityReason | undefined {
    return this.fenced;
  }

  /**
   * The local gate. Returns a decision rather than throwing, so that a caller cannot admit an
   * action by forgetting to catch.
   */
  evaluate(action: string, monotonicNow: number): Decision {
    if (this.fenced !== undefined) {
      return { admitted: false, refusal: 'LEASE_EXPIRED', detail: `fenced: ${this.fenced}` };
    }
    if (this.lease === undefined) {
      return { admitted: false, refusal: 'NOT_LEASED', detail: 'no lease has been adopted' };
    }
    if (monotonicNow < this.lastMonotonic) {
      // A monotonic clock going backwards is not a late lease, it is a broken premise. Fencing is
      // the only honest response: we can no longer reason about when this lease ends.
      this.fenced = 'CLOCK_DISCONTINUITY';
      return {
        admitted: false,
        refusal: 'LEASE_EXPIRED',
        detail:
          `the monotonic clock went backwards (${this.lastMonotonic} -> ${monotonicNow}); ` +
          'lease deadlines cannot be evaluated and the desktop is fenced',
      };
    }
    this.lastMonotonic = monotonicNow;

    if (monotonicNow >= this.lease.deadlineMonotonic) {
      return {
        admitted: false,
        refusal: 'LEASE_EXPIRED',
        detail: 'the local lease deadline has passed; a partition is not permission to keep typing',
      };
    }
    if (this.cancelRequested) {
      return {
        admitted: false,
        refusal: 'CANCELLATION_REQUESTED',
        detail: 'cancellation requested; effects already performed remain recorded',
      };
    }
    if (this.inFlight !== undefined) {
      return {
        admitted: false,
        refusal: 'ACTION_IN_FLIGHT',
        detail: `action ${this.inFlight} is dispatched and unresolved`,
      };
    }
    if (this.actionsUsed >= this.lease.maxActions) {
      return {
        admitted: false,
        refusal: 'ACTION_BUDGET_EXHAUSTED',
        detail: `${this.actionsUsed} of ${this.lease.maxActions} actions used`,
      };
    }
    if (this.wallTimeUsedSeconds >= this.lease.maxWallTimeSeconds) {
      return { admitted: false, refusal: 'WALL_TIME_BUDGET_EXHAUSTED' };
    }
    if (!(ALLOWED_ACTIONS as readonly string[]).includes(action)) {
      return {
        admitted: false,
        refusal: 'ACTION_NOT_ALLOWED',
        detail: `${action} is outside the sealed action policy`,
      };
    }
    return { admitted: true };
  }

  /**
   * Gate, journal, dispatch — in that order, and the order is the mechanism.
   *
   * The intent is flushed to the device before `dispatch` is called. If the process dies in between,
   * the journal says an action was intended and its result is unknown, which is the truth. If the
   * intent were written after dispatch, the same crash would leave no trace of a keystroke that may
   * have landed.
   */
  async performAction(
    action: AllowedAction,
    options: { keyChord?: string; text?: string } = {},
  ): Promise<DispatchOutcome> {
    const monotonicNow = this.options.clock.monotonic();
    const decision = this.evaluate(action, monotonicNow);
    if (!decision.admitted) {
      return {
        status: 'REFUSED',
        ...(decision.refusal !== undefined ? { refusal: decision.refusal } : {}),
        ...(decision.detail !== undefined ? { detail: decision.detail } : {}),
      };
    }

    const lease = this.lease;
    if (lease === undefined) {
      // Unreachable: evaluate() returns NOT_LEASED first. Narrowing for the type checker rather
      // than a real branch, and it throws rather than guessing if that ever changes.
      throw new Error('no lease');
    }

    this.sequence += 1;
    const actionId = `${lease.leaseId}:${lease.epoch}:${this.sequence}`;
    const entry: JournalEntry = {
      actionId,
      leaseId: lease.leaseId,
      epoch: lease.epoch,
      sequence: this.sequence,
      action,
      ...(options.keyChord !== undefined ? { keyChord: options.keyChord } : {}),
      ...(options.text !== undefined ? { text: options.text } : {}),
      intentAtUtc: this.options.clock.utc(),
    };

    // In flight *before* the flush, not after. The flush is real I/O, and a cancellation arriving
    // during it would otherwise find `inFlight` unset and `mayAcknowledgeStop()` true -- reporting a
    // clean stop for a keystroke that is about to be dispatched. The supervisor is committed to this
    // action from the moment it decides to journal it, so that is when the state must say so.
    this.inFlight = actionId;
    this.actionsUsed += 1;
    await this.options.journal.appendAndFlush(entry);

    const command: ActionCommand = {
      actionId,
      sequence: this.sequence,
      action,
      ...(options.keyChord !== undefined ? { keyChord: options.keyChord } : {}),
      ...(options.text !== undefined ? { text: options.text } : {}),
    };

    let status: 'SUCCEEDED' | 'FAILED' | 'AMBIGUOUS';
    let ambiguity: AmbiguityReason | undefined;
    try {
      status = await withTimeout(
        this.options.dispatch(command),
        this.options.actionTimeoutMs,
        this.options.clock,
      );
    } catch {
      // Every failure to obtain a result is the same situation: the keystroke may have landed. A
      // rejected promise is not evidence that the operating system did nothing.
      status = 'AMBIGUOUS';
      ambiguity = 'ACTION_RESULT_NEVER_ARRIVED';
      this.fenced = ambiguity;
    }

    const dispatchedAtMonotonic = this.options.clock.monotonic();
    this.wallTimeUsedSeconds = (dispatchedAtMonotonic - this.startedMonotonic) / 1000;
    await this.options.journal.appendAndFlush({
      ...entry,
      dispatchedAtMonotonic,
      result: status,
      ...(ambiguity !== undefined ? { ambiguityReason: ambiguity } : {}),
    });
    this.inFlight = undefined;

    if (status === 'AMBIGUOUS') {
      return {
        status: 'AMBIGUOUS',
        ...(ambiguity !== undefined ? { ambiguityReason: ambiguity } : {}),
        detail:
          `no result for ${action} within ${this.options.actionTimeoutMs}ms. ` +
          (CONSEQUENTIAL_ACTIONS.includes(action)
            ? 'This action may have taken effect in the application under test and will not be ' +
              'repeated.'
            : 'This action may have taken effect and will not be repeated: a second press would ' +
              'make neither announcement attributable.'),
      };
    }
    return { status };
  }
}

/**
 * What a restarted supervisor must do with its journal.
 *
 * Deliberately returns a report rather than performing recovery. There is no recovery to perform:
 * an intent with no result is permanently unknown, and the only correct response is to terminalize
 * the attempt INTERRUPTED and quarantine the desktop. A function that "recovered" would be a place
 * for someone to later add a retry.
 */
export interface RecoveryReport {
  readonly unresolved: readonly JournalEntry[];
  readonly mustQuarantine: boolean;
  readonly ambiguityReason?: AmbiguityReason;
  readonly mayResumeThisAttempt: false;
  readonly explanation: string;
}

export async function inspectJournalAfterRestart(journal: Journal): Promise<RecoveryReport> {
  const entries = await journal.read();
  const latest = new Map<string, JournalEntry>();
  for (const entry of entries) {
    latest.set(entry.actionId, entry);
  }
  const unresolved = [...latest.values()].filter((e) => e.result === undefined);

  if (unresolved.length === 0) {
    return {
      unresolved: [],
      mustQuarantine: false,
      mayResumeThisAttempt: false,
      explanation:
        'No action was left unresolved. The attempt is still not resumable — a restart invalidates ' +
        'the attempt and a fresh environment and run are required after authorization checks — but ' +
        'the desktop itself needs no quarantine.',
    };
  }

  const names = unresolved.map((e) => e.action).join(', ');
  return {
    unresolved,
    mustQuarantine: true,
    ambiguityReason: 'SUPERVISOR_CRASHED_AFTER_INTENT',
    mayResumeThisAttempt: false,
    explanation:
      `${unresolved.length} action(s) were journaled and never resolved (${names}). Whether the ` +
      'operating system received them is unknown, so the attempt terminalizes INTERRUPTED with an ' +
      'ambiguity reason and the desktop is quarantined until a trusted reset proves the previous ' +
      'activity cannot continue. None of these actions is re-dispatched.',
  };
}

async function withTimeout<T>(promise: Promise<T>, ms: number, clock: Clock): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(
          () => reject(new Error(`no result within ${ms}ms (at ${clock.utc()})`)),
          ms,
        );
      }),
    ]);
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  }
}
