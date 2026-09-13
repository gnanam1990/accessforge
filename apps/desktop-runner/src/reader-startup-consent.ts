/** Pinned SDK effect contract, matching the human review surface. No OS capability. */
import { digest } from '@accessforge/contracts';

export const READER_STARTUP_EFFECTS_DIGEST = digest({
  schemaVersion: 1, sdk: '@guidepup/guidepup', sdkVersion: '0.34.0', reader: 'VoiceOver',
  effects: ['TERMINATE_AND_RESTART_VOICEOVER', 'MOUNT_GUIDEPUP_READER_PREFERENCES',
    'SDK_INTERNAL_STARTUP_ATTEMPTS', 'RESTORE_PREFERENCES_DURING_NORMAL_STOP'],
  requiresDedicatedDesktop: true,
  doesNotAuthorize: ['GRANT_TCC_PERMISSIONS', 'INITIAL_APPLESCRIPT_CONFIGURATION'],
});

export interface ReaderStartupConsentScope {
  readonly consentId: string;
  readonly manifestDigest: string;
  readonly desktopSessionKey: string;
  readonly runnerProfileDigest: string;
  readonly effectsDigest: string;
}

/** Independently provisioned scope, never a navigator-selected grant or server-response echo. */
export function parseReaderStartupConsentScope(value: unknown): Readonly<ReaderStartupConsentScope> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('startup consent scope unavailable');
  const row = value as Record<string, unknown>;
  const keys = ['consentId', 'manifestDigest', 'desktopSessionKey', 'runnerProfileDigest', 'effectsDigest'];
  if (Object.keys(row).length !== keys.length || keys.some((key) => !Object.hasOwn(row, key)) ||
      typeof row.consentId !== 'string' || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(row.consentId) ||
      keys.slice(1).some((key) => typeof row[key] !== 'string' || !/^[0-9a-f]{64}$/.test(row[key] as string)) ||
      row.effectsDigest !== READER_STARTUP_EFFECTS_DIGEST) throw new Error('startup consent scope differs from pinned reader effects');
  return Object.freeze({ consentId: row.consentId, manifestDigest: row.manifestDigest as string,
    desktopSessionKey: row.desktopSessionKey as string, runnerProfileDigest: row.runnerProfileDigest as string,
    effectsDigest: row.effectsDigest as string });
}
