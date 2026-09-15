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

export async function cli(args: readonly string[], write: (line: string) => void = console.error): Promise<number> {
  if (args.length === 0) return main(write);
  if (args.length !== 4 || args[0] !== '--native-host' || args[2] !== '--handoff-file') {
    write('usage: accessforge-runner [--native-host PRIVATE_OPERATOR.mjs --handoff-file NEW_PRIVATE_PATH]');
    return 64;
  }
  const controller = new AbortController();
  const cancel = () => controller.abort();
  process.on('SIGINT', cancel); process.on('SIGTERM', cancel);
  try {
    await runNativeHost(args[1]!, args[3]!, controller.signal);
    write('accessforge-runner: native host execution closed; consult retained evidence for verdict');
    return 0;
  } catch {
    write('accessforge-runner: native host unconfirmed or unavailable; reconcile original attempt, do not retry');
    return 78;
  } finally {
    process.removeListener('SIGINT', cancel); process.removeListener('SIGTERM', cancel);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = await cli(process.argv.slice(2));
}
