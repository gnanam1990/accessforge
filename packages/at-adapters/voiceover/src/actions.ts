/**
 * Mapping the canonical action vocabulary onto VoiceOver, and the chord allowlist.
 *
 * Two separate jobs that are easy to confuse. The *action* vocabulary is the eight values from
 * CONTRACTS section 6 and is platform-independent. The *chord* allowlist is what a `KEY_CHORD` may
 * carry on this platform, and it is where an escape from the sandbox would actually happen: `CMD+L`
 * reaches the address bar, `CMD+OPT+I` opens developer tools, `CMD+V` reads the clipboard, `CMD+Q`
 * quits the browser. None of those are accessibility navigation and all of them are one typo away
 * from being in a list like this.
 *
 * So the allowlist is written as an exhaustive enumeration of permitted chords rather than as a
 * denylist of forbidden ones. A denylist is wrong by construction here: it has to anticipate every
 * dangerous shortcut in an operating system nobody controls, and it fails open on the one nobody
 * thought of.
 */

import { assertRealReaderProven } from './profile.js';

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
 * The complete set of chords a navigator may request on this profile.
 *
 * `CTRL+OPT+RIGHT` and `CTRL+OPT+LEFT` are VoiceOver's own item navigation. `TAB`, `SHIFT+TAB`,
 * `ENTER`, `SPACE` and `ESCAPE` are the keys a person filling in a form actually presses. That is
 * the entire justification for each entry, and an entry without one does not belong here.
 */
export const ALLOWED_CHORDS: readonly string[] = [
  'TAB',
  'SHIFT+TAB',
  'ENTER',
  'SPACE',
  'ESCAPE',
  'CTRL+OPT+RIGHT',
  'CTRL+OPT+LEFT',
];

/**
 * Chords that are refused with a specific explanation rather than a generic one.
 *
 * Not a security mechanism — `ALLOWED_CHORDS` is, and anything absent from it is refused whether or
 * not it appears here. This exists so that a navigator asking for developer tools is told *why* that
 * is not an accessibility action, which is information a reviewer reading the trace wants.
 */
export const EXPLAINED_REFUSALS: Readonly<Record<string, string>> = {
  'CMD+L': 'reaches the address bar, which would let the navigator navigate outside the journey',
  'CMD+OPT+I': 'opens developer tools, which would expose the DOM the navigator must not see',
  'CMD+OPT+U': 'opens view-source, which would expose the DOM the navigator must not see',
  'CMD+V': 'reads the clipboard, which is outside the sealed observation policy',
  'CMD+C': 'writes the clipboard, which leaves state behind outside the fixture',
  'CMD+Q': 'quits the browser, which ends the run without a recorded result',
  'CMD+W': 'closes the window the run is being observed through',
  'CMD+SHIFT+3': 'takes a screenshot, which is pixel evidence the navigator may not receive',
  'CMD+TAB': 'switches application, which takes focus off the desktop the lease covers',
  'CMD+SPACE': 'opens Spotlight, which can launch anything on the machine',
};

export class ActionRefused extends Error {
  constructor(
    message: string,
    readonly code: 'ACTION_NOT_ALLOWED' | 'KEY_CHORD_NOT_ALLOWED' | 'MALFORMED',
  ) {
    super(message);
  }
}

export interface ActionRequest {
  readonly action: string;
  readonly keyChord?: string;
  readonly text?: string;
}

/**
 * Validate one action against this platform's policy. Raises; does not sanitize.
 *
 * Sanitizing would be the wrong shape. An action outside the policy is not a request to be trimmed
 * into an acceptable one — it is evidence that something upstream is trying to do what the policy
 * forbids, and the trace should record the attempt rather than a quietly narrowed version of it.
 */
export function assertActionPermitted(request: ActionRequest): void {
  if (!(ALLOWED_ACTIONS as readonly string[]).includes(request.action)) {
    throw new ActionRefused(
      `${request.action} is not in the sealed action policy. The vocabulary is ` +
        `${ALLOWED_ACTIONS.join(', ')} and it is not extensible by a caller (INV-01).`,
      'ACTION_NOT_ALLOWED',
    );
  }

  if (request.action === 'KEY_CHORD') {
    const chord = request.keyChord;
    if (chord === undefined || chord.trim() === '') {
      throw new ActionRefused('KEY_CHORD requires a chord', 'MALFORMED');
    }
    if (!ALLOWED_CHORDS.includes(chord)) {
      const why = EXPLAINED_REFUSALS[chord];
      throw new ActionRefused(
        why === undefined
          ? `${chord} is not in this profile's chord allowlist. The list is an enumeration of ` +
            'permitted reader and form-navigation chords, not a denylist of dangerous ones, so an ' +
            'unlisted chord is refused whether or not anyone anticipated it.'
          : `${chord} is refused: it ${why}.`,
        'KEY_CHORD_NOT_ALLOWED',
      );
    }
  }

  if (request.action === 'TYPE_TEXT' && (request.text === undefined || request.text === '')) {
    throw new ActionRefused('TYPE_TEXT requires text', 'MALFORMED');
  }
}

/**
 * The Guidepup call each action maps to.
 *
 * A data table rather than a switch, so that the mapping is inspectable by a test and by a reviewer
 * without reading control flow. `STOP` maps to nothing: stopping is the supervisor tearing down its
 * own session, not a keystroke sent to a screen reader.
 */
export const GUIDEPUP_MAPPING: Readonly<Record<AllowedAction, string>> = {
  NEXT: 'voiceOver.next()',
  PREVIOUS: 'voiceOver.previous()',
  ACTIVATE: 'voiceOver.act()',
  TYPE_TEXT: 'voiceOver.type(text)',
  KEY_CHORD: 'voiceOver.press(chord)',
  READ_CURRENT: 'voiceOver.itemText()',
  WAIT_FOR_READER_IDLE: 'voiceOver.lastSpokenPhrase() polled to quiescence',
  STOP: 'supervisor teardown; no reader call',
};

/**
 * Dispatch one action to a real VoiceOver.
 *
 * Validates, then refuses, every time, in this build. The validation runs *first* on purpose: a
 * forbidden chord must be refused as a policy violation rather than as "no reader available", because
 * those are different findings and a reviewer reading the trace needs to see which one happened.
 * Modules 12 and later will read these refusals, and a policy breach reported as an infrastructure
 * gap would be a real misdiagnosis.
 */
export async function dispatch(request: ActionRequest): Promise<'SUCCEEDED' | 'FAILED'> {
  assertActionPermitted(request);
  assertRealReaderProven();
  // Unreachable while PROFILE_STATUS is BLOCKED. When a real reader is configured, the Guidepup call
  // from GUIDEPUP_MAPPING goes here -- and nothing else does: no DOM click, no synthesized speech, no
  // fallback to a virtual reader. The module prompt forbids each of those by name.
  throw new Error('unreachable: assertRealReaderProven must have thrown');
}
