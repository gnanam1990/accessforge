/**
 * Desktop runner entrypoint.
 *
 * Module 07 implements the supervisor protocol: the local action gate, monotonic lease deadlines,
 * the durable action journal and restart inspection. All of that is real, tested and exported from
 * `./supervisor.js` and `./journal.js`.
 *
 * What is still absent is the half that touches a screen reader. There is no VoiceOver adapter
 * (module 08) and no NVDA adapter (module 09), so this binary has nothing to dispatch an admitted
 * action *to*. It therefore refuses to start rather than running: a runner that came up and reported
 * itself healthy would be enrollable, leasable, and incapable of producing a single reader
 * observation — which is precisely the shape of failure this product exists to refuse.
 *
 * It exits 78 (EX_CONFIG): the capability is absent by configuration, not crashed.
 */

export const NOT_IMPLEMENTED_MESSAGE =
  'accessforge-runner: the supervisor protocol from module 07 is implemented (action gate, ' +
  'monotonic lease deadlines, durable action journal, restart inspection) and is importable from ' +
  'this package. No assistive-technology adapter exists yet: VoiceOver is owned by module 08 and ' +
  'NVDA by module 09. With no adapter there is nothing to dispatch an admitted action to, so this ' +
  'binary starts no runner and reports no screen-reader capability.';

export function main(write: (line: string) => void = console.error): number {
  write(NOT_IMPLEMENTED_MESSAGE);
  return 78; // EX_CONFIG: the feature is absent by configuration, not crashed.
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main());
}
