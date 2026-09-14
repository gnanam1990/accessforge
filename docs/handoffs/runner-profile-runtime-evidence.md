# Observed runner profile through finalization

Physical preflight now captures seven profile fields from the host: running VoiceOver/Safari,
macOS product version and build, installed Safari version, current Foundation locale and active
Carbon keyboard input-source ID. No field comes from TARGET_MATRIX or the enrolled profile.
The native locale/input probe is read-only and neither selects an input source nor prompts for
permissions. Missing/inactive/unsupported observations leave the entire profile absent.

The authenticated supervisor transmits only this closed bounded profile alongside its existing
action-bound checks, never free-form diagnostic text. The server validates the exact shape and
retains it in the original immutable canonical preflight record. Existing records without a
profile remain supported but cannot supply observed RUNNER_PROFILE identity.

Finalization requires matching profiles on every action, correct canonical ordering, and positive
reader/version/browser/session/input checks. A missing or changed profile stays unknown. A present
malformed profile is refused; a coherent but differently enrolled profile fails identity matching.
Evaluator version is 1.6.0. Existing immutable evaluations are not rewritten.

Enrollment must use the exact observed values: readerVersion includes the macOS build, and
keyboardLayout is the active input-source identifier, not the old human shorthand ANSI. This
does not certify keyboard hardware geometry, VoiceOver voice/verbosity, physical AT execution,
other applications named like Safari, or remote attestation. Real desktop capability proof and
deployment isolation remain required; VERIFIED_MATRICES is unchanged.

Changed-package build/typecheck and changed-file Python static checks are local gates. Synthetic
capture and finalizer coverage plus authenticated rejection cases are included for CI. No actual
VoiceOver startup, permission change, billable provider call, live migration or deployment occurs.

The native read-only Foundation/Carbon probe was executed once on the operator host: it returned
`en_IN` and `com.apple.keylayout.ABC-India`. This validates probe execution only, not reader readiness,
the dedicated execution desktop or the enrolled profile. Locale canonicalization yields `en-IN`.
After the ExternalSSD migration, pnpm attempted dependency relinking and refused without a TTY;
the existing pinned TypeScript compiler was invoked directly instead. No node_modules purge occurred.

Runtime preflight artifacts, required original runner journals and canonical preflight payloads are
now classified with outcome-bearing proof, not disposable diagnostics. Diagnostic cleanup must
preserve them; deleting their proof class
explicitly invalidates completeness. A focused integration regression exercises both deletion paths.
Changed-file Ruff and strict mypy passed; the object-store regression is delegated to CI, not claimed
as locally executed.
