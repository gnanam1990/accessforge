/**
 * Desktop runner entrypoint.
 *
 * Module 01 establishes this as a real build and packaging target. It deliberately does NOT
 * implement runner enrollment, desktop leases, action admission, or screen-reader control —
 * those belong to modules 07 and 08 and to packages/at-adapters.
 *
 * It exits non-zero and says so. A stub that reported success would be indistinguishable from a
 * working runner to anything downstream, which is exactly the failure this project treats as
 * unacceptable.
 */

export const NOT_IMPLEMENTED_MESSAGE =
  'accessforge-runner: not implemented. Runner admission and screen-reader execution are owned ' +
  'by modules 07 (control plane) and 08 (macOS VoiceOver). This binary exists so the workspace ' +
  'has a real build target; it performs no assistive-technology work and reports no capability.';

export function main(write: (line: string) => void = console.error): number {
  write(NOT_IMPLEMENTED_MESSAGE);
  return 78; // EX_CONFIG: the feature is absent by configuration, not crashed.
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main());
}
