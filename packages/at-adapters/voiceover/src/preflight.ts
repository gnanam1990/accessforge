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
import { spawnSync } from 'node:child_process';

import { BLOCKED_REASON, TARGET_MACOS_VERSION, TARGET_MATRIX } from './profile.js';

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
  /** Exact Safari marketing version reported by the installed application bundle. */
  readonly browserVersion?: () => string | undefined;
}

/** Observations produced by the supervisor's owned setup phase, never by the navigator. */
export interface RuntimeProbeEvidence {
  readonly speechCaptureWorking?: boolean;
  readonly permittedOrigin?: string;
  readonly observedOrigin?: string;
  readonly originReachable?: boolean;
  readonly environmentResetSucceeded?: boolean;
  readonly expectedBuildDigest?: string;
  readonly observedBuildDigest?: string;
  readonly staleInputSourceDetected?: boolean;
  readonly journalWritable?: boolean;
  readonly monotonicClockHealthy?: boolean;
}

export interface CommandResult {
  readonly status: number | null;
  readonly stdout: string;
  readonly stderr: string;
}

export type CommandRunner = (
  executable: string,
  args: readonly string[],
  input?: string,
) => CommandResult;

export interface HostEnvironmentOptions {
  readonly run?: CommandRunner;
  readonly pathExists?: (path: string) => boolean;
}

const systemRun: CommandRunner = (executable, args, input) => {
  const result = spawnSync(executable, [...args], {
    encoding: 'utf8',
    timeout: 5_000,
    ...(input !== undefined ? { input } : {}),
  });
  return {
    status: result.status,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
  };
};

interface ConsoleSession {
  readonly kCGSSessionOnConsoleKey?: boolean;
  readonly kCGSessionLoginDoneKey?: boolean;
  readonly kCGSSessionAuditIDKey?: number;
  readonly CGSSessionScreenIsLocked?: boolean;
}

function readConsoleSession(run: CommandRunner): ConsoleSession | undefined {
  const raw = run('/usr/sbin/ioreg', ['-n', 'Root', '-d1', '-a']);
  if (raw.status !== 0 || raw.stdout === '') return undefined;

  const converted = run('/usr/bin/plutil', ['-convert', 'json', '-o', '-', '-'], raw.stdout);
  if (converted.status !== 0) return undefined;

  try {
    const root = JSON.parse(converted.stdout) as { readonly IOConsoleUsers?: ConsoleSession[] };
    return root.IOConsoleUsers?.find(
      (session) =>
        session.kCGSSessionOnConsoleKey === true && session.kCGSessionLoginDoneKey === true,
    );
  } catch {
    return undefined;
  }
}

const ACCESSIBILITY_PROBE =
  'import ApplicationServices; print(AXIsProcessTrusted() ? "TRUE" : "FALSE")';

// `false` is the important fourth argument: Apple documents that it returns -1744 when consent
// would be required instead of opening a permission dialog from a health check.
const AUTOMATION_PROBE =
  'import Cocoa; import Carbon; ' +
  'let target = NSAppleEventDescriptor(bundleIdentifier: "com.apple.VoiceOver"); ' +
  'print(AEDeterminePermissionToAutomateTarget(target.aeDesc, typeWildCard, typeWildCard, false))';

function readBoolean(output: CommandResult): boolean | undefined {
  if (output.status !== 0) return undefined;
  const value = output.stdout.trim();
  if (value === 'TRUE' || value === 'true' || value === '1') return true;
  if (value === 'FALSE' || value === 'false' || value === '0') return false;
  return undefined;
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
  // Exact equality, not a prefix match. `'26.6 (build …)'.startsWith('26')` is true, so the prefix
  // version of this check accepted macOS 26 as macOS 26.6 -- a whole release apart, with different
  // VoiceOver announcements, reported as the pinned profile.
  if (build !== TARGET_MACOS_VERSION) {
    return no(
      `this host reports macOS ${build} and the pinned profile is ${TARGET_MACOS_VERSION}. ` +
        'VoiceOver announces differently between releases, so a run here is not a run on the ' +
        'pinned profile.',
    );
  }
  return ok;
}

