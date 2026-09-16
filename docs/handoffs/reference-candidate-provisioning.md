# Reference candidate qualification provisioning

`apps/desktop-runner/src/reference-candidate-provisioner.ts` exports
`createReferenceCandidateProvisioner(options)`. A private trusted operator module can export the
returned function as `provisionCandidateProof`, which the existing `--candidate-proof` host loads.
This is the first-profile qualification path, not a canonical candidate repair rerun or enrollment
of a verified matrix. Do not run it under the current build-only/no-VoiceOver-testing instruction.

The options require all of the following from independent operator provisioning:

- A dedicated desktop assignment and original dispatch reference; the observed active session
  does not grant that assignment. All registrations share the same private desktop claim root.
- The live controller artifact-probe capability, independently expected build digest, reserved
  reference fixture, protected setup token and exact Safari form URL. These remain private host
  values, not browser enrollment JSON, committed fixtures or navigator configuration.
- A bounded action list and exact approved typing values. Preparation authorization, reader
  startup authorization and per-action authorization remain separate live functions.
- An independently measured stale-input-source observer returning true, false or unavailable.
  Missing evidence remains UNKNOWN. A PID listing, cooperative desktop claim, current keyboard
  layout or a hard-coded false does not establish absence of previous automation.

The factory snapshots caller data, checks session/build/fixture/action bindings, and returns one
configuration only. Construction/provisioning perform no filesystem writes, socket probes, reader
startup, browser launch, model calls or authorization calls. Cancellation consumes that attempt;
late measurement responses are rejected. The existing candidate host still owns fresh private
output, journal, desktop exclusion, preparation, measured preflight, startup/action authorization,
cleanup and the explicitly labelled local candidate trace. Factory output is not execution proof.

Seven focused synthetic configuration/cancellation tests and desktop TypeScript build passed.
No actual host observation, reader execution or model call was performed. Real private credentials,
live authority callbacks, independent stale-input measurement and physical qualification remain
operator acceptance work; this factory does not silently supply permissive defaults for them.
