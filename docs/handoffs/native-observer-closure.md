# Native controller: concrete independent observer closure

Scope: original modules 08/10/12, FR-006/015, and build-flow audit F1. This is one production
connection in the G2 recovery path, not completion of G2 or the full native operator service.

`runProvisionedNavigatorExecution` now accepts `independentObserver` configuration instead of an
application-authored closure callback. It constructs the concrete one-shot process port before
native startup. The existing navigator owner invokes it only after the navigator child receipt
and clean exit, before native finish. Existing explicit callback integrations remain supported;
supplying both forms is refused. No permissive default is introduced.

The observer launches the installed Python `accessforge_orchestrator.completion_observer` module
with `--final`, the exact workspace/run and one generated source-record identity. Its environment
contains only explicitly provisioned product/observer database URLs and optional TLS certificate
configuration. It does not inherit provider, GitHub, shell or Python-path environment variables.
Python isolated mode ignores the current working directory as an import source. The trusted host
owns the executable and installed package; this is not isolation against malicious same-user code.

Observer credentials/configuration are removed from the planner options and never enter its child
input. A bounded exact KNOWN/exit-0 or UNKNOWN/exit-3 response acknowledges producer closure only;
UNKNOWN remains an unknown measurement and never a PASS. Native finish still rechecks retained
server evidence. Raw child diagnostics are not returned. Cancellation, expiry, malformed/oversized
output or failure fences the one-shot closure and terminates only its owned child, escalating after
two seconds. Lost acknowledgement may follow a committed measurement: reconcile, never retry the
closure automatically or label process termination a database rollback.

## Validation

- TypeScript compilation passed with the installed compiler:
  `node apps/desktop-runner/node_modules/typescript/bin/tsc -p apps/desktop-runner/tsconfig.json`.
- Focused observer-process and existing navigator-process files: 17 tests passed, 0 failed/skipped.
  These use real child processes with explicitly synthetic executables; they do not access a
  database, run VoiceOver or invoke a provider. This is not full Python-observer/DB acceptance.
- `git diff --check` passed.
- The pnpm build wrapper attempted an automatic dependency refresh and refused a non-TTY module
  purge. No purge was approved. The already-installed pinned TypeScript compiler was used directly.
- No actual reader, model, deployment, permission modification or live migration was performed.

## Next unfinished G2 boundaries

Continue F1: concrete operator-owned dispatch/start transport and host runtime/action evidence
configuration, wired into the shipped entrypoint and baseline completion. The current diagnostic
`accessforge-runner` still exits 78; its production path is not enabled by this change. Preserve the
separate first-profile capability-proof path and original journal/STOP/retention identity.

F3 still needs independent forbidden-effect coverage and executable assertion admission. Neither
this final application count nor a zero count establishes absence of transient/external effects.
Actual reader qualification/provider use and complete original baseline evidence remain separate
acceptance gates. Continue original G2 → G3 → G4 → remaining R1, not peripheral feature expansion.
