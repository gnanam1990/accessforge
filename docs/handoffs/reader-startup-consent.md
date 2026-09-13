# Reader startup consent — backend draft

Separate branch: `feat/reader-startup-consent`, based on PR37 head75a8794. Not part of that PR.

Implemented locally: migration0038 and persistence methods for exact per-run operator consent,
reviewable pinned Guidepup0.34.0 effect scope, infrastructure-operator attribution, bounded expiry,
irreversible revocation and one-time binding to an authenticated execution session. Run, runner,
physical desktop key, profile, manifest and effects digest must remain identical; run approval is
separately required. Binding uses service audit attribution, not a fabricated human action.

No consent is issued by this work. The dedicated-desktop acknowledgement is a human statement,
not physical evidence or permission to enable OS/TCC settings. Normal target deletion follows the
existing run cascade; audit events remain under the existing audit-retention policy.

Pending before delivery: HTTP/UI review-and-consent/revoke surfaces, machine endpoint integration,
native consumption, focused storage/authorization/migration cases and deployed operator workflow.
The storage binding helper must only be called after machine authentication and its live-session
check within the same transaction. It is not itself a public authentication endpoint.

Python Ruff/mypy passed for the new module. Forward-migration expectations include0038; no local
tests or migration were executed. No PR, push, merge, OS permission or actual reader startup yet.
