/**
 * The navigator channel: what a reader observation becomes before the navigator sees it.
 *
 * CONTRACTS section 6 lists what must never reach the navigator in screen-reader-only mode: DOM,
 * source, selectors, screenshots, observer secrets and fixture answer keys. This file is the single
 * place that decides what crosses, and it is written as a **construction** rather than a filter.
 *
 * That distinction is the whole design. A filter takes a rich object and removes the dangerous
 * fields, which means a field added upstream is included by default and the next person to add one
 * has to remember this file exists. A construction names the three fields that may cross and builds
 * a new object out of them, so a field added upstream is excluded by default and reaching the
 * navigator requires an edit here, in a file whose tests are about exactly that.
 *
 * Bounded length is part of the contract too. An unbounded announcement is a channel: a page that
 * rendered its own source into an ARIA label could hand the navigator the DOM through the one route
 * that is supposed to be safe.
 */

/** The longest announcement that crosses. Longer ones are truncated and marked. */
export const MAX_PHRASE_LENGTH = 1024;

/** What VoiceOver produced, as the adapter captured it. Never given to the navigator as-is. */
export interface RawObservation {
  readonly phrase: string;
  readonly capturedAtUtc: string;
  readonly actionId: string;
  readonly actionSequence: number;
  /** Everything below is diagnostic and must not cross the boundary. */
  readonly accessibilityTree?: unknown;
  readonly domSnapshot?: string;
  readonly screenshotPath?: string;
  readonly selector?: string;
  readonly observerConfig?: Record<string, string>;
  readonly fixtureAnswers?: Record<string, string>;
}

/**
 * Exactly what the navigator receives.
 *
 * Three fields. `provenance` is present because the navigator must be able to tell an actual reader
 * observation from anything else — CONTRACTS requires provenance on this channel — and because a
 * later reviewer reading a navigator transcript needs to know the announcement was real.
 */
export interface NavigatorObservation {
  readonly phrase: string;
  readonly provenance: 'ACTUAL_READER';
  readonly truncated: boolean;
}

/**
 * Build the navigator's view of one observation.
 *
 * Note what this function does not do: it does not take the raw observation and delete keys from it.
 * It reads two values and returns a new object. `RawObservation` could grow ten diagnostic fields
 * tomorrow and none of them would appear here.
 */
export function projectForNavigator(raw: RawObservation): NavigatorObservation {
  const truncated = raw.phrase.length > MAX_PHRASE_LENGTH;
  return {
    phrase: truncated ? raw.phrase.slice(0, MAX_PHRASE_LENGTH) : raw.phrase,
    provenance: 'ACTUAL_READER',
    truncated,
  };
}

/**
 * A speech capture that timed out.
 *
 * Returns an explicit unknown rather than an empty phrase, because those mean opposite things. An
 * empty announcement says the reader said nothing, which is a real and sometimes correct observation
 * — an unlabelled control announces nothing. A timeout says we do not know what it said. Collapsing
 * the second into the first manufactures an observation, and module 11 would evaluate a missing
 * announcement as FALSE rather than UNKNOWN, turning an infrastructure gap into a reported
 * accessibility defect (INV-02).
 */
export interface UnknownObservation {
  readonly provenance: 'CAPTURE_UNKNOWN';
  readonly reason: string;
}

export function captureTimedOut(timeoutMs: number): UnknownObservation {
  return {
    provenance: 'CAPTURE_UNKNOWN',
    reason:
      `no speech was captured within ${timeoutMs}ms. This is unknown evidence, not silence: an ` +
      'unlabelled control genuinely announcing nothing and a capture that failed are different ' +
      'observations, and reporting the second as the first would turn an infrastructure gap into a ' +
      'reported accessibility defect.',
  };
}

/**
 * Fields on `RawObservation` that must never appear in a navigator projection.
 *
 * Exported so a test can assert over the list rather than spot-checking whichever ones the author
 * happened to think of.
 */
export const FORBIDDEN_ON_NAVIGATOR_CHANNEL: readonly string[] = [
  'accessibilityTree',
  'domSnapshot',
  'screenshotPath',
  'selector',
  'observerConfig',
  'fixtureAnswers',
  'capturedAtUtc',
  'actionId',
  'actionSequence',
];
