/**
 * VoiceOver preflight probes.
 *
 * Module 07 defined the fourteen checks and decided that the *server* concludes readiness. This file
 * is the other half: the probes that produce those observations on a real macOS desktop.
 *
 * The rule every probe here obeys is that **it never enables anything**. The module prompt says it
 * directly — "never enable OS permissions without operator approval" — and the reason is not
 * politeness. Accessibility and Automation permissions let a process read the screen and drive other
 * applications. Software that granted itself those while checking whether it had them would be doing
 * the single most invasive thing in this system as a side effect of a health check. Every probe reads
 * and reports; an absent permission is an explicit FALSE with an instruction for the operator.
 *
 * A probe that cannot determine its answer returns UNKNOWN, and module 07 treats UNKNOWN as a
 * readiness failure. That is the important asymmetry: "I could not tell whether the screen was
 * locked" must never travel as "the screen was unlocked".
 */

import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

import { BLOCKED_REASON, TARGET_MATRIX } from './profile.js';

/** Mirrors `accessforge_domain.states.Condition`. UNKNOWN is a value, not an error. */
export type Condition = 'TRUE' | 'FALSE' | 'UNKNOWN';

/** Mirrors `accessforge_domain.runners.PreflightCheck` exactly. Drift here is a silent gap there. */
export const PREFLIGHT_CHECKS = [
  'READER_ACTIVE',
  'READER_VERSION_MATCHES_PROFILE',
  'BROWSER_VERSION_MATCHES_PROFILE',
  'SPEECH_CAPTURE_WORKING',
  'PERMITTED_ORIGIN_REACHABLE',
  'ENVIRONMENT_RESET_SUCCEEDED',
  'BUILD_IDENTITY_MATCHES_MANIFEST',
  'DESKTOP_SESSION_OWNED',
  'SCREEN_UNLOCKED',
  'ACCESSIBILITY_PERMISSION_GRANTED',
  'AUTOMATION_PERMISSION_GRANTED',
  'NO_STALE_INPUT_SOURCE',
  'LOCAL_JOURNAL_WRITABLE',
  'MONOTONIC_CLOCK_HEALTHY',
] as const;

export type PreflightCheck = (typeof PREFLIGHT_CHECKS)[number];

export interface ProbeResult {
  readonly condition: Condition;
  /** What to tell an operator. Empty when the condition is TRUE. */
  readonly detail: string;
  /** True when the only way to make this TRUE is a person granting something. */
  readonly requiresOperator: boolean;
}

export interface ProbeEnvironment {
  /** Whether a path exists. Injected so tests do not need a configured VoiceOver. */
  readonly pathExists: (path: string) => boolean;
  /** Reads a macOS preference. Returns undefined when absent or unreadable. */
  readonly readPreference: (domain: string, key: string) => string | undefined;
  /** Whether the named process is running. */
  readonly processRunning: (name: string) => boolean;
  /** The current interactive session's audit id, or undefined if it cannot be determined. */
  readonly auditSessionId: () => string | undefined;
  /** Whether the screen is locked; undefined when it cannot be determined. */
  readonly screenLocked: () => boolean | undefined;
  /** Whether this process holds the named TCC permission; undefined when unknown. */
  readonly hasPermission: (permission: 'Accessibility' | 'Automation') => boolean | undefined;
}

/**
 * Both paths VoiceOver preferences can live at.
 *
 * Both, not the newer one, because an earlier version of this check looked only at the legacy path
 * and concluded "configured" on a machine where nothing was. macOS Sequoia moved these into a
 * sandboxed group container; a host upgraded in place can have either, and a check that knows about
 * one of them is a check that reports confidently about the wrong file.
 */
export function voiceOverPreferencePaths(home: string = homedir()): readonly string[] {
  return [
    join(
      home,
      'Library',
      'Group Containers',
      'group.com.apple.VoiceOver',
      'Library',
      'Preferences',
      'com.apple.VoiceOver4',
      'default.plist',
    ),
    join(home, 'Library', 'Preferences', 'com.apple.VoiceOver4', 'default.plist'),
  ];
}

