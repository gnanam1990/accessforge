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
import { runNativeHost } from './native-host.js';
import { runCandidateHost } from './candidate-host.js';
import { observeEnrollment } from './enrollment-observation.js';

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

export async function cli(args: readonly string[], write: (line: string) => void = console.error,
  environment: ProbeEnvironment = hostEnvironment(), output: (line: string) => void = console.log): Promise<number> {
  if (args.length === 1 && args[0] === '--enrollment-observation') {
    try {
      output(JSON.stringify(observeEnrollment(environment)));
      return 0;
    } catch {
      write('accessforge-runner: enrollment observation unavailable or changed; nothing enrolled and no reader started');
      return 78;
    }
  }
  if (args.length === 0) return main(write, environment);
  const candidate = args.length === 5 && args[0] === '--candidate-proof' &&
    args[2] === '--output-dir' && args[4] === '--allow-reader-startup';
  if (!candidate && (args.length !== 4 || args[0] !== '--native-host' || args[2] !== '--handoff-file')) {
    write('usage: accessforge-runner --enrollment-observation or [--native-host PRIVATE_OPERATOR.mjs --handoff-file NEW_PRIVATE_PATH] or --candidate-proof PRIVATE_OPERATOR.mjs --output-dir NEW_PRIVATE_DIRECTORY --allow-reader-startup');
    return 64;
  }
  const controller = new AbortController();
  const cancel = () => controller.abort();
  process.on('SIGINT', cancel); process.on('SIGTERM', cancel);
  try {
    if (candidate) {
      const result = await runCandidateHost(args[1]!, args[3]!, controller.signal);
      write(`accessforge-runner: ${result.status}; local unauthenticated candidate proof only, not a verified profile or canonical verdict`);
      return result.status === 'CANDIDATE_COMPLETE' ? 0 : 78;
    }
    await runNativeHost(args[1]!, args[3]!, controller.signal);
    write('accessforge-runner: native host execution closed; consult retained evidence for verdict');
    return 0;
  } catch {
    write(`accessforge-runner: ${candidate ? 'candidate proof' : 'native host'} unconfirmed or unavailable; reconcile original attempt, do not retry`);
    return 78;
  } finally {
    process.removeListener('SIGINT', cancel); process.removeListener('SIGTERM', cancel);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = await cli(process.argv.slice(2));
}
