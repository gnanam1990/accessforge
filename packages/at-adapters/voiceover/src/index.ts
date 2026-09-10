/**
 * The macOS VoiceOver adapter.
 *
 * **Real-reader execution is BLOCKED on the host this was written on.** VoiceOver has never been
 * configured there, and enabling it, granting Accessibility and Automation permissions, and
 * dedicating a signed-in desktop session are all things only the person at the machine can do.
 *
 * What exists here is everything that can be built and tested without a reader: the pinned platform
 * matrix, the preflight probes, the action and chord policy, and the navigator projection. What does
 * not exist is a single reader observation. `assertRealReaderProven` stands between this package and
 * any claim otherwise, and `dispatch` calls it before it could return anything.
 *
 * The temptation this package is shaped to refuse is the virtual screen reader.
 * `@guidepup/virtual-screen-reader` runs anywhere and produces plausible announcements, and using it
 * to make these tests green would produce a system that reports accessibility results for a reader
 * nobody uses. The module prompt allows it in labelled unit tests only, and this package does not
 * depend on it at all.
 */

export {
  BLOCKED_REASON,
  PROFILE_SCOPE_STATEMENT,
  PROFILE_STATUS,
  RealReaderUnavailable,
  TARGET_MACOS_BUILD,
  TARGET_MACOS_VERSION,
  TARGET_MATRIX,
  VERIFIED_MATRICES,
  assertRealReaderProven,
} from './profile.js';
export type { PlatformMatrix, ProfileStatus } from './profile.js';

export {
  PREFLIGHT_CHECKS,
  hostEnvironment,
  probeDesktopOwned,
  probePermission,
  probeReaderActive,
  probeReaderVersion,
  probeScreenUnlocked,
  runPreflight,
  voiceOverPreferencePaths,
} from './preflight.js';
export type {
  Condition,
  PreflightCheck,
  PreflightReport,
  ProbeEnvironment,
  ProbeResult,
} from './preflight.js';

export {
  ALLOWED_ACTIONS,
  ALLOWED_CHORDS,
  ActionRefused,
  EXPLAINED_REFUSALS,
  GUIDEPUP_MAPPING,
  assertActionPermitted,
  dispatch,
} from './actions.js';
export type { ActionRequest, AllowedAction } from './actions.js';

export {
  FORBIDDEN_ON_NAVIGATOR_CHANNEL,
  MAX_PHRASE_LENGTH,
  captureTimedOut,
  projectForNavigator,
} from './projection.js';
export type { NavigatorObservation, RawObservation, UnknownObservation } from './projection.js';