export function probeBrowserVersion(env: ProbeEnvironment): ProbeResult {
  const version = env.browserVersion?.();
  if (version === undefined || version === '') {
    return unknown('the installed Safari version could not be read');
  }
  if (version !== TARGET_MATRIX.browserVersion) {
    return no(
      `this host reports ${TARGET_MATRIX.browser} ${version} and the pinned profile is ` +
        `${TARGET_MATRIX.browser} ${TARGET_MATRIX.browserVersion}`,
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
function observedBoolean(
  value: boolean | undefined,
  trueDetail: string,
  falseDetail: string,
): ProbeResult {
  if (value === undefined) return unknown(trueDetail);
  return value ? ok : no(falseDetail);
}

function probeOrigin(evidence: RuntimeProbeEvidence): ProbeResult {
  if (
    evidence.permittedOrigin === undefined ||
    evidence.observedOrigin === undefined ||
    evidence.originReachable === undefined
  ) {
    return unknown(
      'the supervisor has not supplied the permitted origin, observed browser origin and ' +
        'reachability result together',
    );
  }
  if (evidence.observedOrigin !== evidence.permittedOrigin) {
    return no(
      `observed origin ${evidence.observedOrigin} does not equal the sealed permitted origin ` +
        evidence.permittedOrigin,
    );
  }
  return evidence.originReachable
    ? ok
    : no(`the sealed permitted origin ${evidence.permittedOrigin} is not reachable`);
}

function probeBuildIdentity(evidence: RuntimeProbeEvidence): ProbeResult {
  if (
    evidence.expectedBuildDigest === undefined ||
    evidence.observedBuildDigest === undefined
  ) {
    return unknown('the expected and observed build digests were not both supplied');
  }
  return evidence.expectedBuildDigest === evidence.observedBuildDigest
    ? ok
    : no(
        `observed build digest ${evidence.observedBuildDigest} does not equal manifest digest ` +
          evidence.expectedBuildDigest,
      );
}

export function runPreflight(
  env: ProbeEnvironment,
  evidence: RuntimeProbeEvidence = {},
): PreflightReport {
  const checks = {
    READER_ACTIVE: probeReaderActive(env),
    READER_VERSION_MATCHES_PROFILE: probeReaderVersion(env),
    BROWSER_VERSION_MATCHES_PROFILE: probeBrowserVersion(env),
    SPEECH_CAPTURE_WORKING: observedBoolean(
      evidence.speechCaptureWorking,
      'speech capture has not been exercised by the supervisor setup phase',
      'the VoiceOver speech-capture probe failed or timed out; this is unknown evidence, not silence',
    ),
    PERMITTED_ORIGIN_REACHABLE: probeOrigin(evidence),
    ENVIRONMENT_RESET_SUCCEEDED: observedBoolean(
      evidence.environmentResetSucceeded,
      'the supervisor has not supplied a reset result',
      'the owned reference environment reset failed',
    ),
    BUILD_IDENTITY_MATCHES_MANIFEST: probeBuildIdentity(evidence),
    DESKTOP_SESSION_OWNED: probeDesktopOwned(env),
    SCREEN_UNLOCKED: probeScreenUnlocked(env),
    ACCESSIBILITY_PERMISSION_GRANTED: probePermission(env, 'Accessibility'),
    AUTOMATION_PERMISSION_GRANTED: probePermission(env, 'Automation'),
    NO_STALE_INPUT_SOURCE: observedBoolean(
      evidence.staleInputSourceDetected === undefined
        ? undefined
        : !evidence.staleInputSourceDetected,
      'the supervisor has not supplied a stale-input-source result',
      'a previous automation input source is still active',
    ),
    LOCAL_JOURNAL_WRITABLE: observedBoolean(
      evidence.journalWritable,
      'the supervisor has not supplied a durable-journal write result',
      'the local action journal could not be written and fsynced',
    ),
    MONOTONIC_CLOCK_HEALTHY: observedBoolean(
      evidence.monotonicClockHealthy ?? true,
      'the monotonic clock has not been sampled',
      'the monotonic clock moved backwards or could not be trusted',
    ),
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

/**
 * A probe environment backed by explicit, read-only macOS commands.
 *
 * None of these commands grants a permission, starts VoiceOver, changes a preference, or prompts.
 * In particular, the Automation API is called with `askUserIfNeeded=false`.
 */
export function createHostEnvironment(options: HostEnvironmentOptions = {}): ProbeEnvironment {
  const run = options.run ?? systemRun;
  const pathExists = options.pathExists ?? existsSync;
  return {
    pathExists,
    readPreference: (domain, key) => {
      const result = run('/usr/bin/defaults', ['read', domain, key]);
      return result.status === 0 ? result.stdout.trim() : undefined;
    },
    processRunning: (name) => {
      const result =
        name === 'VoiceOver'
          ? run('/usr/bin/pgrep', ['-f', 'VoiceOver launchd -s'])
          : run('/usr/bin/pgrep', ['-x', name]);
      return result.status === 0 && result.stdout.trim() !== '';
    },
    auditSessionId: () => {
      const session = readConsoleSession(run);
      return session?.kCGSSessionAuditIDKey?.toString();
    },
    screenLocked: () => {
      const session = readConsoleSession(run);
      if (session === undefined) return undefined;
      // The key is present and true while macOS has locked this console. Its absence on the active,
      // completed console session is the unlocked state; an inactive/incomplete session was already
      // rejected by readConsoleSession rather than interpreted as unlocked.
      return session.CGSSessionScreenIsLocked ?? false;
    },
    hasPermission: (permission) => {
      if (permission === 'Accessibility') {
        return readBoolean(run('/usr/bin/xcrun', ['swift', '-e', ACCESSIBILITY_PROBE]));
      }
      const result = run('/usr/bin/xcrun', ['swift', '-e', AUTOMATION_PROBE]);
      if (result.status !== 0) return undefined;
      const status = Number.parseInt(result.stdout.trim(), 10);
      if (status === 0) return true;
      // -1743: denied. -1744: would require consent, and no prompt was shown.
      if (status === -1743 || status === -1744) return false;
      // -600 means VoiceOver is not running, so macOS cannot answer about that target yet.
      return undefined;
    },
    browserVersion: () => {
      const result = run('/usr/bin/defaults', [
        'read',
        '/Applications/Safari.app/Contents/Info',
        'CFBundleShortVersionString',
      ]);
      return result.status === 0 ? result.stdout.trim() : undefined;
    },
  };
}

/** Backwards-compatible name used by the original module handoff. */
export function hostEnvironment(): ProbeEnvironment {
  return createHostEnvironment();
}
