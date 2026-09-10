/**
 * The Windows NVDA adapter.
 *
 * **Real integration is BLOCKED, and more completely than module 08's.** There is no Windows host
 * available at all — not a machine without NVDA installed, but no Windows machine, and no
 * virtualization software to run one. Module 08 at least runs on the platform it targets.
 *
 * The module prompt's stop condition is explicit about what to do in that case: "If Windows access or
 * setup authority is unavailable, finish only independent adapter/contract work and mark real
 * integration BLOCKED." So this file is the contract work and nothing more.
 *
 * ## What this package deliberately does not do
 *
 * It does not copy module 08. The prompt says to "share protocol contracts with module 07, not
 * platform-specific assumptions copied from VoiceOver", and the difference is not cosmetic:
 *
 * - **Different chords.** VoiceOver navigates with `CTRL+OPT+ARROW`; NVDA uses the browse-mode
 *   arrow keys and the NVDA modifier. A chord list copied across would be simultaneously too
 *   permissive (allowing macOS combinations Windows maps elsewhere) and useless (omitting the keys
 *   NVDA actually needs).
 * - **A different unusable-desktop problem.** macOS worries about a locked screen. Windows worries
 *   about that *and* about a disconnected Remote Desktop session, where the session keeps running
 *   with no console attached and automation silently does nothing.
 * - **Different session identity.** A Terminal Services session id, not an audit session id.
 *
 * It also does not install `@guidepup/virtual-screen-reader` and call the result NVDA support. The
 * prompt forbids exactly that — "do not install a virtual reader and advertise R1 support" — and it
 * is the most tempting shortcut available, because a virtual reader would run on this host and make
 * every test here green while proving nothing about a screen reader anybody uses.
 */

export type ProfileStatus = 'BLOCKED' | 'VERIFIED';

export interface WindowsMatrix {
  readonly windows: string;
  readonly nvda: string;
  readonly browser: string;
  readonly browserVersion: string;
  readonly node: string;
  readonly guidepup: string;
  readonly locale: string;
  readonly keyboardLayout: string;
  readonly nvdaModifier: 'INSERT' | 'CAPSLOCK';
}

/**
 * Matrices on which a real NVDA trace has been captured. Empty, and the status is derived from it,
 * so the two cannot be edited apart.
 */
export const VERIFIED_MATRICES: readonly WindowsMatrix[] = [];

export const PROFILE_STATUS: ProfileStatus =
  VERIFIED_MATRICES.length > 0 ? 'VERIFIED' : 'BLOCKED';

export const BLOCKED_REASON =
  'No Windows host is available: not a Windows machine missing NVDA, but no Windows machine at all, ' +
  'and no virtualization software on this host to run one. Real NVDA integration requires a signed-in ' +
  'interactive Windows session -- not a service session and not a disconnected Remote Desktop ' +
  'session -- with NVDA installed and the browser focused. Nothing in this package can create that.';

export class RealReaderUnavailable extends Error {}

export function assertRealReaderProven(): void {
  if (PROFILE_STATUS !== 'VERIFIED') {
    throw new RealReaderUnavailable(
      `actual NVDA execution is BLOCKED. ${BLOCKED_REASON} No observation produced without a real ` +
        'reader may be recorded as reader evidence (INV-02).',
    );
  }
}

/**
 * Whether module 25 may count the NVDA benchmark as executed.
 *
 * The module prompt asks for this answer explicitly and by name, because a benchmark table with an
 * NVDA row that was never run is the kind of artifact that outlives the caveat printed beside it.
 */
export const MODULE_25_MAY_COUNT_NVDA_BENCHMARK = false;

/**
 * Windows-specific chord allowlist.
 *
 * Derived from what NVDA browse mode and Windows form navigation actually need, not from module 08's
 * list. `NVDA+DOWN` is say-all; the bare arrows move the browse cursor; `TAB` and `ENTER` are what a
 * person filling in a form presses.
 */
export const ALLOWED_CHORDS: readonly string[] = [
  'TAB',
  'SHIFT+TAB',
  'ENTER',
  'SPACE',
  'ESCAPE',
  'DOWN',
  'UP',
  'NVDA+DOWN',
];

/**
 * Windows shortcuts that must never be reachable, with the reason each one matters here.
 *
 * Not a security mechanism — the allowlist is — but a Windows-specific list, because the dangerous
 * combinations are different from macOS's. `CTRL+L` is the address bar, `F12` is developer tools,
 * `WIN+R` opens Run, and `CTRL+ALT+DEL` is intercepted by the operating system before any application
 * sees it, which makes it a good demonstration that an allowlist is the only workable approach:
 * a denylist cannot block what never reaches it.
 */
export const EXPLAINED_REFUSALS: Readonly<Record<string, string>> = {
  'CTRL+L': 'reaches the address bar, allowing navigation outside the journey',
  F12: 'opens developer tools, exposing the DOM the navigator must not see',
  'CTRL+U': 'opens view-source, exposing the DOM the navigator must not see',
  'CTRL+V': 'reads the clipboard, which is outside the sealed observation policy',
  'WIN+R': 'opens the Run dialog, which can launch anything on the machine',
  'ALT+F4': 'closes the window the run is being observed through',
  'ALT+TAB': 'switches application, taking focus off the desktop the lease covers',
  'CTRL+ALT+DEL': 'is intercepted by Windows itself and cannot be sent by an application at all',
};