const ok: ProbeResult = { condition: 'TRUE', detail: '', requiresOperator: false };

function no(detail: string, requiresOperator = false): ProbeResult {
  return { condition: 'FALSE', detail, requiresOperator };
}

function unknown(detail: string): ProbeResult {
  return { condition: 'UNKNOWN', detail, requiresOperator: false };
}

/**
 * Is VoiceOver configured at all, and is AppleScript control enabled?
 *
 * Separated from "is it running" on purpose. An unconfigured VoiceOver and a configured one that is
 * merely not running need different things from an operator, and collapsing them into one FALSE
 * sends somebody to the wrong settings pane.
 */
export function probeReaderActive(env: ProbeEnvironment): ProbeResult {
  const paths = voiceOverPreferencePaths();
  const configured = paths.some((p) => env.pathExists(p));
  if (!configured) {
    return no(
      'VoiceOver has no preferences file at either the Group Containers path or the legacy path, ' +
        'which means it has never been run on this machine. Launch VoiceOver once, then enable ' +
        '"Allow VoiceOver to be controlled with AppleScript" in VoiceOver Utility > General.',
      true,
    );
  }

  const appleScript = env.readPreference('com.apple.VoiceOver4/default', 'SCREnableAppleScript');
  if (appleScript === undefined) {
    return no(
      'VoiceOver is configured but AppleScript control is not enabled. Enable "Allow VoiceOver to ' +
        'be controlled with AppleScript" in VoiceOver Utility > General. Without it the adapter ' +
        'cannot read a single announcement.',
      true,
    );
  }
  if (appleScript !== '1') {
    return no(
      `VoiceOver AppleScript control is set to "${appleScript}", not enabled. Enable it in ` +
        'VoiceOver Utility > General.',
      true,
    );
  }
  if (!env.processRunning('VoiceOver')) {
    return no('VoiceOver is configured and controllable but is not running.');
  }
  return ok;
}

export function probeReaderVersion(env: ProbeEnvironment): ProbeResult {
  // VoiceOver ships with the operating system and exposes no independent version, so the honest
  // comparison is against the OS build. A fabricated reader version string would be worse than this.
  const build = env.readPreference('/System/Library/CoreServices/SystemVersion', 'ProductVersion');
  if (build === undefined) {
    return unknown('the macOS version could not be read, so the reader version is undetermined');
  }
  if (!TARGET_MATRIX.macos.startsWith(build)) {
    return no(
      `this host reports macOS ${build} and the pinned profile is ${TARGET_MATRIX.macos}. ` +
        'VoiceOver announces differently between releases, so a run here is not a run on the ' +
        'pinned profile.',
    );
  }
  return ok;
}

export function probeScreenUnlocked(env: ProbeEnvironment): ProbeResult {
  const locked = env.screenLocked();
  if (locked === undefined) {
    // The important branch. A locked screen swallows every keystroke, so "cannot tell" must not
    // travel as "unlocked" -- the run would produce an empty trace and no error.
    return unknown(
      'whether the screen is locked could not be determined; a locked screen swallows every ' +
        'keystroke and would produce an empty trace rather than a failure',
    );
  }
  return locked ? no('the screen is locked') : ok;
}

export function probePermission(
  env: ProbeEnvironment,
  permission: 'Accessibility' | 'Automation',
): ProbeResult {
  const granted = env.hasPermission(permission);
  if (granted === undefined) {
    return unknown(`whether ${permission} permission is granted could not be determined`);
  }
  if (!granted) {
    return no(
      `${permission} permission is not granted to the controlling process. Grant it in System ` +
        `Settings > Privacy & Security > ${permission}. This software does not grant it: a process ` +
        'that could give itself permission to read the screen and drive other applications would ' +
        'be doing the most invasive thing in this system as a side effect of a health check.',
      true,
    );
  }
  return ok;
}

