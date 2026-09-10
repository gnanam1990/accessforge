/**
 * The exact configuration this adapter has been written against.
 *
 * The module prompt asks for a pinned matrix and is explicit about why: "record exact configuration
 * rather than claiming universal macOS support". A screen reader's announcements are a function of
 * its version, the browser's version, the locale, the keyboard layout and the verbosity setting.
 * "Works on macOS" is not a claim anyone can check and not a claim this adapter makes.
 *
 * Two fields carry the honesty of this file:
 *
 * - `verifiedOn` is empty. Nothing in this matrix has been observed on a real desktop, because
 *   VoiceOver has never been configured on the host that built it. A version listed here is the
 *   version this code was *written against*, not one it has been proved against.
 * - `status` is BLOCKED, and `assertRealReaderProven` throws. There is no path through this package
 *   that yields a reader observation without a real reader, and no default that quietly becomes
 *   "supported" when someone forgets to update it.
 */

export type ProfileStatus = 'BLOCKED' | 'VERIFIED';

export interface PlatformMatrix {
  readonly macos: string;
  readonly browser: string;
  readonly browserVersion: string;
  readonly voiceOver: string;
  readonly node: string;
  readonly guidepup: string;
  readonly locale: string;
  readonly keyboardLayout: string;
  readonly verbosity: string;
}

/**
 * The OS version and build as separate values.
 *
 * Separate because they are compared differently. `TARGET_MATRIX.macos` is prose for a human reading
 * a handoff; the comparison in preflight needs the version alone, and doing it against the prose
 * string is how a prefix match let macOS 26 pass as macOS 26.6.
 */
export const TARGET_MACOS_VERSION = '26.6';
export const TARGET_MACOS_BUILD = '25G72';

/**
 * The configuration the code targets.
 *
 * `voiceOver` says "bundled with macOS 26.6" rather than a number because VoiceOver does not ship a
 * version independent of the operating system; pinning an invented number would look more precise
 * and be less true.
 */
export const TARGET_MATRIX: PlatformMatrix = {
  macos: `${TARGET_MACOS_VERSION} (build ${TARGET_MACOS_BUILD})`,
  browser: 'Safari',
  browserVersion: '26.6',
  voiceOver: 'bundled with macOS 26.6',
  node: '22.23.1',
  guidepup: '@guidepup/guidepup 0.34.0',
  locale: 'en-US',
  keyboardLayout: 'ANSI',
  verbosity: 'default, punctuation "some", speech rate default',
};

/**
 * Matrices on which a real VoiceOver trace has actually been captured.
 *
 * Deliberately empty, and a test asserts it is empty *and* that the status agrees with it. When a
 * real trace is captured this array gains an entry and `PROFILE_STATUS` becomes VERIFIED in the same
 * commit; neither is meaningful alone.
 */
export const VERIFIED_MATRICES: readonly PlatformMatrix[] = [];

export const PROFILE_STATUS: ProfileStatus =
  VERIFIED_MATRICES.length > 0 ? 'VERIFIED' : 'BLOCKED';

/**
 * Why the real-reader capability is blocked, in the words an operator needs to act on.
 *
 * This string is surfaced by preflight, by the runner's unavailable reason, and by the handoff. One
 * place, so the three cannot drift into disagreeing about what is wrong.
 */
export const BLOCKED_REASON =
  'VoiceOver has never been configured on this host: there is no preferences file at either the ' +
  'legacy path or the macOS Sequoia+ Group Containers path, which means VoiceOver has not been run. ' +
  'Real-reader execution additionally requires "Allow VoiceOver to be controlled with AppleScript" ' +
  'in VoiceOver Utility > General, Accessibility and Automation permissions granted to the ' +
  'controlling process, and a dedicated signed-in desktop session that is not the operator\'s own ' +
  'working session. None of these can be enabled by this software: each is a security setting that ' +
  'must be granted by the person at the machine.';

export class RealReaderUnavailable extends Error {}

/**
 * The gate every real-reader path passes through.
 *
 * A function rather than a constant so that it cannot be read once at import time and cached into a
 * boolean somebody later inverts. It throws rather than returning false because the callers that
 * matter are the ones that would otherwise carry on with a synthesized observation.
 */
export function assertRealReaderProven(): void {
  if (PROFILE_STATUS !== 'VERIFIED') {
    throw new RealReaderUnavailable(
      `actual VoiceOver execution is BLOCKED. ${BLOCKED_REASON} ` +
        'No observation produced without a real reader may be recorded as reader evidence (INV-02).',
    );
  }
}

/** What a PASS on this profile would and would not establish, if one ever existed. */
export const PROFILE_SCOPE_STATEMENT =
  'A result on this profile describes one journey, driven by VoiceOver bundled with the pinned ' +
  'macOS build, in the pinned Safari version, at the pinned locale, keyboard layout and verbosity. ' +
  'It is not a statement about other readers, other versions, other locales, other assistive ' +
  'technologies, users with disabilities in general, or legal compliance.';