/**
 * Ways a Windows desktop can be present but unusable.
 *
 * The second entry is the one with no macOS equivalent and the reason this enum exists. A
 * disconnected Remote Desktop session keeps running: processes execute, the automation API responds,
 * and there is no console attached to receive input. Automation against it does nothing, quietly, and
 * produces an empty trace rather than an error — which module 11 would read as a missing announcement
 * and therefore as an accessibility failure.
 */
export const UNUSABLE_DESKTOP_STATES = [
  'SCREEN_LOCKED',
  'RDP_SESSION_DISCONNECTED',
  'SESSION_IS_A_SERVICE_SESSION',
  'NO_INTERACTIVE_LOGON',
  'BROWSER_NOT_FOCUSED',
] as const;

export type UnusableDesktopState = (typeof UNUSABLE_DESKTOP_STATES)[number];

export const UNUSABLE_DESKTOP_EXPLANATIONS: Readonly<Record<UnusableDesktopState, string>> = {
  SCREEN_LOCKED: 'a locked screen swallows input; the trace would be empty rather than failing',
  RDP_SESSION_DISCONNECTED:
    'a disconnected Remote Desktop session keeps running with no console attached. Automation ' +
    'returns success and nothing reaches a screen, so the run produces an empty trace that reads ' +
    'as a missing announcement rather than as infrastructure failure.',
  SESSION_IS_A_SERVICE_SESSION:
    'session 0 is the Windows service session. It has no interactive desktop, and a background ' +
    'service is not automatically a usable desktop however healthy it reports itself to be.',
  NO_INTERACTIVE_LOGON: 'no user is signed in, so there is no desktop for a screen reader to read',
  BROWSER_NOT_FOCUSED: 'NVDA reads the focused window; an unfocused browser is not under test',
};

/**
 * Whether a set of observed desktop states permits a run.
 *
 * Fails closed on an empty observation set: knowing nothing about a desktop is not the same as
 * knowing it is fine, and this is the function a caller would reach for when its probes had not run.
 */
export function desktopIsUsable(
  observed: readonly UnusableDesktopState[] | undefined,
): { usable: boolean; reasons: readonly string[] } {
  if (observed === undefined) {
    return {
      usable: false,
      reasons: [
        'no desktop state was observed at all. An unobserved desktop is not a usable one: every ' +
          'state in this list is invisible from outside the session it describes.',
      ],
    };
  }
  if (observed.length === 0) {
    return { usable: true, reasons: [] };
  }
  return {
    usable: false,
    reasons: observed.map((state) => `${state}: ${UNUSABLE_DESKTOP_EXPLANATIONS[state]}`),
  };
}

export class ActionRefused extends Error {
  constructor(
    message: string,
    readonly code: 'ACTION_NOT_ALLOWED' | 'KEY_CHORD_NOT_ALLOWED' | 'MALFORMED',
  ) {
    super(message);
  }
}

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

export interface ActionRequest {
  readonly action: string;
  readonly keyChord?: string;
  readonly text?: string;
}

export function assertActionPermitted(request: ActionRequest): void {
  if (!(ALLOWED_ACTIONS as readonly string[]).includes(request.action)) {
    throw new ActionRefused(
      `${request.action} is not in the sealed action policy (INV-01)`,
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
          ? `${chord} is not in the Windows chord allowlist. The list enumerates permitted NVDA and ` +
            'form-navigation chords rather than denying dangerous ones, so an unlisted chord is ' +
            'refused whether or not anyone anticipated it.'
          : `${chord} is refused: it ${why}.`,
        'KEY_CHORD_NOT_ALLOWED',
      );
    }
  }
  if (request.action === 'TYPE_TEXT' && (request.text === undefined || request.text === '')) {
    throw new ActionRefused('TYPE_TEXT requires text', 'MALFORMED');
  }
}

export async function dispatch(request: ActionRequest): Promise<'SUCCEEDED' | 'FAILED'> {
  assertActionPermitted(request);
  assertRealReaderProven();
  throw new Error('unreachable: assertRealReaderProven must have thrown');
}

/**
 * How announcements from the two readers may be compared.
 *
 * The prompt requires comparison "through frozen assertions, not byte-for-byte equality with
 * VoiceOver wording", and the reason is that the readers genuinely say different things about the
 * same correct page. VoiceOver might say "Email, invalid data, edit text"; NVDA might say "Email
 * edit invalid entry". Requiring the strings to match would make every correct Windows page fail, and
 * -- worse -- would push someone to normalize NVDA's wording into VoiceOver's, which is manufacturing
 * an observation.
 */
export const CROSS_READER_COMPARISON_RULE =
  'Assertions are evaluated per reader against the frozen assertion set. Two readers announcing the ' +
  'same correct page in different words both satisfy the assertion; neither is normalized into the ' +
  "other's phrasing. A cross-reader difference is a difference in reported outcome, never a " +
  'difference in transcript text.';