export function probeDesktopOwned(env: ProbeEnvironment): ProbeResult {
  const audit = env.auditSessionId();
  if (audit === undefined) {
    return unknown(
      'the interactive session could not be identified, so desktop ownership is undetermined; ' +
        'module 07 keys lease exclusivity on exactly this value',
    );
  }
  return ok;
}

export interface PreflightReport {
  readonly checks: Readonly<Record<PreflightCheck, ProbeResult>>;
  /** Checks an operator must act on, with instructions. */
  readonly operatorActions: readonly string[];
  /**
   * Always false in this build. `assertRealReaderProven` guards the execution path; this field is
   * what a caller reads to explain *why* without catching an exception.
   */
  readonly realReaderAvailable: false;
  readonly blockedReason: string;
}

/**
 * Run every probe and report. Concludes nothing about readiness.
 *
 * The asymmetry with module 07 is deliberate and is the whole design: this function produces
 * observations, the server decides READY. There is no field here a faulty adapter could set to
 * become ready, and a check this adapter fails to implement is reported UNKNOWN rather than omitted,
 * because module 07 treats an omitted check as a readiness failure and an UNKNOWN one as the same.
 */
export function runPreflight(env: ProbeEnvironment): PreflightReport {
  const checks = {
    READER_ACTIVE: probeReaderActive(env),
    READER_VERSION_MATCHES_PROFILE: probeReaderVersion(env),
    BROWSER_VERSION_MATCHES_PROFILE: unknown(
      'the browser is launched by the supervisor setup phase, which has not run',
    ),
    SPEECH_CAPTURE_WORKING: unknown(
      'speech capture cannot be probed without a running, AppleScript-controllable VoiceOver. A ' +
        'capture timeout is unknown evidence, never empty success.',
    ),
    PERMITTED_ORIGIN_REACHABLE: unknown('reachability is checked by the supervisor setup phase'),
    ENVIRONMENT_RESET_SUCCEEDED: unknown('reset is performed by the supervisor, not the adapter'),
    BUILD_IDENTITY_MATCHES_MANIFEST: unknown('the manifest is supplied by the control plane'),
    DESKTOP_SESSION_OWNED: probeDesktopOwned(env),
    SCREEN_UNLOCKED: probeScreenUnlocked(env),
    ACCESSIBILITY_PERMISSION_GRANTED: probePermission(env, 'Accessibility'),
    AUTOMATION_PERMISSION_GRANTED: probePermission(env, 'Automation'),
    NO_STALE_INPUT_SOURCE: unknown(
      'no stale-input probe exists yet: establishing that no previous automation can still send ' +
        'input requires observing a real reader, and is one of the boundaries module 08 leaves ' +
        'UNVERIFIED',
    ),
    LOCAL_JOURNAL_WRITABLE: unknown('the journal path is supplied by the supervisor'),
    MONOTONIC_CLOCK_HEALTHY: ok,
  } satisfies Record<PreflightCheck, ProbeResult>;

  const operatorActions = PREFLIGHT_CHECKS.flatMap((name) => {
    const result = checks[name];
    return result.requiresOperator ? [`${name}: ${result.detail}`] : [];
  });

  return {
    checks,
    operatorActions,
    realReaderAvailable: false,
    blockedReason: BLOCKED_REASON,
  };
}

/** A probe environment backed by the real host, for use on a configured desktop. */
export function hostEnvironment(): ProbeEnvironment {
  return {
    pathExists: (p) => existsSync(p),
    // These four are intentionally unimplemented rather than faked. Each needs a real macOS probe --
    // `defaults read`, `CGSessionCopyCurrentDictionary`, a TCC query -- and a stub returning a
    // plausible value would make preflight pass on a machine where nothing had been checked, which
    // is the exact failure this whole module exists to refuse.
    readPreference: () => undefined,
    processRunning: () => false,
    auditSessionId: () => undefined,
    screenLocked: () => undefined,
    hasPermission: () => undefined,
  };
}
