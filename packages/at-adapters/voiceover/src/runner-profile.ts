/** Host-observed profile values, never copied from the target matrix or enrollment claim. */
import type { ProbeEnvironment } from './preflight.js';

export interface ObservedRunnerProfile {
  readonly platform: 'darwin';
  readonly readerName: 'VoiceOver';
  readonly readerVersion: string;
  readonly browserName: 'Safari';
  readonly browserVersion: string;
  readonly locale: string;
  readonly keyboardLayout: string;
}

export function parseObservedRunnerProfile(value: unknown): ObservedRunnerProfile | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  const fields = ['platform', 'readerName', 'readerVersion', 'browserName', 'browserVersion', 'locale', 'keyboardLayout'];
  if (Object.keys(record).length !== fields.length || fields.some((key) =>
    typeof record[key] !== 'string' || record[key].trim().length < 1 || record[key].length > 128 ||
    !/^[A-Za-z0-9 ._():-]+$/.test(record[key]))) return undefined;
  if (record.platform !== 'darwin' || record.readerName !== 'VoiceOver' || record.browserName !== 'Safari') return undefined;
  return Object.freeze(Object.fromEntries(fields.map((key) => [key, record[key]]))) as unknown as ObservedRunnerProfile;
}

export function observeRunnerProfile(env: ProbeEnvironment): ObservedRunnerProfile | undefined {
  if (env.platform?.() !== 'darwin' || !env.processRunning('VoiceOver') || !env.processRunning('Safari')) return undefined;
  const version = env.readPreference('/System/Library/CoreServices/SystemVersion', 'ProductVersion');
  const build = env.readPreference('/System/Library/CoreServices/SystemVersion', 'ProductBuildVersion');
  const browser = env.browserVersion?.();
  const input = env.localeAndKeyboard?.();
  if (version === undefined || !/^[0-9]+(?:\.[0-9]+){1,2}$/.test(version) ||
      build === undefined || !/^[A-Za-z0-9]{1,32}$/.test(build) || browser === undefined ||
      !/^[0-9]+(?:\.[0-9]+){1,3}$/.test(browser) || input === undefined) return undefined;
  try {
    const locale = new Intl.Locale(input.locale.replaceAll('_', '-')).toString();
    return parseObservedRunnerProfile({ platform: 'darwin', readerName: 'VoiceOver',
      readerVersion: `bundled with macOS ${version} (build ${build})`, browserName: 'Safari',
      browserVersion: browser, locale, keyboardLayout: input.keyboardLayout });
  } catch { return undefined; }
}
