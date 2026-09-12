/**
 * Desktop runner entrypoint.
 *
 * The supervisor and Guidepup-backed VoiceOver adapter are implemented, but an implementation is
 * not the same thing as a proven reader profile. Until a real VoiceOver trace completes on the
 * pinned matrix, this binary exits EX_CONFIG and emits the full fail-closed preflight report.
 */

import {
  hostEnvironment,
  runPreflight,
  type ProbeEnvironment,
} from '@accessforge/at-voiceover';

export const READER_UNAVAILABLE_MESSAGE =
  'accessforge-runner: the Guidepup VoiceOver adapter is implemented and wired to the durable ' +
  'supervisor, but the verified matrix is empty. Until a real VoiceOver run proves the pinned ' +
  'profile, no reader capability is advertised.';

export function main(
  write: (line: string) => void = console.error,
  environment: ProbeEnvironment = hostEnvironment(),
): number {
  const report = runPreflight(environment);
  write(READER_UNAVAILABLE_MESSAGE);
  write(JSON.stringify(report));
  return 78; // EX_CONFIG: code exists, but the real-reader capability remains unproven.
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main());
}
